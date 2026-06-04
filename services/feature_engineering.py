"""
Feature Engineering for EarlyAlert Student Risk Prediction
===========================================================

Input  : unified DataFrame produced by MergeEngine (UNIFIED_COLUMNS names)
Output : ML-ready DataFrame with 14 engineered features + risk_label target

Unified column names this module reads
---------------------------------------
Student_ID, Program, College, SecCode, Year, Sex_code, Home_Address,
Civil_Status, Entrance_Exam_Score, Family_Income, Parent_Highest_Education,
HS_GPA, Year_Graduated, SHS_Strand, HS_Type, Graduation_Honors, HS_School,
Year_Enrolled, Scholarship_Applicant, Scholarship_Type, Birthdate,
Final_Avg_GRD, Religion

Pipeline position
-----------------
[Merge] → [define_target] → [engineer_features] → [drop_raw_cols]
        → [DataPipeline: dedup / fill / encode / scale] → [train]
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd


# ── GPA scale note ──────────────────────────────────────────────────────────
# Philippine university grading (1.0 = Excellent, 5.0 = Failed).
# Passing threshold is typically 3.0 (equivalent to 75 %).
# A value of 3.01+ means the student is failing / at academic risk.

# ── Target variable thresholds ──────────────────────────────────────────────
GPA_HIGH_RISK     = 3.0   # final_avg_grd ≥ 3.0 → high risk
GPA_MODERATE_RISK = 2.5   # final_avg_grd ≥ 2.5 → moderate risk

EXAM_HIGH_RISK     = 60   # entrance_exam_score < 60 → high risk
EXAM_MODERATE_RISK = 75   # entrance_exam_score < 75 → moderate risk

# ── Strand–Program alignment tables ─────────────────────────────────────────
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
    "STEM":  _STEM_PROGRAMS,
    "ABM":   _ABM_PROGRAMS,
    "HUMSS": _HUMSS_PROGRAMS,
    "TVL":   set(),    # vocational — always mismatched for 4-yr programs
    "GAS":   set(),    # general academic — neutral
    "SPORTS": set(),
    "ARTS AND DESIGN": set(),
}

# ── Family income order (ascending) ─────────────────────────────────────────
_INCOME_ORDER: dict[str, int] = {
    "below 10,000": 1,
    "below 10000": 1,
    "10,000-20,000": 2,
    "10000-20000": 2,
    "20,001-30,000": 3,
    "20001-30000": 3,
    "30,001-40,000": 4,
    "30001-40000": 4,
    "40,001-50,000": 5,
    "40001-50000": 5,
    "above 50,000": 6,
    "above 50000": 6,
    "50,001 and above": 6,
}

# ── Non-college parent education labels ─────────────────────────────────────
_NON_COLLEGE_LABELS = {
    "no formal education", "elementary", "grade school",
    "high school", "junior high school", "senior high school",
    "vocational", "als", "alternative learning system",
    "did not finish high school", "none",
}

# ── Columns consumed to build features; dropped before training ──────────────
COLS_TO_DROP: list[str] = [
    # Identifiers
    "Student_ID",
    # Redundant / zero-signal
    "College",        # fully determined by Program
    "SecCode",        # admin artifact
    "Home_Address",   # too granular / privacy concern
    "Civil_Status",   # near-uniform for college-age students
    "Religion",       # no academic relevance
    "HS_School",      # high cardinality, encodes geography not quality
    # Raw sources replaced by engineered features
    "Final_Avg_GRD",          # → GPA_Tier, Has_College_Grade (and used for target)
    "Year",                   # → Year_Level
    "Entrance_Exam_Score",    # → Entrance_Exam_Tier
    "HS_GPA",                 # → HS_Performance_Tier
    "SHS_Strand",             # → Strand_Program_Match
    "Family_Income",          # → Financial_Stress
    "Parent_Highest_Education", # → First_Gen_Student
    "Scholarship_Applicant",  # → Has_Scholarship
    "HS_Type",                # → Private_HS
    "Graduation_Honors",      # → Has_HS_Honors
    "Year_Enrolled",          # → Gap_Years, Age_At_Enrollment
    "Year_Graduated",         # → Gap_Years
    "Birthdate",              # → Age_At_Enrollment
]

# ── Final 15-feature set fed to the model ────────────────────────────────────
FINAL_FEATURES: list[str] = [
    "GPA_Tier",               # 0=excellent · 1=passing · 2=borderline · 3=failing · -1=no grade
    "Has_College_Grade",      # 1 if Final_Avg_GRD was present (first-sem flag)
    "Year_Level",             # 1–4+ (integer year of study)
    "Entrance_Exam_Tier",     # 0=strong · 1=average · 2=weak
    "HS_Performance_Tier",    # 0=high · 1=average · 2=low
    "Strand_Program_Match",   # 0=mismatch · 0.5=unknown · 1=aligned
    "Financial_Stress",       # 1–6 (higher = more stress)
    "First_Gen_Student",      # 1 if neither parent reached college
    "Has_Scholarship",        # 1 if scholarship applicant / recipient
    "Gap_Years",              # years between HS graduation and enrollment
    "Private_HS",             # 1 if attended private high school
    "Has_HS_Honors",          # 1 if graduated with any honours
    "Age_At_Enrollment",      # age in years at time of college enrollment
    "Program",                # label-encoded by DataPipeline
    "Sex_code",               # label-encoded by DataPipeline
]

TARGET_COLUMN = "risk_label"


# ============================================================================
# PUBLIC API
# ============================================================================

def define_target(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add ``risk_label`` column to *df* using grade and exam score rules.

    Priority:
    1. If Final_Avg_GRD is present → use GPA thresholds
    2. Elif Entrance_Exam_Score is present → use exam score thresholds
    3. Otherwise → 'moderate_risk' (conservative default)

    Labels: 'low_risk' | 'moderate_risk' | 'high_risk'
    """
    df = df.copy()

    grd  = pd.to_numeric(df.get("Final_Avg_GRD",      pd.Series(dtype=float)),
                         errors="coerce")
    exam = pd.to_numeric(df.get("Entrance_Exam_Score", pd.Series(dtype=float)),
                         errors="coerce")

    def _label(row_grd: float, row_exam: float) -> str:
        if pd.notna(row_grd):
            if row_grd >= GPA_HIGH_RISK:     return "high_risk"
            if row_grd >= GPA_MODERATE_RISK: return "moderate_risk"
            return "low_risk"
        if pd.notna(row_exam):
            if row_exam < EXAM_HIGH_RISK:     return "high_risk"
            if row_exam < EXAM_MODERATE_RISK: return "moderate_risk"
            return "low_risk"
        return "moderate_risk"

    df[TARGET_COLUMN] = [
        _label(g, e)
        for g, e in zip(grd, exam)
    ]

    counts = df[TARGET_COLUMN].value_counts().to_dict()
    print(
        f"[FeatureEngineering] Target distribution: "
        f"high={counts.get('high_risk', 0)}, "
        f"moderate={counts.get('moderate_risk', 0)}, "
        f"low={counts.get('low_risk', 0)}"
    )
    return df


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Derive the 13 engineered features from raw UNIFIED_COLUMNS.
    Returns *df* with new columns appended (raw columns are NOT dropped here —
    call ``drop_raw_columns`` separately so the caller can inspect the result).
    """
    df = df.copy()

    # ── 1. GPA Tier ──────────────────────────────────────────────────────────
    grd = pd.to_numeric(df.get("Final_Avg_GRD", pd.Series(dtype=float)),
                        errors="coerce")
    df["GPA_Tier"] = grd.apply(_gpa_tier)

    # ── 2. Has College Grade flag ─────────────────────────────────────────────
    df["Has_College_Grade"] = grd.notna().astype(int)

    # ── 3. Year Level (integer) ───────────────────────────────────────────────
    df["Year_Level"] = (
        pd.to_numeric(df.get("Year", pd.Series(dtype=float)), errors="coerce")
        .fillna(1)
        .clip(lower=1)
        .astype(int)
    )

    # ── 4. Entrance Exam Tier ─────────────────────────────────────────────────
    exam = pd.to_numeric(df.get("Entrance_Exam_Score", pd.Series(dtype=float)),
                         errors="coerce")
    df["Entrance_Exam_Tier"] = exam.apply(_exam_tier)

    # ── 5. HS Performance Tier ───────────────────────────────────────────────
    hs_gpa = pd.to_numeric(df.get("HS_GPA", pd.Series(dtype=float)),
                           errors="coerce")
    df["HS_Performance_Tier"] = hs_gpa.apply(_hs_tier)

    # ── 6. Strand–Program Alignment ──────────────────────────────────────────
    df["Strand_Program_Match"] = df.apply(
        lambda r: _strand_match(r.get("SHS_Strand", ""), r.get("Program", "")),
        axis=1,
    )

    # ── 7. Financial Stress (1=low stress / 6=high stress) ───────────────────
    income_raw = df.get("Family_Income", pd.Series(dtype=str)).fillna("")
    income_level = income_raw.str.lower().str.strip().map(_INCOME_ORDER).fillna(3)
    df["Financial_Stress"] = (7 - income_level).clip(lower=1, upper=6).astype(int)

    # ── 8. First-Generation Student ──────────────────────────────────────────
    parent_edu = df.get("Parent_Highest_Education", pd.Series(dtype=str)).fillna("")
    df["First_Gen_Student"] = (
        parent_edu.str.lower().str.strip().isin(_NON_COLLEGE_LABELS)
    ).astype(int)

    # ── 9. Has Scholarship ───────────────────────────────────────────────────
    scholar = df.get("Scholarship_Applicant", pd.Series(dtype=str)).fillna("")
    df["Has_Scholarship"] = scholar.apply(_is_truthy).astype(int)

    # ── 10. Gap Years (between HS graduation and enrollment) ─────────────────
    yr_grad = pd.to_numeric(df.get("Year_Graduated", pd.Series(dtype=float)),
                            errors="coerce")
    yr_enrl = pd.to_numeric(df.get("Year_Enrolled", pd.Series(dtype=float)),
                            errors="coerce")
    df["Gap_Years"] = (
        (yr_enrl - yr_grad - 1)
        .clip(lower=0)
        .fillna(0)
        .astype(int)
    )

    # ── 11. Private HS ────────────────────────────────────────────────────────
    hs_type = df.get("HS_Type", pd.Series(dtype=str)).fillna("")
    df["Private_HS"] = (
        hs_type.str.lower().str.contains("private", na=False)
    ).astype(int)

    # ── 12. Has HS Honors ─────────────────────────────────────────────────────
    honors = df.get("Graduation_Honors", pd.Series(dtype=str)).fillna("")
    df["Has_HS_Honors"] = honors.apply(
        lambda v: 0 if str(v).strip().lower() in ("", "none", "nan", "—", "n/a") else 1
    )

    # ── 13. Age at Enrollment ─────────────────────────────────────────────────
    birthdate = pd.to_datetime(
        df.get("Birthdate", pd.Series(dtype=str)), errors="coerce"
    )
    df["Age_At_Enrollment"] = (
        yr_enrl - birthdate.dt.year
    ).clip(lower=10, upper=60)
    # Fallback median imputation for missing ages
    age_median = df["Age_At_Enrollment"].median()
    df["Age_At_Enrollment"] = (
        df["Age_At_Enrollment"].fillna(age_median if pd.notna(age_median) else 18)
        .astype(float)
    )

    print(
        f"[FeatureEngineering] Engineered features added. "
        f"DataFrame shape: {df.shape}"
    )
    return df


def drop_raw_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Remove source columns that have been replaced by engineered features.
    Only drops columns that actually exist to avoid KeyError.
    """
    existing = [c for c in COLS_TO_DROP if c in df.columns]
    df = df.drop(columns=existing)
    print(
        f"[FeatureEngineering] Dropped {len(existing)} raw columns. "
        f"Remaining: {list(df.columns)}"
    )
    return df


