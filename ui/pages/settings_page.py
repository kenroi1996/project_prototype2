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

import re
from datetime import datetime

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QLineEdit, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QStackedWidget, QTextEdit,
    QScrollArea, QGridLayout, QCheckBox, QSlider, QSizePolicy,
    QSpacerItem, QDialog,
)

from services.data_store import DataStore
from services.system_config import SystemConfig
from services.auth_service import AuthService
from ui.dialogs.confirmation_dialog import (
    ConfirmationDialog, show_error, show_info, show_warning,
)


# ── Constants ─────────────────────────────────────────────────────────────────
_ROLES = ["admin", "counselor"]
_ROLE_LABELS = {"admin": "Administrator", "counselor": "Counselor"}
_ROLE_COLORS = {"admin": "#4f8cff", "counselor": "#34d399"}

_DEFAULT_INSTITUTION = "CTU-Daanbantayan"
_DEFAULT_AY          = "2024-2025"
_DEFAULT_SEM         = "1"

_PW_MIN_LEN   = 8
_PW_RULES_TXT = (
    "At least 8 characters · 1 uppercase · 1 number · 1 special character"
)


# ── Password validation ───────────────────────────────────────────────────────
def _validate_password(pw: str) -> str | None:
    if len(pw) < _PW_MIN_LEN:
        return f"Password must be at least {_PW_MIN_LEN} characters."
    if not re.search(r"[A-Z]", pw):
        return "Password must contain at least one uppercase letter."
    if not re.search(r"\d", pw):
        return "Password must contain at least one number."
    if not re.search(r"[!@#$%^&*()_+\-=\[\]{}|;':\",./<>?]", pw):
        return "Password must contain at least one special character."
    return None


def _hash_password(plain: str) -> str:
    import bcrypt
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def _check_password(plain: str, hashed: str) -> bool:
    import bcrypt
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ── Background workers ────────────────────────────────────────────────────────

class _UserLoader(QThread):
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No database connection.")
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    ALTER TABLE public.users
                    ADD COLUMN IF NOT EXISTS last_login TIMESTAMP
                """)
                conn.commit()
                cur.execute("""
                    SELECT user_id, username, full_name, email,
                           role, office, is_active, created_at, last_login
                    FROM   public.users
                    ORDER  BY created_at ASC
                """)
                rows = [dict(zip(
                    ["user_id","username","full_name","email",
                     "role","office","is_active","created_at","last_login"],
                    r
                )) for r in cur.fetchall()]
            self.finished.emit(rows)
        except Exception as e:
            self.error.emit(str(e))


class _UserDeleter(QThread):
    """
    Permanently removes a user account and all their activity log entries.
    Never call this on the currently-logged-in admin.
    """
    finished = pyqtSignal()
    error    = pyqtSignal(str)

    def __init__(self, user_id: int):
        super().__init__()
        self._user_id = user_id

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No database connection.")
            return
        try:
            with conn.cursor() as cur:
                # Resolve username first (needed for activity_log lookup)
                cur.execute(
                    "SELECT username FROM public.users WHERE user_id = %s",
                    (self._user_id,),
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError(
                        "User not found — may have already been deleted.")
                username = row[0]

                # ── Null out FK references so the DELETE can proceed ──
                # interventions.counselor_id → users.user_id
                cur.execute(
                    """
                    UPDATE public.interventions
                    SET    counselor_id = NULL
                    WHERE  counselor_id = %s
                    """,
                    (self._user_id,),
                )

                # Add further FK nullifications here if other tables
                # reference users.user_id in the future, e.g.:
                #   UPDATE public.some_table SET user_id = NULL
                #   WHERE user_id = %s

                # ── Remove activity log rows by username ──────────────
                cur.execute(
                    "DELETE FROM public.activity_log WHERE user_name = %s",
                    (username,),
                )

                # ── Delete the user row ───────────────────────────────
                cur.execute(
                    "DELETE FROM public.users WHERE user_id = %s",
                    (self._user_id,),
                )
                if cur.rowcount == 0:
                    raise ValueError(
                        "User not found — may have already been deleted.")

            conn.commit()
            self.finished.emit()
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            self.error.emit(str(exc))


class _ConfigLoader(QThread):
    finished = pyqtSignal(dict)
    error    = pyqtSignal(str)

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No DB connection.")
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS public.system_config (
                        key        VARCHAR(100) PRIMARY KEY,
                        value      TEXT,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_by VARCHAR(100)
                    )
                """)
                conn.commit()
                cur.execute("SELECT key, value FROM public.system_config")
                cfg = {r[0]: r[1] for r in cur.fetchall()}
            self.finished.emit(cfg)
        except Exception as e:
            self.error.emit(str(e))


