"""
Risk Persistence Service
========================
Saves PredictionEngine results to fact_student_risk and reads them back.

Schema (testDB — EarlyAlert v2)
--------------------------------
fact_student_risk
  PK  fact_id             serial
  NK  student_id          varchar(20)         ← natural key from portals
      academic_year       varchar(20)
      semester            smallint            ← 1 or 2
      model_run_id        integer             ← FK merge_log.run_id (optional)
      predicted_at        timestamp

  -- Feature groups written from unified dataset --
      program, college, sec_code, year_level, final_avg_grd
      sex_code, home_address, civil_status, birthdate, year_enrolled
      entrance_exam_score, family_income, parent_highest_education
      hs_gpa, year_graduated, shs_strand, hs_type, graduation_honors, hs_school_name
      scholarship_applicant, scholarship_type

  -- ML outputs --
      risk_score          numeric(5,4)        ← probability 0.0–1.0
      risk_level_id       smallint            ← FK dim_risk_level (1=Low,2=Medium,3=High)
      risk_label          varchar(20)         ← 'Low' | 'Medium' | 'High'
      confidence          numeric(5,4)
      top_risk_factor     varchar(255)

  UNIQUE (student_id, academic_year, semester)

dim_risk_level  (1=Low, 2=Medium, 3=High)  — seeded by schema, read-only here.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

import psycopg2

from database.connection import get_connection


# ---------------------------------------------------------------------------
# Risk level mapping  (matches dim_risk_level seed data in schema)
# ---------------------------------------------------------------------------
_RISK_LEVEL_MAP: Dict[str, int] = {
    # PredictionEngine category strings
    "low_risk":      1,
    "moderate_risk": 2,
    "high_risk":     3,
    # Normalised label strings (fallback)
    "low":           1,
    "medium":        2,
    "moderate":      2,
    "high":          3,
}

_RISK_LABEL_MAP: Dict[str, str] = {
    "low_risk":      "Low",
    "moderate_risk": "Medium",
    "high_risk":     "High",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_numeric(value: str, cast) -> Optional[Any]:
    """Cast a string to a numeric type; return None on blank or error."""
    v = str(value).strip()
    if not v or v in ("—", "None", "nan"):
        return None
    try:
        return cast(v)
    except (ValueError, TypeError):
        return None


def _to_bool(value: str) -> Optional[bool]:
    """Convert a string scholarship_applicant value to bool."""
    v = str(value).strip().lower()
    if not v or v in ("—", "none", "nan", ""):
        return None
    return v in ("true", "1", "yes", "t", "y", "with", "approved", "scholar")


def _to_date(value: str) -> Optional[date]:
    """Parse a date string to a date object; return None on failure."""
    v = str(value).strip()
    if not v or v in ("—", "None", "nan"):
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def _semester_int(semester) -> int:
    """Normalise semester to 1 or 2."""
    s = str(semester).strip().lower()
    if s.startswith("2"):
        return 2
    return 1


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------

class RiskPersistenceService:
    """Persist / retrieve prediction results against the EarlyAlert v2 schema."""

    # ------------------------------------------------------------------
    # WRITE — called from PredictionMixin after every successful run
    # ------------------------------------------------------------------

    @staticmethod
    def save_predictions(
        predictions: List[Dict[str, Any]],
        model_id: str = "rf",
        academic_year: str = "2024-2025",
        semester: str = "1",
        model_run_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Upsert prediction results into fact_student_risk.

        Each student row is saved inside its own SAVEPOINT so a single bad
        record never aborts the whole batch.

        Returns
        -------
        dict  {"success": bool, "inserted": int, "updated": int,
               "errors": list[str], "total_processed": int}
        """
        conn = None
        try:
            conn = get_connection()
            if not conn:
                return {"success": False, "error": "Could not connect to database."}

            cur = conn.cursor()
            sem = _semester_int(semester)

            inserted = 0
            updated  = 0
            errors: List[str] = []

            for pred in predictions:
                sid = pred.get("student_id", "unknown")
                try:
                    cur.execute("SAVEPOINT sp_pred")
                    action = RiskPersistenceService._upsert_one(
                        cur, pred, sem, academic_year, model_run_id
                    )
                    cur.execute("RELEASE SAVEPOINT sp_pred")
                    if action == "insert":
                        inserted += 1
                    else:
                        updated += 1

                except Exception as exc:
                    cur.execute("ROLLBACK TO SAVEPOINT sp_pred")
                    cur.execute("RELEASE SAVEPOINT sp_pred")
                    msg = f"Student {sid}: {exc}"
                    errors.append(msg)
                    print(f"[RiskPersistence] {msg}")

            conn.commit()

            print(
                f"[RiskPersistence] Done — inserted={inserted}, "
                f"updated={updated}, errors={len(errors)}"
            )
            return {
                "success": True,
                "inserted": inserted,
                "updated":  updated,
                "errors":   errors,
                "total_processed": len(predictions),
            }

        except Exception as exc:
            if conn:
                conn.rollback()
            print(f"[RiskPersistence] Fatal error: {exc}")
            return {"success": False, "error": str(exc)}

        finally:
            if conn:
                conn.close()

    # ------------------------------------------------------------------
    # Internal — single-row upsert aligned to the v2 schema
    # ------------------------------------------------------------------

    @staticmethod
    def _upsert_one(
        cur,
        pred: Dict[str, Any],
        semester: int,
        academic_year: str,
        model_run_id: Optional[int],
    ) -> str:
        """
        INSERT … ON CONFLICT (student_id, academic_year, semester) DO UPDATE.

        Returns 'insert' or 'update' based on xmax heuristic.
        """
        student_id = str(pred.get("student_id", "")).strip()
        if not student_id:
            raise ValueError("Prediction missing student_id")

        meta = pred  # student_meta dict merged into pred by PredictionEngine

        # ── Risk outputs ──────────────────────────────────────────────
        # PredictionEngine score is 0–100; schema stores 0.0–1.0
        raw_score    = float(pred.get("score", 0))
        risk_score   = round(raw_score / 100.0, 4)
        category     = pred.get("category", "low_risk")
        risk_level_id = _RISK_LEVEL_MAP.get(category.lower(), 1)
        risk_label   = _RISK_LABEL_MAP.get(category.lower(), "Low")
        top_factor   = str(pred.get("factor", "") or "")[:255]

        # ── Feature fields from student_meta ─────────────────────────
        program  = str(meta.get("program", "") or "")[:100]
        college  = str(meta.get("college", "") or "")[:100]
        sec_code = str(meta.get("sec_code", "") or "")[:50]

        year_level   = _to_numeric(meta.get("year_level", ""), int)
        final_avg_grd = _to_numeric(meta.get("gwa", ""), float)

        sex_code     = str(meta.get("sex_code", "") or "")[:10]
        home_address = str(meta.get("home_address", "") or "")
        civil_status = str(meta.get("civil_status", "") or "")[:20]
        birthdate    = _to_date(meta.get("birthdate", ""))
        year_enrolled = _to_numeric(meta.get("year_enrolled", ""), int)

        entrance_exam_score       = _to_numeric(meta.get("entrance_exam_score", ""), float)
        family_income             = str(meta.get("family_income", "") or "")[:100]
        parent_highest_education  = str(meta.get("parent_highest_education", "") or "")[:150]

        hs_gpa           = _to_numeric(meta.get("hs_gpa", ""), float)
        year_graduated   = _to_numeric(meta.get("year_graduated", ""), int)
        shs_strand       = str(meta.get("shs_strand", "") or "")[:100]
        hs_type          = str(meta.get("hs_type", "") or "")[:100]
        graduation_honors = str(meta.get("graduation_honors", "") or "")[:100]
        hs_school_name   = str(meta.get("hs_school_name", "") or "")[:255]

        scholarship_applicant = _to_bool(meta.get("scholarship_applicant", ""))
        scholarship_type      = str(meta.get("scholarship_type", "") or "")[:100]

        now = datetime.now()

        upsert_sql = """
            INSERT INTO public.fact_student_risk (
                student_id,
                academic_year,
                semester,
                model_run_id,
                predicted_at,
                program,
                college,
                sec_code,
                year_level,
                final_avg_grd,
                sex_code,
                home_address,
                civil_status,
                birthdate,
                year_enrolled,
                entrance_exam_score,
                family_income,
                parent_highest_education,
                hs_gpa,
                year_graduated,
                shs_strand,
                hs_type,
                graduation_honors,
                hs_school_name,
                scholarship_applicant,
                scholarship_type,
                risk_score,
                risk_level_id,
                risk_label,
                confidence,
                top_risk_factor
            )
            VALUES (
                %(student_id)s,
                %(academic_year)s,
                %(semester)s,
                %(model_run_id)s,
                %(predicted_at)s,
                %(program)s,
                %(college)s,
                %(sec_code)s,
                %(year_level)s,
                %(final_avg_grd)s,
                %(sex_code)s,
                %(home_address)s,
                %(civil_status)s,
                %(birthdate)s,
                %(year_enrolled)s,
                %(entrance_exam_score)s,
                %(family_income)s,
                %(parent_highest_education)s,
                %(hs_gpa)s,
                %(year_graduated)s,
                %(shs_strand)s,
                %(hs_type)s,
                %(graduation_honors)s,
                %(hs_school_name)s,
                %(scholarship_applicant)s,
                %(scholarship_type)s,
                %(risk_score)s,
                %(risk_level_id)s,
                %(risk_label)s,
                %(confidence)s,
                %(top_risk_factor)s
            )
            ON CONFLICT (student_id, academic_year, semester)
            DO UPDATE SET
                model_run_id             = EXCLUDED.model_run_id,
                predicted_at             = EXCLUDED.predicted_at,
                program                  = EXCLUDED.program,
                college                  = EXCLUDED.college,
                sec_code                 = EXCLUDED.sec_code,
                year_level               = EXCLUDED.year_level,
                final_avg_grd            = EXCLUDED.final_avg_grd,
                sex_code                 = EXCLUDED.sex_code,
                home_address             = EXCLUDED.home_address,
                civil_status             = EXCLUDED.civil_status,
                birthdate                = EXCLUDED.birthdate,
                year_enrolled            = EXCLUDED.year_enrolled,
                entrance_exam_score      = EXCLUDED.entrance_exam_score,
                family_income            = EXCLUDED.family_income,
                parent_highest_education = EXCLUDED.parent_highest_education,
                hs_gpa                   = EXCLUDED.hs_gpa,
                year_graduated           = EXCLUDED.year_graduated,
                shs_strand               = EXCLUDED.shs_strand,
                hs_type                  = EXCLUDED.hs_type,
                graduation_honors        = EXCLUDED.graduation_honors,
                hs_school_name           = EXCLUDED.hs_school_name,
                scholarship_applicant    = EXCLUDED.scholarship_applicant,
                scholarship_type         = EXCLUDED.scholarship_type,
                risk_score               = EXCLUDED.risk_score,
                risk_level_id            = EXCLUDED.risk_level_id,
                risk_label               = EXCLUDED.risk_label,
                confidence               = EXCLUDED.confidence,
                top_risk_factor          = EXCLUDED.top_risk_factor
            RETURNING fact_id, xmax
        """

        cur.execute(upsert_sql, {
            "student_id":               student_id,
            "academic_year":            academic_year,
            "semester":                 semester,
            "model_run_id":             model_run_id,
            "predicted_at":             now,
            "program":                  program,
            "college":                  college,
            "sec_code":                 sec_code or None,
            "year_level":               year_level,
            "final_avg_grd":            final_avg_grd,
            "sex_code":                 sex_code or None,
            "home_address":             home_address or None,
            "civil_status":             civil_status or None,
            "birthdate":                birthdate,
            "year_enrolled":            year_enrolled,
            "entrance_exam_score":      entrance_exam_score,
            "family_income":            family_income or None,
            "parent_highest_education": parent_highest_education or None,
            "hs_gpa":                   hs_gpa,
            "year_graduated":           year_graduated,
            "shs_strand":               shs_strand or None,
            "hs_type":                  hs_type or None,
            "graduation_honors":        graduation_honors or None,
            "hs_school_name":           hs_school_name or None,
            "scholarship_applicant":    scholarship_applicant,
            "scholarship_type":         scholarship_type or None,
            "risk_score":               risk_score,
            "risk_level_id":            risk_level_id,
            "risk_label":               risk_label,
            "confidence":               risk_score,   # same as risk_score
            "top_risk_factor":          top_factor or None,
        })

        row = cur.fetchone()
        # xmax == 0  →  fresh insert;  xmax != 0  →  updated existing row
        return "insert" if (row and row[1] == 0) else "update"

    # ------------------------------------------------------------------
    # READ — used by RiskAlertsPage / DashboardPage
    # ------------------------------------------------------------------

    @staticmethod
    def get_latest_predictions(
        academic_year: Optional[str] = None,
        semester: Optional[int] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """
        Fetch the latest risk snapshot from fact_student_risk via
        v_student_risk_summary (joins dim_risk_level for color / description).

        Falls back to a direct table query if the view is unavailable.
        """
        conn = None
        try:
            conn = get_connection()
            if not conn:
                return []

            cur = conn.cursor()

            # Build optional WHERE clause
            filters: List[str] = []
            params:  List[Any] = []
            if academic_year:
                filters.append("academic_year = %s")
                params.append(academic_year)
            if semester is not None:
                filters.append("semester = %s")
                params.append(_semester_int(semester))

            where = ("WHERE " + " AND ".join(filters)) if filters else ""
            params.append(limit)

            try:
                cur.execute(
                    f"""
                    SELECT *
                    FROM   public.v_student_risk_summary
                    {where}
                    ORDER  BY risk_score DESC
                    LIMIT  %s
                    """,
                    params,
                )
            except psycopg2.Error:
                # View not yet available — fall back to raw table
                conn.rollback()
                cur.execute(
                    f"""
                    SELECT fact_id, student_id, academic_year, semester,
                           program, college, year_level, final_avg_grd,
                           sex_code, entrance_exam_score, hs_gpa,
                           scholarship_applicant, risk_score, risk_label,
                           confidence, top_risk_factor, predicted_at
                    FROM   public.fact_student_risk
                    {where}
                    ORDER  BY risk_score DESC
                    LIMIT  %s
                    """,
                    params,
                )

            columns = [d[0] for d in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

        except Exception as exc:
            print(f"[RiskPersistence] get_latest_predictions error: {exc}")
            return []
        finally:
            if conn:
                conn.close()
