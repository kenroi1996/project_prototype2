"""
tests/test_merge_engine.py
============================
MergeEngine regression tests.

The headline case here is the Municipality / Home_Address collision bug:
both used to map through overlapping alias lists in UNIFIED_FEATURE_MAP,
so whichever column got processed second silently overwrote the other —
location data went missing for any student whose portal exports included
both fields. See services/merge_engine.py UNIFIED_FEATURE_MAP for the fix
(distinct, non-overlapping alias lists).
"""
from __future__ import annotations

from services.merge_engine import MergeEngine, UNIFIED_COLUMNS


def _portal(headers, rows):
    return {"headers": headers, "rows": rows}


def test_municipality_and_home_address_both_survive_merge():
    """
    Regression test for the Municipality/Home_Address collision bug.
    A student record with BOTH fields populated must retain both in the
    merged output — neither should silently overwrite the other.
    """
    mis = _portal(
        ["ID_NO", "PROGRAM", "MUNICIPALITY", "HOME_ADDRESS"],
        [["2024-001", "BSIT", "Daanbantayan", "Purok 3, Poblacion"]],
    )
    sao = _portal(["STUDENT_ID"], [["2024-001"]])
    guidance = _portal(["student_id"], [["2024-001"]])
    registrar = _portal(["student_id"], [["2024-001"]])

    result = MergeEngine.merge({
        "mis": mis, "sao": sao, "guidance": guidance, "registrar": registrar,
    })

    assert result.report.success, result.report.errors
    assert len(result.rows) == 1

    row = dict(zip(result.headers, result.rows[0]))
    assert row["Municipality"] == "Daanbantayan"
    assert row["Home_Address"] == "Purok 3, Poblacion"


def test_merge_requires_all_four_portals():
    result = MergeEngine.merge({
        "mis": _portal(["ID_NO"], [["1"]]),
        "sao": None,
        "guidance": _portal(["student_id"], [["1"]]),
        "registrar": _portal(["student_id"], [["1"]]),
    })
    assert not result.report.success
    assert any("sao" in e for e in result.report.errors)


def test_merge_matches_ids_across_different_dash_formats():
    """
    _normalize_id strips whitespace/dashes/case so '2024-001' (MIS) and
    '2024001' (another portal's export format) are treated as the same
    student rather than silently failing to match.
    """
    mis = _portal(["ID_NO", "PROGRAM"], [["2024-001", "BSIT"]])
    sao = _portal(["STUDENT_ID", "FAMILY_INCOME"], [["2024001", "Low"]])
    guidance = _portal(["student_id"], [[]])
    registrar = _portal(["student_id"], [[]])

    result = MergeEngine.merge({
        "mis": mis, "sao": sao, "guidance": guidance, "registrar": registrar,
    })

    assert result.report.success
    row = dict(zip(result.headers, result.rows[0]))
    assert row["Family_Income"] == "Low"
    assert row["Student_ID"] == "2024001"


def test_mis_master_data_is_never_overwritten_by_other_portals():
    """
    MIS is the master source. If another portal's export happens to
    include a column matching a unified field MIS already populated,
    MIS's value must win — the merge only fills genuinely empty fields.
    """
    mis = _portal(["ID_NO", "PROGRAM"], [["2024-001", "BSIT"]])
    sao = _portal(["STUDENT_ID", "PROGRAM"], [["2024-001", "WRONG-PROGRAM"]])
    guidance = _portal(["student_id"], [[]])
    registrar = _portal(["student_id"], [[]])

    result = MergeEngine.merge({
        "mis": mis, "sao": sao, "guidance": guidance, "registrar": registrar,
    })

    row = dict(zip(result.headers, result.rows[0]))
    assert row["Program"] == "BSIT"


def test_unified_columns_are_all_present_in_every_row():
    mis = _portal(["ID_NO"], [["2024-001"]])
    sao = _portal(["STUDENT_ID"], [["2024-001"]])
    guidance = _portal(["student_id"], [["2024-001"]])
    registrar = _portal(["student_id"], [["2024-001"]])

    result = MergeEngine.merge({
        "mis": mis, "sao": sao, "guidance": guidance, "registrar": registrar,
    })

    assert result.headers == UNIFIED_COLUMNS
    assert len(result.rows[0]) == len(UNIFIED_COLUMNS)