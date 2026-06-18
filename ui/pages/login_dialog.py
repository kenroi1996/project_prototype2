"""
Login Dialog
============
Modal dialog shown before the main window.  Blocks until the user
authenticates successfully or closes the window (which exits the app).

Integration
-----------
In main.py / app.py, before showing the main window:

    from ui.pages.login_dialog import LoginDialog

    dialog = LoginDialog()
    if dialog.exec() != LoginDialog.DialogCode.Accepted:
        sys.exit(0)   # user closed without logging in

    # Session is now populated in AuthService -- show main window
    window = DashboardWindow()
    window.show()
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QFrame, QGraphicsOpacityEffect,
    QMessageBox,
)
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QTimer
from PyQt6.QtGui import QFont, QKeyEvent


class LoginDialog(QDialog):
    """
    Full-screen-style modal login dialog.
    Accepts on successful authentication, rejects if the user closes it.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("EarlyAlert — Login")
        self.setModal(True)
        self.setFixedSize(440, 560)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._conn = None
        self.db_conn = None
        self._drag_pos = None
        self._build_ui()
        self._apply_styles()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)

        card = QFrame()
        card.setObjectName("loginCard")
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(40, 36, 40, 36)
        layout.setSpacing(0)

        # -- Close button (top-right corner) ---------------------------
        close_row = QHBoxLayout()
        close_row.addStretch()
        self._close_btn = QPushButton("✕")
        self._close_btn.setObjectName("loginCloseBtn")
        self._close_btn.setFixedSize(28, 28)
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setToolTip("Exit application")
        self._close_btn.clicked.connect(self._on_close_clicked)
        close_row.addWidget(self._close_btn)
        layout.addLayout(close_row)
        layout.addSpacing(4)

        # -- Logo / branding -------------------------------------------
        logo_row = QHBoxLayout()
        logo_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo = QLabel("🎓")
        logo.setObjectName("loginLogo")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_row.addWidget(logo)
        layout.addLayout(logo_row)
        layout.addSpacing(12)

        app_name = QLabel("EarlyAlert")
        app_name.setObjectName("loginAppName")
        app_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(app_name)

        tagline = QLabel("Student Risk Prediction System")
        tagline.setObjectName("loginTagline")
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(tagline)
        layout.addSpacing(36)

        # -- Username --------------------------------------------------
        layout.addWidget(self._field_label("Username"))
        layout.addSpacing(6)
        self._username = QLineEdit()
        self._username.setObjectName("loginInput")
        self._username.setPlaceholderText("Enter your username")
        self._username.returnPressed.connect(self._on_login)
        layout.addWidget(self._username)
        layout.addSpacing(16)

        # -- Password --------------------------------------------------
        layout.addWidget(self._field_label("Password"))
        layout.addSpacing(6)

        pw_row = QHBoxLayout()
        pw_row.setSpacing(0)

        self._password = QLineEdit()
        self._password.setObjectName("loginInput")
        self._password.setPlaceholderText("Enter your password")
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.returnPressed.connect(self._on_login)

        self._toggle_btn = QPushButton("👁")
        self._toggle_btn.setObjectName("loginToggleBtn")
        self._toggle_btn.setFixedSize(36, 38)
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.toggled.connect(self._toggle_password_visibility)

        pw_row.addWidget(self._password)
        pw_row.addWidget(self._toggle_btn)
        layout.addLayout(pw_row)
        layout.addSpacing(10)

        # -- Error message ---------------------------------------------
        self._error_lbl = QLabel("")
        self._error_lbl.setObjectName("loginError")
        self._error_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_lbl.setWordWrap(True)
        self._error_lbl.hide()
        layout.addWidget(self._error_lbl)
        layout.addSpacing(20)

        # -- Login button ----------------------------------------------
        self._login_btn = QPushButton("Sign In")
        self._login_btn.setObjectName("loginBtn")
        self._login_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._login_btn.setFixedHeight(44)
        self._login_btn.clicked.connect(self._on_login)
        layout.addWidget(self._login_btn)
        layout.addSpacing(8)

        # Forgot password link
        forgot_row = QHBoxLayout()
        forgot_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._forgot_btn = QPushButton("Forgot password?")
        self._forgot_btn.setObjectName("loginForgotBtn")
        self._forgot_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._forgot_btn.setFlat(True)
        self._forgot_btn.clicked.connect(self._on_forgot_password)
        forgot_row.addWidget(self._forgot_btn)
        layout.addLayout(forgot_row)
        layout.addStretch()

        # -- Footer ----------------------------------------------------
        footer = QLabel("Philippine Normal University — Visayas")
        footer.setObjectName("loginFooter")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(footer)

    def _field_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("loginFieldLabel")
        return lbl

    def _toggle_password_visibility(self, checked: bool):
        if checked:
            self._password.setEchoMode(QLineEdit.EchoMode.Normal)
            self._toggle_btn.setText("🙈")
        else:
            self._password.setEchoMode(QLineEdit.EchoMode.Password)
            self._toggle_btn.setText("👁")

    # ------------------------------------------------------------------
    # Exit confirmation
    # ------------------------------------------------------------------

    def _on_close_clicked(self):
        """Ask for confirmation before fully exiting the application."""
        self._confirm_exit()

    def _confirm_exit(self) -> bool:
        """
        Show a styled confirmation dialog.
        Returns True if the user confirmed exit, False otherwise.
        """
        msg = QMessageBox(self)
        msg.setWindowTitle("Exit EarlyAlert")
        msg.setText("Do you want to exit?")
        msg.setInformativeText(
            "Any unsaved data will be lost."
        )
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes |
            QMessageBox.StandardButton.No
        )
        msg.setDefaultButton(QMessageBox.StandardButton.No)
        msg.setStyleSheet("""
            QMessageBox {
                background-color: #13172a;
            }
            QMessageBox QLabel {
                color: #e8eaf0;
                font-size: 13px;
                background: transparent;
            }
            QMessageBox QPushButton {
                background-color: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.14);
                border-radius: 8px;
                color: rgba(255,255,255,0.80);
                font-size: 12px;
                font-weight: 600;
                padding: 8px 24px;
                min-width: 70px;
            }
            QMessageBox QPushButton:hover {
                background-color: rgba(255,255,255,0.12);
            }
            QMessageBox QPushButton[text="Yes"] {
                background-color: rgba(255,91,91,0.15);
                border-color: rgba(255,91,91,0.35);
                color: #ff5b5b;
            }
            QMessageBox QPushButton[text="Yes"]:hover {
                background-color: rgba(255,91,91,0.28);
            }
        """)

        result = msg.exec()
        if result == QMessageBox.StandardButton.Yes:
            self.reject()
            return True
        return False

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _on_login(self):
        username = self._username.text().strip()
        password = self._password.text()

        if not username:
            self._show_error("Please enter your username.")
            self._username.setFocus()
            return
        if not password:
            self._show_error("Please enter your password.")
            self._password.setFocus()
            return

        self._login_btn.setEnabled(False)
        self._login_btn.setText("Signing in…")
        self._error_lbl.hide()

        try:
            if self._conn is None:
                from database.connection import get_connection
                self._conn = get_connection()
                if self._conn is None:
                    raise RuntimeError("Could not connect to the database.")

            from services.auth_service import AuthService
            from services.activity_logger import ActivityLogger
            AuthService.ensure_schema(self._conn)
            ActivityLogger.ensure_schema(self._conn)

            ok, msg = AuthService.login(self._conn, username, password)

        except Exception as exc:
            ok  = False
            msg = f"Connection error: {exc}"

        if ok:
            self._login_btn.setText("Sign In")
            self._login_btn.setEnabled(True)
            self.db_conn = self._conn
            self.accept()
        else:
            self._show_error(msg)
            self._password.clear()
            self._password.setFocus()
            self._login_btn.setText("Sign In")
            self._login_btn.setEnabled(True)
            self._shake()

    def _show_error(self, msg: str):
        self._error_lbl.setText(msg)
        self._error_lbl.show()

    def _shake(self):
        card = self.findChild(QFrame, "loginCard")
        if card is None:
            return
        orig = card.pos()
        offsets = [8, -8, 6, -6, 4, -4, 0]

        def _step(i=0):
            if i >= len(offsets):
                card.move(orig)
                return
            card.move(orig.x() + offsets[i], orig.y())
            QTimer.singleShot(40, lambda: _step(i + 1))

        _step()

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Forgot password
    # ------------------------------------------------------------------

    def _on_forgot_password(self):
        try:
            if self._conn is None:
                from database.connection import get_connection
                self._conn = get_connection()
                if self._conn is None:
                    self._show_error("Could not connect to the database.")
                    return
            from ui.dialogs.password_dialogs import ForgotPasswordDialog
            dlg = ForgotPasswordDialog(self._conn, self)
            if dlg.exec() == ForgotPasswordDialog.DialogCode.Accepted:
                self._error_lbl.setText(
                    "✓  Password reset successfully. Please log in.")
                self._error_lbl.setStyleSheet(
                    "color:#34d399; font-size:12px; background:transparent;")
                self._error_lbl.show()
        except Exception as e:
            self._show_error(f"Recovery error: {e}")

    # Close guard — Escape and window close both trigger confirmation
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        # Prevent the default close; let _confirm_exit handle it.
        # If _confirm_exit calls self.reject(), Qt will call closeEvent
        # again with the dialog already rejected — let it through.
        if self.result() == QDialog.DialogCode.Rejected and not self.isVisible():
            super().closeEvent(event)
            return
        event.ignore()
        self._confirm_exit()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self._confirm_exit()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Frameless window dragging
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self._drag_pos is not None
                and event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # Styles
    # ------------------------------------------------------------------

    def _apply_styles(self):
        self.setStyleSheet("""
            LoginDialog { background: transparent; }

            #loginCard {
                background-color: #13172a;
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 20px;
            }

            /* ── Close button ──────────────────────────────────────── */
            #loginCloseBtn {
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 7px;
                color: rgba(255,255,255,0.35);
                font-size: 13px;
                font-weight: bold;
            }
            #loginCloseBtn:hover {
                background: rgba(255,91,91,0.15);
                border-color: rgba(255,91,91,0.35);
                color: #ff5b5b;
            }

            #loginLogo {
                font-size: 48px;
                background: transparent;
            }
            #loginAppName {
                color: #e8eaf0;
                font-size: 26px;
                font-weight: 800;
                letter-spacing: 1px;
                background: transparent;
            }
            #loginTagline {
                color: rgba(255,255,255,0.40);
                font-size: 12px;
                background: transparent;
            }
            #loginFieldLabel {
                color: rgba(255,255,255,0.70);
                font-size: 12px;
                font-weight: 600;
                background: transparent;
            }
            #loginInput {
                background-color: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.18);
                border-radius: 8px;
                color: white;
                font-size: 13px;
                padding: 10px 14px;
                selection-background-color: #4f8cff;
            }
            #loginInput:focus {
                border-color: #4f8cff;
                background-color: rgba(79,140,255,0.07);
            }
            #loginToggleBtn {
                background-color: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.18);
                border-left: none;
                border-radius: 0 8px 8px 0;
                color: rgba(255,255,255,0.50);
                font-size: 14px;
            }
            #loginToggleBtn:hover {
                background-color: rgba(255,255,255,0.10);
                color: white;
            }
            #loginError {
                color: #ff5b5b;
                font-size: 12px;
                background: transparent;
            }
            #loginBtn {
                background-color: #4f8cff;
                border: none;
                border-radius: 10px;
                color: white;
                font-size: 14px;
                font-weight: 700;
            }
            #loginBtn:hover   { background-color: rgba(79,140,255,0.85); }
            #loginBtn:pressed { background-color: rgba(79,140,255,0.70); }
            #loginBtn:disabled {
                background-color: rgba(255,255,255,0.08);
                color: rgba(255,255,255,0.30);
            }
            #loginForgotBtn {
                color: rgba(255,255,255,0.35);
                font-size: 11px;
                background: transparent;
                border: none;
            }
            #loginForgotBtn:hover { color: #4f8cff; }
            #loginFooter {
                color: rgba(255,255,255,0.20);
                font-size: 11px;
                background: transparent;
            }
        """)