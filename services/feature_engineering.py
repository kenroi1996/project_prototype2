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
               → [drop_raw_columns]   → [drop_duplicates] → [drop_training_leakage]
               → [DataPipeline: encode / scale]
               → [TrainingEngine / PredictionEngine]

  * define_target only in Phase 1 (training).

Unified column names this module reads
---------------------------------------
Student_ID, Program, College, SecCode, Year, Sex_code, Home_Address,
Civil_Status, Entrance_Exam_Score, Family_Income, Parent_Highest_Education,
HS_GPA, Year_Graduated, SHS_Strand, HS_Type, Graduation_Honors, HS_School,
Year_Enrolled, Scholarship_Applicant, Scholarship_Type, Birthdate,
Final_Avg_GRD, Religion, Municipality (optional)
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


# ── GPA scale ────────────────────────────────────────────────────────────────
# Philippine university grading: 1.0 = Excellent, 5.0 = Failed.
# Passing threshold = 3.0 (equivalent to 75 %).
# GPA >= 3.0 means the student is at academic risk.

# ── Target variable thresholds (binary) ──────────────────────────────────────
GPA_AT_RISK_THRESHOLD  = 3.0   # Final_Avg_GRD >= 3.0  → at_risk
EXAM_AT_RISK_THRESHOLD = 60    # Entrance_Exam_Score < 60 → at_risk (fallback)

# ── Strand–Program alignment tables ──────────────────────────────────────────
_STEM_PROGRAMS = {
    "BSCS", "BS CS", "BSIT", "BS IT", "BSCE", "BS CE",
    "BSEcE", "BSME", "BS ME", "BSEE", "BS EE", "BSIE",
    "BSPH", "BSMATH", "BS MATH", "BSTAT", "BSAGRIBUS",
}
_ABM_PROGRAMS = {
    "BSA", "BSBA", "BS BA", "BSMA", "BSEntrep", "BSENTREP",
    "BSHRM", "BS HRM", "BSTM", "BS TM",
}
_HUMSS_PROGRAMS = {
    "AB", "BSED", "BS ED", "BEED", "BS EED",
    "BSCRIM", "BS CRIM", "BSPOLSCI", "BS POLSCI",
    "ABCOMM", "AB COMM", "BSSW", "BS SW",
}
_STRAND_MAP: dict[str, set[str]] = {
    "STEM":           _STEM_PROGRAMS,
    "ABM":            _ABM_PROGRAMS,
    "HUMSS":          _HUMSS_PROGRAMS,
    "TVL":            set(),   # vocational — always mismatched for 4-yr programs
    "GAS":            set(),   # general academic — neutral
    "SPORTS":         set(),
    "ARTS AND DESIGN": set(),
}

# ── Family income → financial stress (1 = lowest income / highest stress) ────
_INCOME_ORDER: dict[str, int] = {
    "below 10,000": 1,   "below 10000": 1,
    "10,000-20,000": 2,  "10000-20000": 2,
    "20,001-30,000": 3,  "20001-30000": 3,
    "30,001-40,000": 4,  "30001-40000": 4,
    "40,001-50,000": 5,  "40001-50000": 5,
    "above 50,000": 6,   "above 50000": 6,
    "50,001 and above": 6,
}

# ── Non-college parent education labels ───────────────────────────────────────
_NON_COLLEGE_LABELS = {
    "no formal education", "elementary", "grade school",
    "high school", "junior high school", "senior high school",
    "vocational", "als", "alternative learning system",
    "did not finish high school", "none",
}

# ── GeoCache: municipality → (lat, lon) ──────────────────────────────────────
_GEO_CACHE: dict[str, tuple[float, float]] = {}

CAMPUS_LAT = 11.0442   # Bogo City — adjust to your actual campus
CAMPUS_LON = 124.0130

_DISTANCE_BUCKETS = {
    "on_campus": (0,   2),
    "very_near": (2,   5),
    "near":      (5,  15),
    "moderate":  (15, 30),
    "far":       (30, 50),
    "very_far":  (50, float("inf")),
}


