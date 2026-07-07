"""
EarlyAlert — Settings Page
==========================
Admin-only page. Counselors never see this in the sidebar.

Tabs
----
  👤  User Management   — add / edit role / disable-enable / remove users
  🔐  Security          — change own password + login audit log
  ⚙️  System Config     — institution name, default term, risk thresholds
  ℹ️  About             — versions, DB info
  📋  Activity Logs     — view and clean up all activity log entries

Each tab, the shared workers, password utilities, and UI-builder helpers
have been split out into their own modules to keep this file focused on
the page container itself:
  services/password_utils.py            -> password validation/hashing
  workers/settings_workers.py           -> all QThread workers
  ui/helpers/settings_render.py         -> shared card/input/badge builders
  ui/pages/settings/user_management_tab.py
  ui/pages/settings/security_tab.py
  ui/pages/settings/system_config_tab.py
  ui/pages/settings/about_tab.py
  ui/pages/settings/activity_logs_tab.py

No logic changes — only relocation and import wiring.

DB additions used here
----------------------
  ALTER TABLE public.users ADD COLUMN IF NOT EXISTS last_login TIMESTAMP;

  CREATE TABLE IF NOT EXISTS public.system_config (
      key        VARCHAR(100) PRIMARY KEY,
      value      TEXT,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_by VARCHAR(100)
  );
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QScrollArea,
)

from services.auth_service import AuthService

from ui.pages.settings.user_management_tab import _UserManagementTab
from ui.pages.settings.security_tab import _SecurityTab
from ui.pages.settings.system_config_tab import _SystemConfigTab
from ui.pages.settings.about_tab import _AboutTab
from ui.pages.settings.activity_logs_tab import _ActivityLogsTab


class SettingsPage(QWidget):
    """Admin-only Settings page."""

    _ALL_TABS = [
        ("👤", "User Management"),
        ("🔐", "Security"),
        ("⚙️", "System Config"),
        ("ℹ️",  "About"),
        ("📋", "Activity Logs"),
    ]
    _COUNSELOR_TABS = {"Security", "About"}

    def __init__(self):
        super().__init__()
        self._tab_btns: list[QPushButton] = []

        from services.auth_service import AuthService
        role = (AuthService.current_role() or "").strip().lower()
        if role == "counselor":
            self._TABS = [t for t in self._ALL_TABS if t[1] in self._COUNSELOR_TABS]
        else:
            self._TABS = list(self._ALL_TABS)

        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("settingsSidebar")
        sidebar.setFixedWidth(200)
        side_lo = QVBoxLayout(sidebar)
        side_lo.setContentsMargins(12, 24, 12, 24)
        side_lo.setSpacing(4)

        title = QLabel("Settings")
        title.setStyleSheet(
            "color: #e8eaf0; font-size:15px; font-weight:bold; "
            "background:transparent; padding: 0 8px 12px 8px;")
        side_lo.addWidget(title)

        for i, (icon, label) in enumerate(self._TABS):
            btn = QPushButton(f"  {icon}  {label}")
            btn.setCheckable(True)
            btn.setObjectName("settingsTabBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(40)
            btn.clicked.connect(lambda _, idx=i: self._switch_tab(idx))
            self._tab_btns.append(btn)
            side_lo.addWidget(btn)

        side_lo.addStretch()

        content_area = QWidget()
        content_area.setObjectName("settingsContent")
        content_lo = QVBoxLayout(content_area)
        content_lo.setContentsMargins(0, 0, 0, 0)
        content_lo.setSpacing(0)

        header = QFrame()
        header.setObjectName("settingsHeader")
        header_lo = QVBoxLayout(header)
        header_lo.setContentsMargins(32, 24, 32, 20)
        self._page_title = QLabel(self._TABS[0][1] if self._TABS else "Settings")
        self._page_title.setStyleSheet(
            "color: #e8eaf0; font-size:18px; font-weight:bold; background:transparent;")
        self._page_sub = QLabel("Manage accounts, roles, and access control")
        self._page_sub.setStyleSheet(
            "color: rgba(255,255,255,0.40); font-size:12px; background:transparent;")
        header_lo.addWidget(self._page_title)
        header_lo.addWidget(self._page_sub)
        content_lo.addWidget(header)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }")

        self._tab_host = QWidget()
        self._tab_lo   = QVBoxLayout(self._tab_host)
        self._tab_lo.setContentsMargins(32, 8, 32, 32)
        self._tab_lo.setSpacing(0)

        self._scroll.setWidget(self._tab_host)
        content_lo.addWidget(self._scroll, 1)

        root.addWidget(sidebar)
        root.addWidget(content_area, 1)

        _all_tab_widgets = {
            "User Management": _UserManagementTab,
            "Security":        _SecurityTab,
            "System Config":   _SystemConfigTab,
            "About":           _AboutTab,
            "Activity Logs":   _ActivityLogsTab,
        }
        self._tabs = [
            _all_tab_widgets[label]()
            for _, label in self._TABS
        ]

        self._current_tab_widget: QWidget | None = None
        self._switch_tab(0)

    def _switch_tab(self, idx: int):
        for i, btn in enumerate(self._tab_btns):
            btn.setChecked(i == idx)

        if self._current_tab_widget is not None:
            self._tab_lo.removeWidget(self._current_tab_widget)
            self._current_tab_widget.hide()

        tab = self._tabs[idx]
        self._tab_lo.addWidget(tab)
        tab.show()
        self._current_tab_widget = tab

        _, label = self._TABS[idx]
        subtitles = {
            "User Management": "Manage accounts, roles, and access control",
            "Security":        "Change your password and review login activity",
            "System Config":   "Institution settings and model configuration",
            "About":           "System information and installed packages",
            "Activity Logs":   "View and clean up all system activity logs",
        }
        self._page_title.setText(label)
        self._page_sub.setText(subtitles.get(label, ""))
        self._scroll.verticalScrollBar().setValue(0)

    def _apply_styles(self):
        self.setStyleSheet("""
            #settingsSidebar {
                background-color: #0e1120;
                border-right: 1px solid rgba(255,255,255,0.07);
            }
            QPushButton#settingsTabBtn {
                background-color: transparent;
                border: none; border-radius: 8px;
                color: rgba(255,255,255,0.50);
                font-size: 12px; font-weight: 500;
                text-align: left; padding: 0 10px;
            }
            QPushButton#settingsTabBtn:hover {
                background-color: rgba(255,255,255,0.05);
                color: rgba(255,255,255,0.80);
            }
            QPushButton#settingsTabBtn:checked {
                background-color: rgba(79,140,255,0.15);
                color: #4f8cff; font-weight: 700;
                border-left: 3px solid #4f8cff;
            }
            #settingsContent { background-color: #0e1120; }
            #settingsHeader  { background-color: #0e1120; }
            QScrollBar:vertical { background: transparent; width: 8px; }
            QScrollBar::handle:vertical {
                background: rgba(255,255,255,0.10);
                border-radius: 4px; min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255,255,255,0.20);
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical { height: 0; }
        """)