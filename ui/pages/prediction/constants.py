"""Shared constants for the Prediction Center wizard."""

ACCENT = "#4f8cff"

PORTAL_CONFIG = {
    "mis":       {"label": "MIS",       "full": "Management Information System",  "icon": "", "color": "#4f8cff"},
    "sao":       {"label": "SAO",       "full": "Student Affairs Office",         "icon": "", "color": "#a78bfa"},
    "guidance":  {"label": "Guidance",  "full": "Guidance Office",                "icon": "", "color": "#34d399"},
    "registrar": {"label": "Registrar", "full": "Registrar's Office",             "icon": "", "color": "#f5b335"},
}

DATASET_CONFIG = {
    "title":  "Prediction Dataset",
    "office": "Incoming First-Year Students",
    "accent": ACCENT,
}

STEP_META = [
    ("01", "Dataset Details"),
    ("02", "Upload Portals"),
    ("03", "Merge & Clean"),
    ("04", "Run & Predict"),
]