def load_geo_cache(rows: list[dict]) -> None:
    """
    Populate _GEO_CACHE from GeoCache table rows.
    Call this before engineer_features() if you have geocoded data.

    Parameters
    ----------
    rows : list of dicts with keys 'municipality', 'latitude', 'longitude'
    """
    global _GEO_CACHE
    _GEO_CACHE = {
        str(r["municipality"]).strip().lower(): (
            float(r["latitude"]),
            float(r["longitude"]),
        )
        for r in rows
        if r.get("latitude") is not None and r.get("longitude") is not None
    }
    print(f"[FeatureEngineering] GeoCache loaded: {len(_GEO_CACHE)} municipalities")


# ── Columns to drop after engineering (raw sources replaced by features) ──────
COLS_TO_DROP: list[str] = [
    # Identifiers
    "Student_ID",
    # Redundant / zero-signal
    "College",          # fully determined by Program
    "SecCode",          # admin artifact, changes each semester
    "Civil_Status",     # near-uniform for college-age students
    "Religion",         # no academic relevance
    "HS_School",        # high cardinality, encodes geography not quality
    # Raw sources replaced by engineered features
    "Final_Avg_GRD",          # → target label + GPA_Tier (display only)
    "Year",                   # → Year_Level (display only)
    "Entrance_Exam_Score",    # → Entrance_Exam_Tier
    "HS_GPA",                 # → HS_Performance_Tier
    "SHS_Strand",             # → Strand_Program_Match
    "Family_Income",          # → Financial_Stress
    "Parent_Highest_Education", # → First_Gen_Student
    "Scholarship_Applicant",  # → Has_Scholarship
    "Scholarship_Type",       # 55 %+ missing — unreliable
    "HS_Type",                # → Private_HS
    "Graduation_Honors",      # → Has_HS_Honors
    "Year_Enrolled",          # → Gap_Years, Age_At_Enrollment
    "Year_Graduated",         # → Gap_Years
    "Birthdate",              # → Age_At_Enrollment
    "Home_Address",           # → Distance_KM / Distance_Bucket
    "Municipality",           # → Distance_KM / Distance_Bucket
]

# ── Safe pre-enrollment features — the ONLY columns fed to the model ──────────
TRAINING_FEATURES: list[str] = [
    "Entrance_Exam_Tier",    # 0=strong · 1=average · 2=weak
    "HS_Performance_Tier",   # 0=high   · 1=average · 2=low
    "Strand_Program_Match",  # 0=mismatch · 0.5=unknown · 1=aligned
    "Financial_Stress",      # 1–6 (higher = more financial pressure)
    "First_Gen_Student",     # 1 if neither parent reached college
    "Has_Scholarship",       # 1 if scholarship applicant / recipient
    "Gap_Years",             # years between HS graduation and enrollment
    "Private_HS",            # 1 if attended private high school
    "Has_HS_Honors",         # 1 if graduated with any honours
    "Age_At_Enrollment",     # age in years at time of enrollment
    "Distance_Bucket",       # on_campus/very_near/near/moderate/far/very_far/unknown
    "Program",               # label-encoded by DataPipeline
    "Sex_code",              # label-encoded by DataPipeline
]

# ── Display-only features — shown in UI but NOT fed to the model ──────────────
# Reason: derived from Final_Avg_GRD which does not exist for incoming students.
DISPLAY_ONLY_FEATURES: list[str] = [
    "GPA_Tier",          # −1=no grade · 0=excellent · 1=passing · 2=borderline · 3=at-risk
    "Has_College_Grade", # 1 if Final_Avg_GRD was present
    "Year_Level",        # integer year of study
    "Distance_KM",       # raw km value (Distance_Bucket used for training)
]

# Legacy alias — kept so any existing code referencing FINAL_FEATURES still works
FINAL_FEATURES = TRAINING_FEATURES

TARGET_COLUMN = "risk_label"


