"""
ui/dialogs/password_dialogs.py
================================
Reusable dialogs for the password recovery and change flows:

  SecuritySetupDialog      — first-login: user sets their security Q&A
  ForgotPasswordDialog     — 3-step recovery: username → answer → new pw
  ForcePasswordChangeDialog— forced change after admin reset
  AdminResetPasswordDialog — admin sets temp password for a user
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFrame, QComboBox, QStackedWidget, QWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont


# ── Shared style ──────────────────────────────────────────────────────────────

_DIALOG_STYLE = """
    QDialog, QWidget { background: transparent; }
    #pwCard {
        background: #13172a;
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 16px;
    }
    #pwTitle {
        color: #e8eaf0; font-size:16px; font-weight:bold;
        background:transparent;
    }
    #pwSub {
        color:rgba(255,255,255,0.38); font-size:11px;
        background:transparent;
    }
    #pwFieldLabel {
        color:rgba(255,255,255,0.60); font-size:11px;
        font-weight:600; background:transparent;
    }
    #pwInput {
        background:rgba(255,255,255,0.06);
        border:1px solid rgba(255,255,255,0.16);
        border-radius:8px; color:white;
        font-size:13px; padding:10px 14px;
    }
    #pwInput:focus {
        border-color:#4f8cff;
        background:rgba(79,140,255,0.07);
    }
    #pwError {
        color:#ff5b5b; font-size:11px; background:transparent;
    }
    #pwSuccess {
        color:#34d399; font-size:11px; background:transparent;
    }
    QPushButton#pwPrimary {
        background:#4f8cff; border:none; border-radius:9px;
        color:white; font-size:13px; font-weight:700;
        min-height:40px;
    }
    QPushButton#pwPrimary:hover { background:rgba(79,140,255,0.85); }
    QPushButton#pwPrimary:disabled {
        background:rgba(255,255,255,0.08); color:rgba(255,255,255,0.30);
    }
    QPushButton#pwSecondary {
        background:rgba(255,255,255,0.05);
        border:1px solid rgba(255,255,255,0.12);
        border-radius:9px; color:rgba(255,255,255,0.65);
        font-size:12px; font-weight:600; min-height:38px;
    }
    QPushButton#pwSecondary:hover {
        background:rgba(255,255,255,0.10);
        color:#e8eaf0;
    }
    QComboBox#pwCombo {
        background:rgba(255,255,255,0.06);
        border:1px solid rgba(255,255,255,0.16);
        border-radius:8px; color:#e8eaf0;
        font-size:12px; padding:8px 12px; min-height:38px;
    }
    QComboBox#pwCombo:focus { border-color:#4f8cff; }
    QComboBox#pwCombo::drop-down { border:none; width:18px; }
    QComboBox#pwCombo QAbstractItemView {
        background:#1a1f35; color:#e8eaf0;
        selection-background-color:rgba(79,140,255,0.20);
    }
    QPushButton#pwLink {
        background:transparent; border:none;
        color:#4f8cff; font-size:11px; text-decoration:underline;
    }
    QPushButton#pwLink:hover { color:rgba(79,140,255,0.75); }
    #pwStep {
        color:rgba(255,255,255,0.25); font-size:10px; background:transparent;
    }
