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
    QSizePolicy,
    QGraphicsOpacityEffect
)

from PyQt6.QtCore import (
    Qt,
    QPropertyAnimation,
    QEasingCurve,
    QRect,
    QTimer,
    QSize,
    QPropertyAnimation,
    QEasingCurve,
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

from PyQt6.QtCharts import (
    QChart, QChartView, QPieSeries, QBarSet, QStackedBarSeries, QBarCategoryAxis, QValueAxis
)



class AnimatedBackground(QWidget):
    def __init__(self):
        super().__init__()
    
        self.color_shift = 0

        self.timer = QTimer()
        self.timer.timeout.connect(self.animate_gradient)
        self.timer.start(60)

    def animate_gradient(self):
        self.color_shift += 1
        if self.color_shift > 360:
            self.color_shift = 0
        self.update()


    def paintEvent(self, event):
        painter = QPainter(self)

        gradient = QLinearGradient(
            0,
            0,
            self.width(),
            self.height()
        )

        gradient.setColorAt(
            1,
            QColor.fromHsv(
                (260 + self.color_shift) % 360,
                180,
                40
            )
        )

        painter.fillRect(
            self.rect(),
            QBrush(gradient)
        )

class DashboardWindw(AnimatedBackground):
    def __init__(self):
        super().__init__()

        self.setWindowTitle(
            "AI-Powered Student Risk Prediction System"
        )

        self.resize(1500,900)
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

    def create_metric_card(self, title_text, value_text, status, remarks):
        """Helper method to create a metric visualization card."""
        card = QFrame()
        card.setObjectName("metricCard")


        layout = QVBoxLayout(card)
        
        title = QLabel(title_text)
        title.setObjectName("metricTitle")

        value = QLabel(value_text)
        value.setObjectName("metricValue")

        status = QLabel(status)
        status.setObjectName("metricStatus")

        remarks = QLabel(remarks)
        remarks.setObjectName("metricRemarks")        
        
        layout.addWidget(title)
        layout.addWidget(value)
        layout.addWidget(status)
        layout.addWidget(remarks)

        return card
        
    # =====================================
    # MODERN DONUT CHART
    # =====================================

    def create_risk_distribution_chart(self):

        # =====================================
        # SERIES
        # =====================================

        series = QPieSeries()

        # VALUES
        high_risk = series.append(
            "High Risk",
            15
        )

        moderate_risk = series.append(
            "Moderate Risk",
            28
        )

        low_risk = series.append(
            "Low Risk",
            57
        )

        # =====================================
        # DONUT STYLE
        # =====================================

        series.setHoleSize(0.40)

        # =====================================
        # COLORS
        # =====================================

        high_risk.setColor(
            QColor("#ff5b5b")
        )

        moderate_risk.setColor(
            QColor("#f5b335")
        )

        low_risk.setColor(
            QColor("#34d399")
        )

        # REMOVE OUTLINES
        for slice in series.slices():

            slice.setBorderColor(
                QColor("transparent")
            )

            slice.setLabelVisible(False)

        # =====================================
        # CHART
        # =====================================

        chart = QChart()

        chart.addSeries(series)

        chart.setBackgroundVisible(False)

        chart.setPlotAreaBackgroundVisible(False)

        chart.legend().setVisible(True)

        chart.legend().setAlignment(
            Qt.AlignmentFlag.AlignBottom
        )

        # LEGEND COLOR
        chart.legend().setLabelColor(
            QColor("#b8bcc8")
        )

        # REMOVE MARGINS
        chart.layout().setContentsMargins(
            0,
            0,
            0,
            0
        )

        chart.setMargins(
            QMargins(0, 0, 0, 0)
        )

        # =====================================
        # CHART VIEW
        # =====================================

        chart_view = QChartView(chart)

        chart_view.setRenderHint(
            QPainter.RenderHint.Antialiasing
        )

        chart_view.setMinimumHeight(350)

        chart_view.setStyleSheet(
            """
            background: transparent;
            border: none;
            """
        )

        return chart_view

    #BAR CHART
    def create_risk_analytics_chart(self):
        """Generates a stacked bar chart matching the CTU Risk Score panel design."""
        
        # 1. Define Data Sets (High and Moderate Risks)
        set_high = QBarSet("High")
        set_moderate = QBarSet("Moderate")
        
        # Matching hex colors from your image
        set_high.setColor(QColor("#C0392B"))      # Crimson Red
        set_moderate.setColor(QColor("#D4AC0D"))  # Muted Amber/Gold

        # Populate the risk data points matching the chart's approximate values
        # Order: CITE, CBAA, CTE, COED, CON, CAS
        set_high.append([100, 100, 100, 100, 100, 100])
        set_moderate.append([68, 62, 55, 48, 38, 71])

        # 2. Assemble the Series
        series = QStackedBarSeries()
        series.append(set_high)
        series.append(set_moderate)
        
        # UI Polish: This turns on subtle round corners on the tops of the bars
        # note: Core feature in PyQt6 QtCharts
        series.setLabelsVisible(False) 

        # 3. Create and Configure the Chart Base
        chart = QChart()
        chart.addSeries(series)
        chart.setTitle("RISK SCORE BY COLLEGE / DEPARTMENT")
        
        # Styling Title & Background
        chart.setTitleFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        chart.setTitleBrush(QColor("#85929E")) # Light muted blue/grey title
        chart.setBackgroundBrush(
            QBrush(Qt.BrushStyle.NoBrush)
        )
        chart.setMargins(self.contentsMargins())

        # 4. Configure X-Axis (Colleges)
        axis_x = QBarCategoryAxis()
        categories = ["CITE", "CBAA", "CTE", "COED", "CON", "CAS"]
        axis_x.append(categories)
        axis_x.setLabelsColor(QColor("#85929E"))
        axis_x.setLabelsFont(QFont("Segoe UI", 10))
        axis_x.setGridLineColor(QColor("#2C3E50")) # Dark subtle gridline split
        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        series.attachAxis(axis_x)

        # 5. Configure Y-Axis (Scale up to 120)
        axis_y = QValueAxis()
        axis_y.setRange(0, 120)
        axis_y.setTickCount(7) # Controls increments: 0, 20, 40, 60, 80, 100, 120
        axis_y.setLabelsColor(QColor("#85929E"))
        axis_y.setLabelsFont(QFont("Segoe UI", 10))
        axis_y.setGridLineColor(QColor("#1C2833")) # Extremely dark internal gridlines
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(axis_y)

        # 6. Align Legend to the Top
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignTop)
        chart.legend().setLabelColor(QColor("white"))
        chart.legend().setFont(QFont("Segoe UI", 9))

        # 7. Package everything into a Render View
        chart_view = QChartView(chart)
        chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        chart_view.setStyleSheet("background: transparent; border: none;")

        chart_view.setSizePolicy(
        QSizePolicy.Policy.Expanding, 
        QSizePolicy.Policy.Expanding
        )
        
        return chart_view

