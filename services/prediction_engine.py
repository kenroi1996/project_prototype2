from __future__ import annotations
import random


# =====================================
# RISK THRESHOLDS
# =====================================

# Risk thresholds — applied to model.predict_proba() probability (0–100 scale).
# These are used for DISPLAY and RANKING only.
# The binary at_risk / not_at_risk label comes from model.predict() directly,
# which uses sklearn's optimal decision boundary (shifted by class_weight=balanced).
#
# Thresholds are calibrated for a ~5% minority class:
#   High Risk     ≥ 50%  (model is confident the student is at risk)
#   Moderate Risk ≥ 25%  (elevated probability, worth monitoring)
#   Low Risk      <  25%
RISK_HIGH     = 50
RISK_MODERATE = 25


def classify_risk(score: float, binary_pred: int = None) -> str:
    """
    Classify a student's risk tier.

    Parameters
    ----------
    score       : float  — predict_proba probability * 100  (0–100 scale)
    binary_pred : int    — model.predict() output (1=at_risk, 0=not_at_risk).
                           When provided, at_risk students are always placed in
                           at least Moderate Risk regardless of probability.
    """
    if binary_pred == 1:
        # Model's optimal decision boundary says at_risk —
        # always at least Moderate, High if probability also confirms it
        return "high_risk" if score >= RISK_HIGH else "moderate_risk"
    # Not flagged as at_risk by model — use probability for gradation
    if score >= RISK_HIGH:
        return "moderate_risk"   # high probability but model says not_at_risk → moderate
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

        headers       = unified_dataset["headers"]
        rows          = unified_dataset["rows"]
        feature_names = model_data["feature_names"]
        target_col    = model_data.get("target_col", "Final_Avg_GRD")

        cb("Preparing feature matrix…", 15)
        X, student_ids, student_meta = cls._prepare(
            headers, rows, feature_names, target_col,
            preprocessor = model_data.get("preprocessor"),
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
        except Exception as _exc:
            print(f"[PredictionEngine] sklearn predict failed ({_exc}), using mock")
            scores, probas = cls._predict_mock(X)
            is_mock = True

        cb("Computing risk categories…", 70)
        predictions = []
        for i, sid in enumerate(student_ids):
            score    = round(probas[i] * 100, 1)
            category = classify_risk(score, binary_pred=int(scores[i]))
            meta     = student_meta[i]

            predictions.append({
                "student_id": sid,
                # Display
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
                # All meta fields (passed through for persistence layer)
                **{k: v for k, v in meta.items()},
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
        preprocessor   = None,
    ) -> tuple[list, list, list]:
        """
        Returns (X, student_ids, student_meta).

        When a fitted preprocessor (DataPipeline) is provided, X is a
        properly encoded + scaled numpy array matching the training feature
        space exactly. Without it, X falls back to float-converted raw values
        (categorical strings become 0.0 — fine for approximate scoring only).
        """

        col_idx   = {h: i for i, h in enumerate(headers)}
        feat_idxs = [col_idx.get(f) for f in feature_names]

        # Student ID
        student_id_keywords = [
            "student_id", "id_no", "id", "keyid", "systemcode",
            "student code", "studentid"
        ]
        id_col = next(
            (c for c in headers if c.lower() in student_id_keywords), None
        )
        if not id_col:
            id_col = next(
                (c for c in headers if "id" in c.lower() or "code" in c.lower()),
                headers[0]
            )
        id_idx = col_idx.get(id_col, 0)

        def _idx(*keys):
            for k in keys:
                v = col_idx.get(k)
                if v is not None:
                    return v
            return None

        # ── Display / meta column resolution ─────────────────────────────────
        # first_name and last_name are stored separately so dim_student can
        # receive them as distinct fields (rather than a combined "name" string).
        fname_idx   = _idx("firstname", "first_name", "First_Name")
        lname_idx   = _idx("lastname",  "last_name",  "Last_Name")
        program_idx = _idx("Program",   "program",    "PROGRAM",   "program_code")
        college_idx = _idx("College",   "college",    "COLLEGE")
        gwa_idx     = _idx("Final_Avg_GRD", "final_avg_grd", "FINAL_AVG_GRD")
        abs_idx     = _idx("Attendance_Rate", "absences")

        seccode_idx      = _idx("SecCode",                  "seccode",                  "sec_code")
        year_idx         = _idx("Year",                     "year",                     "year_level")
        sex_idx          = _idx("Sex_code",                 "sex_code",                 "gender", "GENDER")
        address_idx      = _idx("Home_Address",             "home_address",             "HOME_ADDRESS")
        # Municipality — stored as home_municipality in dim_student
        municipality_idx = _idx("Municipality",             "municipality",             "home_municipality",
                                "MUNICIPALITY")
        civil_idx        = _idx("Civil_Status",             "civil_status",             "CIVIL_STATUS")
        birthdate_idx    = _idx("Birthdate",                "birthdate",                "BIRTHDATE")
        yr_enrolled_idx  = _idx("Year_Enrolled",            "year_enrolled",            "YEAR_ENROLLED")
        exam_score_idx   = _idx("Entrance_Exam_Score",      "entrance_exam_score",      "ENTRANCE_EXAM_SCORE")
        # family_income_bracket — dim_student uses this name; portals may use
        # Family_Income or family_income_bracket interchangeably
        income_idx       = _idx("Family_Income",            "family_income_bracket",    "family_income",
                                "FAMILY_INCOME")
        parent_edu_idx   = _idx("Parent_Highest_Education", "parent_highest_education", "PARENT_HIGHEST_EDUCATION")
        hs_gpa_idx       = _idx("HS_GPA",                   "hs_gpa",                   "HS_GPA")
        yr_grad_idx      = _idx("Year_Graduated",           "year_graduated",           "YEAR_GRADUATED")
        strand_idx       = _idx("SHS_Strand",               "shs_strand",               "SHS_STRAND")
        hs_type_idx      = _idx("HS_Type",                  "hs_type",                  "HS_TYPE")
        honors_idx       = _idx("Graduation_Honors",        "graduation_honors",        "GRADUATION_HONORS")
        hs_school_idx    = _idx("HS_School",                "hs_school",                "HS_SCHOOL",
                                "hs_school_name")
        scholar_idx      = _idx("Scholarship_Applicant",    "scholarship_applicant",    "SCHOLARSHIP_APPLICANT")
        scholar_type_idx = _idx("Scholarship_Type",         "scholarship_type",         "SCHOLARSHIP_TYPE")
        religion_idx     = _idx("Religion",                 "religion",                 "RELIGION")

        _resolved = {
            "program": program_idx, "college": college_idx,
            "gwa": gwa_idx, "sec_code": seccode_idx, "year_level": year_idx,
            "sex_code": sex_idx, "home_address": address_idx,
            "municipality": municipality_idx, "civil_status": civil_idx,
            "birthdate": birthdate_idx, "year_enrolled": yr_enrolled_idx,
            "entrance_exam_score": exam_score_idx, "family_income": income_idx,
            "parent_edu": parent_edu_idx, "hs_gpa": hs_gpa_idx,
            "year_graduated": yr_grad_idx, "shs_strand": strand_idx,
            "hs_type": hs_type_idx, "honors": honors_idx,
            "hs_school": hs_school_idx, "scholarship": scholar_idx,
            "scholarship_type": scholar_type_idx, "religion": religion_idx,
        }
        _missing = [k for k, v in _resolved.items() if v is None]
        _found   = [k for k, v in _resolved.items() if v is not None]
        print(f"[PredictionEngine] Resolved {len(_found)}/{len(_resolved)} meta columns")
        if _missing:
            print(f"[PredictionEngine] NULL columns (not in headers): {_missing}")

        X_raw        = []   # raw string values per feature — for preprocessing
        X            = []   # float matrix (fallback when no preprocessor)
        student_ids  = []
        student_meta = []

        def _cell(row, idx):
            if idx is not None and idx < len(row):
                return str(row[idx]).strip()
            return ""

        # Load the raw meta snapshot captured before feature engineering
        from services.data_store import DataStore
        raw_meta: dict = DataStore.get().raw_meta_snapshot or {}

        for row in rows:
            sid = _cell(row, id_idx)
            if not sid:
                continue

            raw_feature_row = {}   # col_name → raw string value
            float_feature_row = []

            for feat, idx in zip(feature_names, feat_idxs):
                if idx is None or idx >= len(row):
                    raw_feature_row[feat] = ""
                    float_feature_row.append(0.0)
                else:
                    val = row[idx].strip()
                    raw_feature_row[feat] = val
                    try:
                        float_feature_row.append(float(val))
                    except ValueError:
                        float_feature_row.append(0.0)

            X_raw.append(raw_feature_row)
            X.append(float_feature_row)
            student_ids.append(sid)

            first_name = _cell(row, fname_idx)
            last_name  = _cell(row, lname_idx)

            # Pull from the raw snapshot when names aren't in engineered headers
            snap = raw_meta.get(sid, {})
            if not first_name:
                first_name = (snap.get("First_Name") or snap.get("firstname")
                              or snap.get("first_name") or "")
            if not last_name:
                last_name  = (snap.get("Last_Name") or snap.get("lastname")
                              or snap.get("last_name") or "")
            full_name = f"{first_name} {last_name}".strip() or sid

            # Build meta: start with engineered-dataset fields, then overlay
            # the raw snapshot so stripped columns are always populated.
            meta = {
                # ── Display ───────────────────────────────────────────────────
                "name":    full_name,
                "program": _cell(row, program_idx) or snap.get("Program") or "—",
                "college": _cell(row, college_idx) or snap.get("College") or "—",
                "gwa":     _cell(row, gwa_idx)     or snap.get("Final_Avg_GRD") or "—",
                "absences":_cell(row, abs_idx)     or "—",
                # ── dim_student fields ────────────────────────────────────────
                "first_name":               first_name,
                "last_name":                last_name,
                "sex_code":                 _cell(row, sex_idx)         or snap.get("Sex_code", ""),
                "birthdate":                _cell(row, birthdate_idx)   or snap.get("Birthdate", ""),
                "civil_status":             _cell(row, civil_idx)       or snap.get("Civil_Status", ""),
                "home_address":             _cell(row, address_idx)     or snap.get("Home_Address", ""),
                "home_municipality":        _cell(row, municipality_idx)or snap.get("Municipality", ""),
                "family_income_bracket":    _cell(row, income_idx)      or snap.get("Family_Income", ""),
                "parent_highest_education": _cell(row, parent_edu_idx)  or snap.get("Parent_Highest_Education", ""),
                "hs_school_name":           _cell(row, hs_school_idx)   or snap.get("HS_School", ""),
                "hs_type":                  _cell(row, hs_type_idx)     or snap.get("HS_Type", ""),
                "shs_strand":               _cell(row, strand_idx)      or snap.get("SHS_Strand", ""),
                "graduation_honors":        _cell(row, honors_idx)      or snap.get("Graduation_Honors", ""),
                "scholarship_type":         _cell(row, scholar_type_idx)or snap.get("Scholarship_Type", ""),
                "religion":                 _cell(row, religion_idx)    or snap.get("Religion", ""),
                # ── fact fields ───────────────────────────────────────────────
                "sec_code":                 _cell(row, seccode_idx)      or snap.get("SecCode", ""),
                "year_level":               _cell(row, year_idx)         or snap.get("Year", ""),
                "entrance_exam_score":      _cell(row, exam_score_idx)   or "",
                "hs_gpa":                   _cell(row, hs_gpa_idx)       or snap.get("HS_GPA", ""),
                # ── legacy / extra ────────────────────────────────────────────
                "year_enrolled":            _cell(row, yr_enrolled_idx)  or snap.get("Year_Enrolled", ""),
                "year_graduated":           _cell(row, yr_grad_idx)      or snap.get("Year_Graduated", ""),
                "family_income":            _cell(row, income_idx)       or snap.get("Family_Income", ""),
                "scholarship_applicant":    _cell(row, scholar_idx)      or snap.get("Scholarship_Applicant", ""),
            }

            student_meta.append(meta)

        # ── Apply preprocessor (encode + scale) if available ─────────────────
        # X_raw holds original string values per feature column name.
        # We rebuild a DataFrame from these, apply the fitted LabelEncoders
        # and StandardScaler, then overwrite X with the properly typed array.
        if preprocessor is not None and X_raw and feature_names:
            import pandas as pd
            try:
                df_pred = pd.DataFrame(X_raw, columns=feature_names)

                # Re-encode categorical columns using fitted LabelEncoders
                for col, le in preprocessor._encoders.items():
                    if col in df_pred.columns:
                        filled = df_pred[col].fillna("__MISSING__").astype(str)
                        known  = set(le.classes_)
                        # Map unseen labels to the first known class
                        filled = filled.apply(
                            lambda v: v if v in known else le.classes_[0]
                        )
                        df_pred[col] = le.transform(filled)
                    else:
                        # Column not in prediction data — fill with 0
                        df_pred[col] = 0

                # Convert all columns to numeric — '' and unparseable strings → 0.0
                for col in df_pred.columns:
                    df_pred[col] = pd.to_numeric(
                        df_pred[col], errors="coerce"
                    ).fillna(0.0)

                # Extra safety: ensure no NaN remains before scaling
                # (object-dtype columns may not convert cleanly in all pandas versions)
                df_pred = df_pred.fillna(0.0)

                # Re-scale using fitted StandardScaler.
                # CRITICAL: pass columns in the EXACT order the scaler was fitted on
                # (_numerical_columns preserves training order). Mismatched column
                # order silently produces NaN which causes sklearn to reject X.
                if preprocessor._scaler is not None and preprocessor._numerical_columns:
                    scale_cols = [c for c in preprocessor._numerical_columns
                                  if c in df_pred.columns]
                    if scale_cols:
                        import numpy as _np
                        # Pass DataFrame (not .values) so sklearn doesn't warn about
                        # missing feature names. Use a fresh DataFrame with the
                        # scaler's expected column order.
                        scale_df = pd.DataFrame(
                            df_pred[scale_cols].values,
                            columns=scale_cols
                        )
                        scaled = preprocessor._scaler.transform(scale_df)
                        scaled = _np.nan_to_num(scaled, nan=0.0, posinf=0.0, neginf=0.0)
                        df_pred[scale_cols] = scaled

                # Final safety net — ensure absolutely no NaN reaches the model
                df_pred = df_pred.fillna(0.0)

                # Select features in training order
                avail = [f for f in feature_names if f in df_pred.columns]
                if len(avail) == len(feature_names):
                    X = df_pred[feature_names].values.tolist()
                elif avail:
                    X = df_pred[avail].values.tolist()

                # Verify no NaN in final X
                import numpy as _np2
                X_arr = _np2.array(X, dtype=float)
                nan_count = _np2.isnan(X_arr).sum()
                if nan_count > 0:
                    print(f"[PredictionEngine] WARNING: {nan_count} NaN in X "
                          f"after preprocessing — filling with 0")
                    X_arr = _np2.nan_to_num(X_arr, nan=0.0)
                    X = X_arr.tolist()

                print(f"[PredictionEngine] Preprocessor applied — "
                      f"{len(avail)}/{len(feature_names)} features encoded+scaled")

            except Exception as prep_exc:
                print(f"[PredictionEngine] WARNING: preprocessor failed "
                      f"({prep_exc}), using raw float values")

        return X, student_ids, student_meta

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    @staticmethod
    def _predict_sklearn(
        model,
        X: list,
        preprocessor   = None,
        feature_names: list = None,
        pred_headers:  list = None,
    ) -> tuple[list, list]:
        """
        Run sklearn inference, applying the fitted preprocessor first.

        The model was trained on LabelEncoded + StandardScaled data.
        PredictionEngine builds X from raw string rows (categorical columns
        become 0.0 because float("traditional") fails). Without re-applying
        the same transformations, predict_proba raises ValueError and falls
        back to binary [0,1] outputs → scores of 0 or 100.

        When a preprocessor (fitted DataPipeline) is available, we rebuild
        a properly encoded + scaled matrix before calling the model.
        """
        import numpy as np
        import pandas as pd

        X_arr = np.array(X, dtype=float)

        if preprocessor is not None and feature_names and pred_headers:
            try:
                # Build a DataFrame from raw prediction rows using pred_headers
                # (excludes Student_ID — first column of PREDICTION_FEATURES)
                feat_cols = [h for h in pred_headers if h != "Student_ID"]
                df_pred = pd.DataFrame(X, columns=feat_cols
                          if len(feat_cols) == len(X[0]) else
                          pred_headers[:len(X[0])])

                # Re-encode categorical columns using fitted LabelEncoders
                for col, le in preprocessor._encoders.items():
                    if col in df_pred.columns:
                        filled = df_pred[col].fillna("__MISSING__").astype(str)
                        # Handle unseen labels gracefully
                        known = set(le.classes_)
                        filled = filled.apply(
                            lambda v: v if v in known else le.classes_[0]
                        )
                        df_pred[col] = le.transform(filled)

                # Re-scale numerical columns using fitted scaler
                if preprocessor._scaler is not None and preprocessor._numerical_columns:
                    scale_cols = [c for c in preprocessor._numerical_columns
                                  if c in df_pred.columns]
                    if scale_cols:
                        import numpy as _np
                        scaled = preprocessor._scaler.transform(
                            df_pred[scale_cols].values
                        )
                        scaled = _np.nan_to_num(scaled, nan=0.0, posinf=0.0, neginf=0.0)
                        df_pred[scale_cols] = scaled

                # Final safety net
                df_pred = df_pred.fillna(0.0)

                # Select only the features the model was trained on, in order
                avail = [f for f in feature_names if f in df_pred.columns]
                if avail:
                    import numpy as _np2
                    X_arr = _np2.nan_to_num(
                        df_pred[avail].values.astype(float),
                        nan=0.0, posinf=0.0, neginf=0.0
                    )

            except Exception as prep_exc:
                print(f"[PredictionEngine] Preprocessor transform failed "
                      f"({prep_exc}), using raw X")
                X_arr = np.array(X, dtype=float)

        preds = model.predict(X_arr)
        try:
            proba_matrix = model.predict_proba(X_arr)
            classes  = list(model.classes_)
            pos_idx  = classes.index(1) if 1 in classes else -1
            if pos_idx >= 0:
                probas = [float(row[pos_idx]) for row in proba_matrix]
            else:
                probas = [float(p) for p in preds]
        except (AttributeError, ValueError) as exc:
            print(f"[PredictionEngine] predict_proba failed ({exc}), using binary preds")
            probas = [float(p) for p in preds]
        return list(preds), probas

    @staticmethod
    def _predict_mock(X) -> tuple[list, list]:
        preds  = []
        probas = []
        for row in X:
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
        success:     bool             = False,
        predictions: list             = None,
        summary:     "PredictionSummary" = None,
        errors:      list             = None,
        is_mock:     bool             = False,
    ):
        self.success     = success
        self.predictions = predictions or []
        self.summary     = summary or PredictionSummary()
        self.errors      = errors or []
        self.is_mock     = is_mock