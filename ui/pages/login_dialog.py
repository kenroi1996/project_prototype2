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

import math

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QFrame, QGraphicsOpacityEffect,
    QMessageBox, QWidget,
)
from PyQt6.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve, QTimer,
    QThread, pyqtSignal, QRectF,
)
from PyQt6.QtGui import QFont, QKeyEvent, QPainter, QColor, QPen


# ── Background auth worker ────────────────────────────────────────────────────

class _AuthWorker(QThread):
    """
    Runs DB connection + AuthService.login() on a background thread so the
    main thread stays free to animate the spinner.
    """
    finished = pyqtSignal(bool, str, object)   # (ok, message, conn)

    def __init__(self, username: str, password: str):
        super().__init__()
        self._username = username
        self._password = password

    def run(self) -> None:
        try:
            from database.connection import get_connection
            conn = get_connection()
            if conn is None:
                self.finished.emit(False, "Could not connect to the database.", None)
                return

            from services.auth_service import AuthService
            from services.activity_logger import ActivityLogger
            AuthService.ensure_schema(conn)
            ActivityLogger.ensure_schema(conn)

            ok, msg = AuthService.login(conn, self._username, self._password)
            self.finished.emit(ok, msg, conn if ok else None)

        except Exception as exc:
            self.finished.emit(False, f"Connection error: {exc}", None)


# ── Spinner widget ────────────────────────────────────────────────────────────