def run_full_feature_pipeline(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convenience wrapper: define_target → engineer → drop_raw.
    Returns ML-ready DataFrame (target + FINAL_FEATURES + any label-encode cols).
    """
    df = define_target(df)
    df = engineer_features(df)
    df = drop_raw_columns(df)
    return df


# ============================================================================
# PRIVATE HELPERS
# ============================================================================

def _gpa_tier(v: Any) -> int:
    """
    Philippine GPA scale: 1.0 (best) → 5.0 (fail).
    Returns:
        -1  missing / no grade (first-semester students)
         0  excellent  (< 1.75)
         1  passing    (1.75 – 2.49)
         2  borderline (2.50 – 2.99)
         3  at-risk    (≥ 3.0)
    """
    v = pd.to_numeric(v, errors="coerce")
    if pd.isna(v):  return -1
    if v >= 3.0:    return 3
    if v >= 2.5:    return 2
    if v >= 1.75:   return 1
    return 0


def _exam_tier(v: Any) -> int:
    """0 = strong (≥80), 1 = average (65–79), 2 = weak (<65). 1 for missing."""
    v = pd.to_numeric(v, errors="coerce")
    if pd.isna(v): return 1
    if v >= 80:    return 0
    if v >= 65:    return 1
    return 2


def _hs_tier(v: Any) -> int:
    """0 = high (≥90), 1 = average (80–89), 2 = low (<80). 1 for missing."""
    v = pd.to_numeric(v, errors="coerce")
    if pd.isna(v): return 1
    if v >= 90:    return 0
    if v >= 80:    return 1
    return 2


def _strand_match(strand: Any, program: Any) -> float:
    """
    Returns 1.0 (aligned), 0.5 (unknown strand), or 0.0 (mismatched/TVL/GAS).
    """
    s = str(strand).strip().upper()
    p = str(program).strip().upper()
    aligned = _STRAND_MAP.get(s)
    if aligned is None:    return 0.5   # strand not in map
    if not aligned:        return 0.0   # TVL, GAS, Sports, Arts
    return 1.0 if p in aligned else 0.0


def _is_truthy(v: Any) -> bool:
    return str(v).strip().lower() in (
        "true", "1", "yes", "y", "with", "approved", "scholar", "t"
    )
