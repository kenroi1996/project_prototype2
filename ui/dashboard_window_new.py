import sys

from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QSizePolicy,
    QGraphicsDropShadowEffect,
    QGraphicsBlurEffect,
    QProgressBar,
    QScrollArea,
    QStackedWidget
)

from PyQt6.QtCore import (
    Qt,
    QPropertyAnimation,
    QEasingCurve,
    QRect,
    QTimer,
    QSize,
    QMargins,
)

from PyQt6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QBrush,
    QPixmap,
    QIcon
)

from .pages.dashboard_page import DashboardPage
from .pages.risk_alerts_page import RiskAlertsPage
from .pages.student_cohort_page import StudentCohortPage
from .pages.model_training_page import ModelTrainingPage
from .pages.portal_upload_page import (
    MisPortalPage,
    SaoPortalPage,
    GuidancePortalPage,
    RegistrarPortalPage,
)
from .pages.data_merge_pipeline import DataMergePipelinePage
from .pages.prediction_page import PredictionPage
from .pages.prediction_history_page import PredictionHistoryPage   # ← NEW
from .pages.settings_page import SettingsPage                      # ← NEW
#from services.activity_logger import ActivityLogger
from .pages.analytics_page import AnalyticsPage



# ── Page index registry ───────────────────────────────────────────────────────
# Single source of truth for every stacked-widget page index.
# Add new pages here first, then reference _PAGE_IDX below.
_PAGE_IDX = {
    "Dashboard":            0,
    "Data Analytics":       1,
    "Risk Alerts":          2,
    "Student Cohort":       3,
    "Model Training":       4,
    "Data Merge & Pipeline":5,
    "MIS Portal":           6,
    "SAO Portal":           7,
    "Guidance Portal":      8,
    "Registrar Portal":     9,
    "Prediction":           10,
    "Prediction History":   11, # ← NEW
    "Settings":             12  # ← NEW (admin only)
}


class AnimatedBackground(QWidget):
    def __init__(self):
        super().__init__()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#13172a"))


