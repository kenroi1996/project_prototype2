"""
Risk Persistence Service
========================
Saves PredictionEngine results into the EarlyAlert star schema.

Target tables (in dependency order)
-------------------------------------
  dim_student              — one row per student, upserted on student_id
  dim_program              — one row per program code, upserted on program_code
  dim_academic_term        — one row per (academic_year, semester) pair
  fact_student_academic_risk — one row per prediction run per student

Write strategy
--------------
  - dim_* tables: INSERT … ON CONFLICT … DO UPDATE (upsert)
    Dimension rows are created on first encounter and refreshed with the
    latest values on every subsequent prediction run.
  - fact table: plain INSERT (no unique constraint on the table — each
    prediction run produces a new snapshot row).
  - Every student row is wrapped in its own SAVEPOINT so a single bad
    record never aborts the entire batch.
  - dim lookups (student_key, program_key, term_key) are resolved once per
    batch where possible, then cached in a local dict to avoid N+1 queries.

Risk level mapping  (matches dim_risk_level seed rows)
-------------------------------------------------------
  1 = Low       (low_risk)
  2 = Medium    (moderate_risk)
  3 = High      (high_risk)
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from database.connection import get_connection


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RISK_LEVEL_MAP: Dict[str, int] = {
    "low_risk":      1,
    "moderate_risk": 2,
    "high_risk":     3,
    "low":           1,
    "medium":        2,
    "moderate":      2,
    "high":          3,
}


# ---------------------------------------------------------------------------
# Type helpers
# ---------------------------------------------------------------------------

def _str(value, maxlen: int = None) -> Optional[str]:
    """Strip and truncate a value; return None if blank/sentinel."""
    v = str(value).strip() if value is not None else ""
    if not v or v in ("—", "None", "nan", "none"):
        return None
    return v[:maxlen] if maxlen else v


def _num(value, cast):
    """Cast to numeric type; return None on blank or error."""
    v = str(value).strip() if value is not None else ""
    if not v or v in ("—", "None", "nan"):
        return None
    try:
        return cast(v)
    except (ValueError, TypeError):
        return None


def _bool(value) -> Optional[bool]:
    v = str(value).strip().lower() if value is not None else ""
    if not v or v in ("—", "none", "nan", ""):
        return None
    return v in ("true", "1", "yes", "t", "y", "with", "approved", "scholar")


def _date(value) -> Optional[date]:
    v = str(value).strip() if value is not None else ""
    if not v or v in ("—", "None", "nan"):
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def _semester(value) -> int:
    return 2 if str(value).strip().startswith("2") else 1


# ---------------------------------------------------------------------------
# Dimension upserts  (return the surrogate key)
# ---------------------------------------------------------------------------

def _upsert_student(cur, pred: Dict[str, Any]) -> int:
    """
    Upsert dim_student on student_id.
    Returns student_key.
    """
    cur.execute("""
        INSERT INTO public.dim_student (
            student_id, first_name, last_name, sex_code, birthdate,
            civil_status, home_address, home_municipality,
            family_income_bracket, parent_highest_education,
            hs_school_name, hs_type, shs_strand, graduation_honors,
            scholarship_type, religion
        )
        VALUES (
            %(student_id)s, %(first_name)s, %(last_name)s, %(sex_code)s,
            %(birthdate)s, %(civil_status)s, %(home_address)s,
            %(home_municipality)s, %(family_income_bracket)s,
            %(parent_highest_education)s, %(hs_school_name)s,
            %(hs_type)s, %(shs_strand)s, %(graduation_honors)s,
            %(scholarship_type)s, %(religion)s
        )
        ON CONFLICT (student_id) DO UPDATE SET
            first_name               = EXCLUDED.first_name,
            last_name                = EXCLUDED.last_name,
            sex_code                 = EXCLUDED.sex_code,
            birthdate                = COALESCE(EXCLUDED.birthdate,
                                                dim_student.birthdate),
            civil_status             = EXCLUDED.civil_status,
            home_address             = EXCLUDED.home_address,
            home_municipality        = EXCLUDED.home_municipality,
            family_income_bracket    = EXCLUDED.family_income_bracket,
            parent_highest_education = EXCLUDED.parent_highest_education,
            hs_school_name           = EXCLUDED.hs_school_name,
            hs_type                  = EXCLUDED.hs_type,
            shs_strand               = EXCLUDED.shs_strand,
            graduation_honors        = EXCLUDED.graduation_honors,
            scholarship_type         = EXCLUDED.scholarship_type,
            religion                 = EXCLUDED.religion
        RETURNING student_key
    """, {
        "student_id":               _str(pred.get("student_id"), 50),
        "first_name":               _str(pred.get("first_name"), 100),
        "last_name":                _str(pred.get("last_name"),  100),
        "sex_code":                 _str(pred.get("sex_code"),   10),
        "birthdate":                _date(pred.get("birthdate")),
        "civil_status":             _str(pred.get("civil_status"), 50),
        "home_address":             _str(pred.get("home_address")),
        "home_municipality":        _str(pred.get("home_municipality"), 100),
        "family_income_bracket":    _str(pred.get("family_income_bracket"), 100),
        "parent_highest_education": _str(pred.get("parent_highest_education"), 150),
        "hs_school_name":           _str(pred.get("hs_school_name"), 255),
        "hs_type":                  _str(pred.get("hs_type"), 50),
        "shs_strand":               _str(pred.get("shs_strand"), 100),
        "graduation_honors":        _str(pred.get("graduation_honors"), 100),
        "scholarship_type":         _str(pred.get("scholarship_type"), 100),
        "religion":                 _str(pred.get("religion"), 100),
    })
    row = cur.fetchone()
    return row[0]


def _upsert_program(cur, program_code: str, college: str) -> Optional[int]:
    """
    Upsert dim_program on program_code.
    Returns program_key, or None if program_code is blank.
    """
    code = _str(program_code, 50)
    if not code:
        return None

    cur.execute("""
        INSERT INTO public.dim_program (program_code, program_name, college)
        VALUES (%(code)s, %(code)s, %(college)s)
        ON CONFLICT (program_code) DO UPDATE SET
            college = COALESCE(EXCLUDED.college, dim_program.college)
        RETURNING program_key
    """, {
        "code":    code,
        "college": _str(college, 150),
    })
    row = cur.fetchone()
    return row[0] if row else None


def _upsert_term(cur, academic_year: str, semester: int) -> int:
    """
    Upsert dim_academic_term on (academic_year, semester).
    Returns term_key.
    """
    cur.execute("""
        INSERT INTO public.dim_academic_term (academic_year, semester)
        VALUES (%(academic_year)s, %(semester)s)
        ON CONFLICT (academic_year, semester) DO UPDATE SET
            academic_year = EXCLUDED.academic_year
        RETURNING term_key
    """, {
        "academic_year": _str(academic_year, 20),
        "semester":      semester,
    })
    row = cur.fetchone()
    return row[0]


# ---------------------------------------------------------------------------
# Fact insert
# ---------------------------------------------------------------------------

def _insert_fact(
    cur,
    student_key:  int,
    program_key:  Optional[int],
    term_key:     int,
    pred:         Dict[str, Any],
    model_run_id: Optional[int],
) -> int:
    """
    Insert one row into fact_student_academic_risk.
    Returns the new fact_id.
    """
    raw_score          = float(pred.get("score", 0))
    predicted_risk_score = round(raw_score / 100.0, 4)   # store as 0.0–1.0
    risk_level_id      = _RISK_LEVEL_MAP.get(
        str(pred.get("category", "")).lower(), 1
    )

    cur.execute("""
        INSERT INTO public.fact_student_academic_risk (
            student_key,
            program_key,
            term_key,
            risk_level_id,
            model_run_id,
            predicted_at,
            year_level,
            entrance_exam_score,
            high_school_gpa,
            predicted_risk_score,
            prediction_confidence
        )
        VALUES (
            %(student_key)s,
            %(program_key)s,
            %(term_key)s,
            %(risk_level_id)s,
            %(model_run_id)s,
            %(predicted_at)s,
            %(year_level)s,
            %(entrance_exam_score)s,
            %(high_school_gpa)s,
            %(predicted_risk_score)s,
            %(prediction_confidence)s
        )
        RETURNING fact_id
    """, {
        "student_key":          student_key,
        "program_key":          program_key,
        "term_key":             term_key,
        "risk_level_id":        risk_level_id,
        "model_run_id":         model_run_id,
        "predicted_at":         datetime.now(),
        "year_level":           _num(pred.get("year_level"), int),
        "entrance_exam_score":  _num(pred.get("entrance_exam_score"), float),
        "high_school_gpa":      _num(pred.get("hs_gpa"), float),
        "predicted_risk_score": predicted_risk_score,
        "prediction_confidence": predicted_risk_score,   # use same value
    })
    row = cur.fetchone()
    return row[0]


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------

class RiskPersistenceService:
    """
    Persist prediction results into the EarlyAlert star schema.

    Public API (called from PredictionMixin._on_prediction_complete):
        RiskPersistenceService.save_predictions(
            predictions, model_id, academic_year, semester, model_run_id
        )
    """

    @staticmethod
    def save_predictions(
        predictions:   List[Dict[str, Any]],
        model_id:      str           = "rf",
        academic_year: str           = "2024-2025",
        semester:      str           = "1",
        model_run_id:  Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Populate dim_student, dim_program, dim_academic_term, and
        fact_student_academic_risk for every prediction in the batch.

        Each student row is wrapped in a SAVEPOINT so a single bad record
        never aborts the batch.

        Returns
        -------
        dict  {"success": bool, "inserted": int, "errors": list[str],
               "total_processed": int}
        """
        conn = None
        try:
            conn = get_connection()
            if not conn:
                return {"success": False, "error": "Could not connect to database."}

            cur = conn.cursor()
            sem = _semester(semester)

            # ── Resolve / create dim_academic_term once for the whole batch ──
            term_key = _upsert_term(cur, academic_year, sem)
            print(f"[RiskPersistence] term_key={term_key} "
                  f"({academic_year}, sem {sem})")

            # ── Cache program_key lookups to avoid repeated upserts ──────────
            program_key_cache: Dict[str, Optional[int]] = {}

            inserted = 0
            errors: List[str] = []

            for pred in predictions:
                sid = str(pred.get("student_id", "")).strip()
                try:
                    cur.execute("SAVEPOINT sp_pred")

                    # ── dim_student ──────────────────────────────────────────
                    student_key = _upsert_student(cur, pred)

                    # ── dim_program ──────────────────────────────────────────
                    prog_code = _str(pred.get("program"), 50) or ""
                    if prog_code not in program_key_cache:
                        program_key_cache[prog_code] = _upsert_program(
                            cur,
                            prog_code,
                            pred.get("college", ""),
                        )
                    program_key = program_key_cache[prog_code]

                    # ── fact_student_academic_risk ────────────────────────────
                    fact_id = _insert_fact(
                        cur,
                        student_key  = student_key,
                        program_key  = program_key,
                        term_key     = term_key,
                        pred         = pred,
                        model_run_id = model_run_id,
                    )

                    cur.execute("RELEASE SAVEPOINT sp_pred")
                    inserted += 1

                except Exception as exc:
                    cur.execute("ROLLBACK TO SAVEPOINT sp_pred")
                    cur.execute("RELEASE SAVEPOINT sp_pred")
                    msg = f"Student {sid}: {exc}"
                    errors.append(msg)
                    print(f"[RiskPersistence] ERROR — {msg}")

            conn.commit()

            print(
                f"[RiskPersistence] Done — "
                f"inserted={inserted}, errors={len(errors)}, "
                f"term_key={term_key}"
            )
            return {
                "success":         True,
                "inserted":        inserted,
                "updated":         0,           # fact rows are always new inserts
                "errors":          errors,
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
    # READ — used by RiskAlertsPage / DashboardPage
    # ------------------------------------------------------------------

    @staticmethod
    def get_latest_predictions(
        academic_year: Optional[str] = None,
        semester:      Optional[int] = None,
        limit:         int           = 500,
    ) -> List[Dict[str, Any]]:
        """
        Fetch the latest risk snapshot by joining the star schema.
        Falls back to a simpler query if the view is unavailable.
        """
        conn = None
        try:
            conn = get_connection()
            if not conn:
                return []

            cur = conn.cursor()

            filters: List[str] = []
            params:  List[Any] = []
            if academic_year:
                filters.append("t.academic_year = %s")
                params.append(academic_year)
            if semester is not None:
                filters.append("t.semester = %s")
                params.append(_semester(semester))

            where  = ("WHERE " + " AND ".join(filters)) if filters else ""
            params.append(limit)

            cur.execute(f"""
                SELECT
                    f.fact_id,
                    s.student_id,
                    s.first_name,
                    s.last_name,
                    p.program_code,
                    p.college,
                    t.academic_year,
                    t.semester,
                    f.year_level,
                    f.entrance_exam_score,
                    f.high_school_gpa,
                    f.predicted_risk_score,
                    f.prediction_confidence,
                    rl.risk_label,
                    rl.color_hex,
                    f.predicted_at
                FROM   public.fact_student_academic_risk f
                JOIN   public.dim_student       s  ON s.student_key  = f.student_key
                LEFT JOIN public.dim_program    p  ON p.program_key  = f.program_key
                JOIN   public.dim_academic_term t  ON t.term_key     = f.term_key
                LEFT JOIN public.dim_risk_level rl ON rl.risk_level_id = f.risk_level_id
                {where}
                ORDER  BY f.predicted_risk_score DESC
                LIMIT  %s
            """, params)

            columns = [d[0] for d in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

        except Exception as exc:
            print(f"[RiskPersistence] get_latest_predictions error: {exc}")
            return []
        finally:
            if conn:
                conn.close()