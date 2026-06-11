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




class AnimatedBackground(QWidget):
    def __init__(self):
        super().__init__()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#13172a"))

class DashboardWindow(AnimatedBackground):
    def __init__(self):
        super().__init__()

        self.setWindowTitle(
            "AI-Powered Student Risk Prediction System"
        )

        self.resize(1500,900)
        self.nav_buttons = {}
        self.setup_ui()
        self.run_intro_animation()

    def create_nav_button(self, text, icon_path=None):
        """Helper method to create a navigation button with an icon."""
        button = QPushButton(text)
        button.setCheckable(True)
        button.setFixedHeight(35)

        if icon_path:
            icon = QIcon(icon_path)
            button.setIcon(icon)

        return button

    def run_intro_animation(self):
        """Placeholder for the introduction animation."""
        pass

    def on_nav_button_clicked(self, button_text, page_index):
        """Handle navigation button clicks."""
        # Uncheck all buttons
        for btn in self.nav_buttons.values():
            btn.setChecked(False)
        
        # Check the current button
        self.nav_buttons[button_text].setChecked(True)
        
        # Switch to the page
        self.stacked_widget.setCurrentIndex(page_index)

    def create_scrollable_page(self, page_widget):
        """
        Wraps a page inside a container with fixed header and scrollable content.
        The page_widget should have fixed_header_container and main_layout as instance attributes.
        """
        container = QWidget()
        container.setObjectName("pageShell")
        container.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # Add fixed header (non-scrollable)
        if hasattr(page_widget, 'fixed_header_container'):
            # Remove header from main layout first
            if hasattr(page_widget, 'main_layout'):
                # Find and remove the fixed_header_container from main_layout
                for i in range(page_widget.main_layout.count()):
                    item = page_widget.main_layout.itemAt(i)
                    if item and item.widget() is page_widget.fixed_header_container:
                        page_widget.main_layout.removeWidget(page_widget.fixed_header_container)
                        break
            
            # Add the header as fixed (non-scrollable)
            container_layout.addWidget(page_widget.fixed_header_container, 0)
        
        # Create scrollable area for remaining content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        # Add the page as scrollable content
        scroll.setWidget(page_widget)
        container_layout.addWidget(scroll, 1)

        return container

    def setup_ui(self):

        #  =====================================
        # MAIN LAYOUT
        #  =====================================

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Create horizontal layout for sidebar and content
        content_h_layout = QHBoxLayout()
        content_h_layout.setContentsMargins(20, 20, 20, 20)
        content_h_layout.setSpacing(20)
        content_h_layout.setStretch(0, 0)  # sidebar
        content_h_layout.setStretch(1, 1)  # pages

        #  =====================================
        # SIDEBAR
        #  =====================================

        self.sidebar = QFrame()
        self.sidebar.setFixedWidth(320)
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Expanding,
        )
        self.sidebar.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True
        )

        # Shadow
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(40)
        shadow.setXOffset(0)
        shadow.setYOffset(10)
        shadow.setColor(QColor(0,0,0,120))
        self.sidebar.setGraphicsEffect(shadow)

        sidebar_outer = QVBoxLayout(self.sidebar)
        sidebar_outer.setContentsMargins(20, 20, 20, 20)
        sidebar_outer.setSpacing(12)

        #======================================
        # LOGO (fixed — does not scroll)
        # =====================================
        title_layout = QHBoxLayout()
        logo_label = QLabel()
        pixmap = QPixmap("assets/main_logo.png")
        scaled_pixmap = pixmap.scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio)
        logo_label.setPixmap(scaled_pixmap)
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

        #=======================================
        # SCROLLABLE NAVIGATION
        #=======================================

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

        #=======================================
        # OVERVIEW SECTION
        #=======================================

        overview_label = QLabel("OVERVIEW SECTION")
        overview_label.setObjectName("sectionLabel")
        sidebar_layout.addWidget(overview_label)
        overview_buttons = [
            {"text": "Dashboard", "icon": "assets/icons/dashboard.svg"},
            {"text": "Risk Alerts", "icon": "assets/icons/risk-alerts.svg"},            
            {"text": "Student Cohort", "icon": "assets/icons/student-cohorts.svg"},
        ]
        for i, item in enumerate(overview_buttons):
            button = self.create_nav_button(item["text"], item["icon"])
            self.nav_buttons[item["text"]] = button
            button.clicked.connect(lambda checked, idx=i, text=item["text"]: self.on_nav_button_clicked(text, idx))
            sidebar_layout.addWidget(button) 

        sidebar_layout.addSpacing(5) 

        #=======================================
        # Data Uploads Section
        #=======================================

        uploads_label = QLabel("DATA UPLOADS")
        uploads_label.setObjectName("sectionLabel")
        sidebar_layout.addWidget(uploads_label)

        upload_buttons = [
            {"text": "MIS Portal", "icon": "assets/icons/check.svg", "page_index": 5},
            {"text": "SAO Portal", "icon": "assets/icons/check.svg", "page_index": 6},
            {"text": "Guidance Portal", "icon": "assets/icons/check.svg", "page_index": 7},
            {"text": "Registrar Portal", "icon": "assets/icons/check.svg", "page_index": 8},
        ]

        for item in upload_buttons:
            button = self.create_nav_button(item["text"], item["icon"])
            page_index = item["page_index"]
            self.nav_buttons[item["text"]] = button
            button.clicked.connect(
                lambda checked, idx=page_index, text=item["text"]: (
                    self.on_nav_button_clicked(text, idx)
                )
            )
            sidebar_layout.addWidget(button)

        sidebar_layout.addSpacing(10)
        #=======================================
        # MACHINE LEARNING Section
        #=======================================

        ml_label = QLabel("MACHINE LEARNING")
        ml_label.setObjectName("sectionLabel")
        sidebar_layout.addWidget(ml_label)

        ml_buttons = [
            {
                "text": "Data Merge & Pipeline",
                "icon": "assets/icons/pipeline.svg",
                "page_index": 4,
            },
            {
                "text": "Model Training",
                "icon": "assets/icons/model-training.svg",
                "page_index": 3,
            },
        ]
        for item in ml_buttons:
            button = self.create_nav_button(item["text"], item["icon"])
            page_index = item["page_index"]
            self.nav_buttons[item["text"]] = button
            button.clicked.connect(lambda checked, idx=page_index, text=item["text"]: self.on_nav_button_clicked(text, idx))
            sidebar_layout.addWidget(button)

        sidebar_layout.addSpacing(10)
        #=======================================
        # PREDICTION Section
        #=======================================

        prediction_label = QLabel("PREDICTION")
        prediction_label.setObjectName("sectionLabel")
        sidebar_layout.addWidget(prediction_label)

        prediction_buttons = [
            {
                "text": "Prediction",
                "icon": "assets/icons/play.svg",
                "page_index": 9,
            },
        ]
        for item in prediction_buttons:
            button = self.create_nav_button(item["text"], item["icon"])
            page_index = item["page_index"]
            self.nav_buttons[item["text"]] = button
            button.clicked.connect(lambda checked, idx=page_index, text=item["text"]: self.on_nav_button_clicked(text, idx))
            sidebar_layout.addWidget(button)

        sidebar_scroll.setWidget(nav_content)
        sidebar_outer.addWidget(sidebar_scroll, 1)

        #=======================================
        # ADMIN CARD (fixed — does not scroll)
        #=======================================

        admin_card = QFrame()
        admin_card.setObjectName("adminCard")
        admin_layout = QVBoxLayout()
        line2 = QFrame()
        line2.setFrameShape(QFrame.Shape.HLine)

        admin_name = QLabel("Kenneth Roi P. Novabos")
        admin_name.setObjectName("adminName")

        admin_role = QLabel("System Administrator")
        admin_role.setObjectName("adminRole")
        admin_layout.addWidget(line2)
        admin_layout.addWidget(admin_name)
        admin_layout.addWidget(admin_role)

        admin_card.setLayout(admin_layout)
        sidebar_outer.addWidget(admin_card)

        #  =====================================
        # STACKED WIDGET FOR PAGES
        #  =====================================

        self.content_area = QFrame()
        self.content_area.setObjectName("contentArea")
        self.content_area.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.content_area.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True
        )

        content_area_layout = QVBoxLayout(self.content_area)
        content_area_layout.setContentsMargins(0, 0, 0, 0)
        content_area_layout.setSpacing(0)

        self.stacked_widget = QStackedWidget()
        self.stacked_widget.setObjectName("stackedWidget")
        self.stacked_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        content_area_layout.addWidget(self.stacked_widget)
        # Create page instances
        self.dashboard_page = DashboardPage()
        self.risk_alerts_page = RiskAlertsPage()
        self.student_cohort_page = StudentCohortPage()
        self.model_training_page = ModelTrainingPage()
        self.merge_pipeline_page = DataMergePipelinePage()
        self.mis_portal_page = MisPortalPage()
        self.sao_portal_page = SaoPortalPage()
        self.guidance_portal_page = GuidancePortalPage()
        self.registrar_portal_page = RegistrarPortalPage()
        self.prediction_page = PredictionPage()

        self.merge_pipeline_page._on_proceed_training = (
            lambda: self.on_nav_button_clicked("Model Training", 3)
        )
                
        # =====================================
        # WRAP PAGES WITH SCROLL AREA
        # =====================================

        dashboard_scroll = self.create_scrollable_page(
            self.dashboard_page
        )

        risk_alerts_scroll = self.create_scrollable_page(
            self.risk_alerts_page
        )

        student_cohort_scroll = self.create_scrollable_page(
            self.student_cohort_page
        )

        model_training_scroll = self.create_scrollable_page(
            self.model_training_page
        )

        merge_pipeline_scroll = self.create_scrollable_page(
            self.merge_pipeline_page
        )

        mis_portal_scroll = self.create_scrollable_page(
            self.mis_portal_page
        )

        sao_portal_scroll = self.create_scrollable_page(
            self.sao_portal_page
        )

        guidance_portal_scroll = self.create_scrollable_page(
            self.guidance_portal_page
        )

        registrar_portal_scroll = self.create_scrollable_page(
            self.registrar_portal_page
        )

        prediction_scroll = self.create_scrollable_page(
            self.prediction_page
        )

        # =====================================
        # ADD SCROLLABLE PAGES TO STACK
        # =====================================

        self.stacked_widget.addWidget(
            dashboard_scroll
        )

        self.stacked_widget.addWidget(
            risk_alerts_scroll
        )

        self.stacked_widget.addWidget(
            student_cohort_scroll
        )

        self.stacked_widget.addWidget(
            model_training_scroll
        )

        self.stacked_widget.addWidget(
            merge_pipeline_scroll
        )

        self.stacked_widget.addWidget(
            mis_portal_scroll
        )

        self.stacked_widget.addWidget(
            sao_portal_scroll
        )

        self.stacked_widget.addWidget(
            guidance_portal_scroll
        )

        self.stacked_widget.addWidget(
            registrar_portal_scroll
        )

        self.stacked_widget.addWidget(
            prediction_scroll
        )

        # Set dashboard as the starting page
        self.stacked_widget.setCurrentIndex(0)
        self.nav_buttons["Dashboard"].setChecked(True)
        
        # =====================================
        # ADD TO MAIN LAYOUT
        # =====================================
        
        content_h_layout.addWidget(self.sidebar)
        content_h_layout.addWidget(self.content_area, 1)

        main_layout.addLayout(content_h_layout, 1)

        self.setLayout(main_layout)
    