"""
EarlyAlert — Interactive Chart Widgets
=======================================
Upgraded chart_widgets.py with:

  RiskDistributionChart
    • Donut with per-slice labels (count + %)
    • Hover explodes the slice + shows tooltip
    • Click emits risk_filter_changed(category_str) for cross-filtering
    • Centre label shows total students / hovered category

  RiskAnalyticsChart
    • Stacked bar uses REAL college names from data (no hardcoded list)
    • Count labels on each bar segment
    • Hover highlights bar + shows tooltip
    • Click emits college_clicked(college_name) to drill down

  ShapFactor
    • Clickable row — emits clicked(feature_name)
    • Hover highlight
    • Tooltip shows full feature description

  All signals are PyQt6 pyqtSignal so DashboardPage can wire them up.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QProgressBar, QSizePolicy, QToolTip, QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
)
from PyQt6.QtCore import (
    Qt, QMargins, QPointF, pyqtSignal, QPoint,
    QEasingCurve, QPropertyAnimation,
)
from PyQt6.QtGui import (
    QColor, QFont, QBrush, QPainter, QCursor,
)
from PyQt6.QtCharts import (
    QChart, QChartView, QPieSeries, QPieSlice,
    QBarSet, QStackedBarSeries,
    QBarCategoryAxis, QValueAxis,
)


# ── Palette ───────────────────────────────────────────────────────────────────
_HIGH    = QColor("#ff5b5b")
_MOD     = QColor("#f5b335")
_LOW     = QColor("#34d399")
_MUTED   = QColor("#a0aabe")
_BG      = QColor("#13172a")
_SURFACE = QColor("rgba(255,255,255,0.04)")

_CATEGORY_KEYS = {
    "High Risk":     "high_risk",
    "Moderate Risk": "moderate_risk",
    "Low Risk":      "low_risk",
}


def _play_fade_in(widget: QWidget, duration: int = 420) -> None:
    """
    Subtle fade + rise entrance for a chart widget, played whenever it
    loads or refreshes with new data. Cleans up after itself so repeated
    calls (e.g. every update_chart()) don't leak effects/animations.
    """
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)

    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _cleanup():
        widget.setGraphicsEffect(None)

    anim.finished.connect(_cleanup)
    widget._entrance_anim = anim   # keep a reference so it isn't garbage-collected
    anim.start()


# =============================================================================
# RISK DISTRIBUTION DONUT CHART
# =============================================================================

class RiskDistributionChart(QChartView):
    """
    Interactive donut chart.

    Signals
    -------
    risk_filter_changed(str)
        Emitted when a slice is clicked.
        Value is the category key: "high_risk" | "moderate_risk" |
        "low_risk" | "" (all, when the same slice is clicked again).
    """

    risk_filter_changed = pyqtSignal(str)   # "" = clear filter

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active_filter: str = ""       # currently selected category
        self._totals: dict = {}             # slice label → count
        self._total_students: int = 0

        self._centre_lbl = QLabel("—", self)
        self._centre_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._centre_lbl.setStyleSheet(
            "color: #e8eaf0; font-size: 22px; font-weight: bold; "
            "background: transparent; border: none;"
        )
        self._centre_sub = QLabel("students", self)
        self._centre_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._centre_sub.setStyleSheet(
            "color: rgba(255,255,255,0.40); font-size: 11px; "
            "background: transparent; border: none;"
        )

        chart = QChart()
        chart.setBackgroundVisible(False)
        chart.setPlotAreaBackgroundVisible(False)
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
        chart.legend().setLabelColor(QColor("#000000"))
        chart.legend().setFont(QFont("Segoe UI", 9))
        chart.layout().setContentsMargins(0, 0, 0, 0)
        chart.setMargins(QMargins(0, 0, 0, 0))
        chart.setAnimationOptions(QChart.AnimationOption.SeriesAnimations)
        chart.setAnimationDuration(650)
        chart.setAnimationEasingCurve(QEasingCurve.Type.OutCubic)

        self.setChart(chart)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMinimumHeight(320)
        self.setStyleSheet("background: transparent; border: none;")
        self.show_empty()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_centre_labels()

    def _reposition_centre_labels(self):
        """Keep the centre labels positioned in the donut hole."""
        r = self.chart().plotArea()
        cx = int(r.x() + r.width()  / 2)
        cy = int(r.y() + r.height() / 2)
        w, h = 100, 28
        self._centre_lbl.setGeometry(cx - w//2, cy - 20, w, h)
        self._centre_sub.setGeometry(cx - w//2, cy + 8,  w, 18)

    def show_empty(self):
        self.chart().removeAllSeries()
        series = QPieSeries()
        ph = series.append("No data yet", 1)
        ph.setColor(QColor("#1e2540"))
        ph.setBorderColor(QColor("transparent"))
        ph.setLabelVisible(False)
        series.setHoleSize(0.52)
        self.chart().addSeries(series)
        self._centre_lbl.setText("—")
        self._centre_sub.setText("no data")
        self._reposition_centre_labels()

    def update_chart(self, high_risk: int, moderate_risk: int,
                     low_risk: int) -> None:
        self.chart().removeAllSeries()
        total = high_risk + moderate_risk + low_risk
        self._total_students = total
        self._totals = {
            "High Risk":     high_risk,
            "Moderate Risk": moderate_risk,
            "Low Risk":      low_risk,
        }
        self._active_filter = ""

        series = QPieSeries()
        series.setHoleSize(0.52)
        series.setPieSize(0.78)

        data = [
            ("High Risk",     high_risk,     _HIGH),
            ("Moderate Risk", moderate_risk, _MOD),
            ("Low Risk",      low_risk,      _LOW),
        ]
        for label, count, color in data:
            pct = round(count / total * 100, 1) if total else 0
            sl  = series.append(f"{label}  {count:,}  ({pct}%)", count)
            sl.setColor(color)
            sl.setBorderColor(QColor("transparent"))
            sl.setLabelVisible(False)
            sl.setProperty("category", label)

        # ── Signals ──────────────────────────────────────────────────
        series.hovered.connect(self._on_slice_hovered)
        series.clicked.connect(self._on_slice_clicked)

        self.chart().addSeries(series)
        self.chart().legend().setVisible(True)
        self.chart().legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
        self.chart().legend().setLabelColor(QColor("#c9d0e0"))

        self._centre_lbl.setText(f"{total:,}")
        self._centre_sub.setText("students")
        self._reposition_centre_labels()
        _play_fade_in(self)

    def _on_slice_hovered(self, sl: QPieSlice, state: bool):
        if state:
            sl.setExploded(True)
            sl.setExplodeDistanceFactor(0.06)
            cat  = sl.property("category") or ""
            cnt  = self._totals.get(cat, 0)
            tot  = max(self._total_students, 1)
            pct  = round(cnt / tot * 100, 1)
            self._centre_lbl.setText(f"{cnt:,}")
            self._centre_sub.setText(f"{cat.split()[0]}  {pct}%")
            QToolTip.showText(
                QCursor.pos(),
                f"<b>{cat}</b><br>{cnt:,} students ({pct}%)",
            )
        else:
            sl.setExploded(False)
            self._centre_lbl.setText(f"{self._total_students:,}")
            self._centre_sub.setText("students")
            QToolTip.hideText()

    def _on_slice_clicked(self, sl: QPieSlice):
        cat = sl.property("category") or ""
        key = _CATEGORY_KEYS.get(cat, "")

        if self._active_filter == key:
            # Second click → clear filter
            self._active_filter = ""
            self._reset_slice_opacity()
            self.risk_filter_changed.emit("")
        else:
            self._active_filter = key
            self._dim_other_slices(cat)
            self.risk_filter_changed.emit(key)

    def _dim_other_slices(self, active_cat: str):
        series = self.chart().series()
        if not series:
            return
        pie: QPieSeries = series[0]
        for sl in pie.slices():
            if sl.property("category") == active_cat:
                sl.setExploded(True)
                sl.setExplodeDistanceFactor(0.08)
                sl.setBorderColor(QColor("white"))
                sl.setBorderWidth(1)
            else:
                sl.setExploded(False)
                c = sl.color()
                c.setAlpha(60)
                sl.setColor(c)

    def _reset_slice_opacity(self):
        colors = {
            "High Risk":     _HIGH,
            "Moderate Risk": _MOD,
            "Low Risk":      _LOW,
        }
        series = self.chart().series()
        if not series:
            return
        pie: QPieSeries = series[0]
        for sl in pie.slices():
            cat = sl.property("category") or ""
            sl.setColor(colors.get(cat, _MUTED))
            sl.setBorderColor(QColor("transparent"))
            sl.setExploded(False)


# =============================================================================
# RISK ANALYTICS STACKED BAR CHART
# =============================================================================

class RiskAnalyticsChart(QChartView):
    """
    Interactive stacked bar chart — risk by college.

    Signals
    -------
    college_clicked(str)
        Emitted when a bar column is clicked.
        Value is the college name string.
    """

    college_clicked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._axis_x: QBarCategoryAxis | None = None
        self._axis_y: QValueAxis | None = None
        self._colleges: list[str] = []

        chart = self._build_empty_chart()
        self.setChart(chart)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setStyleSheet("background: transparent; border: none;")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(300)

    def _build_empty_chart(self) -> QChart:
        chart = QChart()
        chart.setTitle("RISK BY COLLEGE")
        chart.setTitleFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        chart.setTitleBrush(QColor("#8899bb"))
        chart.setBackgroundVisible(False)
        chart.setMargins(QMargins(0, 0, 0, 0))
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignTop)
        chart.legend().setLabelColor(QColor("#c9d0e0"))
        chart.legend().setFont(QFont("Segoe UI", 9))
        chart.setAnimationOptions(QChart.AnimationOption.SeriesAnimations)
        chart.setAnimationDuration(650)
        chart.setAnimationEasingCurve(QEasingCurve.Type.OutCubic)
        return chart

    def show_empty(self):
        self.chart().removeAllSeries()
        if self._axis_x:
            self.chart().removeAxis(self._axis_x)
        if self._axis_y:
            self.chart().removeAxis(self._axis_y)
        ax = QBarCategoryAxis()
        ax.append(["—"])
        ax.setLabelsColor(_MUTED)
        ay = QValueAxis()
        ay.setRange(0, 10)
        ay.setLabelsColor(_MUTED)
        ay.setGridLineColor(QColor(255,255,255,10))
        self.chart().addAxis(ax, Qt.AlignmentFlag.AlignBottom)
        self.chart().addAxis(ay, Qt.AlignmentFlag.AlignLeft)
        self._axis_x, self._axis_y = ax, ay

    def update_chart(self, by_college: dict) -> None:
        self.chart().removeAllSeries()
        if self._axis_x:
            self.chart().removeAxis(self._axis_x)
        if self._axis_y:
            self.chart().removeAxis(self._axis_y)

        if not by_college:
            self.show_empty()
            return

        # Sort colleges by total at-risk count descending
        sorted_cols = sorted(
            by_college.items(),
            key=lambda x: x[1].get("high", 0) + (x[1].get("total",0) - x[1].get("high",0)),
            reverse=True,
        )
        colleges   = [c for c, _ in sorted_cols]
        self._colleges = colleges

        high_vals = []
        mod_vals  = []
        max_val   = 0

        for col, data in sorted_cols:
            total = max(data.get("total", 0), 0)
            high  = data.get("high",  0)
            mod   = total - high
            high_vals.append(high)
            mod_vals.append(max(mod, 0))
            max_val = max(max_val, total)

        set_high = QBarSet("High Risk")
        set_high.setColor(_HIGH)
        set_high.append(high_vals)

        set_mod = QBarSet("Moderate Risk")
        set_mod.setColor(_MOD)
        set_mod.append(mod_vals)

        series = QStackedBarSeries()
        series.append(set_high)
        series.append(set_mod)
        series.setLabelsVisible(True)
        series.setLabelsPosition(QStackedBarSeries.LabelsPosition.LabelsCenter)
        series.setLabelsFormat("@value")

        # Wire click signal — bar index maps to colleges list
        set_high.clicked.connect(
            lambda idx: self.college_clicked.emit(
                self._colleges[idx] if idx < len(self._colleges) else ""
            )
        )
        set_mod.clicked.connect(
            lambda idx: self.college_clicked.emit(
                self._colleges[idx] if idx < len(self._colleges) else ""
            )
        )
        # Hover tooltip
        set_high.hovered.connect(
            lambda state, idx: self._on_bar_hovered(state, idx, "High Risk", high_vals)
        )
        set_mod.hovered.connect(
            lambda state, idx: self._on_bar_hovered(state, idx, "Moderate Risk", mod_vals)
        )

        ax = QBarCategoryAxis()
        ax.append(colleges)
        ax.setLabelsColor(_MUTED)
        ax.setLabelsFont(QFont("Segoe UI", 9))
        ax.setGridLineColor(QColor(255,255,255,8))

        ay = QValueAxis()
        ay.setRange(0, max(max_val * 1.15, 1))
        ay.setTickCount(5)
        ay.setLabelsColor(_MUTED)
        ay.setLabelsFont(QFont("Segoe UI", 9))
        ay.setGridLineColor(QColor(255,255,255,8))
        ay.setLabelFormat("%d")

        self.chart().addSeries(series)
        self.chart().addAxis(ax, Qt.AlignmentFlag.AlignBottom)
        self.chart().addAxis(ay, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(ax)
        series.attachAxis(ay)
        self._axis_x, self._axis_y = ax, ay
        _play_fade_in(self)

    def _on_bar_hovered(self, state: bool, idx: int,
                         label: str, vals: list):
        if state and idx < len(vals) and idx < len(self._colleges):
            college = self._colleges[idx]
            count   = vals[idx]
            QToolTip.showText(
                QCursor.pos(),
                f"<b>{college}</b><br>{label}: <b>{count:,}</b> students",
            )
        else:
            QToolTip.hideText()


# =============================================================================
# SHAP FACTOR ROW — clickable
# =============================================================================

class ShapFactor(QWidget):
    """
    Single risk factor row with label, progress bar, and percentage.

    Signals
    -------
    clicked(str)
        Emitted when the row is clicked. Value is the feature name.
    """

    clicked = pyqtSignal(str)

    def __init__(self, label_text: str, percentage: int,
                 color: str, feature_name: str = "", parent=None):
        super().__init__(parent)
        self._feature_name = feature_name or label_text
        self._selected     = False

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(
            f'Click to highlight students most affected by\n"{label_text}"'
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(12)

        # Label
        self._label = QLabel(label_text)
        self._label.setObjectName("shapLabel")
        self._label.setFixedWidth(200)
        self._label.setStyleSheet(
            "color: rgba(255,255,255,0.75); font-size:12px; background:transparent;"
        )

        # Bar
        self._bar = QProgressBar()
        self._bar.setValue(max(0, min(100, percentage)))
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)
        self._bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: rgba(255,255,255,0.07);
                border-radius: 4px; border: none;
            }}
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 4px;
            }}
        """)

        # Percentage
        self._pct = QLabel(f"{percentage}%")
        self._pct.setObjectName("shapPercent")
        self._pct.setFixedWidth(38)
        self._pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._pct.setStyleSheet(
            "color: rgba(255,255,255,0.45); font-size:11px; background:transparent;"
        )

        layout.addWidget(self._label)
        layout.addWidget(self._bar, 1)
        layout.addWidget(self._pct)

        self._base_style = (
            "border-radius:6px; background:transparent;"
        )
        self.setStyleSheet(self._base_style)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._selected = not self._selected
            self._apply_selected_style()
            self.clicked.emit(self._feature_name)
        super().mousePressEvent(event)

    def enterEvent(self, event):
        if not self._selected:
            self.setStyleSheet(
                "border-radius:6px; background:rgba(255,255,255,0.04);"
            )
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self._selected:
            self.setStyleSheet(self._base_style)
        super().leaveEvent(event)

    def _apply_selected_style(self):
        if self._selected:
            self.setStyleSheet(
                "border-radius:6px; "
                "background:rgba(79,140,255,0.12); "
                "border: 1px solid rgba(79,140,255,0.30);"
            )
            self._label.setStyleSheet(
                "color: #4f8cff; font-size:12px; "
                "font-weight:bold; background:transparent;"
            )
        else:
            self.setStyleSheet(self._base_style)
            self._label.setStyleSheet(
                "color: rgba(255,255,255,0.75); font-size:12px; background:transparent;"
            )

    def deselect(self):
        self._selected = False
        self._apply_selected_style()