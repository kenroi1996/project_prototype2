#!/usr/bin/env python
"""
Diagnostic script — EarlyAlert v2 schema.
Tests DB connection, staging table populations, and a mock prediction save.
"""

import sys

print("=" * 70)
print("DATABASE PERSISTENCE DIAGNOSTIC  (EarlyAlert v2)")
print("=" * 70)

# ── 1. Connection ─────────────────────────────────────────────────────
print("\n[1] Testing Database Connection...")
try:
    from database.connection import get_connection
    conn = get_connection()
    if conn:
        print("    ✅ Connection successful")
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN (
                  'fact_student_risk', 'dim_risk_level', 'dim_program',
                  'merge_log', 'mis_students'
              )
            ORDER BY table_name
            """
        )
        found = {r[0] for r in cursor.fetchall()}
        for t in ("fact_student_risk", "dim_risk_level", "dim_program",
                  "merge_log", "mis_students"):
            status = "✅" if t in found else "❌"
            print(f"    {status} {t}")
        conn.close()
    else:
        print("    ❌ Connection FAILED — check database/db_config.py")
        sys.exit(1)
except Exception as e:
    print(f"    ❌ {e}")
    sys.exit(1)

# ── 2. Staging table row counts ───────────────────────────────────────
print("\n[2] Staging Table Row Counts...")
try:
    conn = get_connection()
    cursor = conn.cursor()
    for table in ("mis_students", "sao_student_profile",
                  "guidance_student_profile", "registrar_student_profile"):
        try:
            cursor.execute(f"SELECT COUNT(*) FROM public.{table}")
            count = cursor.fetchone()[0]
            status = "✅" if count > 0 else "⚠️ "
            print(f"    {status} {table}: {count:,} rows")
        except Exception:
            print(f"    ❌  {table}: table not found")
    conn.close()
except Exception as e:
    print(f"    ❌ {e}")

# ── 3. dim_risk_level seed check ──────────────────────────────────────
print("\n[3] dim_risk_level Seed Data...")
try:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT risk_level_id, risk_label FROM public.dim_risk_level ORDER BY risk_level_id"
    )
    rows = cursor.fetchall()
    if rows:
        for rid, label in rows:
            print(f"    ✅ {rid} → {label}")
    else:
        print("    ❌ dim_risk_level is empty — run the schema SQL to seed it")
    conn.close()
except Exception as e:
    print(f"    ❌ {e}")

# ── 4. Simulate Prediction Save ───────────────────────────────────────
print("\n[4] Simulating Prediction Save...")
try:
    from services.risk_persistence_service import RiskPersistenceService

    # Grab a real student_id from mis_students if available
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id_no FROM public.mis_students LIMIT 1")
    row = cursor.fetchone()
    conn.close()

    test_sid = row[0] if row else "TEST-0001"
    print(f"    Using student_id: {test_sid}")

    mock_pred = {
        "student_id":             test_sid,
        "name":                   "Test Student",
        "program":                "BSIT",
        "college":                "CITE",
        "score":                  75.5,        # 0-100 scale
        "category":               "high_risk",
        "label":                  "High Risk",
        "factor":                 "final_avg_grd",
        "gwa":                    "2.85",
        "absences":               "15",
        "sec_code":               "A1",
        "year_level":             "2",
        "sex_code":               "M",
        "home_address":           "Cebu City",
        "civil_status":           "Single",
        "birthdate":              "2003-05-12",
        "year_enrolled":          "2022",
        "entrance_exam_score":    "78.5",
        "family_income":          "Low",
        "parent_highest_education": "High School",
        "hs_gpa":                 "88.5",
        "year_graduated":         "2022",
        "shs_strand":             "STEM",
        "hs_type":                "Public",
        "graduation_honors":      "With Honors",
        "hs_school_name":         "Cebu City National High School",
        "scholarship_applicant":  "yes",
        "scholarship_type":       "CHED",
    }

    result = RiskPersistenceService.save_predictions(
        [mock_pred],
        model_id="rf",
        academic_year="2024-2025",
        semester="1",
    )

    if result.get("success"):
        print(f"    ✅ Inserted: {result['inserted']}  Updated: {result['updated']}")
        if result.get("errors"):
            for err in result["errors"]:
                print(f"    ⚠️  {err}")
    else:
        print(f"    ❌ Save failed: {result.get('error')}")

except Exception as e:
    import traceback
    print(f"    ❌ {e}")
    traceback.print_exc()

# ── 5. Verify record count ────────────────────────────────────────────
print("\n[5] fact_student_risk record count...")
try:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM public.fact_student_risk")
    count = cursor.fetchone()[0]
    status = "✅" if count > 0 else "❌"
    print(f"    {status} {count:,} records in fact_student_risk")

    if count > 0:
        cursor.execute(
            """
            SELECT fact_id, student_id, risk_score, risk_label, predicted_at
            FROM   public.fact_student_risk
            ORDER  BY predicted_at DESC
            LIMIT  5
            """
        )
        for r in cursor.fetchall():
            print(f"       fact_id={r[0]}  sid={r[1]}  score={r[2]}  label={r[3]}  at={r[4]}")
    conn.close()
except Exception as e:
    print(f"    ❌ {e}")

print("\n" + "=" * 70)
print("DIAGNOSTIC COMPLETE")
print("=" * 70)
