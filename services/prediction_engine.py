from __future__ import annotations
import random

from services.system_config import SystemConfig


# =====================================
# RISK THRESHOLDS
# =====================================
# These used to be hardcoded here, which meant SystemConfig's admin-
# configurable risk_high_threshold / risk_moderate_threshold (Settings
# page) had no actual effect on classification — a saved threshold was
# never consulted. classify_risk() now reads the live values on every
# call so an admin's change takes effect immediately, no restart needed.
#
# Defaults (50 / 25) match SystemConfig's own fallback defaults, so
# behavior is unchanged for anyone who has never touched the setting.

def _risk_high_threshold() -> float:
    return SystemConfig.risk_high_threshold()


def _risk_moderate_threshold() -> float:
    return SystemConfig.risk_moderate_threshold()


def classify_risk(score: float, binary_pred=None) -> str:
    """
    Classify a student's risk level from probability score and binary prediction.
    Handles both integer (0/1) and string ('at_risk'/'not_at_risk') predictions.
    """
    risk_high     = _risk_high_threshold()
    risk_moderate = _risk_moderate_threshold()

    _POSITIVE = {1, '1', 'at_risk', 'at-risk', True}
    is_positive = binary_pred in _POSITIVE

    if is_positive:
        return "high_risk" if score >= risk_high else "moderate_risk"
    if score >= risk_high:
        return "high_risk"
    elif score >= risk_moderate:
        return "moderate_risk"
    return "low_risk"


RISK_HIGH_LABEL     = "High Risk"
RISK_MODERATE_LABEL = "Moderate Risk"
RISK_LOW_LABEL      = "Low Risk"


def risk_label(category: str) -> str:
    return {
        "high_risk":     RISK_HIGH_LABEL,
        "moderate_risk": RISK_MODERATE_LABEL,
        "low_risk":      RISK_LOW_LABEL,
    }.get(category, "Unknown")


# =====================================
# FEATURE DISPLAY METADATA
# =====================================

# Human-readable labels and value formatters for each training feature.
# Used by _shap_factors() to produce meaningful per-student explanations.
_FEATURE_LABELS: dict[str, str] = {
    "Entrance_Exam_Score":  "Entrance Exam Score",
    "HS_GPA":               "High School GPA",
    "Strand_Program_Match": "SHS Strand–Program Alignment",
    "Financial_Stress":     "Financial Stress Index",
    "First_Gen_Student":    "First-Generation Student",
    "Has_Scholarship":      "Has Scholarship",
    "Gap_Years":            "Gap Years Before College",
    "Private_HS":           "Attended Private High School",
    "Has_HS_Honors":        "Graduated with HS Honors",
    "Age_At_Enrollment":    "Age at Enrollment",
    "Distance_KM":          "Distance from Campus (km)",
}

# Features where a HIGH value is the risk signal (bad = high).
# For these, contribution = importance × value (higher → more risk).
# Features NOT in this set are "protective" — a high value reduces risk,
# so contribution = importance × (1 / (1 + value)) to invert the direction.
_HIGH_IS_RISKY: set[str] = {
    "Financial_Stress",
    "First_Gen_Student",
    "Gap_Years",
    "Distance_KM",
}

# Features where a LOW value is the risk signal (bad = low).
# Contribution = importance × (1 / (1 + value)) — lower score → higher risk weight.
_LOW_IS_RISKY: set[str] = {
    "Entrance_Exam_Score",
    "HS_GPA",
    "Has_HS_Honors",
    "Has_Scholarship",
    "Strand_Program_Match",
}


def _feature_label(name: str) -> str:
    """Return a human-readable label for a feature, stripping one-hot suffixes."""
    # One-hot encoded features look like "Program_BSCS" or "Age_Group_adult"
    for base, label in _FEATURE_LABELS.items():
        if name == base or name.startswith(base + "_"):
            return label
    # Fallback: replace underscores and title-case
    return name.replace("_", " ").title()


