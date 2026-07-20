"""
tests/test_prediction_engine.py
=================================
classify_risk() regression tests.

The headline case: classify_risk() used to read hardcoded module
constants (RISK_HIGH = 50, RISK_MODERATE = 25), completely ignoring
SystemConfig.risk_high_threshold()/risk_moderate_threshold() — the
admin-configurable setting on the Settings page had zero real effect.
classify_risk() now reads SystemConfig live on every call.
"""
from __future__ import annotations

from services.prediction_engine import classify_risk, risk_label
from services.system_config import SystemConfig


def test_default_thresholds_classify_as_before():
    """With no admin override, behavior must match the old hardcoded 50/25."""
    assert classify_risk(60, binary_pred=0) == "high_risk"
    assert classify_risk(30, binary_pred=0) == "moderate_risk"
    assert classify_risk(10, binary_pred=0) == "low_risk"


def test_positive_prediction_is_never_classified_as_low_risk():
    """
    A model-predicted 'at risk' case (binary_pred positive) is only ever
    high or moderate, regardless of score — never downgraded to low_risk.
    """
    assert classify_risk(5, binary_pred=1) == "moderate_risk"
    assert classify_risk(90, binary_pred=1) == "high_risk"


def test_admin_threshold_change_takes_effect_without_restart():
    """
    This is the actual bug: an admin-configured threshold change must be
    reflected immediately, since classify_risk() reads SystemConfig live
    rather than a value captured once at import time.
    """
    # Before any change — default 50/25
    assert classify_risk(60, binary_pred=0) == "high_risk"

    # Admin raises the high-risk bar to 70 via Settings
    SystemConfig.set_cache("risk_high_threshold", "70")
    SystemConfig.set_cache("risk_moderate_threshold", "40")

    assert classify_risk(60, binary_pred=0) == "moderate_risk"  # was high, now moderate
    assert classify_risk(75, binary_pred=0) == "high_risk"
    assert classify_risk(30, binary_pred=0) == "low_risk"        # below new 40 floor


def test_binary_pred_accepts_string_and_bool_forms():
    """
    Real prediction data comes through as int (0/1), string
    ('at_risk'/'not_at_risk'), or bool depending on the source — all
    three positive forms must be treated identically.
    """
    assert classify_risk(60, binary_pred="at_risk") == "high_risk"
    assert classify_risk(60, binary_pred="1") == "high_risk"
    assert classify_risk(60, binary_pred=True) == "high_risk"
    # score=30 is below the high threshold (50) but above moderate (25),
    # so a negative prediction correctly falls through to the score-based
    # branch and lands on moderate_risk — distinct from the positive-path
    # assertions above, which return high_risk regardless of score>=high.
    assert classify_risk(30, binary_pred="not_at_risk") == "moderate_risk"


def test_risk_label_maps_every_category_and_falls_back_on_unknown():
    assert risk_label("high_risk") == "High Risk"
    assert risk_label("moderate_risk") == "Moderate Risk"
    assert risk_label("low_risk") == "Low Risk"
    assert risk_label("something_unrecognized") == "Unknown"