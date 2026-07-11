"""Back/Next navigation bar for the Prediction Center wizard."""

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QLabel
from PyQt6.QtCore import Qt, pyqtSignal


class WizardNavBar(QFrame):
    back_clicked = pyqtSignal()
    next_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("predWizardNav")
        self._build_ui()

    def _build_ui(self):
        row = QHBoxLayout(self)
        row.setContentsMargins(24, 16, 24, 20)
        row.setSpacing(10)

        self.back_btn = QPushButton("←  Back")
        self.back_btn.setObjectName("predSecondaryBtn")
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.setFixedHeight(38)
        self.back_btn.setFixedWidth(110)
        self.back_btn.clicked.connect(self.back_clicked.emit)

        self._progress_lbl = QLabel("")
        self._progress_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.30); font-size: 11px; background: transparent;"
        )

        self.next_btn = QPushButton("Next  →")
        self.next_btn.setObjectName("predPrimaryBtn")
        self.next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_btn.setFixedHeight(38)
        self.next_btn.setFixedWidth(130)
        self.next_btn.clicked.connect(self.next_clicked.emit)

        row.addWidget(self.back_btn)
        row.addStretch()
        row.addWidget(self._progress_lbl)
        row.addStretch()
        row.addWidget(self.next_btn)

    def set_state(self, current: int, total: int,
                  back_visible: bool, next_visible: bool, next_enabled: bool):
        self.back_btn.setVisible(back_visible)
        self.next_btn.setVisible(next_visible)
        self.next_btn.setEnabled(next_enabled)
        self._progress_lbl.setText(f"Step {current + 1} of {total}")