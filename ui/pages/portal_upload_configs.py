"""
ui/pages/portal_upload_configs.py
====================================
Per-portal configuration data (title, office name, description, accent
color, expected fields, etc.) for the four upload portals: MIS, SAO,
Guidance, and Registrar.

Extracted verbatim from ui/pages/portal_upload_page.py — no logic changes.
"""
from __future__ import annotations

PORTAL_CONFIGS = {
    "mis": {
        "title": "MIS PORTAL",
        "office": "Management Information System",
        "subtitle": "Academic records & enrollment data",
        "description": (
            "Upload semester grades, units earned, failed subjects, "
            "and program enrollment from the MIS office."
        ),
        "accent": "#4f8cff",
        "file_hint": "mis_academic_records_2024.csv",
        "fields": [
            "KEYID", "SYSTEMCODE", "ID_NO", "PROGRAM", "COLLEGE",
            "SECCODE", "YEAR", "SEX_CODE", "HOME_ADDRESS", "CIVIL_STATUS",
            "RELIGION", "FINAL_AVG_GRD",
        ],
    },
    "sao": {
        "title": "SAO PORTAL",
        "office": "Student Affairs Office",
        "subtitle": "Attendance, conduct & student life data",
        "description": (
            "Upload attendance logs, org membership, violations, "
            "and financial aid status from the SAO office."
        ),
        "accent": "#34d399",
        "file_hint": "sao_student_affairs_2024.csv",
        "fields": [
            "STUDENT_ID", "SCHOLARSHIP_APPLICANT", "SCHOLARSHIP_TYPE",
            "GENDER", "BIRTHDATE", "MUNICIPALITY", "PROGRAM",
        ],
    },
    "guidance": {
        "title": "Guidance PORTAL",
        "office": "Guidance & Counseling Office",
        "subtitle": "Psychological screening & referral records",
        "description": (
            "Upload psychometric scores, counseling referrals, "
            "and socio-economic background from the Guidance office."
        ),
        "accent": "#f59e0b",
        "file_hint": "guidance_psych_records_2024.csv",
        "fields": [
            "Date", "student_id", "systemcode", "last_name", "first_name",
            "entrance_exam_score", "family_income_bracket",
            "parent_highest_education", "applicant_age",
            "home_municipality", "program_code"
        ],
    },
    "registrar": {
        "title": "Registrar PORTAL",
        "office": "Office of the Registrar",
        "subtitle": "Student biographical & high school background data",
        "description": (
            "Upload student identity, demographic, and high school "
            "background records for cohort mapping and risk modeling."
        ),
        "accent": "#a78bfa",
        "file_hint": "registrar_student_records_2024.csv",
        "fields": [
            "student_id", "lastname", "firstname", "gender", "hs_gpa",
            "year_graduated", "shs_strand", "hs_type", "graduation_honors",
            "hs_school", "municipality", "home_address", "year_enrolled",
        ],
    },
}