class _AuditLoader(QThread):
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def __init__(self, username: str):
        super().__init__()
        self._username = username

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No DB connection.")
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT log_timestamp, action, description, status
                    FROM   public.activity_log
                    WHERE  user_name = %s
                      AND  action IN ('LOGIN', 'LOGIN_FAILED', 'LOGOUT')
                    ORDER  BY log_timestamp DESC
                    LIMIT  20
                """, (self._username,))
                rows = cur.fetchall()
            self.finished.emit(rows)
        except Exception as e:
            self.error.emit(str(e))


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


# =============================================================================
# TAB 1 — USER MANAGEMENT
# =============================================================================

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


# =============================================================================
# TAB 2 — SECURITY
# =============================================================================

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


# =============================================================================
# TAB 3 — SYSTEM CONFIGURATION
# =============================================================================

class _SystemConfigTab(QWidget):
    def __init__(self):
        super().__init__()
        self._loader: _ConfigLoader | None = None
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(20)

        inst_card, inst_lo = _card()
        inst_lo.addWidget(_section_title("INSTITUTION"))
        inst_lo.addSpacing(4)

        grid = QGridLayout()
        grid.setSpacing(12)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)

        self._inst_name = _input("Institution name")
        self._inst_name.setText(_DEFAULT_INSTITUTION)
        self._def_ay = _combo(
            ["2022-2023", "2023-2024", "2024-2025", "2025-2026", "2026-2027"]
        )
        self._def_ay.setCurrentText(_DEFAULT_AY)
        self._def_sem = _combo(["1st Semester", "2nd Semester"])

        for col, (lbl, w) in enumerate([
            ("Institution Name",       self._inst_name),
            ("Default Academic Year",  self._def_ay),
            ("Default Semester",       self._def_sem),
        ]):
            cl = QVBoxLayout()
            cl.setSpacing(4)
            cl.addWidget(_field_label(lbl))
            cl.addWidget(w)
            grid.addLayout(cl, 0, col)

        inst_lo.addLayout(grid)
        self._inst_feedback = _feedback()
        inst_lo.addWidget(self._inst_feedback)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_inst_btn = _primary_btn("💾  Save")
        save_inst_btn.clicked.connect(self._save_institution)
        btn_row.addWidget(save_inst_btn)
        inst_lo.addLayout(btn_row)
        root.addWidget(inst_card)

        risk_card, risk_lo = _card()
        risk_lo.addWidget(_section_title("RISK THRESHOLDS"))
        hint = QLabel(
            "Adjust the probability cutoffs used to classify students into "
            "High Risk and Moderate Risk. Students below the Moderate threshold "
            "are classified as Low Risk."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(
            "color: rgba(255,255,255,0.40); font-size:11px; background:transparent;"
        )
        risk_lo.addWidget(hint)
        risk_lo.addWidget(_divider())

        self._high_thresh_lbl = QLabel("High Risk threshold:  50%")
        self._high_thresh_lbl.setStyleSheet(
            "color:#e8eaf0; font-size:12px; background:transparent;")
        self._high_slider = QSlider(Qt.Orientation.Horizontal)
        self._high_slider.setRange(30, 90)
        self._high_slider.setValue(50)
        self._high_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                background:rgba(255,255,255,0.08); height:6px; border-radius:3px;
            }
            QSlider::handle:horizontal {
                background:#ff5b5b; width:16px; height:16px;
                margin:-5px 0; border-radius:8px;
            }
            QSlider::sub-page:horizontal { background:#ff5b5b; border-radius:3px; }
        """)
        self._high_slider.valueChanged.connect(
            lambda v: self._high_thresh_lbl.setText(
                f"High Risk threshold:  {v}%"))

        self._mod_thresh_lbl = QLabel("Moderate Risk threshold:  25%")
        self._mod_thresh_lbl.setStyleSheet(
            "color:#e8eaf0; font-size:12px; background:transparent;")
        self._mod_slider = QSlider(Qt.Orientation.Horizontal)
        self._mod_slider.setRange(10, 60)
        self._mod_slider.setValue(25)
        self._mod_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                background:rgba(255,255,255,0.08); height:6px; border-radius:3px;
            }
            QSlider::handle:horizontal {
                background:#f5b335; width:16px; height:16px;
                margin:-5px 0; border-radius:8px;
            }
            QSlider::sub-page:horizontal { background:#f5b335; border-radius:3px; }
        """)
        self._mod_slider.valueChanged.connect(
            lambda v: self._mod_thresh_lbl.setText(
                f"Moderate Risk threshold:  {v}%"))

        for lbl, slider in [
            (self._high_thresh_lbl, self._high_slider),
            (self._mod_thresh_lbl,  self._mod_slider),
        ]:
            risk_lo.addWidget(lbl)
            risk_lo.addWidget(slider)

        self._thresh_feedback = _feedback()
        risk_lo.addWidget(self._thresh_feedback)

        btn_row2 = QHBoxLayout()
        btn_row2.addStretch()
        save_thresh_btn = _primary_btn("💾  Save Thresholds", color="#f5b335")
        save_thresh_btn.clicked.connect(self._save_thresholds)
        btn_row2.addWidget(save_thresh_btn)
        risk_lo.addLayout(btn_row2)
        root.addWidget(risk_card)

        ollama_card, ollama_lo = _card()
        ollama_lo.addWidget(_section_title("AI ADVISOR (OLLAMA)"))
        hint_ai = QLabel(
            "Configure the local Ollama server used for AI intervention "
            "recommendations. Ollama must be running on this machine. "
            "Default model: qwen3:4b"
        )
        hint_ai.setWordWrap(True)
        hint_ai.setStyleSheet(
            "color: rgba(255,255,255,0.40); font-size:11px; background:transparent;"
        )
        ollama_lo.addWidget(hint_ai)
        ollama_lo.addWidget(_divider())

        ai_grid = QGridLayout()
        ai_grid.setSpacing(12)
        ai_grid.setColumnStretch(0, 2)
        ai_grid.setColumnStretch(1, 1)

        self._ollama_url   = _input("http://localhost:11434")
        self._ollama_url.setText("http://localhost:11434")
        self._ollama_model = _input("e.g. qwen3:4b")
        self._ollama_model.setText("qwen3:4b")

        for col, (lbl, w) in enumerate([
            ("Ollama Server URL", self._ollama_url),
            ("Model Name",        self._ollama_model),
        ]):
            cl = QVBoxLayout()
            cl.setSpacing(4)
            cl.addWidget(_field_label(lbl))
            cl.addWidget(w)
            ai_grid.addLayout(cl, 0, col)

        ollama_lo.addLayout(ai_grid)
        self._ollama_feedback = _feedback()
        ollama_lo.addWidget(self._ollama_feedback)

        ai_btn_row = QHBoxLayout()
        ai_btn_row.setSpacing(10)
        test_ollama_btn = _primary_btn("⚡  Test Connection", color="#4f8cff")
        test_ollama_btn.clicked.connect(self._test_ollama)
        ai_btn_row.addWidget(test_ollama_btn)
        ai_btn_row.addStretch()
        save_ai_btn = _primary_btn("💾  Save")
        save_ai_btn.clicked.connect(self._save_ollama)
        ai_btn_row.addWidget(save_ai_btn)
        ollama_lo.addLayout(ai_btn_row)
        root.addWidget(ollama_card)
        root.addStretch()

        self._load_config()

    def _load_config(self):
        self._loader = _ConfigLoader()
        self._loader.finished.connect(self._on_config_loaded)
        self._loader.error.connect(lambda _: None)
        self._loader.finished.connect(self._loader.deleteLater)
        self._loader.error.connect(self._loader.deleteLater)
        self._loader.start()

    def _on_config_loaded(self, cfg: dict):
        if "institution_name"       in cfg: self._inst_name.setText(cfg["institution_name"])
        if "default_academic_year"  in cfg: self._def_ay.setCurrentText(cfg["default_academic_year"])
        if "default_semester"       in cfg:
            self._def_sem.setCurrentIndex(0 if cfg["default_semester"] == "1" else 1)
        if "risk_high_threshold"    in cfg: self._high_slider.setValue(int(cfg["risk_high_threshold"]))
        if "risk_moderate_threshold" in cfg: self._mod_slider.setValue(int(cfg["risk_moderate_threshold"]))
        if "ollama_url"             in cfg: self._ollama_url.setText(cfg["ollama_url"])
        if "ollama_model"           in cfg: self._ollama_model.setText(cfg["ollama_model"])

    def _upsert_config(self, key: str, value: str):
        conn = DataStore.get().db_conn
        user = AuthService.current_username() or "system"
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.system_config (key, value, updated_at, updated_by)
                VALUES (%s, %s, NOW(), %s)
                ON CONFLICT (key) DO UPDATE
                    SET value=EXCLUDED.value,
                        updated_at=NOW(),
                        updated_by=EXCLUDED.updated_by
            """, (key, value, user))
        conn.commit()

    def _save_institution(self):
        name = self._inst_name.text().strip()
        if not name:
            self._set_inst_feedback("Institution name cannot be empty.", error=True)
            return
        ay  = self._def_ay.currentText()
        sem = "1" if self._def_sem.currentIndex() == 0 else "2"
        try:
            self._upsert_config("institution_name",      name)
            self._upsert_config("default_academic_year", ay)
            self._upsert_config("default_semester",      sem)
            SystemConfig.reload(DataStore.get().db_conn)
            self._set_inst_feedback("✓  Settings saved.", error=False)
        except Exception as e:
            self._set_inst_feedback(str(e), error=True)

    def _save_thresholds(self):
        high = self._high_slider.value()
        mod  = self._mod_slider.value()
        if mod >= high:
            self._set_thresh_feedback(
                "Moderate Risk threshold must be below High Risk threshold.",
                error=True)
            return
        try:
            self._upsert_config("risk_high_threshold",     str(high))
            self._upsert_config("risk_moderate_threshold", str(mod))
            self._set_thresh_feedback("✓  Thresholds saved.", error=False)
        except Exception as e:
            self._set_thresh_feedback(str(e), error=True)

    def _save_ollama(self):
        url   = self._ollama_url.text().strip()
        model = self._ollama_model.text().strip()
        if not url:
            self._set_ollama_feedback("Ollama URL cannot be empty.", error=True)
            return
        if not model:
            self._set_ollama_feedback("Model name cannot be empty.", error=True)
            return
        try:
            self._upsert_config("ollama_url",   url)
            self._upsert_config("ollama_model", model)
            SystemConfig.reload(DataStore.get().db_conn)
            self._set_ollama_feedback("✓  Ollama settings saved.", error=False)
        except Exception as e:
            self._set_ollama_feedback(str(e), error=True)

    def _test_ollama(self):
        url   = self._ollama_url.text().strip()
        model = self._ollama_model.text().strip()
        self._set_ollama_feedback("Testing connection…", error=False)
        try:
            import requests
            resp = requests.post(
                f"{url}/api/generate",
                json={"model": model, "prompt": "Say OK", "stream": False},
                timeout=10,
            )
            if resp.status_code == 200:
                self._set_ollama_feedback(
                    f"✓  Connected — {model} responded.", error=False)
            else:
                self._set_ollama_feedback(
                    f"⚠ HTTP {resp.status_code}: {resp.text[:80]}", error=True)
        except Exception as e:
            self._set_ollama_feedback(f"⚠ {e}", error=True)

    def _set_ollama_feedback(self, text: str, error: bool = False):
        self._ollama_feedback.setText(text)
        self._ollama_feedback.setStyleSheet(
            f"color:{'#ff5b5b' if error else '#34d399'}; font-size:11px; background:transparent;")
        if not error:
            QTimer.singleShot(4000, lambda: self._ollama_feedback.setText(""))

    def _set_inst_feedback(self, text: str, error: bool = False):
        self._inst_feedback.setText(text)
        self._inst_feedback.setStyleSheet(
            f"color:{'#ff5b5b' if error else '#34d399'}; font-size:11px; background:transparent;")
        QTimer.singleShot(4000, lambda: self._inst_feedback.setText(""))

    def _set_thresh_feedback(self, text: str, error: bool = False):
        self._thresh_feedback.setText(text)
        self._thresh_feedback.setStyleSheet(
            f"color:{'#ff5b5b' if error else '#34d399'}; font-size:11px; background:transparent;")
        QTimer.singleShot(4000, lambda: self._thresh_feedback.setText(""))


