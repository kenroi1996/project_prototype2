"""
tests/test_training_engine.py
================================
Regression test for the SMOTE dtype-coercion bug.

Numeric feature columns arriving from the merge pipeline as strings
(e.g. Entrance_Exam_Score = "88.5") used to be silently skipped by
df.median(numeric_only=True), because a string column isn't "numeric"
to pandas even if every value parses as a number. Their NaNs (from
missing/blank cells) never got filled — RandomForestClassifier tolerates
NaN, so training "worked", but SMOTE rejects NaN outright and failed on
every single fold with real data. The fix: pd.to_numeric(..., errors=
"coerce") on every numeric column before the fillna(median) step.

This test builds a small but realistic *raw* historical dataset (the
shape MergeEngine would hand off — post-merge, pre-engineering) with
numeric fields deliberately passed as strings, and drives it through
the real TrainingEngine._prepare_features() pipeline end to end.
"""
from __future__ import annotations

import numpy as np

from services.training_engine import TrainingEngine


def _raw_headers_and_rows():
    headers = [
        "Student_ID", "Program", "Entrance_Exam_Score", "HS_GPA",
        "Final_Avg_GRD", "Family_Income", "Parent_Highest_Education",
        "Scholarship_Applicant", "Year_Graduated", "Year_Enrolled",
        "HS_Type", "Graduation_Honors", "SHS_Strand", "Municipality",
    ]
    # Deliberately string-typed numeric fields ("88.5" not 88.5), and a
    # mix of passing (< 3.0) and failing (>= 3.0) grades so both target
    # classes exist. A couple of blank cells exercise the NaN path that
    # used to slip through uncoerced.
    rows = [
        ["2024-001", "BSIT", "88.5", "3.40", "1.75", "Low",    "High School Grad", "Yes", "2022", "2023", "Public",  "With Honors", "STEM", "Daanbantayan"],
        ["2024-002", "BSIT", "72.0", "2.90", "3.25", "Middle", "College Grad",      "No",  "2022", "2023", "Private", "None",        "HUMSS", ""],
        ["2024-003", "BSBA", "",     "3.10", "1.50", "Low",    "High School Grad", "No",  "2021", "2023", "Public",  "None",        "ABM",   "Bogo"],
        ["2024-004", "BSBA", "65.5", "",     "3.80", "High",   "College Grad",      "Yes", "2022", "2023", "Private", "With Honors", "STEM",  ""],
        ["2024-005", "BSIT", "91.0", "3.60", "1.25", "Middle", "High School Grad", "No",  "2022", "2023", "Public",  "With Honors", "STEM",  "Medellin"],
        ["2024-006", "BSBA", "58.0", "2.50", "3.50", "Low",    "High School Grad", "No",  "2020", "2023", "Public",  "None",        "GAS",   ""],
    ]
    return headers, rows


def test_prepare_features_produces_no_nan_for_smote():
    """
    The actual regression: after _prepare_features(), the numeric
    feature matrix must contain zero NaN values — the exact precondition
    SMOTE requires. Before the fix, string-typed numeric columns left
    real NaNs in X and every SMOTE call on real data raised.
    """
    headers, rows = _raw_headers_and_rows()
    engine = TrainingEngine(headers, rows)

    X, y, feature_names, engineered_headers, engineered_rows = engine._prepare_features()

    X_arr = np.array(X, dtype=float)
    assert not np.isnan(X_arr).any(), (
        "NaN survived _prepare_features() — SMOTE would reject this data, "
        "exactly like the original bug."
    )


def test_prepare_features_yields_both_target_classes():
    """SMOTE needs at least two classes to do anything meaningful."""
    headers, rows = _raw_headers_and_rows()
    engine = TrainingEngine(headers, rows)

    X, y, *_ = engine._prepare_features()

    assert set(y) == {0, 1}
    assert sum(y) >= 1          # at least one at_risk
    assert len(y) - sum(y) >= 1  # at least one not_at_risk


def test_smote_actually_runs_on_the_prepared_data():
    """
    Not just "no NaN" in isolation — actually hand the prepared matrix
    to SMOTE, the real downstream consumer, and confirm it runs without
    raising. This is the end-to-end version of the bug report.
    """
    from imblearn.over_sampling import SMOTE

    headers, rows = _raw_headers_and_rows()
    engine = TrainingEngine(headers, rows)
    X, y, *_ = engine._prepare_features()

    X_arr = np.array(X, dtype=float)
    y_arr = np.array(y, dtype=int)

    n_minority = min((y_arr == 0).sum(), (y_arr == 1).sum())
    k_neighbors = max(1, min(2, n_minority - 1))

    # Must not raise — this is exactly what failed before the fix.
    X_res, y_res = SMOTE(random_state=42, k_neighbors=k_neighbors).fit_resample(X_arr, y_arr)
    assert len(X_res) >= len(X_arr)


def test_string_numeric_column_is_coerced_not_dropped():
    """
    Entrance_Exam_Score arrives as strings including one blank cell.
    Confirm it survives as a real numeric feature (present in
    feature_names) rather than being silently excluded from training.
    """
    headers, rows = _raw_headers_and_rows()
    engine = TrainingEngine(headers, rows)

    X, y, feature_names, *_ = engine._prepare_features()

    assert "Entrance_Exam_Score" in feature_names
    assert "HS_GPA" in feature_names