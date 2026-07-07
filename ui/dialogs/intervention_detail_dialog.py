"""
ui/dialogs/intervention_detail_dialog.py
==========================================
Read-only detail dialog for a single intervention log record
(per-student recommendations or a cohort systemic-issues summary).

Extracted verbatim from ui/pages/interventions_page.py — no logic changes.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QScrollArea, QWidget, QDialog,
)
from PyQt6.QtCore import Qt

from ui.helpers.intervention_render import _rec_card, _cohort_row


class _InterventionDetailDialog(QDialog):
    def __init__(self, row: dict, recs: list, parent=None):
        super().__init__(parent)
        self.setModal(True)
        self.resize(640, 560)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._row = row; self._recs = recs; self._drag_pos = None
        self._build_ui(); self._apply_styles()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None; super().mouseReleaseEvent(e)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        card = QFrame(); card.setObjectName("detailCard"); outer.addWidget(card)
        root = QVBoxLayout(card)
        root.setContentsMargins(28, 22, 28, 24); root.setSpacing(0)

        hdr = QHBoxLayout(); ic = QVBoxLayout(); ic.setSpacing(4)
        mode   = self._row.get("mode", "per_student")
        sid    = self._row.get("student_id")
        name   = str(self._row.get("student_name") or "").strip() or str(sid or "—")
        ay     = self._row.get("academic_year", "—")
        sem_n  = self._row.get("semester")
        sem_s  = "1st Semester" if sem_n == 1 else "2nd Semester" if sem_n == 2 else ""
        term   = f"{sem_s}  AY {ay}" if sem_s else ay
        risk_l = str(self._row.get("risk_label") or "—")
        score  = self._row.get("risk_score")
        logged = self._row.get("logged_at")
        ls     = (logged.strftime("%B %d, %Y  %H:%M")
                  if hasattr(logged, "strftime") else str(logged or "")[:16])

        hl = QLabel("Cohort Systemic Issues" if mode == "cohort" else name)
        hl.setObjectName("detailHeadline"); hl.setWordWrap(True)
        if mode != "cohort" and score:
            rc = ("#ff5b5b" if "high" in risk_l.lower() else
                  "#f5b335" if "medium" in risk_l.lower() else "#34d399")
            sl = QLabel(f"● {risk_l}  {float(score):.1f}% risk")
            sl.setStyleSheet(
                f"color:{rc}; font-size:12px; font-weight:600; background:transparent;")
            ic.addWidget(sl)
        ic.insertWidget(0, hl)
        for text, style in [
            (f"ID {sid}  ·  {term}",
             "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;"),
            (f"Generated  {ls}" if ls else "",
             "color:rgba(255,255,255,0.25); font-size:10px; background:transparent;"),
        ]:
            if text:
                lbl = QLabel(text); lbl.setStyleSheet(style); ic.addWidget(lbl)
        hdr.addLayout(ic, 1)
        cb = QPushButton("✕"); cb.setObjectName("detailCloseBtn")
        cb.setFixedSize(28, 28); cb.setCursor(Qt.CursorShape.PointingHandCursor)
        cb.clicked.connect(self.reject)
        hdr.addWidget(cb, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(hdr); root.addSpacing(14)
        div = QFrame(); div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("color:rgba(255,255,255,0.07);"); root.addWidget(div)
        root.addSpacing(12)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background:transparent; border:none; }")
        host = QWidget(); host.setStyleSheet("background:transparent;")
        hl2 = QVBoxLayout(host); hl2.setContentsMargins(0, 0, 8, 0); hl2.setSpacing(4)
        if not self._recs:
            el = QLabel("No recommendation data available.")
            el.setStyleSheet(
                "color:rgba(255,255,255,0.30); font-size:12px; background:transparent;")
            el.setAlignment(Qt.AlignmentFlag.AlignCenter); hl2.addWidget(el)
        else:
            for i, rec in enumerate(self._recs):
                hl2.addWidget(_cohort_row(rec, i) if mode == "cohort"
                              else _rec_card(rec, i))
                if i < len(self._recs) - 1:
                    sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
                    sep.setStyleSheet("color:rgba(255,255,255,0.06);"); hl2.addWidget(sep)
        hl2.addStretch(); scroll.setWidget(host); root.addWidget(scroll, 1)
        root.addSpacing(12)
        br = QHBoxLayout(); br.addStretch()
        cl = QPushButton("Close"); cl.setObjectName("detailCloseBottomBtn")
        cl.setFixedHeight(34); cl.setMinimumWidth(100)
        cl.setCursor(Qt.CursorShape.PointingHandCursor); cl.clicked.connect(self.reject)
        br.addWidget(cl); root.addLayout(br)

    def _apply_styles(self):
        self.setStyleSheet("""
            #detailCard { background:#13172a;
                border:1px solid rgba(255,255,255,0.10); border-radius:16px; }
            #detailHeadline { color:#e8eaf0; font-size:16px; font-weight:bold;
                background:transparent; }
            #detailCloseBtn { background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.10); border-radius:7px;
                color:rgba(255,255,255,0.35); font-size:13px; font-weight:bold; }
            #detailCloseBtn:hover { background:rgba(255,91,91,0.15);
                border-color:rgba(255,91,91,0.35); color:#ff5b5b; }
            #detailCloseBottomBtn { background:rgba(255,255,255,0.06);
                border:1px solid rgba(255,255,255,0.12); border-radius:8px;
                color:rgba(255,255,255,0.70); font-size:12px; font-weight:600;
                padding:0 20px; }
            #detailCloseBottomBtn:hover { background:rgba(255,255,255,0.12);
                color:#e8eaf0; }
        """)