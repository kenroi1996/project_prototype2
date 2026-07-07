"""
ui/widgets/student_result_card.py
==================================
Collapsible per-student result card used in the batch-analysis results list.

Extracted verbatim from ui/pages/interventions_page.py — no logic changes.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QVBoxLayout, QHBoxLayout, QFrame
from PyQt6.QtCore import Qt

from ui.helpers.intervention_render import _risk_color, _rec_card


class _StudentResultCard(QFrame):
    def __init__(self, student: dict, recs: list, skipped: bool = False, parent=None):
        super().__init__(parent)
        self._student  = student
        self._recs     = recs
        self._skipped  = skipped
        self._expanded = False
        self._build()

    def _build(self):
        self.setObjectName("studentResultCard")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        hdr = QFrame()
        hdr.setObjectName("studentResultHdr")
        hdr.setCursor(Qt.CursorShape.PointingHandCursor)
        hdr_lo = QHBoxLayout(hdr)
        hdr_lo.setContentsMargins(16, 12, 16, 12)
        hdr_lo.setSpacing(12)

        s         = self._student
        score_raw = s.get("predicted_risk_score") or 0
        score     = round(float(score_raw) * 100, 1)
        color     = _risk_color(s.get("risk_label", "High"))

        dot = QLabel("●")
        dot.setFixedWidth(14)
        dot.setStyleSheet(f"color:{color}; font-size:11px; background:transparent;")
        name_lbl = QLabel(s.get("full_name", "—"))
        name_lbl.setStyleSheet(
            "color:#e8eaf0; font-size:13px; font-weight:600; background:transparent;")
        meta_lbl = QLabel(f"{s.get('program','—')}  ·  {s.get('college','—')}")
        meta_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.38); font-size:11px; background:transparent;")
        score_lbl = QLabel(f"{score:.1f}%")
        score_lbl.setStyleSheet(
            f"color:{color}; font-size:13px; font-weight:700; background:transparent;")
        score_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        rc = len(self._recs)

        if self._skipped:
            pill = QLabel("✓ Already analyzed")
            pill.setStyleSheet(
                "color:rgba(52,211,153,0.60); font-size:10px; font-weight:600; "
                "background:rgba(52,211,153,0.08); "
                "border:1px solid rgba(52,211,153,0.20); "
                "border-radius:8px; padding:2px 10px;")
        elif rc:
            pill = QLabel(f"{rc} recommendations")
            pill.setStyleSheet(
                "color:#4f8cff; font-size:10px; font-weight:600; "
                "background:rgba(79,140,255,0.15); "
                "border:1px solid rgba(79,140,255,0.30); "
                "border-radius:8px; padding:2px 10px;")
        else:
            pill = QLabel("No recommendations")
            pill.setStyleSheet(
                "color:rgba(255,255,255,0.30); font-size:10px; font-weight:600; "
                "background:rgba(255,255,255,0.04); "
                "border:1px solid rgba(255,255,255,0.10); "
                "border-radius:8px; padding:2px 10px;")

        self._arrow = QLabel("▶")
        self._arrow.setStyleSheet(
            "color:rgba(255,255,255,0.25); font-size:10px; background:transparent;")

        for w in [dot, name_lbl, meta_lbl]:
            hdr_lo.addWidget(w)
        hdr_lo.addStretch()
        for w in [score_lbl, pill, self._arrow]:
            hdr_lo.addWidget(w)
        root.addWidget(hdr)

        self._body = QFrame()
        self._body.setObjectName("studentResultBody")
        self._body.setVisible(False)
        body_lo = QVBoxLayout(self._body)
        body_lo.setContentsMargins(20, 8, 20, 14)
        body_lo.setSpacing(0)

        if not self._recs:
            empty = QLabel("AI could not generate recommendations for this student.")
            empty.setStyleSheet(
                "color:rgba(255,255,255,0.30); font-size:12px; background:transparent;")
            body_lo.addWidget(empty)
        else:
            if self._skipped:
                note = QLabel("Previously saved recommendations — Ollama was not called again.")
                note.setStyleSheet(
                    "color:rgba(52,211,153,0.50); font-size:11px; "
                    "background:transparent; padding-bottom:6px;")
                body_lo.addWidget(note)
            for i, rec in enumerate(self._recs):
                body_lo.addWidget(_rec_card(rec, i))
                if i < len(self._recs) - 1:
                    sep = QFrame()
                    sep.setFrameShape(QFrame.Shape.HLine)
                    sep.setStyleSheet("color:rgba(255,255,255,0.06);")
                    body_lo.addWidget(sep)

        root.addWidget(self._body)
        hdr.mousePressEvent = lambda _: self._toggle()
        self._update_style()

    def _toggle(self):
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        self._arrow.setText("▼" if self._expanded else "▶")
        self._update_style()

    def _update_style(self):
        a = "0.12" if self._expanded else "0.07"
        self.setStyleSheet(f"""
            QFrame#studentResultCard {{
                background:rgba(255,255,255,0.02);
                border:1px solid rgba(255,255,255,{a});
                border-radius:10px; margin-bottom:6px;
            }}
            QFrame#studentResultHdr:hover {{
                background:rgba(255,255,255,0.03); border-radius:10px;
            }}
            QFrame#studentResultBody {{
                background:transparent; border:none;
                border-top:1px solid rgba(255,255,255,0.06);
            }}
        """)