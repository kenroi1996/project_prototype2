"""
Feature Engineering for EarlyAlert Student Risk Prediction
===========================================================

Input  : unified DataFrame produced by MergeEngine (UNIFIED_COLUMNS names)
Output : ML-ready DataFrame with pre-enrollment features + binary risk_label

KEY DESIGN PRINCIPLE — NO LEAKAGE
-----------------------------------
Only features available BEFORE a student's first semester are used for
training and prediction.  Grade-derived features (GPA_Tier, Has_College_Grade,
Year_Level) are computed for display purposes only and are NEVER fed to the
model.  Including them causes ~100 % training accuracy that collapses to
near-chance on real incoming students who have no grades yet.

Program_Risk_Index and Municipality_Risk_Index have been REMOVED from the
training feature set.  Both were computed from risk_label on the full dataset
before splitting, causing direct target leakage.  If you want to reintroduce
them in a future iteration, compute them inside each training fold only and
apply the learned mapping to held-out rows — never on the full dataset.

Two-phase usage
---------------
  Phase 1 — Training (historical data that has Final_Avg_GRD):
      df = run_full_feature_pipeline(df)
      → feed result to TrainingEngine

  Phase 2 — Prediction (incoming students, no grades):
      df = run_prediction_pipeline(df)
      → feed result to PredictionEngine

Pipeline position
-----------------
  [MergeEngine] → [normalize_columns] → [define_target*] → [engineer_features]
               → [drop_raw_columns]   → [deduplicate_on_id] → [drop_training_leakage]
               → [DataPipeline: encode / scale]
               → [TrainingEngine / PredictionEngine]

  * define_target only in Phase 1 (training).

DUPLICATE DETECTION LOGIC
--------------------------
Deduplication is performed on Student_ID BEFORE it is dropped, so only true
duplicate student records (same student appearing more than once) are removed.
Doing this AFTER Student_ID is gone would incorrectly collapse distinct students
who happen to share identical engineered feature values — causing ~43% data loss
with low-cardinality bucketed features.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


# ── GPA scale ────────────────────────────────────────────────────────────────
GPA_AT_RISK_THRESHOLD  = 3.0
EXAM_AT_RISK_THRESHOLD = 60

# ── Strand–Program alignment tables ──────────────────────────────────────────
_STEM_PROGRAMS = {
    "BSCS", "BS CS", "BSIT", "BS IT", "BSCE", "BS CE",
    "BSECE", "BSME", "BS ME", "BSEE", "BS EE", "BSIE",
    "BSPH", "BSMATH", "BS MATH", "BSTAT", "BSAGRIBUS",
}
_ABM_PROGRAMS = {
    "BSA", "BSBA", "BS BA", "BSMA", "BSENTREP",
    "BSHRM", "BS HRM", "BSTM", "BS TM",
}
_HUMSS_PROGRAMS = {
    "AB", "BSED", "BS ED", "BEED", "BS EED",
    "BSCRIM", "BS CRIM", "BSPOLSCI", "BS POLSCI",
    "ABCOMM", "AB COMM", "BSSW", "BS SW",
}
_STRAND_MAP: dict[str, set[str]] = {
    "STEM":            _STEM_PROGRAMS,
    "ABM":             _ABM_PROGRAMS,
    "HUMSS":           _HUMSS_PROGRAMS,
    "TVL":             set(),
    "GAS":             set(),
    # ICT is a STEM-adjacent SHS track in the Philippines — maps to STEM programs
    "ICT":             _STEM_PROGRAMS,
    "SPORTS":          set(),
    "ARTS AND DESIGN": set(),
}

_INCOME_ORDER: dict[str, int] = {
    # Standard long-form keys
    "below 10,000": 1,   "below 10000": 1,
    "10,000-20,000": 2,  "10000-20000": 2,
    "20,001-30,000": 3,  "20001-30000": 3,
    "30,001-40,000": 4,  "30001-40000": 4,
    "40,001-50,000": 5,  "40001-50000": 5,
    "above 50,000": 6,   "above 50000": 6,
    "50,001 and above": 6,
    # Guidance Office short-form keys ("Below 10k", "10k-20k", "20k-50k", "50k+")
    "below 10k": 1,
    "10k-20k":   2,
    "20k-30k":   3,
    "30k-40k":   4,
    "40k-50k":   5,
    "20k-50k":   4,   # mid-point of the broader bracket
    "50k+":      6,
    "above 50k": 6,
}

_NON_COLLEGE_LABELS = {
    # No formal / basic education
    "no formal education", "elementary", "grade school",
    # Secondary
    "high school", "junior high school", "senior high school",
    # Vocational / alternative
    "vocational", "als", "alternative learning system",
    "did not finish high school", "none",
    # Note: "college", "graduate", "bachelor" are NOT here —
    # parents with college degrees do NOT make the student first-gen.
}

_GEO_CACHE: dict[str, tuple[float, float]] = {}

CAMPUS_LAT = 11.2527   # Daanbantayan, Cebu — verified
CAMPUS_LON = 124.0165

_DISTANCE_BUCKETS = {
    "on_campus": (0,   2),
    "very_near": (2,   5),
    "near":      (5,  15),
    "moderate":  (15, 30),
    "far":       (30, 50),
    "very_far":  (50, float("inf")),
}


def load_geo_cache(rows: list[dict]) -> None:
    global _GEO_CACHE

    def _norm_muni(name: str) -> str:
        """Normalise a municipality name: lowercase, strip, remove ' city' suffix."""
        return name.strip().lower().replace(" city", "").strip()

    _GEO_CACHE = {
        _norm_muni(str(r["municipality"])): (
            float(r["latitude"]),
            float(r["longitude"]),
        )
        for r in rows
        if r.get("latitude") is not None and r.get("longitude") is not None
    }
    print(f"[FeatureEngineering] GeoCache loaded: {len(_GEO_CACHE)} municipalities")


def _ensure_geo_cache() -> None:
    """Load geo cache from public.geo_cache via psycopg2 (DataStore.db_conn)."""
    if _GEO_CACHE:
        return
    try:
        from services.data_store import DataStore
        conn = DataStore.get().db_conn
        if conn is None:
            print("[FeatureEngineering] GeoCache: no DB connection available.")
            return
        with conn.cursor() as cur:
            cur.execute(
                "SELECT municipality, latitude, longitude "
                "FROM public.geo_cache ORDER BY municipality"
            )
            rows = [
                {
                    "municipality": row[0],
                    "latitude":     float(row[1]),
                    "longitude":    float(row[2]),
                }
                for row in cur.fetchall()
            ]
        if rows:
            load_geo_cache(rows)
        else:
            print(
                "[FeatureEngineering] WARNING: geo_cache table is empty. "
                "Run geo_cache_setup.sql to seed municipality coordinates."
            )
    except Exception as exc:
        print(
            f"[FeatureEngineering] GeoCache DB load failed ({exc}); "
            "distance features will default to 'unknown'."
        )


# ── Columns to drop after engineering ────────────────────────────────────────
COLS_TO_DROP: list[str] = [
    "Student_ID",
    "College",
    "SecCode",
    "Civil_Status",
    "Religion",
    "HS_School",
    "Final_Avg_GRD",
    "Year",
    "SHS_Strand",
    "Family_Income",
    "Parent_Highest_Education",
    "Scholarship_Applicant",
    "Scholarship_Type",
    "HS_Type",
    "Graduation_Honors",
    "Year_Enrolled",
    "Year_Graduated",
    "Birthdate",
    "Home_Address",
    "Municipality",
]

TRAINING_FEATURES = [
    "Entrance_Exam_Score",
    "HS_GPA",
    # Entrance_Exam_Tier and HS_Performance_Tier removed: both are discretized
    # versions of the continuous scores above.  Keeping only the raw scores
    # avoids redundancy and lets the model find its own decision boundaries.
    "Strand_Program_Match",
    "Financial_Stress",
    "First_Gen_Student",
    "Has_Scholarship",
    "Gap_Years",
    "Private_HS",
    "Has_HS_Honors",
    "Age_At_Enrollment",
    "Age_Group",
    "Distance_KM",
    "Distance_Bucket",
    "Program",
    # Sex_code intentionally omitted: sex is not a causal risk factor and
    # including it causes two students with identical academic and socioeconomic
    # profiles to receive different risk scores based solely on sex — a fairness
    # violation. Any correlation in historical data is a proxy for program
    # composition or other confounders, not a direct predictor of failure.
    # Program_Risk_Index and Municipality_Risk_Index intentionally omitted.
    # Both were derived from risk_label on the full dataset before any
    # train/test split, which is direct target leakage.
    # To reintroduce them safely: compute inside each training fold only,
    # then apply the learned mapping to held-out / prediction rows.
    # sklearn's TargetEncoder inside a Pipeline is the cleanest way to do this.
]

DISPLAY_ONLY_FEATURES: list[str] = [
    "GPA_Tier",
    "Has_College_Grade",
    "Year_Level",
    "Entrance_Exam_Tier",
    "HS_Performance_Tier",
]

FINAL_FEATURES = TRAINING_FEATURES

# ── Feature schema version ────────────────────────────────────────────────────
# Short SHA-1 fingerprint of the sorted TRAINING_FEATURES list.
# Stored in every model artifact by ModelRegistry.save_model() and checked on
# load — a mismatch means the artifact was trained on a different feature set
# and must be rejected rather than served silently.
# Updates automatically whenever TRAINING_FEATURES changes; no manual bump needed.
import hashlib as _hashlib, json as _json
FEATURE_SCHEMA_VERSION: str = _hashlib.sha1(
    _json.dumps(sorted(TRAINING_FEATURES)).encode()
).hexdigest()[:8]
del _hashlib, _json

TARGET_COLUMN = "risk_label"

PREDICTION_ID_COLUMN = "Student_ID"

PREDICTION_FEATURES: list[str] = [PREDICTION_ID_COLUMN] + TRAINING_FEATURES

_PREDICTION_FEATURE_DEFAULTS: dict[str, Any] = {
    "Strand_Program_Match":    0.5,
    "Financial_Stress":        3,
    "First_Gen_Student":       0,
    "Has_Scholarship":         0,
    "Gap_Years":               0,
    "Private_HS":              0,
    "Has_HS_Honors":           0,
    "Age_At_Enrollment":       18,
    "Age_Group":               "traditional",
    "Distance_Bucket":         "unknown",
    "Program":                 "UNKNOWN",
    # Sex_code removed — not a training feature
}


# =============================================================================
# PUBLIC API
# =============================================================================

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    column_map = {
        "student_id":               "Student_ID",
        "id_no":                    "Student_ID",
        "program":                  "Program",
        "college":                  "College",
        "seccode":                  "SecCode",
        "year":                     "Year",
        "sex_code":                 "Sex_code",
        "home_address":             "Home_Address",
        "civil_status":             "Civil_Status",
        "entrance_exam_score":      "Entrance_Exam_Score",
        "family_income":            "Family_Income",
        "family_income_bracket":    "Family_Income",
        "parent_highest_education": "Parent_Highest_Education",
        "hs_gpa":                   "HS_GPA",
        "year_graduated":           "Year_Graduated",
        "shs_strand":               "SHS_Strand",
        "hs_type":                  "HS_Type",
        "graduation_honors":        "Graduation_Honors",
        "hs_school":                "HS_School",
        "hs_schoolname":            "HS_School",
        "year_enrolled":            "Year_Enrolled",
        "scholarship_applicant":    "Scholarship_Applicant",
        "scholarship_type":         "Scholarship_Type",
        "birthdate":                "Birthdate",
        "final_avg_grd":            "Final_Avg_GRD",
        "religion":                 "Religion",
        "municipality":             "Municipality",
    }
    # Case-insensitive match so HOME_ADDRESS, home_address, Home_Address
    # all map to the canonical "Home_Address" form.
    actual_lower = {col.lower(): col for col in df.columns}
    rename_dict = {
        actual_lower[key]: canonical
        for key, canonical in column_map.items()
        if key in actual_lower and actual_lower[key] != canonical
    }
    if rename_dict:
        df = df.rename(columns=rename_dict)
    return df


def define_target(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    if TARGET_COLUMN in df.columns:
        return df

    grd = pd.Series(np.nan, index=df.index)
    if "Final_Avg_GRD" in df.columns:
        grd = pd.to_numeric(df["Final_Avg_GRD"], errors="coerce")

    exam = pd.Series(np.nan, index=df.index)
    if "Entrance_Exam_Score" in df.columns:
        exam = pd.to_numeric(df["Entrance_Exam_Score"], errors="coerce")

    def _label(g: float, e: float) -> str:
        if pd.notna(g):
            return "at_risk" if g >= GPA_AT_RISK_THRESHOLD else "not_at_risk"
        if pd.notna(e):
            return "at_risk" if e < EXAM_AT_RISK_THRESHOLD else "not_at_risk"
        return "at_risk"

    df[TARGET_COLUMN] = [_label(g, e) for g, e in zip(grd, exam)]

    counts     = df[TARGET_COLUMN].value_counts().to_dict()
    n_at_risk  = counts.get("at_risk", 0)
    n_not_risk = counts.get("not_at_risk", 0)
    n_total    = n_at_risk + n_not_risk

    print(
        f"[FeatureEngineering] Target distribution: "
        f"at_risk={n_at_risk}, not_at_risk={n_not_risk}"
    )

    # ── Early detection: degenerate label distribution ────────────────────────
    has_grades   = "Final_Avg_GRD" in df.columns and grd.notna().sum() > 0
    grade_source = "Final_Avg_GRD" if has_grades else "Entrance_Exam_Score"

    if n_at_risk == 0:
        raise ValueError(
            f"Zero at-risk students detected after labeling "
            f"(source: {grade_source}).\n\n"
            + (
                f"Final_Avg_GRD is absent from this dataset — the fallback "
                f"rule (Entrance_Exam_Score < {EXAM_AT_RISK_THRESHOLD}) labeled "
                f"everyone 'not_at_risk' because all exam scores are above {EXAM_AT_RISK_THRESHOLD}.\n\n"
                f"This is the INCOMING STUDENTS dataset (no grades yet). "
                f"It is correct for PREDICTION but not for TRAINING.\n\n"
                f"To train the model, use the HISTORICAL MIS export that includes "
                f"Final_Avg_GRD for past students:\n"
                f"  SELECT id_no, program, college, seccode, year, sex_code, "
                f"home_address, civil_status, religion, final_avg_grd "
                f"FROM public.mis_students WHERE final_avg_grd IS NOT NULL;"
                if not has_grades
                else
                f"All {n_total} students have Final_Avg_GRD < {GPA_AT_RISK_THRESHOLD} "
                f"(all passing). Check that the correct historical dataset is uploaded."
            )
        )

    if n_at_risk / max(n_total, 1) < 0.005:
        print(
            f"[FeatureEngineering] WARNING: Only {n_at_risk} at-risk students "
            f"({n_at_risk/max(n_total,1)*100:.1f}%) — model may not learn the "
            f"at-risk pattern reliably. More historical data with failing grades "
            f"will improve prediction quality."
        )

    return df


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    if _is_already_engineered(df):
        print("[FeatureEngineering] WARNING: engineer_features() called on already-"
              "engineered data — skipping to prevent corruption.")
        return df

    # ── 1. Entrance Exam Tier (display-only — not in TRAINING_FEATURES) ───────
    # Entrance_Exam_Score (continuous) is used by the model directly.
    # The tier bucket is kept here for UI display and interpretability only.
    exam = pd.to_numeric(
        df.get("Entrance_Exam_Score", pd.Series(dtype=float, index=df.index)),
        errors="coerce",
    )
    df["Entrance_Exam_Tier"] = exam.apply(_exam_tier)

    # ── 2. HS Performance Tier (display-only — not in TRAINING_FEATURES) ─────
    # HS_GPA (continuous) is used by the model directly.
    # The tier bucket is kept here for UI display and interpretability only.
    hs_gpa = pd.to_numeric(
        df.get("HS_GPA", pd.Series(dtype=float, index=df.index)),
        errors="coerce",
    )
    df["HS_Performance_Tier"] = hs_gpa.apply(_hs_tier)

    # ── 3. Strand–Program Alignment ───────────────────────────────────────────
    df["Strand_Program_Match"] = df.apply(
        lambda r: _strand_match(r.get("SHS_Strand", ""), r.get("Program", "")),
        axis=1,
    )

    # ── 5 & 6. First-Gen + Has Scholarship ───────────────────────────────────
    # Computed before Financial_Stress (step 4) because its formula references
    # both of these columns.
    parent_edu = df.get(
        "Parent_Highest_Education", pd.Series(dtype=str, index=df.index)
    ).fillna("")
    df["First_Gen_Student"] = (
        parent_edu.str.lower().str.strip().isin(_NON_COLLEGE_LABELS)
    ).astype(int)

    scholar = df.get(
        "Scholarship_Applicant", pd.Series(dtype=str, index=df.index)
    ).fillna("")
    df["Has_Scholarship"] = scholar.apply(_is_truthy).astype(int)

    # ── 4. Financial Stress ───────────────────────────────────────────────────
    # fillna(0) on both dependency columns guards against NaN propagation into
    # the arithmetic, which would produce NaN stress values and crash .astype(int).
    income_raw   = df.get("Family_Income", pd.Series(dtype=str, index=df.index)).fillna("")
    income_level = income_raw.str.lower().str.strip().map(_INCOME_ORDER).fillna(3)

    stress = (
        7 - income_level
        + (1 - df["Has_Scholarship"].fillna(0))
        + df["First_Gen_Student"].fillna(0)
    )
    df["Financial_Stress"] = stress.fillna(3).clip(lower=1, upper=10).astype(int)

    # ── 7. Gap Years ──────────────────────────────────────────────────────────
    # FIX: Year_Enrolled stores plain 4-digit integers (e.g. 2023), not date
    # strings. pd.to_datetime("2023") silently coerces many values to NaT,
    # producing NaN years that crash the subsequent .astype(int).
    # pd.to_numeric correctly parses year integers from all portal formats.
    yr_grad = pd.to_numeric(
        df.get("Year_Graduated", pd.Series(dtype=float, index=df.index)),
        errors="coerce",
    )
    yr_enrl = pd.to_numeric(
        df.get("Year_Enrolled", pd.Series(dtype=float, index=df.index)),
        errors="coerce",
    )
    df["Gap_Years"] = (
        (yr_enrl - yr_grad - 1).clip(lower=0).fillna(0).astype(int)
    )

    # ── 8. Private HS ─────────────────────────────────────────────────────────
    hs_type = df.get("HS_Type", pd.Series(dtype=str, index=df.index)).fillna("")
    df["Private_HS"] = (
        hs_type.str.lower().str.contains("private", na=False)
    ).astype(int)

    # ── 9. Has HS Honors ──────────────────────────────────────────────────────
    honors = df.get("Graduation_Honors", pd.Series(dtype=str, index=df.index)).fillna("")
    df["Has_HS_Honors"] = honors.apply(
        lambda v: 0 if str(v).strip().lower() in ("", "none", "nan", "—", "n/a") else 1
    )

    # ── 10. Age at Enrollment ─────────────────────────────────────────────────
    birthdate = pd.to_datetime(
        df.get("Birthdate", pd.Series(dtype=str, index=df.index)),
        errors="coerce",
    )
    age_raw    = (yr_enrl - birthdate.dt.year).clip(lower=10, upper=60)
    age_median = age_raw.median()
    age_filled = age_raw.fillna(age_median if pd.notna(age_median) else 18)

    # FIX: pd.cut is called on the already-filled series so there are no NaN
    # inputs that would produce NaN categories. Without this, students with
    # missing Birthdate got NaN Age_Group, breaking encode_categorical() later.
    df["Age_Group"] = pd.cut(
        age_filled,
        bins=[0, 20, 24, 100],
        labels=["traditional", "adult", "older"],
    ).astype(object)   # cast Categorical → object to prevent imputer issues

    df["Age_At_Enrollment"] = age_filled.astype(float)

    # ── 11. Distance from campus ──────────────────────────────────────────────
    municipality = pd.Series("", index=df.index)

    def _normalise_muni(raw: str) -> str:
        """
        Normalise a raw municipality/address string to match geo cache keys.
        Handles: 'BOGO CITY', 'Brgy. Punta, Daanbantayan, Cebu',
                 'City of Lapu-Lapu', 'DAAN BANTAYAN', typos, all-caps.
        """
        if not raw or str(raw).strip().lower() in ("", "nan", "none", "n/a", "-"):
            return ""
        s = str(raw).strip().lower()
        # Remove common noise tokens
        for noise in ("city of ", "municipality of ", "brgy.", "barangay",
                      "purok ", "sitio ", "poblacion", ", cebu", ", leyte",
                      ", bohol", ", southern leyte"):
            s = s.replace(noise, " ")
        # Collapse whitespace
        import re as _re
        s = _re.sub(r"\s+", " ", s).strip()
        # Strip trailing " city" so "bogo city" → "bogo"
        s = _re.sub(r"\s+city$", "", s).strip()
        return s

    def _match_muni(normalised: str) -> str:
        """
        Match a normalised address token to a geo cache key.
        Uses exact match first, then substring containment both ways,
        so 'daanbantayan' matches key 'daanbantayan' and
        'medellin cebu' matches key 'medellin'.
        """
        if not normalised or not _GEO_CACHE:
            return ""
        # Exact match
        if normalised in _GEO_CACHE:
            return normalised
        # Geo key contained in the address string
        for key in _GEO_CACHE:
            if key in normalised:
                return key
        # Address string contained in geo key (handles abbreviations)
        for key in _GEO_CACHE:
            if normalised in key and len(normalised) >= 4:
                return key
        return ""

    if "Municipality" in df.columns:
        municipality = (
            df["Municipality"].fillna("").astype(str)
            .apply(_normalise_muni)
            .apply(_match_muni)
        )
    elif "Home_Address" in df.columns:
        municipality = (
            df["Home_Address"].fillna("").astype(str)
            .apply(_normalise_muni)
            .apply(_match_muni)
        )
    else:
        municipality = pd.Series("", index=df.index)

    df["Distance_KM"]     = municipality.apply(_calc_distance)
    df["Distance_Bucket"] = df["Distance_KM"].apply(
        lambda d: "unknown" if d < 0 else _distance_bucket(d)
    )

    # ── DISPLAY-ONLY: grade-derived features ─────────────────────────────────
    grd = pd.to_numeric(
        df.get("Final_Avg_GRD", pd.Series(dtype=float, index=df.index)),
        errors="coerce",
    )
    df["GPA_Tier"]          = grd.apply(_gpa_tier)
    df["Has_College_Grade"] = grd.notna().astype(int)
    df["Year_Level"]        = (
        pd.to_numeric(
            df.get("Year", pd.Series(dtype=float, index=df.index)),
            errors="coerce",
        )
        .fillna(1).clip(lower=1).astype(int)
    )

    # ── Final safety: cast any residual Categorical columns to object ─────────
    for col in df.select_dtypes(include="category").columns:
        df[col] = df[col].astype(object)

    print(
        f"[FeatureEngineering] Features engineered. "
        f"DataFrame shape: {df.shape}"
    )
    print("DEBUG: GeoCache Size =", len(_GEO_CACHE))
    print("DEBUG: Unique Extracted Municipalities =", municipality.nunique())
    print("DEBUG: Null Birthdates Count =", birthdate.isna().sum())
    return df


def drop_raw_columns(df: pd.DataFrame) -> pd.DataFrame:
    existing = [c for c in COLS_TO_DROP if c in df.columns]
    df = df.drop(columns=existing)
    print(
        f"[FeatureEngineering] Dropped {len(existing)} raw columns. "
        f"Remaining: {list(df.columns)}"
    )
    return df


def drop_training_leakage(df: pd.DataFrame) -> pd.DataFrame:
    leakage_cols = DISPLAY_ONLY_FEATURES + ["Scholarship_Type"]
    existing = [c for c in leakage_cols if c in df.columns]
    if existing:
        df = df.drop(columns=existing)
        print(f"[FeatureEngineering] Removed leakage/display cols: {existing}")
    return df


def deduplicate_on_student_id(df: pd.DataFrame) -> pd.DataFrame:
    before = len(df)

    if "Student_ID" in df.columns:
        df = (
            df.sort_index()
              .drop_duplicates(subset=["Student_ID"], keep="last")
              .reset_index(drop=True)
        )
        id_col_used = "Student_ID"
    else:
        print(
            "[FeatureEngineering] WARNING: Student_ID not found — "
            "falling back to full-row deduplication. "
            "This may incorrectly collapse distinct students with "
            "identical engineered feature values."
        )
        df = df.drop_duplicates().reset_index(drop=True)
        id_col_used = "all columns"

    removed = before - len(df)
    if removed:
        print(
            f"[FeatureEngineering] Removed {removed} duplicate rows "
            f"({before} → {len(df)}) — keyed on {id_col_used}"
        )
    return df


def run_full_feature_pipeline(df: pd.DataFrame) -> pd.DataFrame:
    print("CRITICAL CHECK - Actual Data Columns:", df.columns.tolist())
    _ensure_geo_cache()

    if _is_already_engineered(df):
        print(
            "[FeatureEngineering] WARNING: run_full_feature_pipeline() received "
            "already-engineered data. Skipping engineering steps. "
            "Pass the RAW unified dataset, not the pre-processed one."
        )
        df = deduplicate_on_student_id(df)
        df = drop_training_leakage(df)
        return df

    df = normalize_columns(df)
    df = define_target(df)
    # NOTE: _build_program_risk_map and _build_municipality_risk_map calls
    # removed here. They computed target-derived statistics on the full dataset
    # before splitting, leaking the label into training features.
    df = engineer_features(df)

    df = deduplicate_on_student_id(df)
    df = drop_raw_columns(df)
    df = drop_training_leakage(df)

    if TARGET_COLUMN in df.columns:
        counts      = df[TARGET_COLUMN].value_counts()
        total       = len(df)
        print("[FeatureEngineering] Final class distribution:")
        for label, count in counts.items():
            print(f"  {label}: {count} ({count / total * 100:.1f} %)")
        minority_pct = counts.min() / total * 100
        if minority_pct < 15:
            print(
                f"[FeatureEngineering] WARNING: minority class = {minority_pct:.1f} %. "
                "Use class_weight='balanced' in your sklearn model."
            )

    return df


def select_prediction_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    if PREDICTION_ID_COLUMN not in df.columns:
        df[PREDICTION_ID_COLUMN] = [f"STU-{i + 1:05d}" for i in range(len(df))]

    for col in TRAINING_FEATURES:
        if col not in df.columns:
            df[col] = _PREDICTION_FEATURE_DEFAULTS.get(col, 0)

    df = df[PREDICTION_FEATURES]

    print(
        f"[FeatureEngineering] Prediction feature set enforced: "
        f"{len(df.columns)} columns -> {list(df.columns)}"
    )
    return df


def run_prediction_pipeline(df: pd.DataFrame) -> pd.DataFrame:
    print("CRITICAL CHECK - Actual Data Columns:", df.columns.tolist())
    _ensure_geo_cache()

    df = normalize_columns(df)

    if _is_already_engineered(df):
        print(
            "[FeatureEngineering] run_prediction_pipeline() received "
            "already-engineered data — skipping engineering steps."
        )
    else:
        df = engineer_features(df)

    return select_prediction_features(df)


# =============================================================================
# PRIVATE HELPERS
# =============================================================================

def _is_already_engineered(df: pd.DataFrame) -> bool:
    engineered_markers = {
        "Strand_Program_Match",
        "First_Gen_Student", "Has_Scholarship", "Financial_Stress",
        "Gap_Years", "Private_HS", "Has_HS_Honors", "Age_At_Enrollment",
        "Distance_Bucket",
    }
    return bool(engineered_markers & set(df.columns))


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import radians, sin, cos, sqrt, atan2
    R    = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a    = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    )
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def _calc_distance(muni: str) -> float:
    if not muni or muni not in _GEO_CACHE:
        return -1.0
    lat, lon = _GEO_CACHE[muni]
    return _haversine(lat, lon, CAMPUS_LAT, CAMPUS_LON)


def _distance_bucket(km: float) -> str:
    for label, (lo, hi) in _DISTANCE_BUCKETS.items():
        if lo <= km < hi:
            return label
    return "unknown"


def _gpa_tier(v: Any) -> int:
    v = pd.to_numeric(v, errors="coerce")
    if pd.isna(v): return -1
    if v >= 3.0:   return 3
    if v >= 2.5:   return 2
    if v >= 1.75:  return 1
    return 0


def _exam_tier(v: Any) -> int:
    v = pd.to_numeric(v, errors="coerce")
    if pd.isna(v): return 1
    if v >= 80:    return 0
    if v >= 65:    return 1
    return 2


def _hs_tier(v: Any) -> int:
    v = pd.to_numeric(v, errors="coerce")
    if pd.isna(v): return 1
    if v >= 90:    return 0
    if v >= 80:    return 1
    return 2


def _strand_match(strand, program) -> int:
    s = str(strand).upper().strip()
    p = str(program).upper().strip()

    aligned = _STRAND_MAP.get(s)
    if aligned is None:
        return -1
    if p in aligned:
        return 2
    if s in {"TVL", "GAS"}:
        return 1
    return 0


def _is_truthy(v: Any) -> bool:
    return str(v).strip().lower() in (
        "true", "1", "yes", "y", "with", "approved", "scholar", "t"
    )