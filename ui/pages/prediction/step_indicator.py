"""
Step indicator bar for the Prediction Center wizard — clickable badges
with connecting lines showing current/completed/upcoming state.

Purely presentational: this widget renders whatever state it's told via
set_state() and emits step_clicked when a badge is clicked. The actual
navigation/completion rules live in PredictionPage.
"""

from PyQt6.QtWidgets import QFrame, QLabel, QHBoxLayout
from PyQt6.QtCore import Qt, pyqtSignal

from ui.pages.prediction.constants import ACCENT, STEP_META


class StepIndicatorBar(QFrame):
    step_clicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("predStepIndicatorBar")
        self._badges: dict[int, QLabel] = {}
        self._titles: dict[int, QLabel] = {}
        self._connectors: list[QFrame] = []
        self._build_ui()

    def _build_ui(self):
        row = QHBoxLayout(self)
        row.setContentsMargins(24, 18, 24, 18)
        row.setSpacing(6)

        for i, (num, title) in enumerate(STEP_META):
            row.addWidget(self._build_chip(i, num, title), 1)
            if i < len(STEP_META) - 1:
                connector = QFrame()
                connector.setFixedHeight(2)
                connector.setFixedWidth(36)
                row.addWidget(connector, 0)
                self._connectors.append(connector)

    def _build_chip(self, idx: int, num: str, title: str) -> QFrame:
        chip = QFrame()
        chip.setObjectName(f"predStepChip_{idx}")
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(chip)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(10)

        badge = QLabel(num)
        badge.setFixedSize(26, 26)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_lbl = QLabel(title)

        row.addWidget(badge)
        row.addWidget(title_lbl)
        row.addStretch()

        # Click-to-jump — same inline mousePressEvent pattern already used
        # elsewhere in this app (e.g. student_cohort_page.py's clickable
        # student ID cell) rather than wrapping in a QPushButton, which
        # doesn't lay out a badge + label combo cleanly.
        chip.mousePressEvent = lambda e, i=idx: (
            self.step_clicked.emit(i)
            if e.button() == Qt.MouseButton.LeftButton else None
        )

        self._badges[idx] = badge
        self._titles[idx] = title_lbl
        return chip

    def set_state(self, current: int, furthest: int, completed: list[bool]):
        """
        current   — index of the step currently shown
        furthest  — furthest index the user has reached
        completed — completed[i] is True if step i's requirement is met
        """
        for i in range(len(STEP_META)):
            badge, title_lbl, num = self._badges[i], self._titles[i], STEP_META[i][0]

            if i == current:
                badge.setText(num)
                badge.setStyleSheet(
                    f"background:{ACCENT}; color:white; border-radius:13px; "
                    "font-size:11px; font-weight:800;"
                )
                title_lbl.setStyleSheet(
                    "color:#e8eaf0; font-size:12px; font-weight:700; background:transparent;"
                )
            elif i < furthest or (i <= furthest and completed[i]):
                badge.setText("✓")
                badge.setStyleSheet(
                    "background:rgba(52,211,153,0.18); color:#34d399; "
                    "border:1px solid rgba(52,211,153,0.4); border-radius:13px; "
                    "font-size:12px; font-weight:800;"
                )
                title_lbl.setStyleSheet(
                    "color:rgba(255,255,255,0.55); font-size:12px; "
                    "font-weight:600; background:transparent;"
                )
            else:
                badge.setText(num)
                badge.setStyleSheet(
                    "background:rgba(255,255,255,0.06); color:rgba(255,255,255,0.35); "
                    "border:1px solid rgba(255,255,255,0.12); border-radius:13px; "
                    "font-size:11px; font-weight:700;"
                )
                title_lbl.setStyleSheet(
                    "color:rgba(255,255,255,0.30); font-size:12px; "
                    "font-weight:600; background:transparent;"
                )

        for i, connector in enumerate(self._connectors):
            passed = i < furthest or i < current
            connector.setStyleSheet(
                f"background:{'#34d399' if passed else 'rgba(255,255,255,0.10)'}; "
                "border-radius:1px;"
            )