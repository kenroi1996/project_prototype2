"""
ui/helpers/settings_render.py
================================
Shared UI-builder helpers and constants used across all Settings page tabs:
role/status badges, card/input/button factories, and shared defaults.

Extracted verbatim from ui/pages/settings_page.py — no logic changes.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QLineEdit, QComboBox,
)

# ── Constants ─────────────────────────────────────────────────────────────────
_ROLES = ["admin", "counselor"]
_ROLE_LABELS = {"admin": "Administrator", "counselor": "Counselor"}
_ROLE_COLORS = {"admin": "#4f8cff", "counselor": "#34d399"}

_DEFAULT_INSTITUTION = "CTU-Daanbantayan"
_DEFAULT_AY          = "2024-2025"
_DEFAULT_SEM         = "1"


# ── Shared UI helpers ─────────────────────────────────────────────────────────

def _section_title(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        "color: rgba(255,255,255,0.35); font-size: 10px; font-weight: bold; "
        "letter-spacing: 1.2px; background: transparent;"
    )
    return lbl


def _card(layout_cls=QVBoxLayout) -> tuple[QFrame, QVBoxLayout | QHBoxLayout]:
    f = QFrame()
    f.setObjectName("settingsCard")
    f.setStyleSheet("""
        QFrame#settingsCard {
            background-color: #13172a;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 12px;
        }
    """)
    lo = layout_cls(f)
    lo.setContentsMargins(24, 20, 24, 20)
    lo.setSpacing(14)
    return f, lo


def _field_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        "color: rgba(255,255,255,0.55); font-size: 11px; background: transparent;"
    )
    return lbl


def _input(placeholder: str = "", password: bool = False) -> QLineEdit:
    w = QLineEdit()
    w.setObjectName("settingsInput")
    w.setPlaceholderText(placeholder)
    w.setFixedHeight(36)
    if password:
        w.setEchoMode(QLineEdit.EchoMode.Password)
    w.setStyleSheet("""
        QLineEdit#settingsInput {
            background-color: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.10);
            border-radius: 8px;
            color: #e8eaf0;
            font-size: 12px;
            padding: 0 12px;
        }
        QLineEdit#settingsInput:focus {
            border-color: rgba(79,140,255,0.50);
        }
    """)
    return w


def _combo(items: list[str]) -> QComboBox:
    w = QComboBox()
    w.addItems(items)
    w.setFixedHeight(36)
    w.setStyleSheet("""
        QComboBox {
            background-color: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.10);
            border-radius: 8px;
            color: #e8eaf0;
            font-size: 12px;
            padding: 0 12px;
        }
        QComboBox:focus { border-color: rgba(79,140,255,0.50); }
        QComboBox::drop-down { border: none; width: 20px; }
        QComboBox QAbstractItemView {
            background-color: #1a1f35;
            border: 1px solid rgba(255,255,255,0.12);
            color: #e8eaf0;
            selection-background-color: rgba(79,140,255,0.20);
        }
    """)
    return w


def _primary_btn(text: str, color: str = "#4f8cff") -> QPushButton:
    b = QPushButton(text)
    b.setFixedHeight(36)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    hover = _darken(color)
    b.setStyleSheet(f"""
        QPushButton {{
            background-color: {color};
            border: none; border-radius: 8px;
            color: white; font-size: 12px; font-weight: 600;
            padding: 0 20px;
        }}
        QPushButton:hover {{ background-color: {hover}; }}
        QPushButton:disabled {{
            background-color: rgba(255,255,255,0.06);
            color: rgba(255,255,255,0.25);
        }}
    """)
    return b


def _ghost_btn(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setFixedHeight(32)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setStyleSheet("""
        QPushButton {
            background-color: transparent;
            border: 1px solid rgba(255,255,255,0.12);
            border-radius: 7px; color: rgba(255,255,255,0.65);
            font-size: 11px; padding: 0 12px;
        }
        QPushButton:hover {
            background-color: rgba(255,255,255,0.06);
            color: #e8eaf0;
        }
    """)
    return b


def _danger_btn(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setFixedHeight(32)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setStyleSheet("""
        QPushButton {
            background-color: rgba(255,91,91,0.10);
            border: 1px solid rgba(255,91,91,0.30);
            border-radius: 7px; color: #ff5b5b;
            font-size: 11px; padding: 0 12px;
        }
        QPushButton:hover { background-color: rgba(255,91,91,0.20); }
        QPushButton:disabled {
            color: rgba(255,91,91,0.30);
            border-color: rgba(255,91,91,0.12);
        }
    """)
    return b


def _divider() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet("color: rgba(255,255,255,0.07); margin: 0;")
    return f


def _darken(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
    r, g, b = max(0,r-25), max(0,g-25), max(0,b-25)
    return f"#{r:02x}{g:02x}{b:02x}"


def _status_badge(active: bool) -> QLabel:
    lbl = QLabel("Active" if active else "Disabled")
    if active:
        lbl.setStyleSheet(
            "color:#34d399; background:rgba(52,211,153,0.12); "
            "border:1px solid rgba(52,211,153,0.30); border-radius:8px; "
            "font-size:10px; font-weight:600; padding:2px 8px;"
        )
    else:
        lbl.setStyleSheet(
            "color:#ff5b5b; background:rgba(255,91,91,0.10); "
            "border:1px solid rgba(255,91,91,0.28); border-radius:8px; "
            "font-size:10px; font-weight:600; padding:2px 8px;"
        )
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setFixedWidth(62)
    return lbl


def _role_badge(role: str) -> QLabel:
    color = _ROLE_COLORS.get(role, "#8b949e")
    label = _ROLE_LABELS.get(role, role.title())
    lbl   = QLabel(label)
    lbl.setStyleSheet(
        f"color:{color}; background:transparent; font-size:11px; font-weight:600;"
    )
    return lbl


def _feedback(text: str = "", color: str = "#34d399") -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color:{color}; font-size:11px; background:transparent;"
    )
    lbl.setWordWrap(True)
    return lbl