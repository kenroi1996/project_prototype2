"""
ui/pages/settings/user_management_tab.py
===========================================
Settings page — Tab 1: User Management.
Add / edit role / disable-enable / permanently remove users.

Extracted verbatim from ui/pages/settings_page.py — no logic changes.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QGridLayout, QDialog,
)

from services.data_store import DataStore
from services.auth_service import AuthService
from services.password_utils import _validate_password, _hash_password
from ui.dialogs.confirmation_dialog import (
    ConfirmationDialog, show_error, show_info, show_warning,
)
from workers.settings_workers import _UserLoader, _UserDeleter
from ui.helpers.settings_render import (
    _ROLES, _ROLE_LABELS,
    _section_title, _card, _field_label, _input, _combo,
    _primary_btn, _ghost_btn, _danger_btn, _feedback,
    _status_badge, _role_badge,
)


class _UserManagementTab(QWidget):
    def __init__(self):
        super().__init__()
        self._users:   list[dict]        = []
        self._loader:  _UserLoader  | None = None
        self._deleter: _UserDeleter | None = None
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(20)

        # ── Add New User card ──────────────────────────────────────────
        add_card, add_lo = _card()
        add_lo.addWidget(_section_title("ADD NEW USER"))
        add_lo.addSpacing(4)

        grid = QGridLayout()
        grid.setSpacing(12)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        grid.setColumnStretch(3, 1)

        self._new_fullname  = _input("Full Name")
        self._new_username  = _input("Username")
        self._new_email     = _input("Email (optional)")
        self._new_office    = _input("Office / Department")
        self._new_role      = _combo([_ROLE_LABELS[r] for r in _ROLES])
        self._new_pw        = _input("Password", password=True)
        self._new_pw_conf   = _input("Confirm Password", password=True)

        for col, (lbl, w) in enumerate([
            ("Full Name",  self._new_fullname),
            ("Username",   self._new_username),
            ("Email",      self._new_email),
            ("Office",     self._new_office),
        ]):
            col_lo = QVBoxLayout()
            col_lo.setSpacing(4)
            col_lo.addWidget(_field_label(lbl))
            col_lo.addWidget(w)
            grid.addLayout(col_lo, 0, col)

        for col, (lbl, w) in enumerate([
            ("Role",              self._new_role),
            ("Password",          self._new_pw),
            ("Confirm Password",  self._new_pw_conf),
        ]):
            col_lo = QVBoxLayout()
            col_lo.setSpacing(4)
            col_lo.addWidget(_field_label(lbl))
            col_lo.addWidget(w)
            grid.addLayout(col_lo, 1, col)

        add_lo.addLayout(grid)

        self._add_feedback = _feedback()
        add_lo.addWidget(self._add_feedback)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._add_btn = _primary_btn("＋  Add User")
        self._add_btn.clicked.connect(self._on_add_user)
        btn_row.addWidget(self._add_btn)
        add_lo.addLayout(btn_row)

        root.addWidget(add_card)

        # ── User list card ─────────────────────────────────────────────
        list_card, list_lo = _card()
        list_header = QHBoxLayout()
        list_lo.addWidget(_section_title("ALL USERS"))
        list_header.addWidget(_section_title("ALL USERS"))
        list_header.addStretch()
        refresh_btn = _ghost_btn("↻  Refresh")
        refresh_btn.clicked.connect(self.load_users)
        list_header.addWidget(refresh_btn)

        list_lo.itemAt(0).widget().deleteLater()
        list_lo.insertLayout(0, list_header)

        self._table = QTableWidget()
        self._table.setObjectName("settingsTable")
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels([
            "Name", "Username", "Role", "Office", "Status",
            "Last Login", "Actions",
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setHighlightSections(False)
        self._table.setShowGrid(False)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            6, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setMinimumHeight(280)
        self._table.setStyleSheet("""
            QTableWidget#settingsTable {
                background-color: transparent; border: none;
                color: rgba(255,255,255,0.85); font-size: 12px;
                alternate-background-color: rgba(255,255,255,0.02);
                selection-background-color: rgba(79,140,255,0.12);
                gridline-color: transparent;
            }
            QTableWidget#settingsTable QHeaderView::section {
                background-color: rgba(255,255,255,0.04);
                color: rgba(255,255,255,0.40); font-size: 10px;
                font-weight: bold; border: none; padding: 8px 10px;
            }
            QScrollBar:vertical { background: transparent; width: 8px; }
            QScrollBar::handle:vertical {
                background: rgba(255,255,255,0.12); border-radius: 4px;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical { height: 0; }
        """)
        list_lo.addWidget(self._table)

        self._list_status = _feedback("", "#8b949e")
        list_lo.addWidget(self._list_status)

        root.addWidget(list_card)
        root.addStretch()

        self.load_users()

    # ── Load users ────────────────────────────────────────────────────

    def load_users(self):
        self._list_status.setText("Loading…")
        self._loader = _UserLoader()
        self._loader.finished.connect(self._on_users_loaded)
        self._loader.error.connect(
            lambda e: self._list_status.setText(f"Error: {e}"))
        self._loader.finished.connect(self._loader.deleteLater)
        self._loader.error.connect(self._loader.deleteLater)
        self._loader.start()

    def _on_users_loaded(self, users: list):
        self._users = users
        self._table.setRowCount(0)
        self._table.setRowCount(len(users))

        current_id = AuthService.current_user_id()

        for row_i, u in enumerate(users):
            name_item = QTableWidgetItem(u["full_name"])
            name_item.setTextAlignment(
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            self._table.setItem(row_i, 0, name_item)

            uname_item = QTableWidgetItem(u["username"])
            uname_item.setTextAlignment(
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            is_self = str(u["user_id"]) == str(current_id)
            if is_self:
                uname_item.setForeground(QColor("#4f8cff"))
                uname_item.setText(f'{u["username"]}  (you)')
            self._table.setItem(row_i, 1, uname_item)

            role_w = QWidget()
            role_lo = QHBoxLayout(role_w)
            role_lo.setContentsMargins(8, 0, 0, 0)
            role_lo.addWidget(_role_badge(u["role"]))
            role_lo.addStretch()
            self._table.setCellWidget(row_i, 2, role_w)

            office_item = QTableWidgetItem(u.get("office") or "—")
            office_item.setTextAlignment(
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            self._table.setItem(row_i, 3, office_item)

            status_w = QWidget()
            status_lo = QHBoxLayout(status_w)
            status_lo.setContentsMargins(8, 0, 0, 0)
            status_lo.addWidget(_status_badge(u["is_active"]))
            status_lo.addStretch()
            self._table.setCellWidget(row_i, 4, status_w)

            ll = u.get("last_login")
            if ll and hasattr(ll, "strftime"):
                ll_str = ll.strftime("%b %d, %Y %H:%M")
            elif ll:
                ll_str = str(ll)[:16]
            else:
                ll_str = "Never"
            ll_item = QTableWidgetItem(ll_str)
            ll_item.setTextAlignment(
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            ll_item.setForeground(QColor("rgba(255,255,255,0.40)"))
            self._table.setItem(row_i, 5, ll_item)

            # ── Actions cell ──────────────────────────────────────────
            actions_w  = QWidget()
            actions_lo = QHBoxLayout(actions_w)
            actions_lo.setContentsMargins(6, 2, 6, 2)
            actions_lo.setSpacing(6)

            # Role toggle
            role_btn = _ghost_btn(
                "→ Counselor" if u["role"] == "admin" else "→ Admin"
            )
            role_btn.setToolTip("Change role")
            role_btn.clicked.connect(
                lambda _, uid=u["user_id"], cur_role=u["role"]:
                self._toggle_role(uid, cur_role)
            )
            if is_self:
                role_btn.setEnabled(False)
                role_btn.setToolTip("Cannot change your own role")
            actions_lo.addWidget(role_btn)

            # Disable / Enable
            if u["is_active"]:
                dis_btn = _danger_btn("Disable")
                dis_btn.clicked.connect(
                    lambda _, uid=u["user_id"], name=u["full_name"]:
                    self._disable_user(uid, name)
                )
                if is_self:
                    dis_btn.setEnabled(False)
                    dis_btn.setToolTip("Cannot disable yourself")
                actions_lo.addWidget(dis_btn)
            else:
                en_btn = _ghost_btn("Enable")
                en_btn.setStyleSheet(en_btn.styleSheet().replace(
                    "rgba(255,255,255,0.65)", "#34d399"))
                en_btn.clicked.connect(
                    lambda _, uid=u["user_id"], name=u["full_name"]:
                    self._enable_user(uid, name)
                )
                actions_lo.addWidget(en_btn)

            # ── Remove (permanent delete) ─────────────────────────────
            rem_btn = _danger_btn("🗑 Remove")
            rem_btn.setToolTip("Permanently delete this account")
            rem_btn.clicked.connect(
                lambda _, uid=u["user_id"], name=u["full_name"],
                       uname=u["username"]:
                self._remove_user(uid, name, uname)
            )
            if is_self:
                rem_btn.setEnabled(False)
                rem_btn.setToolTip("Cannot delete your own account")
            actions_lo.addWidget(rem_btn)

            actions_lo.addStretch()
            self._table.setCellWidget(row_i, 6, actions_w)
            self._table.setRowHeight(row_i, 44)

        self._list_status.setText(
            f"{len(users)} user{'s' if len(users) != 1 else ''} registered"
        )

    # ── Add user ──────────────────────────────────────────────────────

    def _on_add_user(self):
        fn   = self._new_fullname.text().strip()
        un   = self._new_username.text().strip()
        pw   = self._new_pw.text()
        pw2  = self._new_pw_conf.text()
        role = _ROLES[self._new_role.currentIndex()]
        em   = self._new_email.text().strip()
        off  = self._new_office.text().strip()

        if not fn or not un:
            self._set_add_feedback("Full name and username are required.", error=True)
            return
        if not pw:
            self._set_add_feedback("Password is required.", error=True)
            return
        if pw != pw2:
            self._set_add_feedback("Passwords do not match.", error=True)
            return
        err = _validate_password(pw)
        if err:
            self._set_add_feedback(err, error=True)
            return

        conn = DataStore.get().db_conn
        if not conn:
            self._set_add_feedback("No database connection.", error=True)
            return

        try:
            pw_hash = _hash_password(pw)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.users
                        (username, password_hash, full_name, email, role, office)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (un, pw_hash, fn, em or None, role, off or None),
                )
            conn.commit()
            self._set_add_feedback(
                f"✓  User '{un}' created successfully.", error=False)
            for w in (self._new_fullname, self._new_username,
                      self._new_email, self._new_office,
                      self._new_pw, self._new_pw_conf):
                w.clear()
            self._new_role.setCurrentIndex(0)
            self.load_users()
        except Exception as e:
            conn.rollback()
            if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                self._set_add_feedback(
                    f"Username '{un}' already exists.", error=True)
            else:
                self._set_add_feedback(str(e), error=True)

    def _set_add_feedback(self, text: str, error: bool = False):
        self._add_feedback.setText(text)
        self._add_feedback.setStyleSheet(
            f"color:{'#ff5b5b' if error else '#34d399'}; "
            f"font-size:11px; background:transparent;"
        )
        QTimer.singleShot(4000, lambda: self._add_feedback.setText(""))

    # ── Role / disable / enable ───────────────────────────────────────

    def _toggle_role(self, user_id: int, current_role: str):
        new_role  = "counselor" if current_role == "admin" else "admin"
        new_label = _ROLE_LABELS[new_role]
        dlg = ConfirmationDialog(
            "Change Role",
            f"Change this user's role to {new_label}?",
            parent=self,
        )
        if not dlg.exec():
            return
        conn = DataStore.get().db_conn
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE public.users SET role=%s WHERE user_id=%s",
                    (new_role, user_id),
                )
            conn.commit()
            self.load_users()
        except Exception as e:
            conn.rollback()
            show_error(self, "Error", "Could not update role.", str(e))

    def _disable_user(self, user_id: int, name: str):
        dlg = ConfirmationDialog(
            "Disable Account",
            f"Disable {name}'s account?",
            detail="They will not be able to log in until re-enabled.",
            parent=self,
        )
        if not dlg.exec():
            return
        self._set_active(user_id, False)

    def _enable_user(self, user_id: int, name: str):
        self._set_active(user_id, True)

    def _set_active(self, user_id: int, active: bool):
        conn = DataStore.get().db_conn
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE public.users SET is_active=%s WHERE user_id=%s",
                    (active, user_id),
                )
            conn.commit()
            self.load_users()
        except Exception as e:
            conn.rollback()
            show_error(self, "Error", "Could not update account status.", str(e))

    # ── Remove user (permanent) ───────────────────────────────────────

    def _remove_user(self, user_id: int, name: str, username: str):
        """Two-step permanent account deletion with typed-username confirmation."""
        if self._deleter is not None and self._deleter.isRunning():
            show_warning(self, "Busy", "A deletion is already in progress.")
            return

        # ── Step 1: primary confirmation ──────────────────────────────
        dlg1 = ConfirmationDialog(
            "Remove User Account",
            f"Permanently remove \"{name}\" (@{username})?",
            detail=(
                "This will delete the account and all associated activity "
                "log entries. The user will immediately lose access to "
                "EarlyAlert. This action cannot be undone."
            ),
            parent=self,
        )
        if not dlg1.exec():
            return

        # ── Step 2: typed-username confirmation ───────────────────────
        confirm_dlg = QDialog(self)
        confirm_dlg.setWindowTitle("Confirm Permanent Deletion")
        confirm_dlg.setMinimumWidth(440)
        confirm_dlg.setStyleSheet("QDialog { background-color:#0e1120; }")

        dlg_lo = QVBoxLayout(confirm_dlg)
        dlg_lo.setContentsMargins(28, 24, 28, 20)
        dlg_lo.setSpacing(14)

        warn_icon = QLabel("⚠️  Final Warning")
        warn_icon.setStyleSheet(
            "color:#ff5b5b; font-size:15px; font-weight:700; background:transparent;")
        dlg_lo.addWidget(warn_icon)

        warn_text = QLabel(
            f"You are about to <b>permanently delete</b> the account "
            f"<b>@{username}</b>.<br><br>"
            f"Type <b>{username}</b> below to confirm:"
        )
        warn_text.setWordWrap(True)
        warn_text.setTextFormat(Qt.TextFormat.RichText)
        warn_text.setStyleSheet(
            "color:rgba(255,255,255,0.75); font-size:12px; background:transparent;")
        dlg_lo.addWidget(warn_text)

        confirm_input = QLineEdit()
        confirm_input.setPlaceholderText(f"Type {username} to confirm")
        confirm_input.setFixedHeight(36)
        confirm_input.setStyleSheet("""
            QLineEdit {
                background:rgba(255,91,91,0.08);
                border:1px solid rgba(255,91,91,0.35);
                border-radius:8px; color:#e8eaf0;
                font-size:13px; font-weight:600; padding:0 12px;
            }
            QLineEdit:focus { border-color:rgba(255,91,91,0.70); }
        """)
        dlg_lo.addWidget(confirm_input)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        cancel_btn = _ghost_btn("Cancel")
        cancel_btn.setFixedHeight(36)
        cancel_btn.clicked.connect(confirm_dlg.reject)

        delete_btn = QPushButton("🗑  Delete Permanently")
        delete_btn.setFixedHeight(36)
        delete_btn.setEnabled(False)
        delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff5b5b;
                border: none; border-radius: 8px;
                color: white; font-size: 12px;
                font-weight: 700; padding: 0 20px;
            }
            QPushButton:hover    { background-color: #e04444; }
            QPushButton:disabled {
                background-color: rgba(255,91,91,0.18);
                color: rgba(255,91,91,0.40);
            }
        """)
        delete_btn.clicked.connect(confirm_dlg.accept)

        # Enable delete button only when the username matches exactly
        confirm_input.textChanged.connect(
            lambda txt: delete_btn.setEnabled(txt.strip() == username)
        )

        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(delete_btn)
        dlg_lo.addLayout(btn_row)

        if not confirm_dlg.exec():
            return

        # ── Execute deletion via background worker ────────────────────
        self._deleter = _UserDeleter(user_id)
        self._deleter.finished.connect(self._on_user_removed)
        self._deleter.error.connect(self._on_remove_error)
        self._deleter.finished.connect(self._clear_deleter)
        self._deleter.error.connect(self._clear_deleter)
        self._deleter.start()

    def _clear_deleter(self):
        w = self._deleter
        self._deleter = None
        if w is not None:
            try:
                w.quit()
                w.wait(500)
                w.deleteLater()
            except RuntimeError:
                pass

    def _on_user_removed(self):
        show_info(self, "Account Removed",
                  "The user account has been permanently deleted.")
        self.load_users()

    def _on_remove_error(self, msg: str):
        show_error(self, "Deletion Failed",
                   "Could not remove the user account.", msg)