# =============================================================================
# TAB 4 — ABOUT
# =============================================================================

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


# =============================================================================
# TAB 5 — ACTIVITY LOGS
# =============================================================================

class _AllActivityLoader(QThread):
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def __init__(self, filters: dict):
        super().__init__()
        self._filters = filters

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No database connection.")
            return
        try:
            f       = self._filters
            clauses = []
            params  = []
            if f.get("username"):
                clauses.append("al.user_name ILIKE %s")
                params.append(f"%{f['username']}%")
            if f.get("action"):
                clauses.append("al.action = %s")
                params.append(f["action"])
            if f.get("status"):
                clauses.append("al.status = %s")
                params.append(f["status"])
            if f.get("date_from"):
                clauses.append("al.log_timestamp >= %s")
                params.append(f["date_from"])
            if f.get("date_to"):
                clauses.append("al.log_timestamp <= %s")
                params.append(f["date_to"] + " 23:59:59")
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            sql = f"""
                SELECT log_id, log_timestamp, user_name, action,
                       description, status
                FROM   public.activity_log al
                {where}
                ORDER  BY al.log_timestamp DESC
                LIMIT  500
            """
            with conn.cursor() as cur:
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description]
                self.finished.emit([dict(zip(cols, r)) for r in cur.fetchall()])
        except Exception as exc:
            self.error.emit(str(exc))