def _format_value(feature: str, value: float) -> str:
    """Format a raw feature value for display in the risk card."""
    if feature == "Entrance_Exam_Score":
        return f"{value:.0f}/100"
    if feature == "HS_GPA":
        return f"{value:.2f}"
    if feature == "Financial_Stress":
        return f"{value:.0f}/10"
    if feature == "Gap_Years":
        return f"{value:.0f} yr{'s' if value != 1 else ''}"
    if feature == "Distance_KM":
        return f"{value:.1f} km"
    if feature == "Age_At_Enrollment":
        return f"{value:.0f} yrs old"
    if feature in ("First_Gen_Student", "Has_Scholarship",
                   "Private_HS", "Has_HS_Honors"):
        return "Yes" if value >= 0.5 else "No"
    if feature == "Strand_Program_Match":
        return {2: "Aligned", 1: "Partial", 0: "Misaligned", -1: "Unknown"}.get(
            int(round(value)), str(value)
        )
    return str(value)


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
        X, student_ids, student_meta, raw_feature_rows = cls._prepare(
            headers, rows, feature_names, target_col,
            preprocessor=model_data.get("preprocessor"),
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
            # ── DEBUG ──────────────────────────────────────────────────
            print(f"[DEBUG] Top 5 probas:    {sorted(probas, reverse=True)[:5]}")
            print(f"[DEBUG] Bottom 5 probas: {sorted(probas)[:5]}")
            print(f"[DEBUG] Sample probas (first 5): {probas[:5]}")
            # ───────────────────────────────────────────────────────────
        except Exception as _exc:
            print(f"[PredictionEngine] sklearn predict failed ({_exc}), using mock")
            scores, probas = cls._predict_mock(X)
            is_mock = True

        # Apply the optimised decision threshold saved during training.
        # model.predict() always uses 0.5; our threshold (e.g. 0.15) was
        # tuned to maximise recall on the imbalanced training set.
        threshold = 0.15  # fallback if not stored
        try:
            meta_block = model_data.get("metadata", {}) or {}
            threshold  = float(
                model_data.get("decision_threshold")
                or meta_block.get("decision_threshold")
                or meta_block.get("threshold")
                or 0.15
            )
        except (TypeError, ValueError):
            pass
        print(f"[PredictionEngine] Using decision threshold: {threshold}")

        # Override binary scores using threshold on probabilities
        _POSITIVE = {1, '1', 'at_risk', 'at-risk', True}
        scores = ['at_risk' if p >= threshold else 'not_at_risk' for p in probas]

        cb("Computing risk categories…", 70)

        # Pre-extract global importances once — used as weights, not as the
        # final contribution score.  Per-student values modulate these weights.
        try:
            global_importances = list(model_data["model"].feature_importances_)
        except Exception:
            global_importances = [1.0 / max(len(feature_names), 1)] * len(feature_names)

        predictions = []
        for i, sid in enumerate(student_ids):
            score    = round(probas[i] * 100, 1)
            # ── DEBUG ──────────────────────────────────────────────────
            if score >= 99:
                print(f"[DEBUG] 100% student: sid={sid} proba={probas[i]} binary={scores[i]}")
            # ───────────────────────────────────────────────────────────
            category = classify_risk(score, binary_pred=scores[i])
            meta     = student_meta[i]
            raw_vals = raw_feature_rows[i]

            shap_factors = cls._shap_factors(
                feature_names, global_importances, raw_vals
            )
            top_factor = shap_factors[0][0] if shap_factors else "—"

            predictions.append({
                "student_id":   sid,
                "name":         meta.get("name", "—"),
                "program":      meta.get("program", "—"),
                "college":      meta.get("college", "—"),
                "score":        score,
                "category":     category,
                "label":        risk_label(category),
                "factor":       top_factor,
                "shap_factors": shap_factors,
                "gwa":          meta.get("gwa", "—"),
                "absences":     meta.get("absences", "—"),
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
    ) -> tuple[list, list, list, list]:
        """
        Returns (X, student_ids, student_meta, raw_feature_rows).

        raw_feature_rows is a list of dicts {feature_name: float_value}
        containing the PRE-SCALING numeric value for each feature and student.
        These are used by _shap_factors() to produce per-student explanations.
        """

        col_idx   = {h: i for i, h in enumerate(headers)}
        feat_idxs = [col_idx.get(f) for f in feature_names]

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

        fname_idx        = _idx("firstname", "first_name", "First_Name",
                                "FIRSTNAME", "FIRST_NAME", "fname", "FNAME",
                                "given_name", "Given_Name", "GIVEN_NAME")
        lname_idx        = _idx("lastname", "last_name", "Last_Name",
                                "LASTNAME", "LAST_NAME", "lname", "LNAME",
                                "surname", "Surname", "SURNAME")
        fullname_idx     = _idx("full_name", "Full_Name", "FULL_NAME",
                                "name", "NAME", "student_name",
                                "Student_Name", "STUDENT_NAME")
        program_idx      = _idx("Program",   "program",    "PROGRAM",   "program_code")
        college_idx      = _idx("College",   "college",    "COLLEGE")
        gwa_idx          = _idx("Final_Avg_GRD", "final_avg_grd", "FINAL_AVG_GRD")
        abs_idx          = _idx("Attendance_Rate", "absences")
        seccode_idx      = _idx("SecCode",                  "seccode",                  "sec_code")
        year_idx         = _idx("Year",                     "year",                     "year_level")
        sex_idx          = _idx("Sex_code",                 "sex_code",                 "gender", "GENDER")
        address_idx      = _idx("Home_Address",             "home_address",             "HOME_ADDRESS")
        municipality_idx = _idx("Municipality",             "municipality",             "home_municipality", "MUNICIPALITY")
        civil_idx        = _idx("Civil_Status",             "civil_status",             "CIVIL_STATUS")
        birthdate_idx    = _idx("Birthdate",                "birthdate",                "BIRTHDATE")
        yr_enrolled_idx  = _idx("Year_Enrolled",            "year_enrolled",            "YEAR_ENROLLED")
        exam_score_idx   = _idx("Entrance_Exam_Score",      "entrance_exam_score",      "ENTRANCE_EXAM_SCORE")
        income_idx       = _idx("Family_Income",            "family_income_bracket",    "family_income", "FAMILY_INCOME")
        parent_edu_idx   = _idx("Parent_Highest_Education", "parent_highest_education", "PARENT_HIGHEST_EDUCATION")
        hs_gpa_idx       = _idx("HS_GPA",                   "hs_gpa",                   "HS_GPA")
        yr_grad_idx      = _idx("Year_Graduated",           "year_graduated",           "YEAR_GRADUATED")
        strand_idx       = _idx("SHS_Strand",               "shs_strand",               "SHS_STRAND")
        hs_type_idx      = _idx("HS_Type",                  "hs_type",                  "HS_TYPE")
        honors_idx       = _idx("Graduation_Honors",        "graduation_honors",        "GRADUATION_HONORS")
        hs_school_idx    = _idx("HS_School",                "hs_school",                "HS_SCHOOL", "hs_school_name")
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

        X_raw         = []
        X             = []
        student_ids   = []
        student_meta  = []
        raw_feat_rows = []   # list of {feature_name: float} per student

        def _cell(row, idx):
            if idx is not None and idx < len(row):
                return str(row[idx]).strip()
            return ""

        from services.data_store import DataStore
        raw_meta: dict = DataStore.get().raw_meta_snapshot or {}

        # Normalise snapshot keys: strip whitespace + remove '.0' suffix
        # that pandas adds when reading numeric-looking IDs from Excel.
        def _norm_sid(raw_sid: str) -> str:
            s = str(raw_sid).strip()
            return s[:-2] if s.endswith(".0") else s

        raw_meta_norm: dict = {_norm_sid(k): v for k, v in raw_meta.items()}

        for row in rows:
            sid = _norm_sid(_cell(row, id_idx) or "")
            if not sid or sid in ("", "nan", "None"):
                continue

            raw_feature_row = {}
            float_feature_row = []
            raw_vals: dict[str, float] = {}

            for feat, idx in zip(feature_names, feat_idxs):
                if idx is None or idx >= len(row):
                    raw_feature_row[feat] = ""
                    float_feature_row.append(0.0)
                    raw_vals[feat] = 0.0
                else:
                    val = row[idx].strip()
                    raw_feature_row[feat] = val
                    try:
                        fval = float(val)
                    except ValueError:
                        fval = 0.0
                    float_feature_row.append(fval)
                    raw_vals[feat] = fval

            X_raw.append(raw_feature_row)
            X.append(float_feature_row)
            raw_feat_rows.append(raw_vals)
            student_ids.append(sid)

            first_name = _cell(row, fname_idx)
            last_name  = _cell(row, lname_idx)
            snap = raw_meta_norm.get(sid, {})

            # Try snapshot for first/last name
            if not first_name:
                first_name = (
                    snap.get("First_Name") or snap.get("FIRSTNAME")
                    or snap.get("firstname") or snap.get("first_name")
                    or snap.get("fname") or snap.get("given_name") or ""
                )
            if not last_name:
                last_name = (
                    snap.get("Last_Name") or snap.get("LASTNAME")
                    or snap.get("lastname") or snap.get("last_name")
                    or snap.get("lname") or snap.get("surname") or ""
                )

            # Try combined full_name column as last resort
            full_name = f"{first_name} {last_name}".strip()
            if not full_name:
                full_name = (
                    _cell(row, fullname_idx)
                    or snap.get("full_name") or snap.get("FULL_NAME")
                    or snap.get("student_name") or snap.get("STUDENT_NAME")
                    or snap.get("name") or snap.get("NAME") or sid
                )

            meta = {
                # Displayed identifier is the student ID only — real
                # names are never shown in the UI, per the anonymization
                # requirement. first_name/last_name below are still
                # captured and passed through separately so
                # RiskPersistenceService can correctly encrypt and store
                # the real name in dim_student; this "name" field is
                # display-only and intentionally never the real name.
                "name":    sid,
                "program": _cell(row, program_idx) or snap.get("Program") or "—",
                "college": _cell(row, college_idx) or snap.get("College") or "—",
                "gwa":     _cell(row, gwa_idx)     or snap.get("Final_Avg_GRD") or "—",
                "absences":_cell(row, abs_idx)     or "—",
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
                "sec_code":                 _cell(row, seccode_idx)      or snap.get("SecCode", ""),
                "year_level":               _cell(row, year_idx)         or snap.get("Year", ""),
                "entrance_exam_score":      _cell(row, exam_score_idx)   or "",
                "hs_gpa":                   _cell(row, hs_gpa_idx)       or snap.get("HS_GPA", ""),
                "year_enrolled":            _cell(row, yr_enrolled_idx)  or snap.get("Year_Enrolled", ""),
                "year_graduated":           _cell(row, yr_grad_idx)      or snap.get("Year_Graduated", ""),
                "family_income":            _cell(row, income_idx)       or snap.get("Family_Income", ""),
                "scholarship_applicant":    _cell(row, scholar_idx)      or snap.get("Scholarship_Applicant", ""),
            }
            student_meta.append(meta)

        # ── pd.get_dummies fallback (no preprocessor — mirrors TrainingEngine) ─
        # TrainingEngine uses pd.get_dummies on categoricals rather than a fitted
        # LabelEncoder preprocessor. When no preprocessor is saved we replicate
        # that encoding here so categoricals are not silently converted to 0.0.
        if preprocessor is None and X_raw and feature_names:
            import pandas as pd
            import numpy as np

            _CATEGORICAL = {"Program", "Age_Group", "Distance_Bucket"}

            try:
                df_raw = pd.DataFrame(X_raw, columns=feature_names)

                # Force numeric on continuous columns
                num_cols = [c for c in feature_names if c not in _CATEGORICAL]
                for col in num_cols:
                    if col in df_raw.columns:
                        df_raw[col] = pd.to_numeric(
                            df_raw[col], errors="coerce"
                        ).fillna(0.0)

                # One-hot encode categoricals — same as TrainingEngine
                cat_present = [c for c in _CATEGORICAL if c in df_raw.columns]
                if cat_present:
                    df_raw = pd.get_dummies(df_raw, columns=cat_present, dtype=float)

                df_raw = df_raw.fillna(0.0)

                # Align to the model's stored feature_names (includes dummy cols
                # produced during training). Missing → 0, extra → dropped.
                missing_cols = [c for c in feature_names if c not in df_raw.columns]
                aligned_cols = [c for c in feature_names if c in df_raw.columns]

                for mc in missing_cols:
                    df_raw[mc] = 0.0

                df_raw = df_raw[feature_names]
                df_raw = df_raw.fillna(0.0)

                import numpy as np
                X_arr = np.array(df_raw.values, dtype=float)
                X_arr = np.nan_to_num(X_arr, nan=0.0, posinf=0.0, neginf=0.0)
                X = X_arr.tolist()

                print(
                    f"[PredictionEngine] get_dummies encoding — "
                    f"{len(aligned_cols)}/{len(feature_names)} matched, "
                    f"{len(missing_cols)} defaulted to 0"
                )

            except Exception as enc_exc:
                print(f"[PredictionEngine] get_dummies fallback failed: {enc_exc}")

        # ── Apply preprocessor (encode + scale) if available ─────────────────
        if preprocessor is not None and X_raw and feature_names:
            import pandas as pd
            try:
                df_pred = pd.DataFrame(X_raw, columns=feature_names)

                for col, le in preprocessor._encoders.items():
                    if col in df_pred.columns:
                        filled = df_pred[col].fillna("__MISSING__").astype(str)
                        known  = set(le.classes_)
                        filled = filled.apply(
                            lambda v: v if v in known else le.classes_[0]
                        )
                        df_pred[col] = le.transform(filled)
                    else:
                        df_pred[col] = 0

                for col in df_pred.columns:
                    df_pred[col] = pd.to_numeric(
                        df_pred[col], errors="coerce"
                    ).fillna(0.0)

                df_pred = df_pred.fillna(0.0)

                if preprocessor._scaler is not None and preprocessor._numerical_columns:
                    scale_cols = [c for c in preprocessor._numerical_columns
                                  if c in df_pred.columns]
                    if scale_cols:
                        import numpy as _np
                        scale_df = pd.DataFrame(
                            df_pred[scale_cols].values, columns=scale_cols
                        )
                        scaled = preprocessor._scaler.transform(scale_df)
                        scaled = _np.nan_to_num(scaled, nan=0.0, posinf=0.0, neginf=0.0)
                        df_pred[scale_cols] = scaled

                df_pred = df_pred.fillna(0.0)

                avail = [f for f in feature_names if f in df_pred.columns]
                if len(avail) == len(feature_names):
                    X = df_pred[feature_names].values.tolist()
                elif avail:
                    X = df_pred[avail].values.tolist()

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

        return X, student_ids, student_meta, raw_feat_rows

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    @staticmethod
    def _predict_sklearn(model, X, preprocessor=None,
                         feature_names=None, pred_headers=None):
        import numpy as np
 
        X_arr = np.array(X, dtype=float)
 
        preds = model.predict(X_arr)
 
        try:
            proba_matrix = model.predict_proba(X_arr)
            classes      = list(model.classes_)
            print(f"[PredictionEngine] classes={classes}")
 
            # ── Strategy 1: known positive-class string/int labels ────────
            _POSITIVE = {1, '1', 'at_risk', 'at-risk', 'high_risk',
                         'risk', 'positive', True}
            pos_idx = next(
                (i for i, c in enumerate(classes) if c in _POSITIVE), -1
            )
 
            # ── Strategy 2: binary classes — minority has HIGHER mean proba
            #    when class_weight='balanced' inflates minority predictions.
            #    Pick the column whose mean is LOWER (minority class is rarer
            #    so its average predicted probability is lower overall).
            # ✅ With this
            if pos_idx < 0 and len(classes) == 2:
                proba_arr = np.array(proba_matrix)
                means     = proba_arr.mean(axis=0)
                # With class_weight='balanced', the model assigns elevated
                # probabilities to the minority (at-risk) class, so its column
                # has a HIGHER mean. argmin was inverting all predictions,
                # causing top students (exam 128, GPA 97) to show 100% risk.
                pos_idx = int(np.argmax(means))
                print(f"[PredictionEngine] Inferred pos_idx={pos_idx} "
                    f"via argmax(means={means.round(4)}), classes={classes}")
 
            # ── Strategy 3: last resort ───────────────────────────────────
            if pos_idx < 0:
                pos_idx = min(1, len(classes) - 1)
                print(f"[PredictionEngine] WARNING: fallback pos_idx={pos_idx}")
 
            probas = [float(row[pos_idx]) for row in proba_matrix]
            print(f"[PredictionEngine] pos_idx={pos_idx}, "
                  f"sample probas={[round(p, 3) for p in probas[:5]]}")
 
            # ── Sanity check: if majority of probas > 0.5, the column is
            #    likely the negative class — flip to the other column.
            high_count = sum(1 for p in probas if p > 0.5)
            if high_count > len(probas) * 0.6:
                alt_idx = 1 - pos_idx   # only valid for binary
                if 0 <= alt_idx < len(classes):
                    alt_probas = [float(row[alt_idx]) for row in proba_matrix]
                    alt_high   = sum(1 for p in alt_probas if p > 0.5)
                    if alt_high < high_count:
                        print(f"[PredictionEngine] SANITY FLIP: "
                              f"{high_count}/{len(probas)} probas >0.5 "
                              f"with pos_idx={pos_idx}, "
                              f"flipping to col {alt_idx} "
                              f"({alt_high}/{len(probas)} >0.5)")
                        pos_idx = alt_idx
                        probas  = alt_probas
 
        except (AttributeError, ValueError) as exc:
            print(f"[PredictionEngine] predict_proba failed ({exc}), "
                  f"using binary preds")
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
    # Per-student risk factor computation
    # ------------------------------------------------------------------

    @staticmethod
    def _shap_factors(
        feature_names:      list,
        global_importances: list,
        raw_vals:           dict,
    ) -> list[tuple[str, str, str]]:
        """
        Compute per-student risk factor contributions.

        Returns a list of (feature_name, human_label, formatted_value) tuples
        sorted by contribution, highest first, top 6 only.

        How it works
        ------------
        Global feature importance tells us which features the model relies on
        most overall.  But to explain WHY a specific student is flagged, we
        need to weight those importances by the student's actual values.

        For "risky-when-high" features (Financial_Stress, Gap_Years, etc.):
            contribution = importance × normalised_value

        For "risky-when-low" features (Entrance_Exam_Score, HS_GPA, etc.):
            contribution = importance × (1 - normalised_value)
            — a low exam score produces a HIGH contribution, correctly
              identifying it as a risk factor for THIS student.

        One-hot encoded features (Program_BSCS, Age_Group_adult, etc.) are
        only included when their value is 1 (the student belongs to that
        category) — otherwise they contribute nothing and are skipped.
        """
        # Normalisation ranges for continuous features
        _RANGES: dict[str, tuple[float, float]] = {
            "Entrance_Exam_Score": (0,   100),
            "HS_GPA":              (60,  100),   # Philippine GPA scale
            "Financial_Stress":    (1,   10),
            "Gap_Years":           (0,   5),
            "Distance_KM":         (0,   100),
            "Age_At_Enrollment":   (15,  30),
            "Distance_KM":         (0,   100),
        }

        contributions: list[tuple[float, str, float]] = []
        # importance sum for normalising contribution percentages
        importance_total = sum(global_importances) or 1.0

        for feat, imp in zip(feature_names, global_importances):
            if imp <= 0:
                continue

            raw_val = raw_vals.get(feat, 0.0)

            # ── One-hot columns ───────────────────────────────────────────────
            # e.g. "Program_BSCS", "Age_Group_adult", "Distance_Bucket_far"
            is_onehot = False
            for cat_prefix in ("Program_", "Sex_code_", "Age_Group_",
                               "Distance_Bucket_"):
                if feat.startswith(cat_prefix):
                    is_onehot = True
                    break

            if is_onehot:
                # Only include when the student actually belongs to this category
                if raw_val < 0.5:
                    continue
                contribution = imp / importance_total
                contributions.append((contribution, feat, raw_val))
                continue

            # ── Continuous / binary features ──────────────────────────────────
            lo, hi = _RANGES.get(feat, (0.0, 1.0))
            span = (hi - lo) or 1.0
            norm = max(0.0, min(1.0, (raw_val - lo) / span))

            if feat in _LOW_IS_RISKY:
                # Low value = more risk → invert
                contribution = imp / importance_total * (1.0 - norm)
            elif feat in _HIGH_IS_RISKY:
                contribution = imp / importance_total * norm
            else:
                # Binary flags (Has_HS_Honors, Private_HS, etc.)
                # Only meaningful when value is 1; contribution = raw importance
                if raw_val < 0.5:
                    continue
                contribution = imp / importance_total

            if contribution > 0:
                contributions.append((contribution, feat, raw_val))

        # Sort by contribution descending, take top 6
        contributions.sort(key=lambda x: x[0], reverse=True)
        top6 = contributions[:6]

        # Format for display: (feature_name, human_label, formatted_value)
        result = []
        for contribution, feat, raw_val in top6:
            label = _feature_label(feat)
            value = _format_value(feat, raw_val)
            pct   = round(contribution * 100)
            result.append((feat, label, value, pct))

        return result

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
        success:     bool               = False,
        predictions: list               = None,
        summary:     "PredictionSummary" = None,
        errors:      list               = None,
        is_mock:     bool               = False,
    ):
        self.success     = success
        self.predictions = predictions or []
        self.summary     = summary or PredictionSummary()
        self.errors      = errors or []
        self.is_mock     = is_mock