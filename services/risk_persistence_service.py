"""
services/risk_persistence_service.py
=====================================
Saves prediction results to the star-schema fact table.

Score contract (single source of truth)
----------------------------------------
- PredictionEngine always puts  pred["score"] in 0–100 range  (e.g. 81.2)
- _upsert_one converts to 0–1 for DB storage  (÷ 100)
- _TermDataWorker converts back to 0–100 for display  (× 100)

The old ">  1" / ">= 1.5" guard was fragile because a probability of
exactly 0.01 rounds to 1.0 after × 100, making the guard ambiguous.
The new approach is explicit: pred["score"] is ALWAYS 0-100, so we
always divide by 100 before storing — no conditional needed.

Per-student risk-factor breakdown
----------------------------------
pred["shap_factors"] — a list of (feature_name, human_label,
formatted_value, pct) tuples produced by PredictionEngine._shap_factors()
— is persisted verbatim as JSON in shap_factors_json so the dashboard and
per-student risk views can reconstruct the exact per-student breakdown
after an app restart, instead of falling back to the model's static
global feature importances.
"""
from __future__ import annotations

import json

from services.encryption_utils import encrypt_field, decrypt_field

_FEATURE_HUMAN_LABELS = {
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
    factors = pred.get("shap_factors") or []
    if not factors:
        return pred.get("factor") or None
    top = factors[0]
    if len(top) == 4:
        return str(top[1])
    if len(top) == 2:
        label_or_feat = str(top[0])
        human = _FEATURE_HUMAN_LABELS.get(label_or_feat)
        return human if human else label_or_feat
    return None


def _shap_factors_to_json(pred: dict) -> str:
    """
    Serialize pred["shap_factors"] verbatim to a JSON string for storage.

    Stored exactly as PredictionEngine produces it — a list of
    [feature_name, human_label, formatted_value, pct] — so the load side
    can reconstruct each student's breakdown with no reshaping.
    """
    factors = pred.get("shap_factors") or []
    try:
        return json.dumps(list(factors))
    except (TypeError, ValueError):
        return "[]"


def _to_numeric(value) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    if s in ("", "nan", "None", "null", "NULL", "—", "-"):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _score_to_db(pred_score) -> float:
    """
    Convert pred["score"] (always 0–100 from PredictionEngine) to a
    0–1 probability for DB storage.

    Never uses a conditional — the contract is that PredictionEngine
    always emits 0-100, so we always divide by 100.
    """
    raw = _to_numeric(pred_score) or 0.0
    return round(max(0.0, min(1.0, raw / 100.0)), 6)


def _split_name(full_name: str) -> tuple[str, str]:
    """Split 'First Last' into (first, last). Handles single-word names."""
    parts = full_name.strip().split(None, 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return parts[0] if parts else "", ""


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

            has_factor_col = RiskPersistenceService._column_exists(
                conn, "fact_student_academic_risk", "primary_factor"
            )
            has_shap_col = RiskPersistenceService._column_exists(
                conn, "fact_student_academic_risk", "shap_factors_json"
            )

            inserted = 0
            errors   = 0

            with conn.cursor() as cur:
                for pred in predictions:
                    try:
                        RiskPersistenceService._upsert_one(
                            cur, pred, term_key, model_id,
                            has_factor_col, has_shap_col,
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
    def _normalise_risk_label(label: str, score_01: float) -> str:
        """
        Derive a display label from the 0-1 probability score.
        score_01 is always in 0-1 range here.
        """
        lc = (label or "").lower().strip()
        if "high"                        in lc: return "High"
        if "moderate" in lc or "medium" in lc: return "Medium"
        if "low"                         in lc: return "Low"
        if lc in ("at_risk", "1", "true", "yes"):
            return "High" if score_01 >= 0.50 else "Medium"
        if lc in ("not_at_risk", "0", "false", "no"):
            return "Low"
        # Fallback: derive from score
        if score_01 >= 0.50: return "High"
        if score_01 >= 0.25: return "Medium"
        return "Low"

    @staticmethod
    def _ensure_risk_level(conn, label: str) -> int | None:
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
    def _ensure_student(conn, pred: dict) -> int | None:
        """
        Upsert a student row in dim_student.
        Never overwrites an existing non-null value.
        """
        student_id = str(pred.get("student_id", "")).strip()
        if not student_id or student_id in ("", "—", "None"):
            return None

        _JUNK = {"", "—", "none", "nan", "unknown", "null", "n/a", "-"}

        def _s(*keys) -> str | None:
            for k in keys:
                v = str(pred.get(k) or "").strip()
                if v.lower() not in _JUNK:
                    return v
            return None

        # ── Names ─────────────────────────────────────────────────────
        full_name  = str(pred.get("name",       "") or "").strip()
        first_name = str(pred.get("first_name", "") or "").strip()
        last_name  = str(pred.get("last_name",  "") or "").strip()

        if full_name and (not first_name or not last_name):
            derived_first, derived_last = _split_name(full_name)
            first_name = first_name or derived_first
            last_name  = last_name  or derived_last

        # ── Biographical ──────────────────────────────────────────────
        shs_strand               = _s("SHS_Strand",            "shs_strand")
        hs_type                  = _s("HS_Type",               "hs_type")
        graduation_honors        = _s("Graduation_Honors",     "graduation_honors")
        hs_school_name           = _s("HS_School",             "hs_school_name")
        sex_code                 = _s("Sex_Code",              "sex_code")
        civil_status             = _s("Civil_Status",          "civil_status")
        home_municipality        = _s("Home_Municipality",     "home_municipality",
                                      "Municipality",          "municipality")
        family_income_bracket    = _s("Family_Income_Bracket", "family_income_bracket",
                                      "Family_Income",         "family_income")
        parent_highest_education = _s("Parent_Highest_Education",
                                      "parent_highest_education", "Parent_Edu")
        scholarship_type         = _s("Scholarship_Type",      "scholarship_type")
        religion                 = _s("Religion",              "religion")

        # ── Staging table fallback ────────────────────────────────────
        needs_staging = any(v is None for v in [
            home_municipality, shs_strand, hs_type, sex_code,
            civil_status, family_income_bracket, parent_highest_education,
        ])

        if needs_staging:
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT municipality, shs_strand, hs_type,
                               graduation_honors, hs_school
                        FROM   public.registrar_student_profile
                        WHERE  student_id = %s LIMIT 1
                    """, (student_id,))
                    r = cur.fetchone()
                    if r:
                        home_municipality = home_municipality or (r[0] or "").strip() or None
                        shs_strand        = shs_strand        or (r[1] or "").strip() or None
                        hs_type           = hs_type           or (r[2] or "").strip() or None
                        graduation_honors = graduation_honors or (r[3] or "").strip() or None
                        hs_school_name    = hs_school_name    or (r[4] or "").strip() or None

                    cur.execute("""
                        SELECT family_income_bracket, parent_highest_education
                        FROM   public.guidance_student_profile
                        WHERE  student_id = %s LIMIT 1
                    """, (student_id,))
                    g = cur.fetchone()
                    if g:
                        family_income_bracket    = (family_income_bracket
                                                    or (g[0] or "").strip() or None)
                        parent_highest_education = (parent_highest_education
                                                    or (g[1] or "").strip() or None)

                    cur.execute("""
                        SELECT scholarship_type
                        FROM   public.sao_student_profile
                        WHERE  student_id = %s LIMIT 1
                    """, (student_id,))
                    s = cur.fetchone()
                    if s:
                        scholarship_type = scholarship_type or (s[0] or "").strip() or None

                    cur.execute("""
                        SELECT sex_code, civil_status, religion
                        FROM   public.mis_students
                        WHERE  id_no = %s LIMIT 1
                    """, (student_id,))
                    m = cur.fetchone()
                    if m:
                        sex_code     = sex_code     or (m[0] or "").strip() or None
                        civil_status = civil_status or (m[1] or "").strip() or None
                        religion     = religion     or (m[2] or "").strip() or None

            except Exception as exc:
                print(f"[RiskPersistence] staging lookup error (id={student_id}): {exc}")

        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO public.dim_student (
                        student_id,
                        first_name, last_name,
                        shs_strand, hs_type, graduation_honors, hs_school_name,
                        sex_code, civil_status, home_municipality,
                        family_income_bracket, parent_highest_education,
                        scholarship_type, religion
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (student_id) DO UPDATE SET
                        first_name  = COALESCE(NULLIF(dim_student.first_name,  ''),
                                               NULLIF(EXCLUDED.first_name,     '')),
                        last_name   = COALESCE(NULLIF(dim_student.last_name,   ''),
                                               NULLIF(EXCLUDED.last_name,      '')),
                        shs_strand               = COALESCE(dim_student.shs_strand,
                                                            EXCLUDED.shs_strand),
                        hs_type                  = COALESCE(dim_student.hs_type,
                                                            EXCLUDED.hs_type),
                        graduation_honors        = COALESCE(dim_student.graduation_honors,
                                                            EXCLUDED.graduation_honors),
                        hs_school_name           = COALESCE(dim_student.hs_school_name,
                                                            EXCLUDED.hs_school_name),
                        sex_code                 = COALESCE(dim_student.sex_code,
                                                            EXCLUDED.sex_code),
                        civil_status             = COALESCE(dim_student.civil_status,
                                                            EXCLUDED.civil_status),
                        home_municipality        = COALESCE(dim_student.home_municipality,
                                                            EXCLUDED.home_municipality),
                        family_income_bracket    = COALESCE(dim_student.family_income_bracket,
                                                            EXCLUDED.family_income_bracket),
                        parent_highest_education = COALESCE(dim_student.parent_highest_education,
                                                            EXCLUDED.parent_highest_education),
                        scholarship_type         = COALESCE(dim_student.scholarship_type,
                                                            EXCLUDED.scholarship_type),
                        religion                 = COALESCE(dim_student.religion,
                                                            EXCLUDED.religion)
                    RETURNING student_key
                """, (
                    student_id,
                    encrypt_field(first_name or None), encrypt_field(last_name or None),
                    shs_strand, hs_type, graduation_honors, hs_school_name,
                    sex_code, civil_status, home_municipality,
                    family_income_bracket, parent_highest_education,
                    scholarship_type, religion,
                ))
                result = cur.fetchone()
                return result[0] if result else None

        except Exception as exc:
            print(f"[RiskPersistence] _ensure_student error (id={student_id}): {exc}")
            return None

    @staticmethod
    def _ensure_program(conn, program_name: str, college: str) -> int | None:
        if not program_name or program_name in ("—", "Unknown"):
            return None
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT program_key FROM public.dim_program
                    WHERE  program_name = %s LIMIT 1
                """, (program_name,))
                row = cur.fetchone()
                return row[0] if row else None
        except Exception:
            return None

    @staticmethod
    def _upsert_one(cur, pred: dict, term_key: int,
                    model_id: str, has_factor_col: bool,
                    has_shap_col: bool = False) -> None:
        from services.data_store import DataStore
        conn = DataStore.get().db_conn

        student_key = RiskPersistenceService._ensure_student(conn, pred)
        if student_key is None:
            return

        program_key = RiskPersistenceService._ensure_program(
            conn, pred.get("program", ""), pred.get("college", ""))

        # ── Score: pred["score"] is ALWAYS 0-100 from PredictionEngine.
        #    Divide by 100 unconditionally to get 0-1 for DB storage.
        score_01 = _score_to_db(pred.get("score", 0))

        raw_label     = pred.get("label", "") or pred.get("category", "")
        risk_label    = RiskPersistenceService._normalise_risk_label(
            raw_label, score_01)
        risk_level_id = RiskPersistenceService._ensure_risk_level(
            conn, risk_label)

        entrance = _to_numeric(pred.get("entrance_exam_score"))
        hs_gpa   = _to_numeric(
            pred.get("hs_gpa") or pred.get("high_school_gpa"))

        primary_factor = _extract_primary_factor(pred)
        shap_json       = _shap_factors_to_json(pred)

        # ── Build column/value lists dynamically so this still works on
        #    a DB that hasn't run the shap_factors_json migration yet. ──
        cols   = [
            "student_key", "program_key", "term_key", "risk_level_id",
            "predicted_risk_score", "prediction_confidence",
            "entrance_exam_score", "high_school_gpa",
        ]
        values = [
            student_key, program_key, term_key, risk_level_id,
            score_01, score_01,
            entrance, hs_gpa,
        ]
        update_clauses = [
            "risk_level_id         = EXCLUDED.risk_level_id",
            "predicted_risk_score  = EXCLUDED.predicted_risk_score",
            "prediction_confidence = EXCLUDED.prediction_confidence",
            "entrance_exam_score   = EXCLUDED.entrance_exam_score",
            "high_school_gpa       = EXCLUDED.high_school_gpa",
        ]

        if has_factor_col:
            cols.append("primary_factor")
            values.append(primary_factor)
            update_clauses.append("primary_factor = EXCLUDED.primary_factor")

        if has_shap_col:
            cols.append("shap_factors_json")
            values.append(shap_json)
            update_clauses.append(
                "shap_factors_json = EXCLUDED.shap_factors_json")

        cols.append("predicted_at")
        placeholders = ", ".join(["%s"] * (len(cols) - 1)) + ", NOW()"
        update_clauses.append("predicted_at = NOW()")

        sql = f"""
            INSERT INTO public.fact_student_academic_risk (
                {", ".join(cols)}
            ) VALUES (
                {placeholders}
            )
            ON CONFLICT (student_key, term_key) DO UPDATE SET
                {", ".join(update_clauses)}
        """
        cur.execute(sql, values)