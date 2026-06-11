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
from ui.components.activity_log import ActivityLogPanel
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
        if key in ("predictions", "all"):
            result = DataStore.get().predictions
            if result and result.success:
                self._apply_predictions(result)
            else:
                # No (successful) prediction available → keep dashboard blank
                self._show_empty_state()
        if key in ("predictions", "last_prediction_run", "all"):
            self._refresh_prediction_status()

    # ------------------------------------------------------------------
    # Prediction results → update UI
    # ------------------------------------------------------------------

    def _apply_predictions(self, result):
        """Update dashboard metrics and charts from real prediction results."""
        s = result.summary

        # Update metric cards
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

        # Update Risk Distribution Chart (donut chart)
        self._risk_distribution_chart.update_chart(
            high_risk=s.high_risk,
            moderate_risk=s.moderate_risk,
            low_risk=s.low_risk,
        )

        # Update Risk Analytics Chart (college breakdown bar chart)
        self._risk_analytics_chart.update_chart(by_college=s.by_college)

        # Update SHAP factors with actual data from predictions
        self._update_shap_factors(result.predictions)

        # Refresh recent alerts panel with top 3 high-risk students
        self._refresh_alerts_panel(result.predictions[:3])

    def _update_shap_factors(self, predictions: list) -> None:
        """
        Update SHAP factors display with aggregated feature importance from predictions.

        Parameters
        ----------
        predictions : list
            List of prediction results with shap_factors
        """
        # Clear existing factors
        while self._shap_factors_layout.count():
            item = self._shap_factors_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Aggregate SHAP values across all predictions
        factor_scores = {}
        for pred in predictions:
            shap_factors = pred.get("shap_factors", [])
            for factor_name, importance in shap_factors:
                if factor_name not in factor_scores:
                    factor_scores[factor_name] = []
                factor_scores[factor_name].append(importance)

        # Calculate average importance per factor
        avg_factors = [
            (name, round(sum(scores) / len(scores), 1))
            for name, scores in factor_scores.items()
        ]

        # Sort by importance and take top 8
        avg_factors.sort(key=lambda x: x[1], reverse=True)
        top_factors = avg_factors[:8]

        # Color mapping for risk factors
        color_map = {
            "GWA": "#ff5b5b",
            "Absences": "#ff5b5b",
            "Failed": "#f5b335",
            "Referral": "#f5b335",
            "attendance": "#4f8cff",
            "Financial": "#4f8cff",
            "Psych": "#4f8cff",
        }

        # Get color based on factor name
        def get_color(factor_name: str) -> str:
            for keyword, color in color_map.items():
                if keyword.lower() in factor_name.lower():
                    return color
            return "#4f8cff"  # Default blue

        # Display top factors
        for factor_name, importance in top_factors:
            color = get_color(factor_name)
            self._shap_factors_layout.addWidget(
                ShapFactor(factor_name, int(importance), color)
            )

        self._shap_factors_layout.addStretch()

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
    # Empty / blank states (no prediction yet)
    # ------------------------------------------------------------------

    def _clear_layout(self, layout) -> None:
        """Remove every item (widgets and stretches) from a layout."""
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _show_empty_shap(self) -> None:
        """Render the SHAP panel with a placeholder message."""
        self._clear_layout(self._shap_factors_layout)
        placeholder = QLabel("No prediction data yet. Run a prediction to view risk factors.")
        placeholder.setObjectName("analyticsText")
        placeholder.setWordWrap(True)
        self._shap_factors_layout.addWidget(placeholder)
        self._shap_factors_layout.addStretch()

    def _show_empty_alerts(self) -> None:
        """Render the alerts panel with a placeholder message."""
        self._clear_layout(self._alerts_content_layout)
        placeholder = QLabel("No high-risk alerts yet. Run a prediction to populate.")
        placeholder.setObjectName("analyticsText")
        placeholder.setWordWrap(True)
        self._alerts_content_layout.addWidget(placeholder)
        self._alerts_content_layout.addStretch()

    def _reset_metric_cards(self) -> None:
        """Reset prediction-driven metric cards to their pending state."""
        for card in (self._metric_1, self._metric_2, self._metric_3):
            card.update_values(
                value="—",
                status="Pending",
                remarks="Run prediction to update",
            )

    def _show_empty_state(self) -> None:
        """Blank every prediction-driven visualization on the dashboard."""
        self._reset_metric_cards()
        self._risk_distribution_chart.show_empty()
        self._risk_analytics_chart.show_empty()
        self._show_empty_shap()
        self._show_empty_alerts()

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
        self._last_run_lbl = QLabel("Last prediction run: Not yet run")
        self._last_run_lbl.setObjectName("subHeader")

        header_text_layout.addWidget(header)
        header_text_layout.addWidget(subHeader)
        header_text_layout.addWidget(self._last_run_lbl)

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
        # ACTIVITY LOG PANEL
        # =====================================

        self._activity_log = ActivityLogPanel()
        self.main_layout.addWidget(self._activity_log)

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

        self._risk_distribution_chart = RiskDistributionChart()
        distribution_layout.addWidget(self._risk_distribution_chart)
        distribution_layout.addStretch()

        risk_distribution_panel.setLayout(distribution_layout)

        # RISK SCORE PANEL
        risk_score_panel = QFrame()
        risk_score_panel.setObjectName("analyticsPanel")
        risk_score_panel.setMinimumHeight(350)

        score_layout = QVBoxLayout()
        self._risk_analytics_chart = RiskAnalyticsChart()
        risk_score_panel.setLayout(score_layout)
        score_layout.addWidget(self._risk_analytics_chart)

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

        # Dynamic SHAP factors container (populated only after prediction)
        self._shap_factors_layout = QVBoxLayout()
        self._show_empty_shap()
        shap_layout.addLayout(self._shap_factors_layout)

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

        # ── Dynamic alerts content (populated only after prediction) ───
        self._alerts_content_layout = QVBoxLayout()
        self._show_empty_alerts()
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
        self._refresh_prediction_status()

        # If a successful prediction already exists, populate immediately;
        # otherwise the dashboard stays blank until one is run.
        existing = DataStore.get().predictions
        if existing and existing.success:
            self._apply_predictions(existing)

    def _refresh_prediction_status(self):
        """Show the latest successful prediction run time."""
        last_run = DataStore.get().last_prediction_run
        if last_run:
            self._last_run_lbl.setText(f"Last prediction run: {last_run}")
        else:
            self._last_run_lbl.setText("Last prediction run: Not yet run")

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