class _ActivityLogCleaner(QThread):
    finished = pyqtSignal(int)
    error    = pyqtSignal(str)

    def __init__(self, ids: list[int] | None = None,
                 filters: dict | None = None):
        super().__init__()
        self._ids     = ids
        self._filters = filters or {}

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No database connection.")
            return
        try:
            with conn.cursor() as cur:
                if self._ids is not None:
                    cur.execute(
                        "DELETE FROM public.activity_log "
                        "WHERE log_id = ANY(%s) RETURNING log_id",
                        (self._ids,)
                    )
                else:
                    f       = self._filters
                    clauses = []
                    params  = []
                    if f.get("username"):
                        clauses.append("user_name ILIKE %s")
                        params.append(f"%{f['username']}%")
                    if f.get("action"):
                        clauses.append("action = %s")
                        params.append(f["action"])
                    if f.get("status"):
                        clauses.append("status = %s")
                        params.append(f["status"])
                    if f.get("date_from"):
                        clauses.append("log_timestamp >= %s")
                        params.append(f["date_from"])
                    if f.get("date_to"):
                        clauses.append("log_timestamp <= %s")
                        params.append(f["date_to"] + " 23:59:59")
                    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
                    cur.execute(
                        f"DELETE FROM public.activity_log {where} RETURNING log_id",
                        params
                    )
                deleted = len(cur.fetchall())
            conn.commit()
            self.finished.emit(deleted)
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            self.error.emit(str(exc))


