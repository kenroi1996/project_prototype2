"""
ui/pages/settings/security_tab.py
====================================
Settings page — Tab 2: Security.
Change own password + view login audit log.

Extracted verbatim from ui/pages/settings_page.py — no logic changes.
"""
from __future__ import annotations
import re

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QGridLayout,
)

from services.data_store import DataStore
from services.auth_service import AuthService
from services.password_utils import (
    _validate_password, _hash_password, _check_password, _PW_RULES_TXT,
)
from workers.settings_workers import _AuditLoader
from ui.helpers.settings_render import (
    _section_title, _card, _field_label, _input,
    _ghost_btn, _primary_btn, _feedback,
)


class _SecurityTab(QWidget):
    def __init__(self):
        super().__init__()
        self._audit_loader: _AuditLoader | None = None
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(20)

        pw_card, pw_lo = _card()
        pw_lo.addWidget(_section_title("CHANGE MY PASSWORD"))
        pw_lo.addSpacing(4)

        rules_lbl = QLabel(f"🔒  {_PW_RULES_TXT}")
        rules_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.35); font-size: 11px; "
            "background: rgba(255,255,255,0.03); border-radius:6px; padding:8px;"
        )
        rules_lbl.setWordWrap(True)
        pw_lo.addWidget(rules_lbl)

        grid = QGridLayout()
        grid.setSpacing(12)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)

        self._cur_pw  = _input("Current password",    password=True)
        self._new_pw1 = _input("New password",         password=True)
        self._new_pw2 = _input("Confirm new password", password=True)

        for col, (lbl, w) in enumerate([
            ("Current Password", self._cur_pw),
            ("New Password",     self._new_pw1),
            ("Confirm Password", self._new_pw2),
        ]):
            cl = QVBoxLayout()
            cl.setSpacing(4)
            cl.addWidget(_field_label(lbl))
            cl.addWidget(w)
            grid.addLayout(cl, 0, col)

        pw_lo.addLayout(grid)

        strength_row = QHBoxLayout()
        self._strength_lbl = QLabel("Password strength: —")
        self._strength_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.35); font-size:11px; background:transparent;"
        )
        self._new_pw1.textChanged.connect(self._update_strength)
        strength_row.addWidget(self._strength_lbl)
        strength_row.addStretch()
        pw_lo.addLayout(strength_row)

        self._pw_feedback = _feedback()
        pw_lo.addWidget(self._pw_feedback)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._change_pw_btn = _primary_btn("🔐  Change Password")
        self._change_pw_btn.clicked.connect(self._on_change_password)
        btn_row.addWidget(self._change_pw_btn)
        pw_lo.addLayout(btn_row)

        root.addWidget(pw_card)

        audit_card, audit_lo = _card()
        audit_header = QHBoxLayout()
        audit_header.addWidget(_section_title("LOGIN AUDIT LOG"))
        audit_header.addStretch()
        refresh_btn = _ghost_btn("↻  Refresh")
        refresh_btn.clicked.connect(self._load_audit)
        audit_header.addWidget(refresh_btn)
        audit_lo.addLayout(audit_header)

        self._audit_table = QTableWidget()
        self._audit_table.setObjectName("settingsTable")
        self._audit_table.setColumnCount(4)
        self._audit_table.setHorizontalHeaderLabels([
            "Timestamp", "Action", "Description", "Status"
        ])
        self._audit_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._audit_table.verticalHeader().setVisible(False)
        self._audit_table.setShowGrid(False)
        self._audit_table.setAlternatingRowColors(True)
        self._audit_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self._audit_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self._audit_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        self._audit_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents)
        self._audit_table.setMinimumHeight(200)
        self._audit_table.setStyleSheet("""
            QTableWidget#settingsTable {
                background-color: transparent; border: none;
                color: rgba(255,255,255,0.80); font-size: 12px;
                alternate-background-color: rgba(255,255,255,0.02);
                gridline-color: transparent;
            }
            QTableWidget#settingsTable QHeaderView::section {
                background-color: rgba(255,255,255,0.04);
                color: rgba(255,255,255,0.35); font-size:10px;
                font-weight: bold; border:none; padding:8px 10px;
            }
        """)
        audit_lo.addWidget(self._audit_table)
        root.addWidget(audit_card)
        root.addStretch()

        self._load_audit()

    def _update_strength(self, pw: str):
        score = 0
        if len(pw) >= 8:            score += 1
        if re.search(r"[A-Z]", pw): score += 1
        if re.search(r"\d", pw):    score += 1
        if re.search(r"[!@#$%^&*()_+\-=\[\]{}|;':\",./<>?]", pw): score += 1
        labels = ["—", "Weak", "Fair", "Good", "Strong"]
        colors = ["rgba(255,255,255,0.35)", "#ff5b5b",
                  "#f5b335", "#4f8cff", "#34d399"]
        self._strength_lbl.setText(f"Password strength: {labels[score]}")
        self._strength_lbl.setStyleSheet(
            f"color:{colors[score]}; font-size:11px; background:transparent;"
        )

    def _on_change_password(self):
        cur_pw = self._cur_pw.text()
        new_pw = self._new_pw1.text()
        cfm_pw = self._new_pw2.text()

        if not cur_pw:
            self._set_pw_feedback("Enter your current password.", error=True)
            return
        if new_pw != cfm_pw:
            self._set_pw_feedback("New passwords do not match.", error=True)
            return
        err = _validate_password(new_pw)
        if err:
            self._set_pw_feedback(err, error=True)
            return

        conn = DataStore.get().db_conn
        user = AuthService.current_user()
        if not conn or not user:
            self._set_pw_feedback("Session error. Please log in again.", error=True)
            return

        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT password_hash FROM public.users WHERE user_id=%s",
                    (user["user_id"],),
                )
                row = cur.fetchone()
            if not row or not _check_password(cur_pw, row[0]):
                self._set_pw_feedback("Current password is incorrect.", error=True)
                return

            new_hash = _hash_password(new_pw)
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE public.users SET password_hash=%s WHERE user_id=%s",
                    (new_hash, user["user_id"]),
                )
            conn.commit()

            self._cur_pw.clear()
            self._new_pw1.clear()
            self._new_pw2.clear()
            self._set_pw_feedback("✓  Password changed successfully.", error=False)

        except Exception as e:
            conn.rollback()
            self._set_pw_feedback(str(e), error=True)

    def _set_pw_feedback(self, text: str, error: bool = False):
        self._pw_feedback.setText(text)
        self._pw_feedback.setStyleSheet(
            f"color:{'#ff5b5b' if error else '#34d399'}; "
            f"font-size:11px; background:transparent;"
        )
        QTimer.singleShot(5000, lambda: self._pw_feedback.setText(""))

    def _load_audit(self):
        user = AuthService.current_user()
        if not user:
            return
        self._audit_loader = _AuditLoader(user["username"])
        self._audit_loader.finished.connect(self._on_audit_loaded)
        self._audit_loader.error.connect(lambda _: None)
        self._audit_loader.finished.connect(self._audit_loader.deleteLater)
        self._audit_loader.error.connect(self._audit_loader.deleteLater)
        self._audit_loader.start()

    def _on_audit_loaded(self, rows: list):
        self._audit_table.setRowCount(0)
        self._audit_table.setRowCount(len(rows))
        action_colors = {
            "LOGIN":        "#34d399",
            "LOGIN_FAILED": "#ff5b5b",
            "LOGOUT":       "#8b949e",
        }
        for i, (ts, action, desc, status) in enumerate(rows):
            ts_str = (ts.strftime("%b %d, %Y %H:%M")
                      if hasattr(ts, "strftime") else str(ts)[:16])

            for col, (text, color) in enumerate([
                (ts_str,   "rgba(255,255,255,0.50)"),
                (action,   action_colors.get(action, "#8b949e")),
                (desc or "—", "rgba(255,255,255,0.75)"),
                (status or "—",
                 "#34d399" if status == "SUCCESS" else "#ff5b5b"),
            ]):
                item = QTableWidgetItem(text)
                item.setForeground(QColor(color))
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                self._audit_table.setItem(i, col, item)
            self._audit_table.setRowHeight(i, 36)