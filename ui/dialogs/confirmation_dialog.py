import sys
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QFrame,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QColor, QPainter, QPen


# =====================================
# INLINE CONFIRMATION DIALOG
# (copied so this file runs standalone)
# =====================================

_ACTION_PRESETS = {
    "delete":    ("danger",  ""),
    "clear":     ("danger",  ""),
    "overwrite": ("danger",  ""),
    "retrain":   ("warning", ""),
    "train":     ("warning", ""),
    "pipeline":  ("warning", ""),
    "run":       ("warning", ""),
    "exit":      ("info",    ""),
}

_TIER_COLORS = {
    "danger":  {"accent": "#ff5b5b", "accent_hover": "#e04444", "icon_bg": "rgba(255, 91, 91, 0.15)", "icon_char": "✕"},
    "warning": {"accent": "#f5b335", "accent_hover": "#d99b20", "icon_bg": "rgba(245, 179, 53, 0.15)", "icon_char": "!"},
    "info":    {"accent": "#4f8cff", "accent_hover": "#3370e0", "icon_bg": "rgba(79, 140, 255, 0.15)", "icon_char": "?"},
}

def _resolve_tier(action_title: str) -> str:
    lower = action_title.lower()
    for keyword, (tier, _) in _ACTION_PRESETS.items():
        if keyword in lower:
            return tier
    return "info"


from PyQt6.QtWidgets import QDialog


class ConfirmationDialog(QDialog):

    def __init__(self, action_title, message, detail="", confirm_label="", parent=None):
        super().__init__(parent)

        self._action_title  = action_title
        self._message       = message
        self._detail        = detail
        self._confirm_label = confirm_label or action_title
        self._tier          = _resolve_tier(action_title)
        self._colors        = _TIER_COLORS[self._tier]

        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedWidth(400)

        self._build_ui()

    def _build_ui(self):
        accent       = self._colors["accent"]
        accent_hover = self._colors["accent_hover"]
        icon_bg      = self._colors["icon_bg"]
        icon_char    = self._colors["icon_char"]

        card = QFrame(self)
        card.setObjectName("confirmCard")
        card.setStyleSheet("""
            #confirmCard {
                background-color: #161b2e;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 16px;
            }
        """)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 28, 28, 24)
        card_layout.setSpacing(0)

        # Icon badge
        icon_row = QHBoxLayout()
        icon_row.setAlignment(Qt.AlignmentFlag.AlignLeft)

        icon_badge = QLabel(icon_char)
        icon_badge.setFixedSize(48, 48)
        icon_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_badge.setStyleSheet(f"""
            background-color: {icon_bg};
            border-radius: 12px;
            color: {accent};
            font-size: 20px;
            font-weight: bold;
        """)

        icon_row.addWidget(icon_badge)
        icon_row.addStretch()
        card_layout.addLayout(icon_row)
        card_layout.addSpacing(20)

        # Title
        title = QLabel(self._action_title)
        title.setStyleSheet("""
            color: #e8eaf0;
            font-size: 16px;
            font-weight: 600;
            background: transparent;
        """)
        card_layout.addWidget(title)
        card_layout.addSpacing(10)

        # Message
        message = QLabel(self._message)
        message.setWordWrap(True)
        message.setStyleSheet("""
            color: #8b93a8;
            font-size: 13px;
            background: transparent;
        """)
        card_layout.addWidget(message)

        # Detail
        if self._detail:
            card_layout.addSpacing(8)
            detail = QLabel(self._detail)
            detail.setWordWrap(True)
            detail.setStyleSheet(f"""
                color: {accent};
                font-size: 12px;
                background: transparent;
            """)
            card_layout.addWidget(detail)

        card_layout.addSpacing(28)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("color: rgba(255,255,255,0.07);")
        card_layout.addWidget(divider)
        card_layout.addSpacing(20)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedHeight(38)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #8b93a8;
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 8px;
                font-size: 13px;
                padding: 0 20px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.05);
                color: #e8eaf0;
            }
            QPushButton:pressed {
                background-color: rgba(255, 255, 255, 0.08);
            }
        """)
        cancel_btn.clicked.connect(self.reject)

        confirm_btn = QPushButton(self._confirm_label)
        confirm_btn.setFixedHeight(38)
        confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        confirm_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {accent};
                color: #ffffff;
                border: none;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 600;
                padding: 0 20px;
            }}
            QPushButton:hover {{
                background-color: {accent_hover};
            }}
            QPushButton:pressed {{
                background-color: {accent_hover};
                padding-top: 2px;
            }}
        """)
        confirm_btn.clicked.connect(self.accept)

        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(confirm_btn)
        card_layout.addLayout(btn_row)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)


# =====================================
# PREVIEW WINDOW
# =====================================

class PreviewWindow(QWidget):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ConfirmationDialog — Preview")
        self.setFixedSize(520, 560)
        self.setStyleSheet("background-color: #000d1a;")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(16)

        heading = QLabel("ConfirmationDialog Preview")
        heading.setStyleSheet("color: #e8eaf0; font-size: 15px; font-weight: 600;")
        layout.addWidget(heading)

        sub = QLabel("Click a button to preview each dialog variant.")
        sub.setStyleSheet("color: #6b7a99; font-size: 12px;")
        layout.addWidget(sub)

        layout.addSpacing(8)

        scenarios = [
            ("Delete Record",  "#ff5b5b", "Delete Record",  "Are you sure you want to delete John Doe?",           "This action cannot be undone."),
            ("Train Model",    "#ff5b5b", "Train Model",     "Are you sure you want to retrain the model?",         "This will overwrite all existing predictions."),
            ("Clear Dataset",  "#ff5b5b", "Clear Dataset",  "This will remove all uploaded data for this semester.", ""),
            ("Run Pipeline",   "#ff5b5b", "Run Pipeline",   "Are you sure you want to run the data pipeline now?",  ""),
            ("Exit",           "#ff5b5b", "Exit",           "Are you sure you want to exit the application?",       ""),
            ("Overwrite Data", "#ff5b5b", "Overwrite Data", "This will replace all current prediction results.",    "Existing data will be permanently lost."),
        ]

        for btn_label, color, title, message, detail in scenarios:
            btn = QPushButton(btn_label)
            btn.setFixedHeight(42)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    color: {color};
                    border: 1px solid {color}55;
                    border-radius: 8px;
                    font-size: 13px;
                    text-align: left;
                    padding: 0 16px;
                }}
                QPushButton:hover {{
                    background-color: {color}18;
                }}
            """)
            btn.clicked.connect(
                lambda _, t=title, m=message, d=detail: self._show(t, m, d)
            )
            layout.addWidget(btn)

        layout.addStretch()

        self._result_label = QLabel("")
        self._result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._result_label.setStyleSheet("color: #6b7a99; font-size: 12px;")
        layout.addWidget(self._result_label)

    def _show(self, title, message, detail):
        dialog = ConfirmationDialog(title, message, detail=detail, parent=self)
        if dialog.exec():
            self._result_label.setText(f'✓  "{title}" confirmed.')
            self._result_label.setStyleSheet("color: #34d399; font-size: 12px;")
        else:
            self._result_label.setText(f'✕  "{title}" cancelled.')
            self._result_label.setStyleSheet("color: #ff5b5b; font-size: 12px;")


# =====================================
# ENTRY POINT
# =====================================

#if __name__ == "__main__":
   # app = QApplication(sys.argv)
   # window = PreviewWindow()
   # window.show()
   # sys.exit(app.exec())