class _ActivityLogsTab(QWidget):
    PAGE_SIZE = 25

    _ACTIONS = [
        "All Actions",
        "LOGIN", "LOGOUT", "LOGIN_FAILED",
        "PREDICTION_RUN", "MODEL_TRAINED",
        "DATA_UPLOAD", "DATA_MERGE",
        "INTERVENTION_SAVED", "PASSWORD_CHANGED",
        "USER_CREATED", "ROLE_CHANGED", "USER_DISABLED",
    ]
    _STATUSES = ["All Statuses", "SUCCESS", "FAILED", "INFO", "WARNING"]

    def __init__(self):
        super().__init__()
        self._all_rows: list[dict]          = []
        self._page      = 0
        self._loader:   _AllActivityLoader  | None = None
        self._cleaner:  _ActivityLogCleaner | None = None
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(20)

        filter_card, filter_lo = _card()
        filter_lo.addWidget(_section_title("FILTER LOGS"))
        filter_lo.addSpacing(4)

        row1 = QHBoxLayout(); row1.setSpacing(12)

        self._f_username  = _input("Username")
        self._f_username.setFixedHeight(32)

        _cb_ss = """
            QComboBox {
                background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.10);
                border-radius:7px; color:#e8eaf0;
                font-size:12px; padding:0 10px;
            }
            QComboBox:hover { border-color:rgba(79,140,255,0.40); }
            QComboBox::drop-down { border:none; width:16px; }
            QComboBox QAbstractItemView {
                background:#1a1f35; color:#e8eaf0;
                selection-background-color:rgba(79,140,255,0.18);
            }
        """
        self._f_action = QComboBox()
        self._f_action.addItems(self._ACTIONS)
        self._f_action.setFixedHeight(32)
        self._f_action.setStyleSheet(_cb_ss)

        self._f_status = QComboBox()
        self._f_status.addItems(self._STATUSES)
        self._f_status.setFixedHeight(32)
        self._f_status.setStyleSheet(_cb_ss)

        self._f_date_from = _input("From (YYYY-MM-DD)")
        self._f_date_from.setFixedHeight(32)
        self._f_date_to   = _input("To (YYYY-MM-DD)")
        self._f_date_to.setFixedHeight(32)

        clear_btn = _ghost_btn("✕  Clear")
        clear_btn.setFixedHeight(32)
        clear_btn.clicked.connect(self._clear_filters)

        load_btn = _primary_btn("🔍  Search")
        load_btn.setFixedHeight(32)
        load_btn.clicked.connect(self._load)

        for lbl_txt, w in [
            ("Username",  self._f_username),
            ("Action",    self._f_action),
            ("Status",    self._f_status),
            ("Date From", self._f_date_from),
            ("Date To",   self._f_date_to),
        ]:
            col = QVBoxLayout(); col.setSpacing(3)
            lbl = QLabel(lbl_txt)
            lbl.setStyleSheet(
                "color:rgba(255,255,255,0.40); font-size:10px; background:transparent;")
            col.addWidget(lbl); col.addWidget(w)
            row1.addLayout(col)

        row1.addWidget(clear_btn, 0, Qt.AlignmentFlag.AlignBottom)
        row1.addWidget(load_btn,  0, Qt.AlignmentFlag.AlignBottom)
        row1.addStretch()
        filter_lo.addLayout(row1)
        root.addWidget(filter_card)

        self._batch_frame = QFrame()
        self._batch_frame.setObjectName("logBatchBar")
        self._batch_frame.setStyleSheet("""
            QFrame#logBatchBar {
                background:rgba(255,91,91,0.06);
                border:1px solid rgba(255,91,91,0.18); border-radius:8px;
            }
        """)
        self._batch_frame.setVisible(False)
        bb = QHBoxLayout(self._batch_frame)
        bb.setContentsMargins(12, 8, 12, 8); bb.setSpacing(10)

        self._sel_lbl = QLabel("0 selected")
        self._sel_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.55); font-size:12px; background:transparent;")

        self._del_sel_btn = QPushButton("🗑  Delete Selected")
        self._del_sel_btn.setFixedHeight(30)
        self._del_sel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._del_sel_btn.setStyleSheet("""
            QPushButton {
                background:rgba(255,91,91,0.14);
                border:1px solid rgba(255,91,91,0.35); border-radius:7px;
                color:#ff5b5b; font-size:12px; font-weight:600; padding:0 14px;
            }
            QPushButton:hover { background:rgba(255,91,91,0.26); }
        """)
        self._del_sel_btn.clicked.connect(self._on_delete_selected)

        self._del_all_btn = QPushButton("🗑  Delete All Filtered")
        self._del_all_btn.setFixedHeight(30)
        self._del_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._del_all_btn.setStyleSheet("""
            QPushButton {
                background:rgba(255,91,91,0.08);
                border:1px solid rgba(255,91,91,0.25); border-radius:7px;
                color:rgba(255,91,91,0.75); font-size:12px;
                font-weight:600; padding:0 14px;
            }
            QPushButton:hover { background:rgba(255,91,91,0.20); }
        """)
        self._del_all_btn.clicked.connect(self._on_delete_all_filtered)

        desel_btn = _ghost_btn("✕  Deselect All")
        desel_btn.setFixedHeight(30)
        desel_btn.clicked.connect(self._deselect_all)

        bb.addWidget(self._sel_lbl)
        bb.addWidget(self._del_sel_btn)
        bb.addWidget(self._del_all_btn)
        bb.addStretch()
        bb.addWidget(desel_btn)
        root.addWidget(self._batch_frame)

        log_card, log_lo = _card()
        log_hdr = QHBoxLayout()
        log_hdr.addWidget(_section_title("ACTIVITY LOGS  (latest 500)"))
        log_hdr.addStretch()
        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;")
        log_hdr.addWidget(self._count_lbl)
        log_lo.addLayout(log_hdr)

        self._table = QTableWidget()
        self._table.setObjectName("settingsTable")
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels([
            "☐", "Timestamp", "User", "Action", "Description", "Status"
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setHighlightSections(False)
        self._table.setShowGrid(False)
        self._table.horizontalHeader().sectionClicked.connect(
            self._on_header_clicked)

        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for col, mode, width in [
            (0, QHeaderView.ResizeMode.Fixed,            32),
            (1, QHeaderView.ResizeMode.ResizeToContents, 0),
            (2, QHeaderView.ResizeMode.Fixed,            110),
            (3, QHeaderView.ResizeMode.ResizeToContents, 0),
            (5, QHeaderView.ResizeMode.ResizeToContents, 0),
        ]:
            hh.setSectionResizeMode(col, mode)
            if width:
                self._table.setColumnWidth(col, width)

        self._table.setStyleSheet("""
            QTableWidget#settingsTable {
                background:transparent; border:none;
                color:#e8eaf0; font-size:12px;
                alternate-background-color:rgba(255,255,255,0.025);
                selection-background-color:transparent;
                gridline-color:transparent;
            }
            QTableWidget#settingsTable QHeaderView::section {
                background:rgba(255,255,255,0.05);
                color:rgba(255,255,255,0.55); font-size:11px;
                font-weight:bold; border:none; padding:8px 6px;
            }
        """)
        self._table.setMinimumHeight(320)
        log_lo.addWidget(self._table, 1)

        pag = QHBoxLayout(); pag.setSpacing(8)
        self._prev_btn = _ghost_btn("‹  Prev")
        self._prev_btn.setFixedHeight(28)
        self._prev_btn.clicked.connect(self._prev_page)
        self._prev_btn.setEnabled(False)

        self._page_lbl = QLabel("Page 1 of 1")
        self._page_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;")
        self._page_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_lbl.setFixedWidth(100)

        self._next_btn = _ghost_btn("Next  ›")
        self._next_btn.setFixedHeight(28)
        self._next_btn.clicked.connect(self._next_page)
        self._next_btn.setEnabled(False)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;")

        pag.addWidget(self._prev_btn)
        pag.addWidget(self._page_lbl)
        pag.addWidget(self._next_btn)
        pag.addStretch()
        pag.addWidget(self._status_lbl)
        log_lo.addLayout(pag)
        root.addWidget(log_card)

        QTimer.singleShot(100, self._load)

    def _checked_ids(self) -> list[int]:
        ids   = []
        start = self._page * self.PAGE_SIZE
        for ri in range(self._table.rowCount()):
            cell = self._table.cellWidget(ri, 0)
            cb   = (cell if isinstance(cell, QPushButton)
                    else cell.findChild(QPushButton) if cell else None)
            if cb and cb.isChecked():
                row = self._all_rows[start + ri]
                if row.get("log_id") is not None:
                    ids.append(int(row["log_id"]))
        return ids

    def _update_batch_bar(self):
        n = len(self._checked_ids())
        self._sel_lbl.setText(
            f"{n} row{'s' if n != 1 else ''} selected on this page")
        self._batch_frame.setVisible(n > 0)

    def _on_header_clicked(self, col: int):
        if col != 0:
            return
        def _get_cb(r):
            cell = self._table.cellWidget(r, 0)
            if isinstance(cell, QPushButton): return cell
            return cell.findChild(QPushButton) if cell else None
        any_unchecked = any(
            not cb.isChecked()
            for r in range(self._table.rowCount())
            if (cb := _get_cb(r)) is not None
        )
        for r in range(self._table.rowCount()):
            cb = _get_cb(r)
            if cb:
                cb.setChecked(any_unchecked)
        self._update_batch_bar()

    def _deselect_all(self):
        for r in range(self._table.rowCount()):
            cell = self._table.cellWidget(r, 0)
            cb   = (cell if isinstance(cell, QPushButton)
                    else cell.findChild(QPushButton) if cell else None)
            if cb:
                cb.setChecked(False)
        self._update_batch_bar()

    def _build_filters(self) -> dict:
        action = self._f_action.currentText()
        status = self._f_status.currentText()
        return {
            "username":  self._f_username.text().strip(),
            "action":    action if action != "All Actions"  else "",
            "status":    status if status != "All Statuses" else "",
            "date_from": self._f_date_from.text().strip(),
            "date_to":   self._f_date_to.text().strip(),
        }

    def _clear_filters(self):
        self._f_username.clear()
        self._f_date_from.clear()
        self._f_date_to.clear()
        self._f_action.setCurrentIndex(0)
        self._f_status.setCurrentIndex(0)
        self._load()

    def _load(self):
        if self._loader is not None:
            try:
                self._loader.finished.disconnect()
                self._loader.error.disconnect()
                if self._loader.isRunning():
                    self._loader.quit(); self._loader.wait(1000)
                self._loader.deleteLater()
            except RuntimeError:
                pass
            self._loader = None
        self._status_lbl.setText("Loading…")
        self._loader = _AllActivityLoader(self._build_filters())
        self._loader.finished.connect(self._on_loaded)
        self._loader.error.connect(lambda e: self._status_lbl.setText(f"⚠ {e}"))
        self._loader.finished.connect(self._clear_loader)
        self._loader.error.connect(self._clear_loader)
        self._loader.start()

    def _clear_loader(self):
        w = self._loader
        self._loader = None
        if w is not None:
            try: w.deleteLater()
            except RuntimeError: pass

    def _on_loaded(self, rows: list):
        self._all_rows = rows
        self._page     = 0
        self._batch_frame.setVisible(False)
        self._render_page()
        self._status_lbl.setText("")

    def _total_pages(self):
        return max(1, (len(self._all_rows) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)

    def _prev_page(self):
        if self._page > 0:
            self._page -= 1; self._render_page()

    def _next_page(self):
        if self._page < self._total_pages() - 1:
            self._page += 1; self._render_page()

    def _render_page(self):
        start = self._page * self.PAGE_SIZE
        rows  = self._all_rows[start:start + self.PAGE_SIZE]
        total = len(self._all_rows)
        pages = self._total_pages()

        self._count_lbl.setText(f"{total:,} record{'s' if total != 1 else ''}")
        self._page_lbl.setText(f"Page {self._page+1} of {pages}")
        self._prev_btn.setEnabled(self._page > 0)
        self._next_btn.setEnabled(self._page < pages - 1)

        self._table.setRowCount(0)
        self._table.setRowCount(len(rows))
        self._table.setUpdatesEnabled(False)

        _action_colors = {
            "LOGIN": "#34d399", "LOGOUT": "#8b949e", "LOGIN_FAILED": "#ff5b5b",
            "PREDICTION_RUN": "#4f8cff", "MODEL_TRAINED": "#4f8cff",
            "DATA_UPLOAD": "#a78bfa", "DATA_MERGE": "#a78bfa",
            "INTERVENTION_SAVED": "#f5b335", "PASSWORD_CHANGED": "#f5b335",
            "USER_CREATED": "#34d399", "ROLE_CHANGED": "#f5b335",
            "USER_DISABLED": "#ff5b5b",
        }
        _status_colors = {
            "SUCCESS": "#34d399", "FAILED": "#ff5b5b",
            "INFO": "#4f8cff",    "WARNING": "#f5b335",
        }

        for ri, row in enumerate(rows):
            ts     = row.get("log_timestamp")
            ts_str = (ts.strftime("%b %d, %Y %H:%M")
                      if hasattr(ts, "strftime") else str(ts or "—")[:16])
            user   = str(row.get("user_name")    or "—")
            action = str(row.get("action")        or "—")
            desc   = str(row.get("description")   or "—")
            status = str(row.get("status")         or "—")
            a_color = _action_colors.get(action, "#8b949e")
            s_color = _status_colors.get(status,  "#8b949e")

            cb = QPushButton()
            cb.setObjectName("logCheckBtn")
            cb.setCheckable(True)
            cb.setFixedSize(18, 18)
            cb.setStyleSheet("""
                QPushButton#logCheckBtn {
                    background:rgba(255,255,255,0.06);
                    border:1px solid rgba(255,255,255,0.18);
                    border-radius:4px; color:transparent;
                }
                QPushButton#logCheckBtn:hover {
                    background:rgba(255,255,255,0.12);
                    border-color:rgba(255,91,91,0.40);
                }
                QPushButton#logCheckBtn:checked {
                    background:#ff5b5b; border-color:#ff5b5b;
                }
            """)
            cb.toggled.connect(lambda _: self._update_batch_bar())
            cb_cell = QWidget()
            cb_cell.setStyleSheet("background:transparent;")
            cb_lo = QHBoxLayout(cb_cell)
            cb_lo.setContentsMargins(0, 0, 0, 0)
            cb_lo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_lo.addWidget(cb)
            self._table.setCellWidget(ri, 0, cb_cell)

            for ci, (text, color) in enumerate([
                (ts_str, "#a0aabe"),
                (user,   "#e8eaf0"),
                (action, a_color),
                (desc,   "#e8eaf0"),
                (status, s_color),
            ], 1):
                item = QTableWidgetItem(text)
                item.setForeground(QColor(color))
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                self._table.setItem(ri, ci, item)

            self._table.setRowHeight(ri, 36)

        self._table.setUpdatesEnabled(True)
        self._update_batch_bar()

    def _confirm_delete(self, title: str, text: str, detail: str = "") -> bool:
        dlg = ConfirmationDialog(title, text, detail=detail, parent=self)
        return bool(dlg.exec())

    def _on_delete_selected(self):
        ids = self._checked_ids()
        if not ids:
            return
        if not self._confirm_delete(
            "Delete Selected Logs",
            f"Permanently delete {len(ids):,} selected log "
            f"{'entry' if len(ids) == 1 else 'entries'}?",
            "This cannot be undone.",
        ):
            return
        self._run_delete(ids=ids)

    def _on_delete_all_filtered(self):
        total = len(self._all_rows)
        if not total:
            return
        if not self._confirm_delete(
            "Delete All Filtered Logs",
            f"Permanently delete all {total:,} filtered log "
            f"{'entry' if total == 1 else 'entries'}?",
            "This deletes every log currently shown — across all pages. "
            "This cannot be undone.",
        ):
            return
        self._run_delete(ids=None, filters=self._build_filters())

    def _run_delete(self, ids: list[int] | None, filters: dict | None = None):
        self._status_lbl.setText("Deleting…")
        self._del_sel_btn.setEnabled(False)
        self._del_all_btn.setEnabled(False)
        self._cleaner = _ActivityLogCleaner(ids=ids, filters=filters)
        self._cleaner.finished.connect(self._on_deleted)
        self._cleaner.error.connect(lambda e: (
            self._status_lbl.setText(f"⚠ {e}"),
            self._del_sel_btn.setEnabled(True),
            self._del_all_btn.setEnabled(True),
        ))
        self._cleaner.finished.connect(self._clear_cleaner)
        self._cleaner.error.connect(self._clear_cleaner)
        self._cleaner.start()

    def _clear_cleaner(self):
        w = self._cleaner
        self._cleaner = None
        if w is not None:
            try: w.deleteLater()
            except RuntimeError: pass

    def _on_deleted(self, n: int):
        self._del_sel_btn.setEnabled(True)
        self._del_all_btn.setEnabled(True)
        self._status_lbl.setText(
            f"✓  {n:,} log {'entry' if n == 1 else 'entries'} deleted.")
        QTimer.singleShot(3000, lambda: self._status_lbl.setText(""))
        self._load()


# =============================================================================
# SETTINGS PAGE — main container
# =============================================================================

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