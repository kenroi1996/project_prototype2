from __future__ import annotations

from PyQt6.QtGui import QColor

RISK_HIGH_HEX     = "#ff5b5b"
RISK_MODERATE_HEX = "#f5b335"
RISK_LOW_HEX      = "#34d399"

_HEX_BY_LEVEL = {
    "high":     RISK_HIGH_HEX,
    "moderate": RISK_MODERATE_HEX,
    "low":      RISK_LOW_HEX,
}


def risk_hex(level: str) -> str:
    """'high' | 'moderate' | 'low' -> hex string. Raises KeyError on typo."""
    return _HEX_BY_LEVEL[level.lower()]


def risk_qcolor(level: str) -> QColor:
    """'high' | 'moderate' | 'low' -> QColor."""
    return QColor(risk_hex(level))