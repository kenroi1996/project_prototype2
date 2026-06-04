from __future__ import annotations
import random


# =====================================
# RISK THRESHOLDS
# =====================================

RISK_HIGH     = 70   # score >= 70 → High Risk
RISK_MODERATE = 40   # score >= 40 → Moderate Risk
                     # score <  40 → Low Risk


def classify_risk(score: float) -> str:
    if score >= RISK_HIGH:
        return "high_risk"
    elif score >= RISK_MODERATE:
        return "moderate_risk"
    return "low_risk"


def risk_label(category: str) -> str:
    return {
        "high_risk":     "High Risk",
        "moderate_risk": "Moderate Risk",
        "low_risk":      "Low Risk",
    }.get(category, "Unknown")


# =====================================
# PREDICTION ENGINE
# =====================================

class PredictionEngine:
    """
    Runs the trained model against the unified dataset and
    produces a prediction record for each student.

    Uses sklearn when available; falls back to a mock engine.

    Usage
    -----
        result = PredictionEngine.run(
            model_data       = DataStore.get().trained_model,
            unified_dataset  = DataStore.get().unified_dataset,
            progress_cb      = lambda step, pct: ...,
        )
        result.predictions   # list[dict]
        result.summary       # PredictionSummary
    """

    @classmethod
    def run(
        cls,
        model_data:      dict,
        unified_dataset: dict,
        progress_cb      = None,
    ) -> "PredictionResult":

        cb = progress_cb or (lambda s, p: None)

        cb("Validating inputs…", 5)
        errors = cls._validate(model_data, unified_dataset)
        if errors:
            return PredictionResult(success=False, errors=errors)

        headers      = unified_dataset["headers"]
        rows         = unified_dataset["rows"]
        feature_names = model_data["feature_names"]
        target_col   = model_data.get("target_col", "Final_Avg_GRD")

        cb("Preparing feature matrix…", 15)
        X, student_ids, student_meta = cls._prepare(
            headers, rows, feature_names, target_col
        )

        if not X:
            return PredictionResult(
                success=False,
                errors=["No valid rows found for prediction."]
            )

        cb("Running model inference…", 40)
        try:
            scores, probas = cls._predict_sklearn(model_data["model"], X)
            is_mock = False
        except Exception:
            cb("Falling back to mock engine…", 40)
            scores, probas = cls._predict_mock(X)
            is_mock = True

        cb("Computing risk categories…", 70)
        predictions = []
        for i, sid in enumerate(student_ids):
            score    = round(probas[i] * 100, 1)
            category = classify_risk(score)
            meta     = student_meta[i]

            predictions.append({
                "student_id": sid,
                "name":       meta.get("name", "—"),
                "program":    meta.get("program", "—"),
                "college":    meta.get("college", "—"),
                "score":      score,
                "category":   category,
                "label":      risk_label(category),
                "factor":     cls._top_factor(model_data, feature_names, X[i]),
                "shap_factors": cls._shap_factors(model_data, feature_names, X[i]),
                "gwa":        meta.get("gwa", "—"),
                "absences":   meta.get("absences", "—"),
            })

        cb("Building summary…", 90)
        summary = cls._build_summary(predictions)

        cb("Done ✅", 100)

        return PredictionResult(
            success     = True,
            predictions = predictions,
            summary     = summary,
            is_mock     = is_mock,
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate(model_data, unified_dataset) -> list[str]:
        errors = []
        if not model_data:
            errors.append(
                "No trained model found. Complete Stage 3 (Model Training) first."
            )
        elif "model" not in model_data and not model_data.get("is_mock"):
            errors.append("Model artifact is missing.")
        if not unified_dataset:
            errors.append(
                "No unified dataset found. Complete Stage 2 (Data Merge) first."
            )
        elif not unified_dataset.get("rows"):
            errors.append("Unified dataset is empty.")
        return errors

    # ------------------------------------------------------------------
    # Feature preparation
    # ------------------------------------------------------------------

    @classmethod
    def _prepare(
        cls,
        headers:       list,
        rows:          list,
        feature_names: list,
        target_col:    str,
    ) -> tuple[list, list, list]:
        """Returns (X, student_ids, student_meta)."""

        # Column index maps
        col_idx    = {h: i for i, h in enumerate(headers)}
        feat_idxs  = [col_idx.get(f) for f in feature_names]

        # Student ID column - try multiple possible names
        student_id_keywords = [
            "student_id", "id_no", "id", "keyid", "systemcode", 
            "student code", "studentid"
        ]
        id_col = next(
            (c for c in headers if c.lower() in student_id_keywords),
            None
        )
        
        if not id_col:
            # If still not found, look for any column with "id" or "code" in name
            id_col = next(
                (c for c in headers if "id" in c.lower() or "code" in c.lower()),
                headers[0]  # Last resort
            )
        
        id_idx = col_idx.get(id_col, 0)

        # Name / meta columns (best-effort) — use explicit None check to avoid
        # treating column index 0 as falsy
        def _idx(*keys):
            """Return the first column index found for any of the given keys."""
            for k in keys:
                v = col_idx.get(k)
                if v is not None:
                    return v
            return None

        # MergeEngine produces UNIFIED_COLUMNS names (e.g. "Student_ID", "Sex_code",
        # "SecCode"). The lookups below cover both unified names and raw portal names
        # so the engine works whether the dataset came from MergeEngine or a raw upload.
        name_idx    = _idx("firstname", "first_name", "First_Name")
        lname_idx   = _idx("lastname", "last_name", "Last_Name")
        program_idx = _idx("Program", "program", "PROGRAM", "program_code")
        college_idx = _idx("College", "college", "COLLEGE")
        gwa_idx     = _idx("Final_Avg_GRD", "final_avg_grd", "FINAL_AVG_GRD")
        abs_idx     = _idx("Attendance_Rate", "absences")

        # Extra fields for fact_student_risk — unified names first, raw names as fallback
        seccode_idx      = _idx("SecCode",                  "seccode",                  "SECCODE",                  "sec_code")
        year_idx         = _idx("Year",                     "year",                     "YEAR",                     "year_level")
        sex_idx          = _idx("Sex_code",                 "sex_code",                 "SEX_CODE",                 "gender",   "GENDER")
        address_idx      = _idx("Home_Address",             "home_address",             "HOME_ADDRESS",             "municipality", "MUNICIPALITY")
        civil_idx        = _idx("Civil_Status",             "civil_status",             "CIVIL_STATUS")
        birthdate_idx    = _idx("Birthdate",                "birthdate",                "BIRTHDATE")
        yr_enrolled_idx  = _idx("Year_Enrolled",            "year_enrolled",            "YEAR_ENROLLED")
        exam_score_idx   = _idx("Entrance_Exam_Score",      "entrance_exam_score",      "ENTRANCE_EXAM_SCORE")
        income_idx       = _idx("Family_Income",            "family_income_bracket",    "family_income",            "FAMILY_INCOME")
        parent_edu_idx   = _idx("Parent_Highest_Education", "parent_highest_education", "PARENT_HIGHEST_EDUCATION")
        hs_gpa_idx       = _idx("HS_GPA",                   "hs_gpa",                   "HS_GPA")
        yr_grad_idx      = _idx("Year_Graduated",           "year_graduated",           "YEAR_GRADUATED")
        strand_idx       = _idx("SHS_Strand",               "shs_strand",               "SHS_STRAND")
        hs_type_idx      = _idx("HS_Type",                  "hs_type",                  "HS_TYPE")
        honors_idx       = _idx("Graduation_Honors",        "graduation_honors",        "GRADUATION_HONORS")
        hs_school_idx    = _idx("HS_School",                "hs_school",                "HS_SCHOOL",                "hs_school_name")
        scholar_idx      = _idx("Scholarship_Applicant",    "scholarship_applicant",    "SCHOLARSHIP_APPLICANT")
        scholar_type_idx = _idx("Scholarship_Type",         "scholarship_type",         "SCHOLARSHIP_TYPE")

        X            = []
        student_ids  = []
        student_meta = []

        def _cell(row, idx):
            """Safely retrieve and strip a cell value; returns '' if missing."""
            if idx is not None and idx < len(row):
                return str(row[idx]).strip()
            return ""

        # Debug: log which unified columns were resolved (printed once)
        _resolved = {
            "program": program_idx, "college": college_idx,
            "gwa": gwa_idx, "sec_code": seccode_idx, "year_level": year_idx,
            "sex_code": sex_idx, "home_address": address_idx,
            "civil_status": civil_idx, "birthdate": birthdate_idx,
            "year_enrolled": yr_enrolled_idx, "entrance_exam_score": exam_score_idx,
            "family_income": income_idx, "parent_edu": parent_edu_idx,
            "hs_gpa": hs_gpa_idx, "year_graduated": yr_grad_idx,
            "shs_strand": strand_idx, "hs_type": hs_type_idx,
            "honors": honors_idx, "hs_school": hs_school_idx,
            "scholarship": scholar_idx, "scholarship_type": scholar_type_idx,
        }
        _missing = [k for k, v in _resolved.items() if v is None]
        _found   = [k for k, v in _resolved.items() if v is not None]
        print(f"[PredictionEngine] Resolved {len(_found)}/{len(_resolved)} meta columns")
        if _missing:
            print(f"[PredictionEngine] NULL columns (not found in headers): {_missing}")
            print(f"[PredictionEngine] Available headers: {headers[:30]}")

        for row in rows:
            sid = _cell(row, id_idx)
            if not sid:
                continue

            feature_row = []
            for idx in feat_idxs:
                if idx is None or idx >= len(row):
                    feature_row.append(0.0)
                else:
                    val = row[idx].strip()
                    try:
                        feature_row.append(float(val))
                    except ValueError:
                        feature_row.append(0.0)

            X.append(feature_row)
            student_ids.append(sid)

            fname = _cell(row, name_idx)
            lname = _cell(row, lname_idx)
            name  = f"{fname} {lname}".strip() or sid

            student_meta.append({
                # Display fields
                "name":    name,
                "program": _cell(row, program_idx) or "—",
                "college": _cell(row, college_idx) or "—",
                "gwa":     _cell(row, gwa_idx)     or "—",
                "absences":_cell(row, abs_idx)     or "—",
                # fact_student_risk feature fields — keys must match
                # what risk_persistence_service._upsert_one() reads
                "sec_code":                  _cell(row, seccode_idx),
                "year_level":                _cell(row, year_idx),
                "sex_code":                  _cell(row, sex_idx),
                "home_address":              _cell(row, address_idx),
                "civil_status":              _cell(row, civil_idx),
                "birthdate":                 _cell(row, birthdate_idx),
                "year_enrolled":             _cell(row, yr_enrolled_idx),
                "entrance_exam_score":       _cell(row, exam_score_idx),
                "family_income":             _cell(row, income_idx),
                "parent_highest_education":  _cell(row, parent_edu_idx),
                "hs_gpa":                    _cell(row, hs_gpa_idx),
                "year_graduated":            _cell(row, yr_grad_idx),
                "shs_strand":                _cell(row, strand_idx),
                "hs_type":                   _cell(row, hs_type_idx),
                "graduation_honors":         _cell(row, honors_idx),
                "hs_school_name":            _cell(row, hs_school_idx),
                "scholarship_applicant":     _cell(row, scholar_idx),
                "scholarship_type":          _cell(row, scholar_type_idx),
            })

        return X, student_ids, student_meta

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    @staticmethod
    def _predict_sklearn(model, X) -> tuple[list, list]:
        preds  = model.predict(X)
        try:
            probas = [p[1] for p in model.predict_proba(X)]
        except AttributeError:
            probas = [float(p) for p in preds]
        return list(preds), probas

    @staticmethod
    def _predict_mock(X) -> tuple[list, list]:
        """Simulate realistic risk score distribution."""
        preds  = []
        probas = []
        for row in X:
            # Use feature values to create a somewhat meaningful score
            base  = sum(row) / max(len(row), 1) if row else 0.5
            score = min(1.0, max(0.0, base / 4.0 + random.uniform(-0.1, 0.3)))
            pred  = 1 if score >= 0.5 else 0
            preds.append(pred)
            probas.append(round(score, 3))
        return preds, probas

    # ------------------------------------------------------------------
    # Top factor helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _top_factor(model_data: dict, feature_names: list, row: list) -> str:
        try:
            importances = model_data["model"].feature_importances_
            top_idx     = max(range(len(importances)), key=lambda i: importances[i])
            return feature_names[top_idx] if top_idx < len(feature_names) else "—"
        except Exception:
            # Fallback: pick the feature with the highest value
            if not row or not feature_names:
                return "—"
            top_idx = max(range(len(row)), key=lambda i: row[i])
            return feature_names[top_idx] if top_idx < len(feature_names) else "—"

    @staticmethod
    def _shap_factors(
        model_data:    dict,
        feature_names: list,
        row:           list,
    ) -> list[tuple[str, int]]:
        try:
            importances = model_data["model"].feature_importances_
            total       = sum(importances) or 1
            pairs       = [
                (feature_names[i], round(v / total * 100))
                for i, v in enumerate(importances)
                if i < len(feature_names)
            ]
            pairs.sort(key=lambda x: x[1], reverse=True)
            return pairs[:6]
        except Exception:
            # Fallback
            if not row or not feature_names:
                return []
            total = sum(abs(v) for v in row) or 1
            pairs = [
                (feature_names[i], round(abs(row[i]) / total * 100))
                for i in range(min(len(row), len(feature_names)))
            ]
            pairs.sort(key=lambda x: x[1], reverse=True)
            return pairs[:6]

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    @staticmethod
    def _build_summary(predictions: list) -> "PredictionSummary":
        total    = len(predictions)
        high     = sum(1 for p in predictions if p["category"] == "high_risk")
        moderate = sum(1 for p in predictions if p["category"] == "moderate_risk")
        low      = sum(1 for p in predictions if p["category"] == "low_risk")

        avg_score = (
            round(sum(p["score"] for p in predictions) / total, 1)
            if total else 0.0
        )

        # College breakdown
        by_college: dict[str, dict] = {}
        for p in predictions:
            col = p["college"]
            if col not in by_college:
                by_college[col] = {"total": 0, "high": 0}
            by_college[col]["total"] += 1
            if p["category"] == "high_risk":
                by_college[col]["high"] += 1

        return PredictionSummary(
            total         = total,
            high_risk     = high,
            moderate_risk = moderate,
            low_risk      = low,
            avg_score     = avg_score,
            by_college    = by_college,
        )


# =====================================
# RESULT CLASSES
# =====================================

class PredictionSummary:
    def __init__(
        self,
        total:         int   = 0,
        high_risk:     int   = 0,
        moderate_risk: int   = 0,
        low_risk:      int   = 0,
        avg_score:     float = 0.0,
        by_college:    dict  = None,
    ):
        self.total         = total
        self.high_risk     = high_risk
        self.moderate_risk = moderate_risk
        self.low_risk      = low_risk
        self.avg_score     = avg_score
        self.by_college    = by_college or {}

    @property
    def high_risk_pct(self) -> float:
        return round(self.high_risk / self.total * 100, 1) if self.total else 0.0

    @property
    def overall_risk_score(self) -> str:
        return f"{self.avg_score}%"


class PredictionResult:
    def __init__(
        self,
        success:     bool       = False,
        predictions: list       = None,
        summary:     "PredictionSummary" = None,
        errors:      list       = None,
        is_mock:     bool       = False,
    ):
        self.success     = success
        self.predictions = predictions or []
        self.summary     = summary or PredictionSummary()
        self.errors      = errors or []
        self.is_mock     = is_mock