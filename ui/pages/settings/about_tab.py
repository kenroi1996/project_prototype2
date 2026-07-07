"""
ui/pages/settings/about_tab.py
=================================
Settings page — Tab 4: About.
Versions, DB info, installed packages, credits.

Extracted verbatim from ui/pages/settings_page.py — no logic changes.
"""
from __future__ import annotations
from datetime import datetime

from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout

from services.data_store import DataStore
from ui.helpers.settings_render import _DEFAULT_INSTITUTION, _section_title, _card


class _AboutTab(QWidget):
    def __init__(self):
        super().__init__()
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(20)

        sys_card, sys_lo = _card()
        sys_lo.addWidget(_section_title("SYSTEM"))

        rows = [
            ("Application", "EarlyAlert"),
            ("Version",      "1.0.0"),
            ("Build",        datetime.now().strftime("%Y-%m-%d")),
            ("Institution",  _DEFAULT_INSTITUTION),
            ("Database",     self._db_info()),
        ]
        for label, value in rows:
            row_lo = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setFixedWidth(120)
            lbl.setStyleSheet(
                "color: rgba(255,255,255,0.35); font-size:12px; background:transparent;")
            val = QLabel(value)
            val.setStyleSheet(
                "color: #e8eaf0; font-size:12px; background:transparent;")
            row_lo.addWidget(lbl)
            row_lo.addWidget(val, 1)
            sys_lo.addLayout(row_lo)

        root.addWidget(sys_card)

        pkg_card, pkg_lo = _card()
        pkg_lo.addWidget(_section_title("INSTALLED PACKAGES"))

        packages = [
            ("PyQt6",           self._pkg_version("PyQt6")),
            ("scikit-learn",    self._pkg_version("sklearn")),
            ("pandas",          self._pkg_version("pandas")),
            ("numpy",           self._pkg_version("numpy")),
            ("imbalanced-learn", self._pkg_version("imblearn")),
            ("reportlab",       self._pkg_version("reportlab")),
            ("bcrypt",          self._pkg_version("bcrypt")),
            ("psycopg2",        self._pkg_version("psycopg2")),
        ]
        grid = QGridLayout()
        grid.setSpacing(8)
        for i, (name, version) in enumerate(packages):
            name_lbl = QLabel(name)
            name_lbl.setStyleSheet(
                "color: rgba(255,255,255,0.50); font-size:11px; background:transparent;")
            ver_lbl = QLabel(version)
            ver_lbl.setStyleSheet(
                f"color: {'#34d399' if version != '—' else '#ff5b5b'}; "
                "font-size:11px; background:transparent; font-weight:600;")
            grid.addWidget(name_lbl, i // 2, (i % 2) * 2)
            grid.addWidget(ver_lbl,  i // 2, (i % 2) * 2 + 1)
        pkg_lo.addLayout(grid)
        root.addWidget(pkg_card)

        cred_card, cred_lo = _card()
        cred_lo.addWidget(_section_title("CREDITS"))
        cred_lbl = QLabel(
            "EarlyAlert is an AI-powered student academic risk prediction system "
            "developed for Cebu Technological University — Daanbantayan Campus.\n\n"
            "Built with PyQt6, scikit-learn, PostgreSQL, and reportlab."
        )
        cred_lbl.setWordWrap(True)
        cred_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.50); font-size:12px; "
            "line-height:1.6; background:transparent;")
        cred_lo.addWidget(cred_lbl)
        root.addWidget(cred_card)
        root.addStretch()

    @staticmethod
    def _db_info() -> str:
        conn = DataStore.get().db_conn
        if not conn:
            return "Not connected"
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                ver = cur.fetchone()[0]
            return ver.split(",")[0]
        except Exception:
            return "Connected"

    @staticmethod
    def _pkg_version(mod: str) -> str:
        try:
            m = __import__(mod)
            return getattr(m, "__version__", "installed")
        except ImportError:
            return "—"