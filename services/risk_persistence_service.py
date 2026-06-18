"""
services/risk_persistence_service.py
=====================================
Saves prediction results to the star-schema fact table.

Changes vs previous version:
  - Extracts `primary_factor` (top SHAP feature human label) per student
    and persists it in `fact_student_academic_risk.primary_factor`.
  - Falls back gracefully if the column doesn't exist yet (pre-migration).
"""
from __future__ import annotations

_FEATURE_HUMAN_LABELS = {
    # feature_engineering.py output names → human labels
    "Entrance_Exam_Score":      "Entrance Exam Score",
    "entrance_exam_score":      "Entrance Exam Score",
    "HS_GPA":                   "High School GPA",
    "high_school_gpa":          "High School GPA",
    "Financial_Stress":         "Financial Stress Index",
    "Financial_Stress_Index":   "Financial Stress Index",
    "First_Gen_Student":        "First-Generation Student",
    "Gap_Years":                "Gap Years Before College",
    "Distance_Bucket":          "Distance from Campus",
    "Distance_KM":              "Distance from Campus",
    "Strand_Program_Match":     "SHS Strand–Program Alignment",
    "Strand_Program_Alignment": "SHS Strand–Program Alignment",
    "Has_Scholarship":          "Has Scholarship",
    "Has_HS_Honors":            "Graduated with HS Honors",
    "Graduation_Honors":        "Graduated with HS Honors",
    "Private_HS":               "Attended Private High School",
    "HS_Type_Private":          "Attended Private High School",
    "Age_At_Enrollment":        "Age at Enrollment",
    "Age_Group":                "Age at Enrollment",
}


def _extract_primary_factor(pred: dict) -> str | None:
    """
    Return the human-readable label of the top SHAP factor for this student.

    shap_factors entry formats supported:
      (feature_name, human_label, formatted_value, pct)  ← 4-tuple
      (feature_name, pct)                                 ← 2-tuple
      (human_label, pct)                                  ← 2-tuple (legacy)
    """
    factors = pred.get("shap_factors") or []
    if not factors:
        # Fall back to the "factor" string if shap_factors is absent
        return pred.get("factor") or None

    top = factors[0]

    if len(top) == 4:
        # (feature_name, human_label, formatted_value, pct)
        return str(top[1])

    if len(top) == 2:
        label_or_feat = str(top[0])
        # Check if it's a known machine feature name → map to human label
        human = _FEATURE_HUMAN_LABELS.get(label_or_feat)
        return human if human else label_or_feat

    return None


def _to_numeric(value) -> float | None:
    """Convert a value to float, returning None for blanks/nulls."""
    if value is None:
        return None
    s = str(value).strip()
    if s in ("", "nan", "None", "null", "NULL", "—", "-"):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


