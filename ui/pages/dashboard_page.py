from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QSizePolicy,
    QGraphicsOpacityEffect,
    QProgressBar,
    QScrollArea,
)

from PyQt6.QtCore import (
    QTimer,
    Qt,
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

from ui.components.metric_card import MetricCard
from ui.components.chart_widgets import (
    RiskDistributionChart,
    RiskAnalyticsChart,
    ShapFactor,
)
from ui.components.loading_overlay import LoadingOverlay
from ui.mixins.prediction_mixin import PredictionMixin
from services.data_store import DataStore


class DashboardPage(PredictionMixin, QWidget):
    """Dashboard page with analytics, metrics, and charts."""

    def __init__(self):
        super().__init__()
        self.setup_ui()
        # Listen for prediction results from any page
        DataStore.get().add_listener(self._on_store_updated)

    # ------------------------------------------------------------------
    # DataStore listener
    # ------------------------------------------------------------------

    def _on_store_updated(self, key: str):
        if key == "predictions":
            result = DataStore.get().predictions
            if result and result.success:
                self._apply_predictions(result)

    # ------------------------------------------------------------------
    # Prediction results → update UI
    # ------------------------------------------------------------------

    def _apply_predictions(self, result):
        """Update dashboard metrics and alerts from real prediction results."""
        s = result.summary

        self._metric_1.update_values(
            value   = f"{s.avg_score}%",
            status  = "High Risk" if s.avg_score >= 70 else "Moderate Risk",
            remarks = f"Based on {s.total:,} students",
        )
        self._metric_2.update_values(
            value   = f"{s.high_risk + s.moderate_risk:,}",
            status  = "High Risk" if s.high_risk_pct >= 30 else "Moderate Risk",
            remarks = f"{s.high_risk_pct}% of total students",
        )
        self._metric_3.update_values(
            value   = f"{s.high_risk:,}",
            status  = "High Risk",
            remarks = (
                f"{round(s.high_risk / s.total * 100, 1)}% flagged this run"
                if s.total else "—"
            ),
        )

        # Refresh recent alerts panel with top 3 high-risk students
        self._refresh_alerts_panel(result.predictions[:3])

    def _refresh_alerts_panel(self, top_predictions: list):
        """Clear and repopulate the Recent High-Risk Alerts panel."""
        while self._alerts_content_layout.count():
            item = self._alerts_content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for pred in top_predictions:
            lbl = QLabel(
                f"⚠ {pred['college']}-{pred['program']} | "
                f"{pred['name']} | {pred['label']}"
            )
            lbl.setObjectName("alertItem")
            self._alerts_content_layout.addWidget(lbl)

        self._alerts_content_layout.addStretch()

    # ------------------------------------------------------------------
    # Setup UI
    # ------------------------------------------------------------------

    def setup_ui(self):
        """Setup the dashboard page UI."""

        self.setObjectName("page")
        self.overlay = LoadingOverlay(self)
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(30, 30, 30, 30)
        self.main_layout.setSpacing(20)

        # =====================================
        # FIXED HEADER PANEL (NOT SCROLLABLE)
        # =====================================

        self.fixed_header_container = QFrame()
        self.fixed_header_container.setObjectName("fixedHeaderContainer")
        fixed_header_layout = QVBoxLayout()
        fixed_header_layout.setContentsMargins(20, 20, 20, 20)
        fixed_header_layout.setSpacing(20)

        # HEADER
        header_layout = QHBoxLayout()
        header_layout.setSpacing(15)

        header_text_layout = QVBoxLayout()
        header_text_layout.setSpacing(5)

        header = QLabel("DASHBOARD")
        header.setObjectName("header")

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
        model_layout.setContentsMargins(20, 15, 20, 15)

        self.model_status = QLabel("Model Active")
        self.model_status.setObjectName("modelStatus")
        self.model_status.setStyleSheet("""
            #modelStatus {
                color: #2ecc71;
                font-weight: bold;
                font-size: 12px;
            }
        """)

        self.opacity_effect = QGraphicsOpacityEffect(self.model_status)
        self.model_status.setGraphicsEffect(self.opacity_effect)

        self.status_animation = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.status_animation.setDuration(1200)
        self.status_animation.setStartValue(1.0)
        self.status_animation.setKeyValueAt(0.5, 0.3)
        self.status_animation.setEndValue(1.0)
        self.status_animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.status_animation.setLoopCount(-1)
        self.status_animation.start()

        model_semester = QLabel("1st Semester AY 2024-2025")
        model_semester.setObjectName("modelSemester")

        run_button = QPushButton("Run Prediction")
        run_button.setObjectName("runButton")
        run_button.setCursor(Qt.CursorShape.PointingHandCursor)
        run_button.setIcon(QIcon("assets/icons/play.svg"))
        run_button.clicked.connect(self.on_run_prediction)
        run_button.setFixedWidth(120)

        model_layout.addWidget(self.model_status)
        model_layout.addWidget(model_semester)
        model_layout.addSpacing(5)
        model_layout.addWidget(run_button)

        model_card.setLayout(model_layout)

        header_layout.addStretch()
        header_layout.addWidget(model_card)

        fixed_header_layout.addLayout(header_layout)
        self.fixed_header_container.setLayout(fixed_header_layout)
        self.main_layout.addWidget(self.fixed_header_container)

        # =====================================
        # METRIC CARDS
        # =====================================

        self.main_layout.addLayout(self._build_metric_cards())

        # =====================================
        # ANALYTICS PANELS CONTAINER
        # =====================================

        analytics_layout = QHBoxLayout()
        analytics_layout.setSpacing(20)

        # RISK DISTRIBUTION PANEL
        risk_distribution_panel = QFrame()
        risk_distribution_panel.setObjectName("analyticsPanel")
        risk_distribution_panel.setMinimumHeight(300)

        distribution_layout = QVBoxLayout()

        distribution_title = QLabel("Risk Distribution Panel")
        distribution_title.setObjectName("cardTitle")

        distribution_text = QLabel(
            "Visual distribution charts and risk clusters will appear here."
        )
        distribution_text.setObjectName("analyticsText")
        distribution_text.setWordWrap(True)

        distribution_layout.addWidget(distribution_title)
        distribution_layout.addSpacing(10)
        distribution_layout.addWidget(distribution_text)

        donut_chart = RiskDistributionChart()
        distribution_layout.addWidget(donut_chart)
        distribution_layout.addStretch()

        risk_distribution_panel.setLayout(distribution_layout)

        # RISK SCORE PANEL
        risk_score_panel = QFrame()
        risk_score_panel.setObjectName("analyticsPanel")
        risk_score_panel.setMinimumHeight(350)

        score_layout = QVBoxLayout()
        chart_view = RiskAnalyticsChart()
        risk_score_panel.setLayout(score_layout)
        score_layout.addWidget(chart_view)

        analytics_layout.addWidget(risk_distribution_panel)
        analytics_layout.addWidget(risk_score_panel)

        self.main_layout.addLayout(analytics_layout)

        # =====================================
        # SECOND ANALYTICS ROW
        # =====================================

        analytics_row_2 = QHBoxLayout()
        analytics_row_2.setSpacing(20)

        # SHAP IMPORTANCE PANEL
        shap_panel = QFrame()
        shap_panel.setObjectName("analyticsPanel")
        shap_panel.setMinimumHeight(320)

        shap_layout = QVBoxLayout()

        shap_title = QLabel("Top Risk Factors (SHAP Importance)")
        shap_title.setObjectName("cardTitle")

        shap_text = QLabel(
            "Top predictive features contributing to student risk scores will appear here."
        )
        shap_text.setWordWrap(True)
        shap_text.setObjectName("analyticsText")

        shap_layout.addSpacing(10)
        shap_layout.addWidget(shap_title)
        shap_layout.addSpacing(10)
        shap_layout.addWidget(shap_text)
        shap_layout.addSpacing(10)
        shap_layout.addStretch()

        factors = [
            ("GWA drop (sem 1)", 38, "#ff5b5b"),
            ("Absences > 20%",   22, "#ff5b5b"),
            ("No org membership",14, "#f5b335"),
            ("Working student",  11, "#f5b335"),
            ("Failed ≥ 2 subjects", 9, "#4f8cff"),
            ("Low psych score",   8, "#4f8cff"),
            ("Financial aid lapse",7, "#4f8cff"),
            ("Referral to guidance",6,"#4f8cff"),
        ]
        for label, value, color in factors:
            shap_layout.addWidget(ShapFactor(label, value, color))

        shap_panel.setLayout(shap_layout)

        # RECENT ALERTS PANEL
        alerts_panel = QFrame()
        alerts_panel.setObjectName("analyticsPanel")
        alerts_panel.setMinimumHeight(320)

        alerts_layout = QVBoxLayout()

        alerts_title = QLabel("Recent High-Risk Alerts")
        alerts_title.setObjectName("cardTitle")

        alerts_text = QLabel(
            "Recently detected high-risk students requiring intervention."
        )
        alerts_text.setWordWrap(True)
        alerts_text.setObjectName("analyticsText")

        alerts_layout.addWidget(alerts_title)
        alerts_layout.addSpacing(10)
        alerts_layout.addWidget(alerts_text)
        alerts_layout.addSpacing(10)

        # ── Dynamic alerts content (updated after prediction) ──────────
        self._alerts_content_layout = QVBoxLayout()

        # Seed with placeholder alerts
        for text in [
            "⚠ BSIT-101 | John Doe | High Risk",
            "⚠ BSED-202 | Jane Smith | Critical",
            "⚠ BSBA-303 | Mark Reyes | High Risk",
        ]:
            lbl = QLabel(text)
            lbl.setObjectName("alertItem")
            self._alerts_content_layout.addWidget(lbl)

        self._alerts_content_layout.addStretch()
        alerts_layout.addLayout(self._alerts_content_layout)

        alerts_panel.setLayout(alerts_layout)

        analytics_row_2.addWidget(shap_panel)
        analytics_row_2.addWidget(alerts_panel)
        self.main_layout.addLayout(analytics_row_2)

        # =====================================
        # DATA SOURCE COVERAGE ROW
        # =====================================

        coverage_card = QFrame()
        coverage_card.setObjectName("coverageCard")

        coverage_layout = QVBoxLayout()
        coverage_layout.setContentsMargins(25, 20, 25, 20)
        coverage_layout.setSpacing(18)

        coverage_title = QLabel("DATA SOURCE COVERAGE")
        coverage_title.setObjectName("coverageTitle")
        coverage_layout.addWidget(coverage_title)

        sources_layout = QHBoxLayout()
        sources_layout.setSpacing(20)

        # MIS
        mis_container = QVBoxLayout()
        mis_header = QLabel("●  MIS — Academic Records        1,248 / 1,248")
        mis_header.setObjectName("sourceHeader")
        mis_progress = QProgressBar()
        mis_progress.setValue(100)
        mis_progress.setObjectName("greenProgress")
        mis_status = QLabel("Complete · Last updated: Oct 14")
        mis_status.setObjectName("sourceStatus")
        mis_container.addWidget(mis_header)
        mis_container.addWidget(mis_progress)
        mis_container.addWidget(mis_status)

        # SAO
        sao_container = QVBoxLayout()
        sao_header = QLabel("●  SAO — Student Affairs        1,102 / 1,248")
        sao_header.setObjectName("sourceHeader")
        sao_progress = QProgressBar()
        sao_progress.setValue(88)
        sao_progress.setObjectName("greenProgress")
        sao_status = QLabel("88% · 146 missing records")
        sao_status.setObjectName("sourceStatus")
        sao_container.addWidget(sao_header)
        sao_container.addWidget(sao_progress)
        sao_container.addWidget(sao_status)

        # Guidance
        guidance_container = QVBoxLayout()
        guidance_header = QLabel("●  Guidance — Psych Records        934 / 1248")
        guidance_header.setObjectName("sourceHeader")
        guidance_progress = QProgressBar()
        guidance_progress.setValue(75)
        guidance_progress.setObjectName("orangeProgress")
        guidance_status = QLabel("75% · Upload pending")
        guidance_status.setObjectName("sourceStatus")
        guidance_container.addWidget(guidance_header)
        guidance_container.addWidget(guidance_progress)
        guidance_container.addWidget(guidance_status)

        sources_layout.addLayout(mis_container)
        sources_layout.addLayout(sao_container)
        sources_layout.addLayout(guidance_container)

        coverage_layout.addLayout(sources_layout)
        coverage_card.setLayout(coverage_layout)

        self.main_layout.addWidget(coverage_card)

        self.setLayout(self.main_layout)
        self.init_prediction()

    # ------------------------------------------------------------------
    # Metric cards builder — stores refs for live updates
    # ------------------------------------------------------------------

    def _build_metric_cards(self) -> QHBoxLayout:
        metrics_layout = QHBoxLayout()

        self._metric_1 = MetricCard(
            "Overall Risk Score",
            "—",
            "Pending",
            "Run prediction to update",
        )
        self._metric_2 = MetricCard(
            "At-Risk Students",
            "—",
            "Pending",
            "Run prediction to update",
        )
        self._metric_3 = MetricCard(
            "New High-Risk Cases",
            "—",
            "Pending",
            "Run prediction to update",
        )
        self._metric_4 = MetricCard(
            "Intervention Success Rate",
            "78%",
            "Moderate Risk",
            "↑ 10% from last semester",
        )

        metrics_layout.addWidget(self._metric_1)
        metrics_layout.addWidget(self._metric_2)
        metrics_layout.addWidget(self._metric_3)
        metrics_layout.addWidget(self._metric_4)

        return metrics_layout