# =============================================================================
# PUBLIC API
# =============================================================================

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Rename DB-style lowercase column names to PascalCase expected by the pipeline.
    Safe to call even when columns are already correctly named.
    """
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
    rename_dict = {old: new for old, new in column_map.items() if old in df.columns}
    if rename_dict:
        df = df.rename(columns=rename_dict)
    return df


def define_target(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add binary ``risk_label`` column to *df*.  Call only during TRAINING (Phase 1).

    Logic (Philippine GPA scale — 1.0 = best, 5.0 = fail):
        at_risk     — GPA >= 3.0   (at or past the failing threshold)
        not_at_risk — GPA <  3.0   (passing)

    Fallback when no GPA (student has no Final_Avg_GRD):
        at_risk     — entrance exam score < 60
        not_at_risk — entrance exam score >= 60

    Default when neither is available: 'at_risk' (conservative — flag for
    review rather than assume safety).
    """
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
        return "at_risk"   # conservative default

    df[TARGET_COLUMN] = [_label(g, e) for g, e in zip(grd, exam)]

    counts = df[TARGET_COLUMN].value_counts().to_dict()
    print(
        f"[FeatureEngineering] Target distribution: "
        f"at_risk={counts.get('at_risk', 0)}, "
        f"not_at_risk={counts.get('not_at_risk', 0)}"
    )
    return df


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Derive all engineered features from raw UNIFIED_COLUMNS.

    Produces both TRAINING_FEATURES and DISPLAY_ONLY_FEATURES.
    Does NOT drop raw source columns — call drop_raw_columns() separately
    so the caller can still inspect the full DataFrame if needed.
    Safe to call for both Phase 1 (training) and Phase 2 (prediction) data.
    """
    df = df.copy()

    # Guard: skip if already engineered (prevents double-run corruption)
    if _is_already_engineered(df):
        print("[FeatureEngineering] WARNING: engineer_features() called on already-"
              "engineered data — skipping to prevent corruption.")
        return df

    # ── 1. Entrance Exam Tier (pre-enrollment) ────────────────────────────────
    exam = pd.to_numeric(
        df.get("Entrance_Exam_Score", pd.Series(dtype=float, index=df.index)),
        errors="coerce",
    )
    df["Entrance_Exam_Tier"] = exam.apply(_exam_tier)

    # ── 2. HS Performance Tier (pre-enrollment) ───────────────────────────────
    hs_gpa = pd.to_numeric(
        df.get("HS_GPA", pd.Series(dtype=float, index=df.index)),
        errors="coerce",
    )
    df["HS_Performance_Tier"] = hs_gpa.apply(_hs_tier)

    # ── 3. Strand–Program Alignment (pre-enrollment) ──────────────────────────
    df["Strand_Program_Match"] = df.apply(
        lambda r: _strand_match(r.get("SHS_Strand", ""), r.get("Program", "")),
        axis=1,
    )

    # ── 4. Financial Stress (pre-enrollment) ──────────────────────────────────
    income_raw   = df.get("Family_Income", pd.Series(dtype=str, index=df.index)).fillna("")
    income_level = income_raw.str.lower().str.strip().map(_INCOME_ORDER).fillna(3)
    df["Financial_Stress"] = (7 - income_level).clip(lower=1, upper=6).astype(int)

    # ── 5. First-Generation Student (pre-enrollment) ──────────────────────────
    parent_edu = df.get(
        "Parent_Highest_Education", pd.Series(dtype=str, index=df.index)
    ).fillna("")
    df["First_Gen_Student"] = (
        parent_edu.str.lower().str.strip().isin(_NON_COLLEGE_LABELS)
    ).astype(int)

    # ── 6. Has Scholarship (pre-enrollment) ───────────────────────────────────
    scholar = df.get(
        "Scholarship_Applicant", pd.Series(dtype=str, index=df.index)
    ).fillna("")
    df["Has_Scholarship"] = scholar.apply(_is_truthy).astype(int)

    # ── 7. Gap Years (pre-enrollment) ─────────────────────────────────────────
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

    # ── 8. Private HS (pre-enrollment) ────────────────────────────────────────
    hs_type = df.get("HS_Type", pd.Series(dtype=str, index=df.index)).fillna("")
    df["Private_HS"] = (
        hs_type.str.lower().str.contains("private", na=False)
    ).astype(int)

    # ── 9. Has HS Honors (pre-enrollment) ─────────────────────────────────────
    honors = df.get("Graduation_Honors", pd.Series(dtype=str, index=df.index)).fillna("")
    df["Has_HS_Honors"] = honors.apply(
        lambda v: 0 if str(v).strip().lower() in ("", "none", "nan", "—", "n/a") else 1
    )

    # ── 10. Age at Enrollment (pre-enrollment) ────────────────────────────────
    birthdate = pd.to_datetime(
        df.get("Birthdate", pd.Series(dtype=str, index=df.index)),
        errors="coerce",
    )
    df["Age_At_Enrollment"] = (yr_enrl - birthdate.dt.year).clip(lower=10, upper=60)
    age_median = df["Age_At_Enrollment"].median()
    df["Age_At_Enrollment"] = (
        df["Age_At_Enrollment"]
        .fillna(age_median if pd.notna(age_median) else 18)
        .astype(float)
    )

    # ── 11. Distance from campus (pre-enrollment) ─────────────────────────────
    # Resolve municipality from dedicated column or extract from Home_Address.
    municipality = pd.Series("", index=df.index)

    if "Municipality" in df.columns:
        municipality = df["Municipality"].fillna("").str.strip().str.lower()
    elif "Home_Address" in df.columns:
        def _extract_muni(addr: str) -> str:
            if not addr or addr.strip().lower() in ("", "nan", "none", "n/a"):
                return ""
            parts = [p.strip().lower() for p in addr.split(",")]
            for part in reversed(parts):
                clean = part.replace(" city", "").strip()
                if clean in _GEO_CACHE:
                    return clean
            return ""
        municipality = df["Home_Address"].fillna("").astype(str).apply(_extract_muni)

    df["Distance_KM"] = municipality.apply(_calc_distance)
    df["Distance_Bucket"] = df["Distance_KM"].apply(
        lambda d: "unknown" if d < 0 else _distance_bucket(d)
    )

    # ── DISPLAY-ONLY: grade-derived features (NOT used in training) ───────────
    # These are computed so the UI can show them on student profile cards,
    # but drop_training_leakage() removes them before the model sees the data.
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
        .fillna(1)
        .clip(lower=1)
        .astype(int)
    )

    print(
        f"[FeatureEngineering] Features engineered. "
        f"DataFrame shape: {df.shape}"
    )
    return df


def drop_raw_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Remove source columns that have been replaced by engineered features.
    Only drops columns that actually exist to avoid KeyError.
    Safe to call multiple times — no-ops if raw columns are already gone.
    """
    existing = [c for c in COLS_TO_DROP if c in df.columns]
    df = df.drop(columns=existing)
    print(
        f"[FeatureEngineering] Dropped {len(existing)} raw columns. "
        f"Remaining: {list(df.columns)}"
    )
    return df