"""


def _field(label: str, placeholder: str = "",
           password: bool = False) -> tuple[QLabel, QLineEdit]:
    lbl = QLabel(label)
    lbl.setObjectName("pwFieldLabel")
    inp = QLineEdit()
    inp.setObjectName("pwInput")
    inp.setPlaceholderText(placeholder)
    if password:
        inp.setEchoMode(QLineEdit.EchoMode.Password)
    return lbl, inp


def _divider():
    d = QFrame()
    d.setFrameShape(QFrame.Shape.HLine)
    d.setStyleSheet("color:rgba(255,255,255,0.07);")
    return d


# ── Security Setup Dialog ─────────────────────────────────────────────────────

class SecuritySetupDialog(QDialog):
    """
    Shown on first login when security_question IS NULL.
    User must set a question and answer before the main window opens.
    Cannot be dismissed without completing setup.
    """

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self._conn = conn
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setMaximumWidth(560)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._drag_pos = None
        self._build_ui()
        self.adjustSize()
        self.setStyleSheet(_DIALOG_STYLE)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                e.globalPosition().toPoint() - self.frameGeometry().topLeft())
    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    def closeEvent(self, e):
        e.ignore()   # Cannot close without completing setup

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            pass    # Block Escape
        else:
            super().keyPressEvent(e)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        card = QFrame()
        card.setObjectName("pwCard")
        outer.addWidget(card)
        lo = QVBoxLayout(card)
        lo.setContentsMargins(32, 24, 32, 24)
        lo.setSpacing(10)

        title = QLabel("Set Up Security Question")
        title.setObjectName("pwTitle")
        sub = QLabel(
            "Choose a security question and answer. This is used to recover "
            "your account if you forget your password."
        )
        sub.setObjectName("pwSub")
        sub.setWordWrap(True)
        lo.addWidget(title)
        lo.addWidget(sub)
        lo.addWidget(_divider())

        from services.auth_service import AuthService
        q_lbl = QLabel("Security Question")
        q_lbl.setObjectName("pwFieldLabel")
        self._q_combo = QComboBox()
        self._q_combo.setObjectName("pwCombo")
        self._q_combo.addItems(AuthService.SECURITY_QUESTIONS)
        lo.addWidget(q_lbl)
        lo.addWidget(self._q_combo)

        a_lbl, self._answer = _field(
            "Your Answer", "Type your answer here")
        lo.addWidget(a_lbl)
        lo.addWidget(self._answer)

        c_lbl, self._confirm = _field(
            "Confirm Answer", "Type your answer again")
        lo.addWidget(c_lbl)
        lo.addWidget(self._confirm)

        self._error = QLabel("")
        self._error.setObjectName("pwError")
        self._error.hide()
        lo.addWidget(self._error)

        save_btn = QPushButton("Save & Continue")
        save_btn.setObjectName("pwPrimary")
        save_btn.clicked.connect(self._on_save)
        lo.addWidget(save_btn)

    def _on_save(self):
        from services.auth_service import AuthService
        q      = self._q_combo.currentText()
        ans    = self._answer.text().strip()
        confirm = self._confirm.text().strip()

        if not ans:
            self._show_error("Please enter your answer.")
            return
        if ans != confirm:
            self._show_error("Answers do not match.")
            return
        if len(ans) < 3:
            self._show_error("Answer must be at least 3 characters.")
            return

        ok, msg = AuthService.set_security_question(self._conn, q, ans)
        if ok:
            self.accept()
        else:
            self._show_error(f"Could not save: {msg}")

    def _show_error(self, msg: str):
        self._error.setText(msg)
        self._error.show()


# ── Forgot Password Dialog ────────────────────────────────────────────────────

class ForgotPasswordDialog(QDialog):
    """
    3-step recovery flow:
      Step 0 — Enter username
      Step 1 — Answer security question
      Step 2 — Set new password
    """

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self._conn     = conn
        self._username = ""
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setMaximumWidth(560)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._drag_pos = None
        self._build_ui()
        self.adjustSize()
        self.setStyleSheet(_DIALOG_STYLE)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                e.globalPosition().toPoint() - self.frameGeometry().topLeft())
    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        card = QFrame()
        card.setObjectName("pwCard")
        outer.addWidget(card)
        self._root = QVBoxLayout(card)
        self._root.setContentsMargins(32, 24, 32, 24)
        self._root.setSpacing(10)

        # Header (always visible)
        hdr = QHBoxLayout()
        self._title = QLabel("Forgot Password")
        self._title.setObjectName("pwTitle")
        self._step_lbl = QLabel("Step 1 of 3")
        self._step_lbl.setObjectName("pwStep")
        hdr.addWidget(self._title)
        hdr.addStretch()
        hdr.addWidget(self._step_lbl)
        self._root.addLayout(hdr)

        sub = QLabel("Enter your username to begin account recovery.")
        sub.setObjectName("pwSub")
        sub.setWordWrap(True)
        self._sub = sub
        self._root.addWidget(sub)
        self._root.addWidget(_divider())

        # Step stack
        self._stack = QStackedWidget()
        self._root.addWidget(self._stack, 1)

        self._stack.addWidget(self._build_step0())
        self._stack.addWidget(self._build_step1())
        self._stack.addWidget(self._build_step2())

        # Error label
        self._error = QLabel("")
        self._error.setObjectName("pwError")
        self._error.setWordWrap(True)
        self._error.hide()
        self._root.addWidget(self._error)

        # Button row
        btn_row = QHBoxLayout()
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setObjectName("pwSecondary")
        self._cancel_btn.clicked.connect(self.reject)

        self._next_btn = QPushButton("Next →")
        self._next_btn.setObjectName("pwPrimary")
        self._next_btn.clicked.connect(self._on_next)

        btn_row.addWidget(self._cancel_btn)
        btn_row.addSpacing(10)
        btn_row.addWidget(self._next_btn)
        self._root.addLayout(btn_row)

    def _build_step0(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        lo = QVBoxLayout(w)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(6)
        lbl, self._user_input = _field("Username", "Enter your username")
        lo.addWidget(lbl)
        lo.addWidget(self._user_input)
        lo.addStretch()
        return w

    def _build_step1(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        lo = QVBoxLayout(w)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(6)
        self._q_lbl = QLabel("")
        self._q_lbl.setObjectName("pwFieldLabel")
        self._q_lbl.setWordWrap(True)
        lbl, self._answer_input = _field("Your Answer", "Type your answer")
        lo.addWidget(self._q_lbl)
        lo.addWidget(lbl)
        lo.addWidget(self._answer_input)
        lo.addStretch()
        return w

    def _build_step2(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        lo = QVBoxLayout(w)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(6)
        lbl1, self._new_pw    = _field("New Password",
                                        "At least 8 characters", password=True)
        lbl2, self._confirm_pw = _field("Confirm Password",
                                         "Repeat new password", password=True)
        lo.addWidget(lbl1)
        lo.addWidget(self._new_pw)
        lo.addWidget(lbl2)
        lo.addWidget(self._confirm_pw)
        lo.addStretch()
        return w

    def _on_next(self):
        self._error.hide()
        step = self._stack.currentIndex()

        if step == 0:
            self._do_step0()
        elif step == 1:
            self._do_step1()
        elif step == 2:
            self._do_step2()

    def _do_step0(self):
        from services.auth_service import AuthService
        username = self._user_input.text().strip()
        if not username:
            self._show_error("Please enter your username.")
            return
        ok, result = AuthService.get_security_question(self._conn, username)
        if not ok:
            self._show_error(result)
            return
        self._username = username
        self._q_lbl.setText(result)
        self._stack.setCurrentIndex(1)
        self._step_lbl.setText("Step 2 of 3")
        self._sub.setText("Answer your security question to verify your identity.")
        self._next_btn.setText("Verify →")

    def _do_step1(self):
        from services.auth_service import AuthService
        answer = self._answer_input.text().strip()
        if not answer:
            self._show_error("Please enter your answer.")
            return
        ok, msg = AuthService.verify_security_answer(
            self._conn, self._username, answer)
        if not ok:
            self._show_error(msg)
            return
        self._stack.setCurrentIndex(2)
        self._step_lbl.setText("Step 3 of 3")
        self._sub.setText("Choose a strong new password for your account.")
        self._next_btn.setText("Reset Password")
        self._title.setText("Set New Password")

    def _do_step2(self):
        from services.auth_service import AuthService
        pw      = self._new_pw.text()
        confirm = self._confirm_pw.text()
        if not pw:
            self._show_error("Please enter a new password.")
            return
        if len(pw) < 8:
            self._show_error("Password must be at least 8 characters.")
            return
        if pw != confirm:
            self._show_error("Passwords do not match.")
            return
        ok, msg = AuthService.reset_password_with_answer(
            self._conn, self._username, pw)
        if not ok:
            self._show_error(f"Reset failed: {msg}")
            return
        self.accept()

    def _show_error(self, msg: str):
        self._error.setText(msg)
        self._error.show()


# ── Force Password Change Dialog ──────────────────────────────────────────────

class ForcePasswordChangeDialog(QDialog):
    """
    Shown after login when force_password_change = TRUE.
    Cannot be dismissed — user must set a new password.
    """

    def __init__(self, conn, user_id: int, parent=None):
        super().__init__(parent)
        self._conn    = conn
        self._user_id = user_id
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setMaximumWidth(560)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._drag_pos = None
        self._build_ui()
        self.adjustSize()
        self.setStyleSheet(_DIALOG_STYLE)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                e.globalPosition().toPoint() - self.frameGeometry().topLeft())
    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    def closeEvent(self, e):
        e.ignore()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            pass
        else:
            super().keyPressEvent(e)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        card = QFrame()
        card.setObjectName("pwCard")
        outer.addWidget(card)
        lo = QVBoxLayout(card)
        lo.setContentsMargins(28, 20, 28, 20)
        lo.setSpacing(10)

        # Warning banner
        banner = QFrame()
        banner.setStyleSheet("""
            QFrame {
                background: rgba(245,179,53,0.10);
                border: 1px solid rgba(245,179,53,0.30);
                border-radius: 8px;
            }
        """)
        bl = QHBoxLayout(banner)
        bl.setContentsMargins(12, 8, 12, 8)
        icon = QLabel("🔑")
        icon.setStyleSheet("font-size:18px; background:transparent;")
        msg = QLabel(
            "Your password has been reset by an administrator. "
            "You must set a new password before continuing."
        )
        msg.setWordWrap(True)
        msg.setStyleSheet(
            "color:#f5b335; font-size:11px; background:transparent;")
        bl.addWidget(icon)
        bl.addWidget(msg, 1)
        lo.addWidget(banner)

        title = QLabel("Set New Password")
        title.setObjectName("pwTitle")
        lo.addWidget(title)
        lo.addWidget(_divider())

        lbl1, self._new_pw     = _field("New Password",
                                         "At least 8 characters", password=True)
        lbl2, self._confirm_pw = _field("Confirm New Password",
                                         "Repeat password", password=True)
        lo.addWidget(lbl1)
        lo.addWidget(self._new_pw)
        lo.addWidget(lbl2)
        lo.addWidget(self._confirm_pw)

        self._error = QLabel("")
        self._error.setObjectName("pwError")
        self._error.hide()
        lo.addWidget(self._error)

        save_btn = QPushButton("Save New Password")
        save_btn.setObjectName("pwPrimary")
        save_btn.clicked.connect(self._on_save)
        lo.addWidget(save_btn)

    def _on_save(self):
        from services.auth_service import AuthService
        pw      = self._new_pw.text()
        confirm = self._confirm_pw.text()
        if not pw:
            self._show_error("Please enter a new password.")
            return
        if len(pw) < 8:
            self._show_error("Password must be at least 8 characters.")
            return
        if pw != confirm:
            self._show_error("Passwords do not match.")
            return
        ok, msg = AuthService.change_password(self._conn, self._user_id, pw)
        if ok:
            self.accept()
        else:
            self._show_error(f"Could not save: {msg}")

    def _show_error(self, msg: str):
        self._error.setText(msg)
        self._error.show()


# ── Admin Reset Password Dialog ───────────────────────────────────────────────

class AdminResetPasswordDialog(QDialog):
    """
    Admin sets a temporary password for a specific user.
    The user will be forced to change it on next login.
    """

    def __init__(self, conn, user_id: int, username: str,
                 full_name: str, parent=None):
        super().__init__(parent)
        self._conn      = conn
        self._user_id   = user_id
        self._username  = username
        self._full_name = full_name
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setMaximumWidth(560)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._drag_pos = None
        self._build_ui()
        self.adjustSize()
        self.setStyleSheet(_DIALOG_STYLE)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                e.globalPosition().toPoint() - self.frameGeometry().topLeft())
    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        card = QFrame()
        card.setObjectName("pwCard")
        outer.addWidget(card)
        lo = QVBoxLayout(card)
        lo.setContentsMargins(28, 20, 28, 20)
        lo.setSpacing(10)

        title = QLabel("Reset Password")
        title.setObjectName("pwTitle")
        sub = QLabel(
            f"Set a temporary password for  {self._full_name}  "
            f"(@{self._username}).  They will be required to change "
            "it on their next login."
        )
        sub.setObjectName("pwSub")
        sub.setWordWrap(True)
        lo.addWidget(title)
        lo.addWidget(sub)
        lo.addWidget(_divider())

        lbl1, self._pw      = _field("Temporary Password",
                                      "At least 8 characters", password=True)
        lbl2, self._confirm = _field("Confirm Password",
                                      "Repeat password", password=True)
        lo.addWidget(lbl1)
        lo.addWidget(self._pw)
        lo.addWidget(lbl2)
        lo.addWidget(self._confirm)

        self._error = QLabel("")
        self._error.setObjectName("pwError")
        self._error.hide()
        lo.addWidget(self._error)

        btn_row = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.setObjectName("pwSecondary")
        cancel.clicked.connect(self.reject)
        reset_btn = QPushButton("Reset Password")
        reset_btn.setObjectName("pwPrimary")
        reset_btn.clicked.connect(self._on_reset)
        btn_row.addWidget(cancel)
        btn_row.addSpacing(10)
        btn_row.addWidget(reset_btn)
        lo.addLayout(btn_row)

    def _on_reset(self):
        from services.auth_service import AuthService
        pw      = self._pw.text()
        confirm = self._confirm.text()
        if not pw:
            self._show_error("Please enter a temporary password.")
            return
        if len(pw) < 8:
            self._show_error("Password must be at least 8 characters.")
            return
        if pw != confirm:
            self._show_error("Passwords do not match.")
            return
        ok, msg = AuthService.admin_reset_password(
            self._conn, self._user_id, pw)
        if ok:
            self.accept()
        else:
            self._show_error(f"Reset failed: {msg}")

    def _show_error(self, msg: str):
        self._error.setText(msg)
        self._error.show()