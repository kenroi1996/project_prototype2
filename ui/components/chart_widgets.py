from PyQt6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
)

from PyQt6.QtCore import Qt, QMargins

from PyQt6.QtGui import (
    QColor,
    QFont,
    QBrush,
    QPainter,
)

from PyQt6.QtCharts import (
    QChart,
    QChartView,
    QPieSeries,
    QBarSet,
    QStackedBarSeries,
    QBarCategoryAxis,
    QValueAxis,
)


# =====================================
# RISK DISTRIBUTION DONUT CHART
# =====================================

class RiskDistributionChart(QChartView):

    def __init__(self, parent=None):
        super().__init__(parent)

        chart = self._build_chart()

        self.setChart(chart)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMinimumHeight(350)
        self.setStyleSheet("background: transparent; border: none;")

        # Start blank — populated only after a successful prediction
        self.show_empty()

    def _build_chart(self):
        chart = QChart()
        chart.setBackgroundVisible(False)
        chart.setPlotAreaBackgroundVisible(False)

        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
        chart.legend().setLabelColor(QColor("#b8bcc8"))

        chart.layout().setContentsMargins(0, 0, 0, 0)
        chart.setMargins(QMargins(0, 0, 0, 0))

        return chart

    def show_empty(self) -> None:
        """Render a blank placeholder ring (no prediction data yet)."""
        self.chart().removeAllSeries()

        series = QPieSeries()
        placeholder = series.append("No prediction data", 1)
        series.setHoleSize(0.40)

        placeholder.setColor(QColor("#2c3038"))
        placeholder.setBorderColor(QColor("transparent"))
        placeholder.setLabelVisible(False)

        self.chart().addSeries(series)
        self.chart().legend().setVisible(True)
        self.chart().legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
        self.chart().legend().setLabelColor(QColor("#6b7280"))

    def update_chart(self, high_risk: int, moderate_risk: int, low_risk: int) -> None:
        """
        Update the pie chart with new risk distribution data.

        Parameters
        ----------
        high_risk : int
            Count of high-risk students
        moderate_risk : int
            Count of moderate-risk students
        low_risk : int
            Count of low-risk students
        """
        # Clear existing series
        self.chart().removeAllSeries()

        # Create new series with updated data
        series = QPieSeries()

        high = series.append("High Risk", high_risk)
        moderate = series.append("Moderate Risk", moderate_risk)
        low = series.append("Low Risk", low_risk)

        series.setHoleSize(0.40)

        high.setColor(QColor("#ff5b5b"))
        moderate.setColor(QColor("#f5b335"))
        low.setColor(QColor("#34d399"))

        for slice in series.slices():
            slice.setBorderColor(QColor("transparent"))
            slice.setLabelVisible(False)

        self.chart().addSeries(series)
        self.chart().legend().setVisible(True)
        self.chart().legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
        self.chart().legend().setLabelColor(QColor("#b8bcc8"))


# =====================================
# RISK ANALYTICS STACKED BAR CHART
# =====================================

