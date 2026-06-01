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

        series = self._build_series()
        chart = self._build_chart(series)

        self.setChart(chart)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMinimumHeight(350)
        self.setStyleSheet("background: transparent; border: none;")

    def _build_series(self):
        series = QPieSeries()

        high_risk     = series.append("High Risk",     15)
        moderate_risk = series.append("Moderate Risk", 28)
        low_risk      = series.append("Low Risk",      57)

        series.setHoleSize(0.40)

        high_risk.setColor(QColor("#ff5b5b"))
        moderate_risk.setColor(QColor("#f5b335"))
        low_risk.setColor(QColor("#34d399"))

        for slice in series.slices():
            slice.setBorderColor(QColor("transparent"))
            slice.setLabelVisible(False)

        return series

    def _build_chart(self, series):
        chart = QChart()
        chart.addSeries(series)
        chart.setBackgroundVisible(False)
        chart.setPlotAreaBackgroundVisible(False)

        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
        chart.legend().setLabelColor(QColor("#b8bcc8"))

        chart.layout().setContentsMargins(0, 0, 0, 0)
        chart.setMargins(QMargins(0, 0, 0, 0))

        return chart


# =====================================
# RISK ANALYTICS STACKED BAR CHART
# =====================================

class RiskAnalyticsChart(QChartView):

    def __init__(self, parent=None):
        super().__init__(parent)

        series, axis_x, axis_y = self._build_series()
        chart = self._build_chart(series, axis_x, axis_y)

        self.setChart(chart)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setStyleSheet("background: transparent; border: none;")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

    def _build_series(self):
        set_high = QBarSet("High")
        set_moderate = QBarSet("Moderate")

        set_high.setColor(QColor("#C0392B"))
        set_moderate.setColor(QColor("#D4AC0D"))

        set_high.append([100, 100, 100, 100, 100, 100])
        set_moderate.append([68, 62, 55, 48, 38, 71])

        series = QStackedBarSeries()
        series.append(set_high)
        series.append(set_moderate)
        series.setLabelsVisible(False)

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

        return series, axis_x, axis_y

    def _build_chart(self, series, axis_x, axis_y):
        chart = QChart()
        chart.addSeries(series)
        chart.setTitle("RISK SCORE BY COLLEGE / DEPARTMENT")
        chart.setTitleFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        chart.setTitleBrush(QColor("#85929E"))
        chart.setBackgroundBrush(QBrush(Qt.BrushStyle.NoBrush))
        chart.setMargins(QMargins(0, 0, 0, 0))

        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(axis_x)
        series.attachAxis(axis_y)

        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignTop)
        chart.legend().setLabelColor(QColor("white"))
        chart.legend().setFont(QFont("Segoe UI", 9))

        return chart


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