class DashboardWindow(AnimatedBackground):
    def __init__(self, db_conn=None):
        super().__init__()
        self._db_conn = db_conn

        self.setWindowTitle("AI-Powered Student Risk Prediction System")
        self.resize(1500, 900)
        self.nav_buttons = {}
        self.setup_ui()
        self.run_intro_animation()

    def create_nav_button(self, text, icon_path=None):
        button = QPushButton(text)
        button.setCheckable(True)
        button.setFixedHeight(35)
        if icon_path:
            button.setIcon(QIcon(icon_path))
        return button

    def run_intro_animation(self):
        pass

    def on_nav_button_clicked(self, button_text, page_index):
        for btn in self.nav_buttons.values():
            btn.setChecked(False)
        self.nav_buttons[button_text].setChecked(True)
        self.stacked_widget.setCurrentIndex(page_index)

    def create_scrollable_page(self, page_widget):
        container = QWidget()
        container.setObjectName("pageShell")
        container.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        if hasattr(page_widget, "fixed_header_container"):
            if hasattr(page_widget, "main_layout"):
                for i in range(page_widget.main_layout.count()):
                    item = page_widget.main_layout.itemAt(i)
                    if item and item.widget() is page_widget.fixed_header_container:
                        page_widget.main_layout.removeWidget(
                            page_widget.fixed_header_container
                        )
                        break
            container_layout.addWidget(page_widget.fixed_header_container, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page_widget)
        container_layout.addWidget(scroll, 1)
        return container

    def setup_ui(self):

        # ── Main layout ───────────────────────────────────────────────
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        content_h_layout = QHBoxLayout()
        content_h_layout.setContentsMargins(20, 20, 20, 20)
        content_h_layout.setSpacing(20)
        content_h_layout.setStretch(0, 0)
        content_h_layout.setStretch(1, 1)

        # ── Sidebar ───────────────────────────────────────────────────
        self.sidebar = QFrame()
        self.sidebar.setFixedWidth(320)
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding
        )
        self.sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(40)
        shadow.setXOffset(0)
        shadow.setYOffset(10)
        shadow.setColor(QColor(0, 0, 0, 120))
        self.sidebar.setGraphicsEffect(shadow)

        sidebar_outer = QVBoxLayout(self.sidebar)
        sidebar_outer.setContentsMargins(20, 20, 20, 20)
        sidebar_outer.setSpacing(12)

        # Logo
        title_layout = QHBoxLayout()
        logo_label = QLabel()
        pixmap = QPixmap("assets/main_logo.png")
        logo_label.setPixmap(
            pixmap.scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio)
        )
        title = QLabel("EarlyAlert")
        title.setObjectName("systemTitle")
        subtitle = QLabel("Student Risk System")
        subtitle.setObjectName("subtitle")
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)

        title_layout.addWidget(logo_label)
        title_layout.addSpacing(5)
        title_layout.addWidget(title)
        title_layout.addStretch()

        sidebar_outer.addLayout(title_layout)
        sidebar_outer.addWidget(subtitle)
        sidebar_outer.addSpacing(6)
        sidebar_outer.addWidget(line)

        # Scrollable nav
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setObjectName("sidebarScroll")
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setFrameShape(QFrame.Shape.NoFrame)
        sidebar_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        sidebar_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        nav_content = QWidget()
        nav_content.setObjectName("sidebarNavContent")
        sidebar_layout = QVBoxLayout(nav_content)
        sidebar_layout.setContentsMargins(0, 10, 4, 10)
        sidebar_layout.setSpacing(12)

        # ── OVERVIEW ─────────────────────────────────────────────────
        sidebar_layout.addWidget(self._section_label("OVERVIEW SECTION"))

        for text, icon, key in [
            ("Dashboard",     "assets/icons/dashboard.svg",       "Dashboard"),
            ("Data Analytics","assets/icons/analytics.png",       "Data Analytics"),
            ("Risk Alerts",   "assets/icons/risk-alerts.svg",     "Risk Alerts"),
            ("Student Cohort","assets/icons/student-cohorts.svg", "Student Cohort"),
        ]:
            idx = _PAGE_IDX[key]
            btn = self.create_nav_button(text, icon)
            self.nav_buttons[key] = btn
            btn.clicked.connect(
                lambda _, i=idx, k=key: self.on_nav_button_clicked(k, i)
            )
            sidebar_layout.addWidget(btn)

        sidebar_layout.addSpacing(5)

        # ── DATA UPLOADS ──────────────────────────────────────────────
        sidebar_layout.addWidget(self._section_label("DATA UPLOADS"))

        for text, key in [
            ("MIS Portal",       "MIS Portal"),
            ("SAO Portal",       "SAO Portal"),
            ("Guidance Portal",  "Guidance Portal"),
            ("Registrar Portal", "Registrar Portal"),
        ]:
            idx = _PAGE_IDX[key]
            btn = self.create_nav_button(text, "assets/icons/check.svg")
            self.nav_buttons[key] = btn
            btn.clicked.connect(
                lambda _, i=idx, k=key: self.on_nav_button_clicked(k, i)
            )
            sidebar_layout.addWidget(btn)

        sidebar_layout.addSpacing(10)

        # ── MACHINE LEARNING ──────────────────────────────────────────
        sidebar_layout.addWidget(self._section_label("MACHINE LEARNING"))

        for text, icon, key in [
            ("Data Merge & Pipeline", "assets/icons/pipeline.svg",       "Data Merge & Pipeline"),
            ("Model Training",        "assets/icons/model-training.svg", "Model Training"),
        ]:
            idx = _PAGE_IDX[key]
            btn = self.create_nav_button(text, icon)
            self.nav_buttons[key] = btn
            btn.clicked.connect(
                lambda _, i=idx, k=key: self.on_nav_button_clicked(k, i)
            )
            sidebar_layout.addWidget(btn)

        sidebar_layout.addSpacing(10)

        # ── PREDICTION ────────────────────────────────────────────────
        sidebar_layout.addWidget(self._section_label("PREDICTION"))

        for text, icon, key in [
            ("Prediction",         "assets/icons/play.svg",    "Prediction"),
            ("Prediction History", "assets/icons/play.svg",    "Prediction History"),  # ← NEW
        ]:
            idx = _PAGE_IDX[key]
            btn = self.create_nav_button(text, icon)
            self.nav_buttons[key] = btn
            btn.clicked.connect(
                lambda _, i=idx, k=key: self.on_nav_button_clicked(k, i)
            )
            sidebar_layout.addWidget(btn)

        # ── ADMINISTRATION (admin-only) ──────────────────────────────
        from services.auth_service import AuthService as _AS
        if _AS.current_role() == "admin":
            sidebar_layout.addSpacing(10)
            sidebar_layout.addWidget(self._section_label("ADMINISTRATION"))
            idx  = _PAGE_IDX["Settings"]
            key  = "Settings"
            btn  = self.create_nav_button("Settings", "assets/icons/check.svg")
            self.nav_buttons[key] = btn
            btn.clicked.connect(
                lambda _, i=idx, k=key: self.on_nav_button_clicked(k, i)
            )
            sidebar_layout.addWidget(btn)

        sidebar_layout.addStretch()
        sidebar_scroll.setWidget(nav_content)
        sidebar_outer.addWidget(sidebar_scroll, 1)

        # ── Admin card ────────────────────────────────────────────────
        admin_card = QFrame()
        admin_card.setObjectName("adminCard")
        admin_layout = QVBoxLayout()
        line2 = QFrame()
        line2.setFrameShape(QFrame.Shape.HLine)

        from services.auth_service import AuthService
        _user = AuthService.current_user() or {}
        admin_name = QLabel(_user.get("full_name", "—"))
        admin_name.setObjectName("adminName")
        admin_role = QLabel(_user.get("role", "").title())
        admin_role.setObjectName("adminRole")

        logout_btn = QPushButton("Sign Out")
        logout_btn.setObjectName("logoutBtn")
        logout_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        logout_btn.setFixedHeight(30)
        logout_btn.clicked.connect(self._on_logout)

        admin_layout.addWidget(logout_btn)
        admin_layout.addWidget(line2)
        admin_layout.addWidget(admin_name)
        admin_layout.addWidget(admin_role)
        admin_card.setLayout(admin_layout)
        sidebar_outer.addWidget(admin_card)

        # ── Content area ──────────────────────────────────────────────
        self.content_area = QFrame()
        self.content_area.setObjectName("contentArea")
        self.content_area.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.content_area.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        content_area_layout = QVBoxLayout(self.content_area)
        content_area_layout.setContentsMargins(0, 0, 0, 0)
        content_area_layout.setSpacing(0)

        self.stacked_widget = QStackedWidget()
        self.stacked_widget.setObjectName("stackedWidget")
        self.stacked_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        content_area_layout.addWidget(self.stacked_widget)

        # ── Set DB connection FIRST so pages can use it immediately ─────
        if self._db_conn:
            from services.data_store import DataStore
            DataStore.get().set_db_conn(self._db_conn)

        # ── Instantiate pages ─────────────────────────────────────────
        self.dashboard_page          = DashboardPage()
        self._analytics_page         = AnalyticsPage()
        self.risk_alerts_page        = RiskAlertsPage()
        self.student_cohort_page     = StudentCohortPage()
        self.model_training_page     = ModelTrainingPage()
        self.merge_pipeline_page     = DataMergePipelinePage()
        self.mis_portal_page         = MisPortalPage()
        self.sao_portal_page         = SaoPortalPage()
        self.guidance_portal_page    = GuidancePortalPage()
        self.registrar_portal_page   = RegistrarPortalPage()
        self.prediction_page         = PredictionPage()
        self.prediction_history_page = PredictionHistoryPage()   # ← NEW
        self.settings_page           = SettingsPage()             # ← NEW

        # Wire proceed button on merge page → Model Training
        self.merge_pipeline_page._on_proceed_training = (
            lambda: self.on_nav_button_clicked(
                "Model Training", _PAGE_IDX["Model Training"]
            )
        )

        # ── Wrap pages that have a fixed header ───────────────────────
        # PredictionHistoryPage has no fixed_header_container so it goes
        # directly into a plain scroll area via create_scrollable_page.
        pages_in_order = [
            (self.dashboard_page,          _PAGE_IDX["Dashboard"]),
            (self._analytics_page,         _PAGE_IDX["Data Analytics"]),
            (self.risk_alerts_page,        _PAGE_IDX["Risk Alerts"]),
            (self.student_cohort_page,     _PAGE_IDX["Student Cohort"]),
            (self.model_training_page,     _PAGE_IDX["Model Training"]),
            (self.merge_pipeline_page,     _PAGE_IDX["Data Merge & Pipeline"]),
            (self.mis_portal_page,         _PAGE_IDX["MIS Portal"]),
            (self.sao_portal_page,         _PAGE_IDX["SAO Portal"]),
            (self.guidance_portal_page,    _PAGE_IDX["Guidance Portal"]),
            (self.registrar_portal_page,   _PAGE_IDX["Registrar Portal"]),
            (self.prediction_page,         _PAGE_IDX["Prediction"]),
            (self.prediction_history_page, _PAGE_IDX["Prediction History"]),
            (self.settings_page,           _PAGE_IDX["Settings"]),
        ]

        # Sort by index so they land in the stacked widget in the right order
        pages_in_order.sort(key=lambda x: x[1])

        for page_widget, _ in pages_in_order:
            scrollable = self.create_scrollable_page(page_widget)
            self.stacked_widget.addWidget(scrollable)

        # ── Default page ──────────────────────────────────────────────
        self.stacked_widget.setCurrentIndex(_PAGE_IDX["Dashboard"])
        self.nav_buttons["Dashboard"].setChecked(True)

        # ── Assemble ──────────────────────────────────────────────────
        content_h_layout.addWidget(self.sidebar)
        content_h_layout.addWidget(self.content_area, 1)
        main_layout.addLayout(content_h_layout, 1)
        self.setLayout(main_layout)

    # ── Helper ────────────────────────────────────────────────────────
    @staticmethod
    def _section_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("sectionLabel")
        return lbl

    def closeEvent(self, event):
        """Clean up page listeners and workers before window is destroyed."""
        try:
            from services.data_store import DataStore
            store = DataStore.get()
            for attr in ("dashboard_page", "risk_alerts_page",
                         "student_cohort_page", "prediction_history_page",
                         "settings_page"):
                page = getattr(self, attr, None)
                if page and hasattr(page, "_on_store_updated"):
                    store.remove_listener(page._on_store_updated)
                if page and hasattr(page, "closeEvent"):
                    try:
                        page.closeEvent(event)
                    except Exception:
                        pass
        except Exception:
            pass
        super().closeEvent(event)

    def _on_logout(self):
        from PyQt6.QtWidgets import QMessageBox
        from services.auth_service import AuthService
        from ui.pages.login_dialog import LoginDialog
        from ui.counselor_window import _launch_for_role

        # ── Confirmation ──────────────────────────────────────────────
        msg = QMessageBox(self)
        msg.setWindowTitle("Sign Out")
        msg.setText("Are you sure you want to sign out?")
        msg.setInformativeText(
            "You will be returned to the login screen.")
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes |
            QMessageBox.StandardButton.Cancel
        )
        msg.setDefaultButton(QMessageBox.StandardButton.Cancel)
        msg.button(QMessageBox.StandardButton.Yes).setText("Sign Out")
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
                padding: 8px 24px; min-width: 80px;
            }
            QMessageBox QPushButton:hover {
                background-color: rgba(255,255,255,0.12);
            }
            QMessageBox QPushButton[text="Sign Out"] {
                background-color: rgba(255,91,91,0.15);
                border-color: rgba(255,91,91,0.40);
                color: #ff5b5b;
            }
            QMessageBox QPushButton[text="Sign Out"]:hover {
                background-color: rgba(255,91,91,0.28);
            }
        """)

        if msg.exec() != QMessageBox.StandardButton.Yes:
            return

        AuthService.logout(self._db_conn)
        self.close()

        dialog = LoginDialog()
        if dialog.exec() != LoginDialog.DialogCode.Accepted:
            sys.exit(0)

        # Route to correct window based on role — not always DashboardWindow
        _launch_for_role(dialog.db_conn)