class RiskPersistenceService:
    """Save / upsert prediction results to fact_student_academic_risk."""

    # ── Public API ────────────────────────────────────────────────────

    @staticmethod
    def save_predictions(
        predictions:   list,
        model_id:      str,
        academic_year: str,
        semester:      str | int,
    ) -> dict:
        """
        Upsert prediction rows into the star schema.

        Parameters
        ----------
        predictions   : list of prediction dicts from PredictionEngine
        model_id      : e.g. "rf"
        academic_year : e.g. "2024-2025"
        semester      : 1 or 2 (or "1" / "2")

        Returns
        -------
        {"success": bool, "inserted": int, "errors": int}
        """
        from services.data_store import DataStore
        conn = DataStore.get().db_conn
        if not conn:
            print("[RiskPersistence] No DB connection — skipping save.")
            return {"success": False, "inserted": 0, "errors": 0}

        semester_int = int(semester)

        try:
            term_key = RiskPersistenceService._ensure_term(
                conn, academic_year, semester_int
            )
            print(f"[RiskPersistence] term_key={term_key} "
                  f"({academic_year}, sem {semester_int})")

            # Check whether the primary_factor column exists
            has_factor_col = RiskPersistenceService._column_exists(
                conn, "fact_student_academic_risk", "primary_factor"
            )

            inserted = 0
            errors   = 0

            with conn.cursor() as cur:
                for pred in predictions:
                    try:
                        RiskPersistenceService._upsert_one(
                            cur, pred, term_key, model_id, has_factor_col
                        )
                        inserted += 1
                    except Exception as e:
                        errors += 1
                        if errors <= 3:
                            print(f"[RiskPersistence] Row error: {e}")

            conn.commit()
            print(f"[RiskPersistence] Done — "
                  f"inserted={inserted}, errors={errors}, term_key={term_key}")
            return {"success": True, "inserted": inserted, "errors": errors}

        except Exception as e:
            print(f"[RiskPersistence] FATAL: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            return {"success": False, "inserted": 0, "errors": 0}

    # ── Internals ─────────────────────────────────────────────────────

    @staticmethod
    def _column_exists(conn, table: str, column: str) -> bool:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 1
                    FROM   information_schema.columns
                    WHERE  table_schema = 'public'
                      AND  table_name   = %s
                      AND  column_name  = %s
                """, (table, column))
                return cur.fetchone() is not None
        except Exception:
            return False

    @staticmethod
    def _ensure_term(conn, academic_year: str, semester: int) -> int:
        """Return term_key, inserting the term row if it doesn't exist."""
        with conn.cursor() as cur:
            cur.execute("""
                SELECT term_key FROM public.dim_academic_term
                WHERE  academic_year = %s AND semester = %s
            """, (academic_year, semester))
            row = cur.fetchone()
            if row:
                return row[0]

            sem_label = "1st" if semester == 1 else "2nd"
            cur.execute("""
                INSERT INTO public.dim_academic_term
                    (academic_year, semester, term_label)
                VALUES (%s, %s, %s)
                RETURNING term_key
            """, (academic_year, semester,
                  f"{sem_label} Semester {academic_year}"))
            conn.commit()
            return cur.fetchone()[0]

    @staticmethod
    def _normalise_risk_label(label: str, score: float) -> str:
        """
        Normalise whatever the prediction engine emits to a human label
        that will match dim_risk_level.risk_label values like
        'High Risk', 'Moderate Risk', 'Low Risk'.

        Handles:
          - "at_risk" / "not_at_risk"   (raw model binary output)
          - "high_risk" / "moderate_risk" / "low_risk"  (category keys)
          - "High Risk" / "Moderate Risk" / "Low Risk"  (already correct)
          - Anything else → derive from score thresholds
        """
        lc = (label or "").lower().strip()

        # Already a human label — return as-is
        if "high" in lc:                    return "High"
        if "moderate" in lc or "medium" in lc: return "Medium"
        if "low" in lc:                     return "Low"

        # Binary model output — derive from score
        if lc in ("at_risk", "1", "true", "yes"):
            return "High" if score >= 0.50 else "Medium"
        if lc in ("not_at_risk", "0", "false", "no"):
            return "Low"

        # Unknown label — fall back to score thresholds
        if score >= 0.50:   return "High"
        if score >= 0.25:   return "Medium"
        return "Low"

    @staticmethod
    def _ensure_risk_level(conn, label: str) -> int | None:
        """Return risk_level_id for label, or None if not found."""
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT risk_level_id FROM public.dim_risk_level
                    WHERE  risk_label ILIKE %s
                    LIMIT  1
                """, (f"%{label}%",))
                row = cur.fetchone()
                return row[0] if row else None
        except Exception:
            return None

    @staticmethod
    def _ensure_student(conn, student_id: str) -> int | None:
        """Return student_key for student_id, or None."""
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT student_key FROM public.dim_student
                    WHERE  student_id = %s
                    LIMIT  1
                """, (student_id,))
                row = cur.fetchone()
                return row[0] if row else None
        except Exception:
            return None

    @staticmethod
    def _ensure_program(conn, program_name: str, college: str) -> int | None:
        """Return program_key for program_name, or None."""
        if not program_name or program_name in ("—", "Unknown"):
            return None
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT program_key FROM public.dim_program
                    WHERE  program_name = %s
                    LIMIT  1
                """, (program_name,))
                row = cur.fetchone()
                return row[0] if row else None
        except Exception:
            return None

    @staticmethod
    def _upsert_one(cur, pred: dict, term_key: int,
                    model_id: str, has_factor_col: bool) -> None:
        from services.data_store import DataStore

        conn = DataStore.get().db_conn

        student_id   = str(pred.get("student_id", "")).strip()
        student_key  = RiskPersistenceService._ensure_student(conn, student_id)
        if student_key is None:
            return  # Can't link to dim_student — skip

        program_name = pred.get("program", "")
        college      = pred.get("college", "")
        program_key  = RiskPersistenceService._ensure_program(
            conn, program_name, college)

        raw_label    = pred.get("label", "") or pred.get("category", "")
        score_raw    = pred.get("score", 0)
        score_norm   = float(score_raw) / 100.0 if float(score_raw or 0) > 1 else float(score_raw or 0)
        risk_label   = RiskPersistenceService._normalise_risk_label(
            raw_label, score_norm)
        risk_level_id = RiskPersistenceService._ensure_risk_level(
            conn, risk_label)

        score_raw    = _to_numeric(pred.get("score", 0)) or 0.0
        score        = score_raw / 100.0 if score_raw > 1 else score_raw

        entrance     = _to_numeric(pred.get("entrance_exam_score"))
        hs_gpa       = _to_numeric(
            pred.get("hs_gpa") or pred.get("high_school_gpa")
        )

        primary_factor = _extract_primary_factor(pred)

        if has_factor_col:
            cur.execute("""
                INSERT INTO public.fact_student_academic_risk (
                    student_key, program_key, term_key, risk_level_id,
                    predicted_risk_score, prediction_confidence,
                    entrance_exam_score, high_school_gpa,
                    primary_factor, predicted_at
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, NOW()
                )
                ON CONFLICT (student_key, term_key)
                DO UPDATE SET
                    risk_level_id        = EXCLUDED.risk_level_id,
                    predicted_risk_score = EXCLUDED.predicted_risk_score,
                    prediction_confidence= EXCLUDED.prediction_confidence,
                    entrance_exam_score  = EXCLUDED.entrance_exam_score,
                    high_school_gpa      = EXCLUDED.high_school_gpa,
                    primary_factor       = EXCLUDED.primary_factor,
                    predicted_at         = NOW()
            """, (
                student_key, program_key, term_key, risk_level_id,
                score, score,
                entrance, hs_gpa,
                primary_factor,
            ))
        else:
            # Graceful fallback: table doesn't have primary_factor yet
            cur.execute("""
                INSERT INTO public.fact_student_academic_risk (
                    student_key, program_key, term_key, risk_level_id,
                    predicted_risk_score, prediction_confidence,
                    entrance_exam_score, high_school_gpa,
                    predicted_at
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    NOW()
                )
                ON CONFLICT (student_key, term_key)
                DO UPDATE SET
                    risk_level_id        = EXCLUDED.risk_level_id,
                    predicted_risk_score = EXCLUDED.predicted_risk_score,
                    prediction_confidence= EXCLUDED.prediction_confidence,
                    entrance_exam_score  = EXCLUDED.entrance_exam_score,
                    high_school_gpa      = EXCLUDED.high_school_gpa,
                    predicted_at         = NOW()
            """, (
                student_key, program_key, term_key, risk_level_id,
                score, score,
                entrance, hs_gpa,
            ))