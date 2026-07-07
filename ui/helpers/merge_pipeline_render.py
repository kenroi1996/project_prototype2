"""
ui/helpers/merge_pipeline_render.py
======================================
Small stateless render helpers shared across the Data Merge & Pipeline page:

  - _divider       : thin horizontal divider line
  - _quality_badge  : small colored status pill (pending/ready/warning/error)
  - _stat_tile      : value + label stat tile

Extracted verbatim from ui/pages/data_merge_pipeline_page.py — no logic changes.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QVBoxLayout, QFrame
from PyQt6.QtCore import Qt


def _divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: rgba(255,255,255,0.06); margin: 0;")
    return line


def _quality_badge(text: str, level: str = "pending") -> QLabel:
    colors = {
        "pending": ("rgba(255,255,255,0.28)", "rgba(255,255,255,0.06)", "rgba(255,255,255,0.10)"),
        "ready":   ("#34d399", "rgba(52,211,153,0.12)", "rgba(52,211,153,0.30)"),
        "warning": ("#f5b335", "rgba(245,179,53,0.12)", "rgba(245,179,53,0.30)"),
        "error":   ("#ff5b5b", "rgba(255,91,91,0.12)", "rgba(255,91,91,0.30)"),
    }
    fg, bg, border = colors.get(level, colors["pending"])
    badge = QLabel(text)
    badge.setStyleSheet(f"""
        color: {fg};
        background-color: {bg};
        border: 1px solid {border};
        border-radius: 10px;
        font-size: 11px;
        font-weight: 600;
        padding: 3px 10px;
    """)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    badge.setFixedWidth(76)
    return badge


def _stat_tile(value: str, label: str, accent: str = "rgba(255,255,255,0.75)") -> QFrame:
    tile = QFrame()
    tile.setObjectName("mergeStatTile")
    tile.setStyleSheet("""
        #mergeStatTile {
            background-color: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 10px;
        }
    """)
    layout = QVBoxLayout(tile)
    layout.setContentsMargins(16, 12, 16, 12)
    layout.setSpacing(4)

    val = QLabel(value)
    val.setStyleSheet(f"color: {accent}; font-size: 18px; font-weight: bold;")

    lbl = QLabel(label)
    lbl.setStyleSheet("color: rgba(255,255,255,0.35); font-size: 11px;")

    layout.addWidget(val)
    layout.addWidget(lbl)
    return tile