class RiskAnalyticsChart(QChartView):

    def __init__(self, parent=None):
        super().__init__(parent)

        self.series = None
        self.axis_x = None
        self.axis_y = None
        chart = self._build_chart()

        self.setChart(chart)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setStyleSheet("background: transparent; border: none;")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        # Start blank — populated only after a successful prediction
        self.show_empty()

    def _build_chart(self):
        chart = QChart()
        chart.setTitle("RISK SCORE BY COLLEGE / DEPARTMENT")
        chart.setTitleFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        chart.setTitleBrush(QColor("#85929E"))
        chart.setBackgroundBrush(QBrush(Qt.BrushStyle.NoBrush))
        chart.setMargins(QMargins(0, 0, 0, 0))

        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignTop)
        chart.legend().setLabelColor(QColor("white"))
        chart.legend().setFont(QFont("Segoe UI", 9))

        return chart

    def show_empty(self) -> None:
        """Render blank axes with no bars (no prediction data yet)."""
        self.chart().removeAllSeries()

        if self.axis_x is not None:
            self.chart().removeAxis(self.axis_x)
        if self.axis_y is not None:
            self.chart().removeAxis(self.axis_y)

        axis_x = QBarCategoryAxis()
        axis_x.append(["CITE", "CBAA", "CTE", "COED", "CON", "CAS"])
        axis_x.setLabelsColor(QColor("#85929E"))
        axis_x.setLabelsFont(QFont("Segoe UI", 10))
        axis_x.setGridLineColor(QColor("#2C3E50"))

        axis_y = QValueAxis()
        axis_y.setRange(0, 120)
        axis_y.setTickCount(7)
        axis_y.setLabelsColor(QColor("#85929E"))
        axis_y.setLabelsFont(QFont("Segoe UI", 10))
        axis_y.setGridLineColor(QColor("#1C2833"))

        self.chart().addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        self.chart().addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)

        self.series = None
        self.axis_x = axis_x
        self.axis_y = axis_y

    def update_chart(self, by_college: dict) -> None:
        """
        Update the bar chart with college-wise risk distribution.

        Parameters
        ----------
        by_college : dict
            College breakdown: {"COLLEGE": {"total": int, "high": int}, ...}
        """
        # Extract college names and calculate percentages
        colleges = []
        high_values = []
        moderate_values = []

        # Map college codes for display
        college_order = ["CITE", "CBAA", "CTE", "COED", "CON", "CAS"]
        college_names = {
            "CITE": "CITE",
            "CBAA": "CBAA",
            "CTE": "CTE",
            "COED": "COED",
            "CON": "CON",
            "CAS": "CAS",
        }

        for college in college_order:
            colleges.append(college)
            data = by_college.get(college, {"total": 0, "high": 0})
            total = data.get("total", 1)
            high = data.get("high", 0)
            moderate = total - high

            # Calculate percentages (scale to 0-120 range for visibility)
            high_pct = (high / total * 100) if total > 0 else 0
            mod_pct = (moderate / total * 100) if total > 0 else 0

            high_values.append(max(high_pct, 5))  # Min 5 for visibility
            moderate_values.append(max(mod_pct, 5))

        # Clear existing series
        self.chart().removeAllSeries()

        # Create new series
        set_high = QBarSet("High Risk")
        set_moderate = QBarSet("Moderate Risk")

        set_high.setColor(QColor("#C0392B"))
        set_moderate.setColor(QColor("#D4AC0D"))

        set_high.append(high_values)
        set_moderate.append(moderate_values)

        series = QStackedBarSeries()
        series.append(set_high)
        series.append(set_moderate)
        series.setLabelsVisible(False)

        # Clear and update axes
        self.chart().removeAxis(self.axis_x)
        self.chart().removeAxis(self.axis_y)

        axis_x = QBarCategoryAxis()
        axis_x.append(colleges)
        axis_x.setLabelsColor(QColor("#85929E"))
        axis_x.setLabelsFont(QFont("Segoe UI", 10))
        axis_x.setGridLineColor(QColor("#2C3E50"))

        axis_y = QValueAxis()
        axis_y.setRange(0, 120)
        axis_y.setTickCount(7)
        axis_y.setLabelsColor(QColor("#85929E"))
        axis_y.setLabelsFont(QFont("Segoe UI", 10))
        axis_y.setGridLineColor(QColor("#1C2833"))

        self.series = series
        self.axis_x = axis_x
        self.axis_y = axis_y

        self.chart().addSeries(series)
        self.chart().addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        self.chart().addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(axis_x)
        series.attachAxis(axis_y)


# =====================================
# SHAP FACTOR ROW WIDGET
# =====================================

class ShapFactor(QWidget):

    def __init__(self, label_text, percentage, color, parent=None):
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(15)

        # Feature label
        label = QLabel(label_text)
        label.setObjectName("shapLabel")
        label.setFixedWidth(190)

        # Progress bar
        progress = QProgressBar()
        progress.setValue(percentage)
        progress.setTextVisible(False)
        progress.setFixedHeight(10)
        progress.setObjectName("shapProgress")
        progress.setStyleSheet(f"""
            QProgressBar {{
                background-color: rgba(255,255,255,0.08);
                border-radius: 5px;
                border: none;
            }}
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 5px;
            }}
        """)

        # Percentage label
        percent = QLabel(f"{percentage}%")
        percent.setObjectName("shapPercent")
        percent.setFixedWidth(40)

        layout.addWidget(label)
        layout.addWidget(progress)
        layout.addWidget(percent)