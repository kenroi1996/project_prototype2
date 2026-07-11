"""
Header card for the Prediction Center — title + pulsing "Model Active"
pill with the current academic term.

Fully self-sufficient: reads SystemConfig itself. Callers just need to
call refresh_term_label() whenever the "system_config" DataStore key
changes.
"""

from PyQt6.QtWidgets import (
    QFrame, QLabel, QVBoxLayout, QHBoxLayout, QGraphicsOpacityEffect,
)
from PyQt6.QtCore import QPropertyAnimation, QEasingCurve

from services.system_config import SystemConfig


class HeaderCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("predHeaderCard")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)

        row = QHBoxLayout()
        row.setSpacing(20)

        text_col = QVBoxLayout()
        text_col.setSpacing(5)
        title = QLabel("PREDICTION CENTER")
        title.setObjectName("header")
        sub = QLabel(
            "Upload incoming student datasets, merge portals, "
            "and score each student with the trained risk model"
        )
        sub.setObjectName("subHeader")
        text_col.addWidget(title)
        text_col.addWidget(sub)
        row.addLayout(text_col, 1)

        pill = QFrame()
        pill.setObjectName("predModelPill")
        pill_row = QHBoxLayout(pill)
        pill_row.setContentsMargins(20, 12, 20, 12)
        pill_row.setSpacing(12)

        dot = QLabel("●")
        dot.setObjectName("predModelDot")
        opacity = QGraphicsOpacityEffect(dot)
        dot.setGraphicsEffect(opacity)
        anim = QPropertyAnimation(opacity, b"opacity")
        anim.setDuration(1200)
        anim.setStartValue(1.0)
        anim.setKeyValueAt(0.5, 0.3)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        anim.setLoopCount(-1)
        anim.start()
        self._status_anim = anim   # keep alive — QPropertyAnimation is GC'd otherwise

        status_lbl = QLabel("Model Active")
        status_lbl.setObjectName("predModelStatus")
        self._sem_pill_lbl = QLabel(f"{SystemConfig.term_label()}  ▾")
        self._sem_pill_lbl.setObjectName("predSemesterPill")

        pill_row.addWidget(dot)
        pill_row.addWidget(status_lbl)
        pill_row.addSpacing(8)
        pill_row.addWidget(self._sem_pill_lbl)
        row.addWidget(pill)

        layout.addLayout(row)

    def refresh_term_label(self):
        self._sem_pill_lbl.setText(f"{SystemConfig.term_label()}  ▾")