class _Spinner(QWidget):
    """
    Lightweight arc spinner drawn with QPainter.
    Starts/stops cleanly; no external assets required.
    """
    def __init__(self, parent=None, size: int = 22,
                 color: str = "#4f8cff", thickness: int = 3):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._color     = QColor(color)
        self._thickness = thickness
        self._angle     = 0
        self._timer     = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def start(self) -> None:
        self._angle = 0
        self._timer.start(16)   # ~60 fps
        self.show()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _tick(self) -> None:
        self._angle = (self._angle + 6) % 360
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        pen = QPen(self._color, self._thickness, Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        margin = self._thickness
        rect   = QRectF(margin, margin,
                        self.width()  - margin * 2,
                        self.height() - margin * 2)
        p.drawArc(rect, (-self._angle) * 16, 270 * 16)


# ── Login Dialog ──────────────────────────────────────────────────────────────

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
        self._conn      = None
        self.db_conn    = None
        self._drag_pos  = None
        self._worker: _AuthWorker | None = None
        self._build_ui()
        self._apply_styles()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)

        card = QFrame()
        card.setObjectName("loginCard")
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(40, 36, 40, 36)
        layout.setSpacing(0)

        # Close button
        close_row = QHBoxLayout()
        close_row.addStretch()
        self._close_btn = QPushButton("x")
        self._close_btn.setObjectName("loginCloseBtn")
        self._close_btn.setFixedSize(28, 28)
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setToolTip("Exit application")
        self._close_btn.clicked.connect(self._on_close_clicked)
        close_row.addWidget(self._close_btn)
        layout.addLayout(close_row)
        layout.addSpacing(4)

        # Logo
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

        # Username
        layout.addWidget(self._field_label("Username"))
        layout.addSpacing(6)
        self._username = QLineEdit()
        self._username.setObjectName("loginInput")
        self._username.setPlaceholderText("Enter your username")
        self._username.returnPressed.connect(self._on_login)
        layout.addWidget(self._username)
        layout.addSpacing(16)

        # Password
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

        # Error label
        self._error_lbl = QLabel("")
        self._error_lbl.setObjectName("loginError")
        self._error_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_lbl.setWordWrap(True)
        self._error_lbl.hide()
        layout.addWidget(self._error_lbl)
        layout.addSpacing(20)

        # Sign In button + spinner (side by side, centred)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._login_btn = QPushButton("Sign In")
        self._login_btn.setObjectName("loginBtn")
        self._login_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._login_btn.setFixedHeight(44)
        self._login_btn.clicked.connect(self._on_login)

        self._spinner = _Spinner(self, size=22, color="#4f8cff", thickness=3)

        btn_row.addWidget(self._login_btn, 1)
        btn_row.addWidget(self._spinner)
        layout.addLayout(btn_row)
        layout.addSpacing(8)

        # Forgot password
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

        # Footer
        footer = QLabel("Cebu Technological University - Daanbantayan Campus")
        footer.setObjectName("loginFooter")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(footer)

    def _field_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("loginFieldLabel")
        return lbl

    def _toggle_password_visibility(self, checked: bool) -> None:
        if checked:
            self._password.setEchoMode(QLineEdit.EchoMode.Normal)
            self._toggle_btn.setText("🙈")
        else:
            self._password.setEchoMode(QLineEdit.EchoMode.Password)
            self._toggle_btn.setText("👁")

    # ── Loading state ─────────────────────────────────────────────────────────

    def _set_loading(self, loading: bool) -> None:
        """Toggle the busy state: disable inputs and spin the arc."""
        self._login_btn.setEnabled(not loading)
        self._login_btn.setText("Signing in…" if loading else "Sign In")
        self._username.setEnabled(not loading)
        self._password.setEnabled(not loading)
        self._toggle_btn.setEnabled(not loading)
        self._forgot_btn.setEnabled(not loading)
        self._close_btn.setEnabled(not loading)

        if loading:
            self._error_lbl.hide()
            self._spinner.start()
        else:
            self._spinner.stop()

    # ── Exit confirmation ─────────────────────────────────────────────────────

    def _on_close_clicked(self) -> None:
        self._confirm_exit()

    def _confirm_exit(self) -> bool:
        msg = QMessageBox(self)
        msg.setWindowTitle("Exit EarlyAlert")
        msg.setText("Do you want to exit?")
        msg.setInformativeText("Any unsaved data will be lost.")
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.setDefaultButton(QMessageBox.StandardButton.No)
        msg.setStyleSheet("""
            QMessageBox { background-color: #13172a; }
            QMessageBox QLabel {
                color: #e8eaf0; font-size: 13px; background: transparent;
            }
            QMessageBox QPushButton {
                background-color: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.14);
                border-radius: 8px; color: rgba(255,255,255,0.80);
                font-size: 12px; font-weight: 600;
                padding: 8px 24px; min-width: 70px;
            }
            QMessageBox QPushButton:hover {
                background-color: rgba(255,255,255,0.12);
            }
            QMessageBox QPushButton[text="Yes"] {
                background-color: rgba(255,91,91,0.15);
                border-color: rgba(255,91,91,0.35); color: #ff5b5b;
            }
            QMessageBox QPushButton[text="Yes"]:hover {
                background-color: rgba(255,91,91,0.28);
            }
        """)
        if msg.exec() == QMessageBox.StandardButton.Yes:
            self.reject()
            return True
        return False

    # ── Authentication ────────────────────────────────────────────────────────

    def _on_login(self) -> None:
        # Guard: don't fire again if already authenticating.
        # Check None only — never call .isRunning() on a potentially
        # deleted C++ object. self._worker is set to None in _clear_worker
        # before deleteLater fires, so this is always safe.
        if self._worker is not None:
            return

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

        self._set_loading(True)

        self._worker = _AuthWorker(username, password)
        self._worker.finished.connect(self._on_auth_finished)
        self._worker.finished.connect(self._clear_worker)
        self._worker.start()

    def _clear_worker(self) -> None:
        """Null our reference first, then schedule C++ cleanup."""
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()

    def _on_auth_finished(self, ok: bool, msg: str, conn) -> None:
        self._set_loading(False)

        if ok:
            self.db_conn = conn
            self.accept()
        else:
            self._show_error(msg)
            self._password.clear()
            self._password.setFocus()
            self._shake()

    def _show_error(self, msg: str) -> None:
        self._error_lbl.setText(msg)
        self._error_lbl.show()

    def _shake(self) -> None:
        card = self.findChild(QFrame, "loginCard")
        if card is None:
            return
        orig    = card.pos()
        offsets = [8, -8, 6, -6, 4, -4, 0]

        def _step(i=0):
            if i >= len(offsets):
                card.move(orig)
                return
            card.move(orig.x() + offsets[i], orig.y())
            QTimer.singleShot(40, lambda: _step(i + 1))

        _step()

    # ── Forgot password ───────────────────────────────────────────────────────

    def _on_forgot_password(self) -> None:
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

    # ── Window guards ─────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        if self.result() == QDialog.DialogCode.Rejected and not self.isVisible():
            super().closeEvent(event)
            return
        event.ignore()
        self._confirm_exit()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._confirm_exit()
        else:
            super().keyPressEvent(event)

    # ── Frameless dragging ────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (self._drag_pos is not None
                and event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    # ── Styles ────────────────────────────────────────────────────────────────

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            LoginDialog { background: transparent; }

            #loginCard {
                background-color: #13172a;
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 20px;
            }

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

            #loginLogo    { font-size: 48px; background: transparent; }
            #loginAppName {
                color: #e8eaf0; font-size: 26px; font-weight: 800;
                letter-spacing: 1px; background: transparent;
            }
            #loginTagline {
                color: rgba(255,255,255,0.40);
                font-size: 12px; background: transparent;
            }
            #loginFieldLabel {
                color: rgba(255,255,255,0.70);
                font-size: 12px; font-weight: 600; background: transparent;
            }
            #loginInput {
                background-color: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.18);
                border-radius: 8px; color: white;
                font-size: 13px; padding: 10px 14px;
                selection-background-color: #4f8cff;
            }
            #loginInput:focus {
                border-color: #4f8cff;
                background-color: rgba(79,140,255,0.07);
            }
            #loginInput:disabled {
                color: rgba(255,255,255,0.30);
                background-color: rgba(255,255,255,0.03);
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
                background-color: rgba(255,255,255,0.10); color: white;
            }
            #loginError {
                color: #ff5b5b; font-size: 12px; background: transparent;
            }
            #loginBtn {
                background-color: #4f8cff;
                border: none; border-radius: 10px;
                color: white; font-size: 14px; font-weight: 700;
            }
            #loginBtn:hover   { background-color: rgba(79,140,255,0.85); }
            #loginBtn:pressed { background-color: rgba(79,140,255,0.70); }
            #loginBtn:disabled {
                background-color: rgba(255,255,255,0.08);
                color: rgba(255,255,255,0.30);
            }
            #loginForgotBtn {
                color: rgba(255,255,255,0.35);
                font-size: 11px; background: transparent; border: none;
            }
            #loginForgotBtn:hover { color: #4f8cff; }
            #loginFooter {
                color: rgba(255,255,255,0.20);
                font-size: 11px; background: transparent;
            }
        """)