def drop_training_leakage(df: pd.DataFrame) -> pd.DataFrame:
    """
    Remove grade-derived and display-only columns before the model sees the data.
    Call this as the LAST step before TrainingEngine or PredictionEngine.

    Removes: GPA_Tier, Has_College_Grade, Year_Level (grade-derived),
             Distance_KM (redundant — Distance_Bucket used instead),
             Scholarship_Type (55 %+ missing in historical data).
    """
    leakage_cols = DISPLAY_ONLY_FEATURES + ["Scholarship_Type"]
    existing = [c for c in leakage_cols if c in df.columns]
    if existing:
        df = df.drop(columns=existing)
        print(f"[FeatureEngineering] Removed leakage/display cols: {existing}")
    return df


def run_full_feature_pipeline(df: pd.DataFrame) -> pd.DataFrame:
    """
    PHASE 1 — Complete training pipeline for historical data (has Final_Avg_GRD).

    Steps:
        normalize_columns → define_target → engineer_features → drop_raw_columns
        → drop_duplicates → drop_training_leakage → class-imbalance warning

    Returns a DataFrame whose columns are TRAINING_FEATURES + risk_label,
    ready for DataPipeline (encode / scale) then TrainingEngine.
    """
    # ── Guard: detect if pipeline has already run on this data ───────────────
    # This happens when the training page passes already-processed data.
    # Symptom: engineered columns present + raw source columns absent.
    # Effect without this guard: deduplication collapses rows to ~28,
    # metrics become undefined, model produces 0 % accuracy.
    if _is_already_engineered(df):
        print(
            "[FeatureEngineering] WARNING: run_full_feature_pipeline() received "
            "already-engineered data. Skipping engineering steps. "
            "Pass the RAW unified dataset, not the pre-processed one."
        )
        # Still drop leakage cols and deduplicate if not yet done
        df = df.drop_duplicates()
        df = drop_training_leakage(df)
        return df

    df = normalize_columns(df)
    df = define_target(df)
    df = engineer_features(df)
    df = drop_raw_columns(df)

    before = len(df)
    df = df.drop_duplicates()
    removed = before - len(df)
    if removed:
        print(
            f"[FeatureEngineering] Removed {removed} duplicate rows "
            f"({before} → {len(df)})"
        )

    df = drop_training_leakage(df)

    if TARGET_COLUMN in df.columns:
        counts       = df[TARGET_COLUMN].value_counts()
        total        = len(df)
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


