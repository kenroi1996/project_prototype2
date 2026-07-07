"""
ui/helpers/intervention_render.py
==================================
Small stateless render helpers shared by the intervention dialogs/widgets:

  - _risk_color   : label -> hex color
  - _rec_card     : per-student recommendation row widget
  - _cohort_row   : cohort systemic-issue row widget

Extracted verbatim from ui/pages/interventions_page.py — no logic changes.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QHBoxLayout, QFrame


# ══════════════════════════════════════════════════════════════════════════════
# Colour helper
# ══════════════════════════════════════════════════════════════════════════════

def _risk_color(label: str) -> str:
    lc = label.lower()
    if "high" in lc:                   return "#ff5b5b"
    if "medium" in lc or "mod" in lc:  return "#f5b335"
    return "#34d399"


# ══════════════════════════════════════════════════════════════════════════════
# Reusable stripe-style row widgets
# ══════════════════════════════════════════════════════════════════════════════

def _rec_card(rec: dict, idx: int) -> QWidget:
    rtype    = rec.get("type",      "—")
    action   = rec.get("action",    "—")
    rat      = rec.get("rationale", "—")
    timeline = rec.get("timeline",  "—")
    color = {
        "Academic Support": "#4f8cff", "Financial Aid": "#f5b335",
        "Counseling": "#a78bfa", "Program Guidance": "#34d399",
        "Peer Support": "#f59e0b",
    }.get(rtype, "#8b949e")

    outer = QWidget()
    outer.setStyleSheet("background:transparent;")
    row = QHBoxLayout(outer)
    row.setContentsMargins(0, 6, 0, 6)
    row.setSpacing(0)
    stripe = QFrame()
    stripe.setFixedWidth(4)
    stripe.setStyleSheet(f"background:{color}; border-radius:2px;")
    row.addWidget(stripe)
    cw = QWidget()
    cw.setStyleSheet("background:transparent;")
    cl = QVBoxLayout(cw)
    cl.setContentsMargins(18, 2, 8, 2)
    cl.setSpacing(5)
    meta = QHBoxLayout()
    meta.setSpacing(10)
    tl = QLabel(rtype.upper())
    tl.setStyleSheet(
        f"color:{color}; font-size:11px; font-weight:700; "
        "letter-spacing:0.6px; background:transparent;")
    tll = QLabel(f"⏱  {timeline}")
    tll.setStyleSheet(
        "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;")
    meta.addWidget(tl); meta.addWidget(tll); meta.addStretch()
    cl.addLayout(meta)
    al = QLabel(action)
    al.setWordWrap(True)
    al.setStyleSheet(
        "color:#e8eaf0; font-size:13px; font-weight:600; background:transparent;")
    cl.addWidget(al)
    rl = QLabel(rat)
    rl.setWordWrap(True)
    rl.setStyleSheet(
        "color:rgba(255,255,255,0.50); font-size:12px; background:transparent;")
    cl.addWidget(rl)
    row.addWidget(cw, 1)
    return outer


def _cohort_row(issue: dict, idx: int) -> QWidget:
    title  = issue.get("issue",             "—")
    count  = issue.get("affected_count",     0)
    desc   = issue.get("description",        "—")
    action = issue.get("recommended_action", "—")
    color  = ["#ff5b5b","#f5b335","#4f8cff","#34d399","#a78bfa"][min(idx, 4)]

    outer = QWidget()
    outer.setStyleSheet("background:transparent;")
    row = QHBoxLayout(outer)
    row.setContentsMargins(0, 8, 0, 8)
    row.setSpacing(0)
    stripe = QFrame()
    stripe.setFixedWidth(4)
    stripe.setStyleSheet(f"background:{color}; border-radius:2px;")
    row.addWidget(stripe)
    cw = QWidget()
    cw.setStyleSheet("background:transparent;")
    cl = QVBoxLayout(cw)
    cl.setContentsMargins(18, 2, 8, 2)
    cl.setSpacing(5)
    meta = QHBoxLayout()
    tl = QLabel(title)
    tl.setStyleSheet(
        f"color:{color}; font-size:14px; font-weight:700; background:transparent;")
    cl_ = QLabel(f"{int(count):,} students" if str(count).isdigit() else str(count))
    cl_.setStyleSheet(
        "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;")
    meta.addWidget(tl); meta.addStretch(); meta.addWidget(cl_)
    cl.addLayout(meta)
    dl = QLabel(desc)
    dl.setWordWrap(True)
    dl.setStyleSheet(
        "color:rgba(255,255,255,0.50); font-size:12px; background:transparent;")
    cl.addWidget(dl)
    al = QLabel(action)
    al.setWordWrap(True)
    al.setStyleSheet(
        "color:#e8eaf0; font-size:13px; font-weight:600; background:transparent;")
    cl.addWidget(al)
    row.addWidget(cw, 1)
    return outer