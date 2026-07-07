"""
ui/widgets/merge_spinner.py
=============================
Small rotating-arc loading spinner used on the Data Merge & Pipeline page
(and reusable anywhere else a lightweight custom spinner is needed).

Extracted verbatim from ui/pages/data_merge_pipeline_page.py — no logic changes.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen


class _MergeSpinner(QWidget):
    def __init__(self, size=40, color="#4f8cff", parent=None):
        super().__init__(parent)
        self._angle = 0
        self._color = QColor(color)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def start(self):
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._rotate)
        self._timer.start(16)
        self.show()

    def stop(self):
        if hasattr(self, "_timer"):
            self._timer.stop()
        self.hide()

    def _rotate(self):
        self._angle = (self._angle + 8) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        margin = 6
        rect = self.rect().adjusted(margin, margin, -margin, -margin)

        track = QPen(QColor(255, 255, 255, 15))
        track.setWidth(3)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track)
        painter.drawEllipse(rect)

        arc = QPen(self._color)
        arc.setWidth(3)
        arc.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc)
        painter.drawArc(rect, -self._angle * 16, -120 * 16)