def run_prediction_pipeline(df: pd.DataFrame) -> pd.DataFrame:
    """
    PHASE 2 — Prediction pipeline for incoming students (no Final_Avg_GRD).

    Same steps as run_full_feature_pipeline() but WITHOUT define_target(),
    because there are no grades to derive a label from.

    Returns a DataFrame whose columns are TRAINING_FEATURES,
    ready to be scored by the saved model.
    """
    if _is_already_engineered(df):
        print(
            "[FeatureEngineering] WARNING: run_prediction_pipeline() received "
            "already-engineered data. Skipping engineering steps."
        )
        df = drop_training_leakage(df)
        return df

    df = normalize_columns(df)
    df = engineer_features(df)
    df = drop_raw_columns(df)
    df = drop_training_leakage(df)
    return df


# =============================================================================
# PRIVATE HELPERS
# =============================================================================

def _is_already_engineered(df: pd.DataFrame) -> bool:
    """
    Return True if the DataFrame has already been through engineer_features().
    Detected by the presence of any engineered output column.
    Used to guard against running the pipeline twice on the same data.
    """
    engineered_markers = {
        "Entrance_Exam_Tier", "HS_Performance_Tier", "Strand_Program_Match",
        "Financial_Stress", "First_Gen_Student", "Has_Scholarship",
        "Gap_Years", "Private_HS", "Has_HS_Honors", "Age_At_Enrollment",
        "Distance_Bucket",
    }
    return bool(engineered_markers & set(df.columns))

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in kilometres between two coordinates."""
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
    """Return distance in km from municipality to campus, or -1.0 if unknown."""
    if not muni or muni not in _GEO_CACHE:
        return -1.0
    lat, lon = _GEO_CACHE[muni]
    return _haversine(lat, lon, CAMPUS_LAT, CAMPUS_LON)


def _distance_bucket(km: float) -> str:
    """Classify a distance value into a human-readable bucket label."""
    for label, (lo, hi) in _DISTANCE_BUCKETS.items():
        if lo <= km < hi:
            return label
    return "unknown"


def _gpa_tier(v: Any) -> int:
    """
    Philippine GPA: 1.0 (best) → 5.0 (fail).
        -1  missing (no college grade yet)
         0  excellent  (< 1.75)
         1  passing    (1.75 – 2.49)
         2  borderline (2.50 – 2.99)
         3  at-risk    (>= 3.0)
    """
    v = pd.to_numeric(v, errors="coerce")
    if pd.isna(v): return -1
    if v >= 3.0:   return 3
    if v >= 2.5:   return 2
    if v >= 1.75:  return 1
    return 0


def _exam_tier(v: Any) -> int:
    """0=strong(>=80)  1=average(65–79)  2=weak(<65).  Returns 1 for missing."""
    v = pd.to_numeric(v, errors="coerce")
    if pd.isna(v): return 1
    if v >= 80:    return 0
    if v >= 65:    return 1
    return 2


def _hs_tier(v: Any) -> int:
    """0=high(>=90)  1=average(80–89)  2=low(<80).  Returns 1 for missing."""
    v = pd.to_numeric(v, errors="coerce")
    if pd.isna(v): return 1
    if v >= 90:    return 0
    if v >= 80:    return 1
    return 2


def _strand_match(strand: Any, program: Any) -> float:
    """Return 1.0=aligned, 0.5=unknown strand, 0.0=mismatched or vocational."""
    s = str(strand).strip().upper()
    p = str(program).strip().upper()
    aligned = _STRAND_MAP.get(s)
    if aligned is None: return 0.5   # strand not in map → unknown
    if not aligned:     return 0.0   # TVL / GAS / Sports / Arts → mismatch
    return 1.0 if p in aligned else 0.0


def _is_truthy(v: Any) -> bool:
    return str(v).strip().lower() in (
        "true", "1", "yes", "y", "with", "approved", "scholar", "t"
    )