# =====================================
# SHAP FACTOR ITEM
# =====================================

    def create_shap_factor(
        self,
        label_text,
        percentage,
        color
    ):

        container = QWidget()

        layout = QHBoxLayout(container)

        layout.setContentsMargins(0, 0, 0, 0)

        layout.setSpacing(15)

        # =====================================
        # FEATURE LABEL
        # =====================================

        label = QLabel(label_text)

        label.setObjectName("shapLabel")

        label.setFixedWidth(190)

        # =====================================
        # PROGRESS BAR
        # =====================================

        progress = QProgressBar()

        progress.setValue(percentage)

        progress.setTextVisible(False)

        progress.setFixedHeight(10)

        progress.setObjectName("shapProgress")

        progress.setStyleSheet(f'''
            QProgressBar {{
                background-color: rgba(255,255,255,0.08);
                border-radius: 5px;
                border: none;
            }}

            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 5px;
            }}
        ''')

        # =====================================
        # PERCENTAGE LABEL
        # =====================================

        percent = QLabel(f"{percentage}%")

        percent.setObjectName("shapPercent")

        percent.setFixedWidth(40)

        # =====================================
        # ADD TO LAYOUT
        # =====================================

        layout.addWidget(label)

        layout.addWidget(progress)

        layout.addWidget(percent)

        return container

    def run_intro_animation(self):
        """Placeholder for the introduction animation."""
        pass

    def setup_ui(self):

        #  =====================================
        # MAIN LAYOUT
        #  =====================================

        main_layout = QVBoxLayout()

        main_layout.setContentsMargins(
            0,
            0,
            0,
            0
        )

        main_layout.setSpacing(0)
        
        # Create horizontal layout for sidebar and content
        content_h_layout = QHBoxLayout()
        content_h_layout.setContentsMargins(
            20,
            20,
            20,
            20
        )
        content_h_layout.setSpacing(20)

        #  =====================================
        # SIDEBAR
        #  =====================================

        self.sidebar = QFrame()
        self.sidebar.setFixedWidth(320)
        self.sidebar.setObjectName("sidebar")

        # Blur effect (Commented out because applying blur makes child text elements unreadable)
        blur = QGraphicsBlurEffect()
        blur.setBlurRadius(20)
        self.sidebar.setGraphicsEffect(blur)

        # Shadow
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(40)
        shadow.setXOffset(0)
        shadow.setYOffset(10)
        shadow.setColor(QColor(0,0,0,120))
        self.sidebar.setGraphicsEffect(shadow)

        sidebar_layout = QVBoxLayout()

        sidebar_layout.setContentsMargins(
            20,
            20,
            20,
            20
        )

        sidebar_layout.setSpacing(15)

        #======================================
        # LOGO
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
        title_layout.addWidget(logo_label, alignment=Qt.AlignmentFlag.AlignLeft)

        sidebar_layout.addLayout(title_layout)
        sidebar_layout.addWidget(subtitle)
        sidebar_layout.addSpacing(10)
        sidebar_layout.addWidget(line)

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

        for item in overview_buttons:
            button = self.create_nav_button(item["text"], item["icon"])
            sidebar_layout.addWidget(button) 

        sidebar_layout.addSpacing(5) 

        #=======================================
        # Data Uploads Section
        #=======================================

        uploads_label = QLabel("DATA UPLOADS")
        uploads_label.setObjectName("sectionLabel")
        sidebar_layout.addWidget(uploads_label)

        upload_buttons = [
            {"text": "MIS Portal", "icon": "assets/icons/check.svg"},
            {"text": "SAO Portal", "icon": "assets/icons/check.svg"},            
            {"text": "Guidance Portal", "icon": "assets/icons/check.svg"},
        ]

        for item in upload_buttons:
            button = self.create_nav_button(item["text"], item["icon"])
            sidebar_layout.addWidget(button)

        sidebar_layout.addSpacing(10)
        #=======================================
        # MACHINE LEARNING Section
        #=======================================

        ml_label = QLabel("MACHINE LEARNING")
        ml_label.setObjectName("sectionLabel")

        sidebar_layout.addWidget(ml_label)

        ml_buttons = [
            {"text": "Data Pipeline", "icon": "assets/icons/pipeline.svg"},
            {"text": "Model Training", "icon": "assets/icons/model-training.svg"},            
        ]
        for item in ml_buttons:
            button = self.create_nav_button(item["text"], item["icon"])
            sidebar_layout.addWidget(button)

        sidebar_layout.addStretch()

        #=======================================
        # ADMIN CARD
        #=======================================

        admin_card = QFrame()
        admin_card.setObjectName("adminCard")
        admin_layout = QVBoxLayout()
        line2 = QFrame()
        line2.setFrameShape(QFrame.Shape.HLine)

        admin_name = QLabel("Kenneth Roi P. Novabos")

        admin_name.setObjectName("adminName")  # Fixed: Lowercase 'o'

        admin_role = QLabel("System Administrator")
        admin_role.setObjectName("adminRole")
        admin_layout.addWidget(line2)
        admin_layout.addWidget(admin_name)
        admin_layout.addWidget(admin_role)
    

        admin_card.setLayout(admin_layout)
        sidebar_layout.addWidget(admin_card)

        self.sidebar.setLayout(sidebar_layout)

        #  =====================================
        # MAIN CONTENT AREA
        #  =====================================
        # =====================================
        # SCROLL AREA
        # =====================================

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setObjectName("scrollArea")
        # =====================================
        # CONTENT CONTAINER
        # =====================================

        self.content = QFrame()

        self.content.setObjectName(
            "contentArea"
        )

        scroll.setWidget(self.content)

        self.content.setObjectName("contentArea")

        content_layout = QVBoxLayout()

        content_layout.setContentsMargins(
            30,
            30,
            30,
            30
        )

        content_layout.setSpacing(20)

        # =====================================
        # FIXED HEADER PANEL (NOT SCROLLABLE)
        # =====================================
        
        fixed_header_container = QFrame()
        fixed_header_container.setObjectName("fixedHeaderContainer")
        fixed_header_layout = QVBoxLayout()
        fixed_header_layout.setContentsMargins(20, 20, 20, 20)
        fixed_header_layout.setSpacing(20)
        
        # HEADER

        header_layout = QHBoxLayout()

        header_layout.setSpacing(15)

        #Dashboard Title and Subtitle

        header_text_layout = QVBoxLayout()
        header_text_layout.setSpacing(5)

        #Main Header    
        header = QLabel("DASHBOARD")
        header.setObjectName("header")

        #Subheader
        subHeader = QLabel("AI-powered student risk monitoring overview")
        subHeader.setObjectName("subHeader")

        header_text_layout.addWidget(header)
        header_text_layout.addWidget(subHeader)

        header_layout.addLayout(header_text_layout)
        header_layout.addStretch()
        
        # Model Status

        model_card = QFrame()
        model_card.setObjectName("modelCard")

        model_layout = QHBoxLayout()
        model_layout.setContentsMargins(
            20, 15, 20, 15
        )

        self.model_status =  QLabel("Model Active")
        self.model_status.setObjectName("modelStatus")


        self.model_status.setStyleSheet("""
            #modelStatus {
                color: #2ecc71;
                font-weight: bold;
                font-size: 12px;
            }
        """)

        # 2. Set up the opacity effect for the fade/pulse animation
        self.opacity_effect = QGraphicsOpacityEffect(self.model_status)
        self.model_status.setGraphicsEffect(self.opacity_effect)

        # 3. Create a property animation on the opacity effect
        # We target the 'opacity' property of the effect object
        self.status_animation = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.status_animation.setDuration(1200)               # Duration of one loop in milliseconds
        self.status_animation.setStartValue(1.0)               # Start fully visible
        self.status_animation.setKeyValueAt(0.5, 0.3)          # Fade down to 30% opacity at the halfway mark
        self.status_animation.setEndValue(1.0)                 # Fade back to 100% visible
        self. status_animation.setEasingCurve(QEasingCurve.Type.InOutQuad) # Smooth slowing down and speeding up
        self.status_animation.setLoopCount(-1)                 # Loop infinitely

        # 4. Start the animation
        self.status_animation.start()


        model_semester = QLabel("1st Semester AY 2024-2025")
        model_semester.setObjectName("modelSemester")
        run_button = QPushButton("Run Prediction")
        run_button.setObjectName("runButton")
        run_button.setCursor(Qt.CursorShape.PointingHandCursor)
        run_button.setIcon(QIcon("assets/icons/play.svg"))

        run_button.setFixedWidth(120)
        model_layout.addWidget(self.model_status)
        model_layout.addWidget(model_semester)
        model_layout.addSpacing(5)
        model_layout.addWidget(run_button)  

        model_card.setLayout(model_layout)

        header_layout.addStretch()
        header_layout.addWidget(model_card)
        
        # Add header layout to fixed container
        fixed_header_layout.addLayout(header_layout)
        fixed_header_container.setLayout(fixed_header_layout)


        # METRIC CARDS  
        metrics_layout = QHBoxLayout()

        metric_1 = self.create_metric_card(
            "Total First-Year Students",
            "248",
            "asdasdasd",
            "asdasdada"
        )

        metric_2 = self.create_metric_card(
            "High Risk",
            "94.6%",
              "asdasdasd",
                "asdasdada"
        )

        metric_3 = self.create_metric_card(
            "Moderate Risk",
            "12,489",
            "asdasdasd",
            "asdasdada"
        )
        
        metric_4 = self.create_metric_card(
            "Model Accuracty",
            "12,489",
            "asdasdasd",
            "asdasdada"
        )

        metrics_layout.addWidget(metric_1)
        metrics_layout.addWidget(metric_2)
        metrics_layout.addWidget(metric_3)
        metrics_layout.addWidget(metric_4)

        content_layout.addLayout(metrics_layout)

        # LOADING CARD
        #loading_card = QFrame()

        #loading_card.setObjectName(
         #   "loadingCard"
        #)

        #loading_layout = QVBoxLayout()

       # loading_title = QLabel(
        #    "Model Training Progress"
        #)

        #loading_title.setObjectName(
         #   "cardTitle"
        #)

        #self.progress = QProgressBar()

        #self.progress.setValue(65)

        #self.progress.setTextVisible(True)

        #loading_layout.addWidget(loading_title)
        #loading_layout.addWidget(self.progress)

        #loading_card.setLayout(loading_layout)

        #content_layout.addWidget(loading_card)

        # LARGE ANALYTICS PANEL
        analytics_panel = QFrame()

        # =====================================
        # ANALYTICS PANELS CONTAINER
        # =====================================

        analytics_layout = QHBoxLayout()

        analytics_layout.setSpacing(20)

        # =====================================
        # RISK DISTRIBUTION PANEL
        # =====================================

        risk_distribution_panel = QFrame()

        risk_distribution_panel.setObjectName(
            "analyticsPanel"
        )

        risk_distribution_panel.setMinimumHeight(300)

        distribution_layout = QVBoxLayout()

        distribution_title = QLabel(
            "Risk Distribution Panel"
        )

        distribution_title.setObjectName(
            "cardTitle"
        )

        distribution_text = QLabel(
            "Visual distribution charts and risk clusters will appear here."
        )

        distribution_text.setObjectName(
            "analyticsText"
        )

        distribution_text.setWordWrap(True)

        distribution_layout.addWidget(
            distribution_title
        )
        distribution_layout.addSpacing(10)
        distribution_layout.addWidget(
            distribution_text
        )

        # Donut CHART
        donut_chart = self.create_risk_distribution_chart()

        distribution_layout.addWidget(
            donut_chart
        )

        distribution_layout.addStretch()

        risk_distribution_panel.setLayout(
            distribution_layout
        )

        # =====================================
        # RISK SCORE PANEL
        # =====================================

        risk_score_panel = QFrame()

        risk_score_panel.setObjectName(
            "analyticsPanel"
        )

        risk_score_panel.setMinimumHeight(350)

        score_layout = QVBoxLayout()
  
        # Call the chart generator method we just built
        chart_view = self.create_risk_analytics_chart()
        risk_score_panel.setLayout(
            score_layout
        )
        # =====================================
        # ADD PANELS TO LAYOUT
        # =====================================
        score_layout.addWidget(
            chart_view
        )

        analytics_layout.addWidget(
            risk_distribution_panel
        )

        analytics_layout.addWidget(
            risk_score_panel
        )

        # =====================================
        # ADD TO CONTENT AREA
        # =====================================

        content_layout.addLayout(
            analytics_layout
        )

        # =====================================
        # SECOND ANALYTICS ROW
        # =====================================

        analytics_row_2 = QHBoxLayout()

        analytics_row_2.setSpacing(20)

        # =====================================
        # SHAP IMPORTANCE PANEL
        # =====================================

        shap_panel = QFrame()

        shap_panel.setObjectName(
            "analyticsPanel"
        )

        shap_panel.setMinimumHeight(320)

        shap_layout = QVBoxLayout()

        shap_title = QLabel(
            "Top Risk Factors (SHAP Importance)"
        )

        shap_title.setObjectName(
            "cardTitle"
        )

        shap_text = QLabel(
            "Top predictive features contributing to student risk scores will appear here."
        )

        shap_text.setWordWrap(True)

        shap_text.setObjectName(
            "analyticsText"
        )

        # =====================================
        # SHAP FACTORS
        # =====================================

        shap_layout.addSpacing(10)

        factors = [

            ("GWA drop (sem 1)", 38, "#ff5b5b"),

            ("Absences > 20%", 22, "#ff5b5b"),

            ("No org membership", 14, "#f5b335"),

            ("Working student", 11, "#f5b335"),

            ("Failed ≥ 2 subjects", 9, "#4f8cff"),

            ("Low psych score", 8, "#4f8cff"),

            ("Financial aid lapse", 7, "#4f8cff"),

            ("Referral to guidance", 6, "#4f8cff")
        ]

        for label, value, color in factors:

            factor_widget = self.create_shap_factor(
                label,
                value,
                color
            )

            shap_layout.addWidget(
                factor_widget
            )

        shap_layout.addWidget(shap_title)

        shap_layout.addSpacing(10)

        shap_layout.addWidget(shap_text)

        shap_layout.addSpacing(10)



        shap_layout.addStretch()

        shap_panel.setLayout(shap_layout)

        # =====================================
        # RECENT ALERTS PANEL
        # =====================================

        alerts_panel = QFrame()

        alerts_panel.setObjectName(
            "analyticsPanel"
        )

        alerts_panel.setMinimumHeight(320)

        alerts_layout = QVBoxLayout()

        alerts_title = QLabel(
            "Recent High-Risk Alerts"
        )

        alerts_title.setObjectName(
            "cardTitle"
        )

        alerts_text = QLabel(
            "Recently detected high-risk students requiring intervention."
        )

        alerts_text.setWordWrap(True)

        alerts_text.setObjectName(
            "analyticsText"
        )

        # SAMPLE ALERTS
        alert_1 = QLabel(
            "⚠ BSIT-101 | John Doe | High Risk"
        )

        alert_2 = QLabel(
            "⚠ BSED-202 | Jane Smith | Critical"
        )

        alert_3 = QLabel(
            "⚠ BSBA-303 | Mark Reyes | High Risk"
        )

        alerts = [
            alert_1,
            alert_2,
            alert_3
        ]

        for alert in alerts:
            alert.setObjectName(
                "alertItem"
            )

        alerts_layout.addWidget(alerts_title)

        alerts_layout.addSpacing(10)

        alerts_layout.addWidget(alerts_text)

        alerts_layout.addSpacing(10)

        for alert in alerts:
            alerts_layout.addWidget(alert)

        alerts_layout.addStretch()

        alerts_panel.setLayout(alerts_layout)

        # =====================================
        # ADD PANELS TO SECOND ROW
        # =====================================

        analytics_row_2.addWidget(
            shap_panel
        )

        analytics_row_2.addWidget(
            alerts_panel
        )

        # =====================================
        # ADD SECOND ROW TO CONTENT
        # =====================================

        content_layout.addLayout(
            analytics_row_2
        )

        # =====================================
        # DATA SOURCE COVERAGE ROW
        # =====================================

        coverage_card = QFrame()

        coverage_card.setObjectName(
            "coverageCard"
        )

        coverage_layout = QVBoxLayout()

        coverage_layout.setContentsMargins(
            25,
            20,
            25,
            20
        )

        coverage_layout.setSpacing(18)

        # TITLE
        coverage_title = QLabel(
            "DATA SOURCE COVERAGE"
        )

        coverage_title.setObjectName(
            "coverageTitle"
        )

        coverage_layout.addWidget(
            coverage_title
        )

        # =====================================
        # SOURCES ROW
        # =====================================

        sources_layout = QHBoxLayout()

        sources_layout.setSpacing(20)

        # =====================================
        # MIS SOURCE
        # =====================================

        mis_container = QVBoxLayout()

        mis_header = QLabel(
            "●  MIS — Academic Records        1,248 / 1,248"
        )

        mis_header.setObjectName(
            "sourceHeader"
        )

        mis_progress = QProgressBar()

        mis_progress.setValue(100)

        mis_progress.setObjectName(
            "greenProgress"
        )

        mis_status = QLabel(
            "Complete · Last updated: Oct 14"
        )

        mis_status.setObjectName(
            "sourceStatus"
        )

        mis_container.addWidget(mis_header)
        mis_container.addWidget(mis_progress)
        mis_container.addWidget(mis_status)

        # =====================================
        # SAO SOURCE
        # =====================================

        sao_container = QVBoxLayout()

        sao_header = QLabel(
            "●  SAO — Student Affairs        1,102 / 1,248"
        )

        sao_header.setObjectName(
            "sourceHeader"
        )

        sao_progress = QProgressBar()

        sao_progress.setValue(88)

        sao_progress.setObjectName(
            "greenProgress"
        )

        sao_status = QLabel(
            "88% · 146 missing records"
        )

        sao_status.setObjectName(
            "sourceStatus"
        )

        sao_container.addWidget(sao_header)
        sao_container.addWidget(sao_progress)
        sao_container.addWidget(sao_status)

        # =====================================
        # GUIDANCE SOURCE
        # =====================================

        guidance_container = QVBoxLayout()

        guidance_header = QLabel(
            "●  Guidance — Psych Records        934 / 1248"
        )

        guidance_header.setObjectName(
            "sourceHeader"
        )

        guidance_progress = QProgressBar()

        guidance_progress.setValue(75)

        guidance_progress.setObjectName(
            "orangeProgress"
        )

        guidance_status = QLabel(
            "75% · Upload pending"
        )

        guidance_status.setObjectName(
            "sourceStatus"
        )

        guidance_container.addWidget(
            guidance_header
        )

        guidance_container.addWidget(
            guidance_progress
        )

        guidance_container.addWidget(
            guidance_status
        )

        # =====================================
        # ADD SOURCES
        # =====================================

        sources_layout.addLayout(
            mis_container
        )

        sources_layout.addLayout(
            sao_container
        )

        sources_layout.addLayout(
            guidance_container
        )

        coverage_layout.addLayout(
            sources_layout
        )

        coverage_card.setLayout(
            coverage_layout
        )

        # =====================================
        # ADD TO MAIN CONTENT
        # =====================================

        content_layout.addWidget(
            coverage_card
        )

        self.content.setLayout(content_layout)

        # =====================================
        # ADD TO MAIN LAYOUT
        # =====================================
        
        # Create vertical layout for fixed header and scrollable content
        right_side_layout = QVBoxLayout()
        right_side_layout.setContentsMargins(20, 20, 20, 20)
        right_side_layout.setSpacing(0)
        
        # Add fixed header
        right_side_layout.addWidget(fixed_header_container)
        
        # Add scrollable content
        right_side_layout.addWidget(scroll)
        
        # Add sidebar and right side to main layout
        content_h_layout.addWidget(self.sidebar)
        content_h_layout.addLayout(right_side_layout)
        
        main_layout.addLayout(content_h_layout)

        self.setLayout(main_layout)

        # =====================================
        # STYLESHEET
        # =====================================

        self.setStyleSheet("""
            QWidget {
                font-family: 'Segoe UI';
                color: white;
            }
            #sidebar, #contentArea {
                background-color: rgba(255, 255, 255, 0.08);
                border-radius: 25px;
                border: 1px solid rgba(255, 255, 255, 0.12);
            }
            #systemTitle {
                font-size: 24px;
                font-weight: bold;
                color: white;
            }
            #subtitle {
                font-size: 13px;
                color: rgba(255,255,255,0.7);
            }
            #sectionLabel {
                font-size: 11px;
                color: rgba(255,255,255,0.5);
                font-weight: bold;
                margin-top: 10px;
            }
            QPushButton {
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
                padding: 5px 10px;
                text-align: left;
            }
            #runButton {
                    /* Base Blue Styling with White Text */
            background-color: #1A73E8; 
            color: #FFFFFF;
            font-family: 'Segoe UI';
            font-size: 11px;
            font-weight: 500;
            
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 8px;
            padding: 8px 15px;
            text-align: left;
            }
            #runButton:hover {
            /* Slightly lighter blue on hover */
            background-color: #2980B9;
            }
            #runButton:checked {
                /* Active Status styling: Green text with a dark/glowing contrast background */
                background-color: #0E2F1A;
                color: #2ECC71; /* Vibrant Green text */
                font-weight: bold;
                border: 1px solid #2ECC71;
            }               
            #header {
            font-size: 24px;
            font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.15);
            }
            QPushButton:checked {
                background-color: rgba(255, 255, 255, 0.25);
                font-weight: bold;
            }
            #metricCard, #loadingCard {
                background-color: rgba(0, 0, 0, 0.2);
                border-radius: 15px;
                padding: 15px;
                border-top: 4px solid #2ecc71;       /* Adds a thick, solid green top border */             
            }
            #analyticsPanel {
                background-color: rgba(0, 0, 0, 0.2);
                border-radius: 15px;
                padding: 15px;
                border-top: 4px solid #FFFF00;      
            }
            #metricValue {
                font-size: 20px;
                font-weight: bold;
            }
            #cardTitle {
                font-size: 16px;
                font-weight: bold;
            }
            #factorItem {
                font-size: 13px;
                padding: 10px;
                border-radius: 10px;
                background-color: rgba(255,255,255,0.06);
                margin-top: 4px;
            }
            #alertItem {
                font-size: 13px;
                padding: 10px;
                border-radius: 10px;
                background-color: rgba(255, 99, 71, 0.15);
                margin-top: 4px;
            }
             QScrollArea {
                background: transparent;
                border: none;
            }

            QScrollBar:vertical {
                background: transparent;
                width: 10px;
                margin: 0px;
            }

            QScrollBar::handle:vertical {
                background: rgba(255,255,255,0.2);
                border-radius: 5px;
                min-height: 30px;
            }

            QScrollBar::handle:vertical:hover {
                background: rgba(255,255,255,0.35);
            }

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
            }

            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: transparent;
            }
            #coverageCard {
                background-color: rgba(255,255,255,0.06);
                border-radius: 24px;
                border: 1px solid rgba(255,255,255,0.08);
            }

            #coverageTitle {
                font-size: 15px;
                font-weight: bold;
                color: rgba(255,255,255,0.65);
                letter-spacing: 1px;
            }

            #sourceHeader {
                font-size: 14px;
                font-weight: bold;
                color: white;
            }

            #sourceStatus {
                font-size: 13px;
                color: rgba(255,255,255,0.55);
            }

                QProgressBar {
                    background-color: rgba(255,255,255,0.08);
                    border-radius: 6px;
                    height: 10px;
                    text-align: center;
                    border: none;
                }

                QProgressBar::chunk {
                    border-radius: 6px;
                }

                #greenProgress::chunk {
                    background-color: #34d399;
                }

                #orangeProgress::chunk {
                    background-color: #f59e0b;
                }
                #shapLabel {
                    font-size: 14px;
                    color: #d6deff;
                }

                #shapPercent {
                    font-size: 13px;
                    font-weight: bold;
                    color: #9fb3ff;
                }
        """)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DashboardWindw()
    window.show()
    sys.exit(app.exec())