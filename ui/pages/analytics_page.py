"""
ui/pages/analytics_page.py
============================
Dedicated Level-1 Data Analytics page for EarlyAlert.

Layout
------
  ┌─ Header: ANALYTICS + term filter bar ──────────────────────────┐
  │  AY [combo] Sem [combo] [Load]   status label                  │
  └────────────────────────────────────────────────────────────────┘
  ┌─ Metric strip: 3 summary cards ────────────────────────────────┐
  └────────────────────────────────────────────────────────────────┘
  ┌─ Row 1 ─────────────────────────────────┬──────────────────────┐
  │  Primary Risk Factor Frequency (h-bar)  │ Student Origin Map   │
  └─────────────────────────────────────────┴──────────────────────┘
  ┌─ Row 1b ────────────────────────────────────────────────────────┐
  │  Municipality Risk Rate (ranked list, now includes Distance col) │
  └─────────────────────────────────────────────────────────────────┘
  ┌─ Row 2 ─────────────────────────────────┬──────────────────────┐
  │  HS Type vs Risk (grouped bar)          │ Income Bracket Risk   │
  └─────────────────────────────────────────┴──────────────────────┘
  ┌─ Row 3 ─────────────────────────────────┬──────────────────────┐
  │  Term Comparison grouped bar            │ Intervention Coverage │
  └─────────────────────────────────────────┴──────────────────────┘
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QScrollArea, QComboBox, QSizePolicy,
    QProgressBar, QToolTip,
)
from PyQt6.QtCore import Qt, QTimer, QMargins
from PyQt6.QtGui import QColor, QFont, QPainter, QCursor
from PyQt6.QtCharts import (
    QChart, QChartView,
    QBarSet, QBarSeries, QHorizontalBarSeries,
    QBarCategoryAxis, QValueAxis,
)

from services.analytics_service import AnalyticsLoader, AnalyticsTermLoader
from services.data_store import DataStore
from ui.components.municipality_risk_map import (
    MunicipalityRiskMap,
    distance_from_campus_km,    # Option 3 — precompute distance per row
    normalize_municipality,     # collapse name variants (e.g. Bogo/Bogo City)
)


# ══════════════════════════════════════════════════════════════════════════════
# Summary card
# ══════════════════════════════════════════════════════════════════════════════

class _SummaryCard(QFrame):
    def __init__(self, title: str, accent: str = "#4f8cff", parent=None):
        super().__init__(parent)
        self._accent = accent
        self.setObjectName("analyticsPanel")
        lo = QVBoxLayout(self)
        lo.setContentsMargins(20, 16, 20, 16)
        lo.setSpacing(6)

        self._title_lbl = QLabel(title)
        self._title_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.45); font-size:11px; "
            "font-weight:600; letter-spacing:0.5px; background:transparent;")

        self._value_lbl = QLabel("—")
        self._value_lbl.setStyleSheet(
            f"color:{accent}; font-size:28px; font-weight:800; background:transparent;")

        self._sub_lbl = QLabel("")
        self._sub_lbl.setWordWrap(True)
        self._sub_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;")

        lo.addWidget(self._title_lbl)
        lo.addWidget(self._value_lbl)
        lo.addWidget(self._sub_lbl)

    def update(self, value: str, sub: str = ""):
        self._value_lbl.setText(value)
        self._sub_lbl.setText(sub)


# ══════════════════════════════════════════════════════════════════════════════
# Shared panel / chart helpers
# ══════════════════════════════════════════════════════════════════════════════

def _panel(min_height: int = 0) -> QFrame:
    f = QFrame()
    f.setObjectName("analyticsPanel")
    if min_height:
        f.setMinimumHeight(min_height)
    lo = QVBoxLayout(f)
    lo.setContentsMargins(20, 16, 20, 16)
    lo.setSpacing(10)
    return f


def _panel_header(title: str, hint: str = "") -> QHBoxLayout:
    row = QHBoxLayout()
    lbl = QLabel(title)
    lbl.setObjectName("cardTitle")
    row.addWidget(lbl)
    row.addStretch()
    if hint:
        h = QLabel(hint)
        h.setStyleSheet(
            "color:rgba(255,255,255,0.25); font-size:10px; background:transparent;")
        row.addWidget(h)
    return row


def _empty_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("analyticsText")
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setWordWrap(True)
    return lbl


def _clear_host(host: QFrame):
    lo = host.layout()
    if lo is None:
        return
    while lo.count():
        item = lo.takeAt(0)
        w = item.widget()
        if w:
            w.hide()
            w.deleteLater()


def _clear_layout(lo):
    while lo.count():
        item = lo.takeAt(0)
        if item.widget():
            item.widget().deleteLater()


def _base_chart() -> QChart:
    chart = QChart()
    chart.setBackgroundVisible(False)
    chart.setPlotAreaBackgroundVisible(False)
    chart.setMargins(QMargins(0, 0, 0, 0))
    chart.legend().setVisible(False)
    return chart


def _chart_view(chart: QChart, min_h: int = 200) -> QChartView:
    view = QChartView(chart)
    view.setRenderHint(QPainter.RenderHint.Antialiasing)
    view.setStyleSheet("background:transparent; border:none;")
    view.setMinimumHeight(min_h)
    return view


def _value_axis(max_val: float, label_fmt: str = "%d") -> QValueAxis:
    ax = QValueAxis()
    ax.setRange(0, max_val * 1.15)
    ax.setTickCount(5)
    ax.setLabelsColor(QColor("#a0aabe"))
    ax.setLabelsFont(QFont("Segoe UI", 8))
    ax.setGridLineColor(QColor(255, 255, 255, 8))
    ax.setLabelFormat(label_fmt)
    return ax


def _category_axis(labels: list[str], angle: int = 0) -> QBarCategoryAxis:
    ax = QBarCategoryAxis()
    ax.append(labels)
    ax.setLabelsColor(QColor("#a0aabe"))
    ax.setLabelsFont(QFont("Segoe UI", 8))
    ax.setGridLineColor(QColor(255, 255, 255, 0))
    if angle:
        ax.setLabelsAngle(angle)
    return ax


def _dist_color(dist_km: float | None) -> str:
    """
    Return a hex colour string matching the distance-line palette used by
    _MapCanvas, so the ranked list and the map legend are consistent.
    """
    if dist_km is None:
        return "rgba(255,255,255,0.35)"
    if dist_km <= 20:
        return "#4f8cff"
    if dist_km <= 60:
        return "#34d399"
    if dist_km <= 120:
        return "#f5b335"
    return "#ff5b5b"


def _normalise_muni_rows(rows: list[dict]) -> list[dict]:
    """
    Normalise municipality_risk rows from AnalyticsLoader into the key
    schema expected by MunicipalityRiskMap._render():
        municipality, total, high_risk, moderate_risk, low_risk

    Also merges rows whose municipality names are variants of the same
    place (e.g. "Bogo" and "Bogo City") using normalize_municipality(),
    since the SQL GROUP BY happens on the raw string and can't know
    they're the same municipality. Distance (dist_km) is computed once
    per canonical name after merging.

    AnalyticsLoader may return 'high'/'moderate'/'low' or the full key names.
    """
    merged: dict[str, dict] = {}

    for r in rows:
        raw_muni = r.get("municipality", "—")
        muni     = normalize_municipality(raw_muni)

        high = r.get("high",     r.get("high_risk",     0))
        mod  = r.get("moderate", r.get("moderate_risk", 0))
        low  = r.get("low",      r.get("low_risk",      0))
        tot  = r.get("total", 0)

        if muni not in merged:
            merged[muni] = {
                "municipality":  muni,
                "total":         0,
                "high_risk":     0,
                "moderate_risk": 0,
                "low_risk":      0,
            }
        merged[muni]["total"]         += tot
        merged[muni]["high_risk"]     += high
        merged[muni]["moderate_risk"] += mod
        merged[muni]["low_risk"]      += low

    result = list(merged.values())
    for r in result:
        r["dist_km"] = distance_from_campus_km(r["municipality"])

    # Preserve original ordering preference: highest combined risk first
    result.sort(key=lambda r: r["high_risk"] + r["moderate_risk"], reverse=True)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Analytics Page
# ══════════════════════════════════════════════════════════════════════════════

class AnalyticsPage(QWidget):
    """
    Dedicated Level-1 analytics page.
    Accessible to both Admin and Counselor roles.
    Loads fresh from DB on every page visit via _load().
    """

    def __init__(self):
        super().__init__()
        self._loader:      AnalyticsLoader     | None = None
        self._term_loader: AnalyticsTermLoader | None = None
        self._data:        dict                = {}
        self._first_show:  bool               = True   # tracks initial visit
        self._setup_ui()
        QTimer.singleShot(200, self._load_terms)

    # ══════════════════════════════════════════════════════════════════
    # UI Build
    # ══════════════════════════════════════════════════════════════════

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 24, 30, 24)
        root.setSpacing(16)

        # ── Header ────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        tc  = QVBoxLayout(); tc.setSpacing(3)
        t1  = QLabel("ANALYTICS")
        t1.setObjectName("header")
        t2  = QLabel("Student profile insights · Risk factor breakdown · Intervention coverage")
        t2.setObjectName("subHeader")
        tc.addWidget(t1); tc.addWidget(t2)
        hdr.addLayout(tc, 1)

        self._refresh_btn = QPushButton("↻  Refresh")
        self._refresh_btn.setFixedHeight(32)
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.setStyleSheet("""
            QPushButton {
                background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.12); border-radius:7px;
                color:rgba(255,255,255,0.65); font-size:12px; padding:0 14px;
            }
            QPushButton:hover {
                background:rgba(79,140,255,0.12);
                border-color:rgba(79,140,255,0.35); color:#4f8cff;
            }
            QPushButton:disabled { color:rgba(255,255,255,0.20); }
        """)
        self._refresh_btn.clicked.connect(self._load)
        hdr.addWidget(self._refresh_btn)
        root.addLayout(hdr)

        # ── Term filter bar ───────────────────────────────────────────
        fbar = QFrame()
        fbar.setObjectName("dashTermBar")
        fbar.setStyleSheet("""
            QFrame#dashTermBar {
                background:rgba(255,255,255,0.03);
                border:1px solid rgba(255,255,255,0.07); border-radius:8px;
            }
        """)
        flo = QHBoxLayout(fbar)
        flo.setContentsMargins(16, 10, 16, 10); flo.setSpacing(10)

        term_lbl = QLabel("Term Filter:")
        term_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.45); font-size:12px; background:transparent;")

        _combo_ss = """
            QComboBox {
                background:rgba(255,255,255,0.06);
                border:1px solid rgba(255,255,255,0.12);
                border-radius:6px; color:#e8eaf0;
                font-size:12px; padding:4px 10px; min-height:28px;
            }
            QComboBox:hover { border-color:rgba(79,140,255,0.35); }
            QComboBox::drop-down { border:none; width:16px; }
            QComboBox QAbstractItemView {
                background:#1a1f35; color:#e8eaf0;
                selection-background-color:rgba(79,140,255,0.18);
            }
        """

        self._ay_combo = QComboBox()
        self._ay_combo.setMinimumWidth(130)
        self._ay_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ay_combo.addItem("All Terms")
        self._ay_combo.setStyleSheet(_combo_ss)

        self._sem_combo = QComboBox()
        self._sem_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sem_combo.addItems(["All Semesters", "1st Semester", "2nd Semester"])
        self._sem_combo.setStyleSheet(_combo_ss)

        self._load_btn = QPushButton("Load")
        self._load_btn.setFixedHeight(30)
        self._load_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._load_btn.setEnabled(False)
        self._load_btn.setStyleSheet("""
            QPushButton {
                background:rgba(79,140,255,0.15);
                border:1px solid rgba(79,140,255,0.30);
                border-radius:6px; color:#4f8cff;
                font-size:12px; font-weight:600; padding:0 16px;
            }
            QPushButton:hover { background:rgba(79,140,255,0.28); }
            QPushButton:disabled {
                background:rgba(255,255,255,0.04);
                border-color:rgba(255,255,255,0.08);
                color:rgba(255,255,255,0.20);
            }
        """)
        self._load_btn.clicked.connect(self._load)

        self._status_lbl = QLabel("Loading available terms…")
        self._status_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;")

        flo.addWidget(term_lbl)
        flo.addWidget(self._ay_combo)
        flo.addWidget(self._sem_combo)
        flo.addWidget(self._load_btn)
        flo.addWidget(self._status_lbl)
        flo.addStretch()
        root.addWidget(fbar)

        # ── Scrollable content ────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background:transparent; border:none; }")

        content = QWidget()
        content.setStyleSheet("background:transparent;")
        self._content_lo = QVBoxLayout(content)
        self._content_lo.setContentsMargins(0, 0, 8, 0)
        self._content_lo.setSpacing(16)

        # ── Metric strip ──────────────────────────────────────────────
        self._metric_lo = QHBoxLayout()
        self._metric_lo.setSpacing(16)
        self._card_total    = _SummaryCard("TOTAL STUDENTS SCORED", "#4f8cff")
        self._card_atrisk   = _SummaryCard("AT-RISK STUDENTS",      "#f5b335")
        self._card_coverage = _SummaryCard("INTERVENTION COVERAGE", "#34d399")
        for c in (self._card_total, self._card_atrisk, self._card_coverage):
            self._metric_lo.addWidget(c, 1)
        self._content_lo.addLayout(self._metric_lo)

        # ── Row 1: Factor chart + Municipality map ────────────────────
        row1 = QHBoxLayout(); row1.setSpacing(16)

        self._factor_panel = _panel(min_height=280)
        self._factor_panel.layout().addLayout(
            _panel_header("Primary Risk Factor Frequency",
                          hint="Count of students per top factor"))
        self._factor_host = QFrame()
        self._factor_host.setStyleSheet("background:transparent;")
        QVBoxLayout(self._factor_host).setContentsMargins(0, 0, 0, 0)
        self._factor_panel.layout().addWidget(self._factor_host, 1)
        row1.addWidget(self._factor_panel, 3)

        self._map_panel = _panel(min_height=420)
        self._map_panel.layout().addLayout(
            _panel_header("Student Origin Risk Map",
                          hint="High-risk concentration by home municipality"))
        self._risk_map = MunicipalityRiskMap()
        self._map_panel.layout().addWidget(self._risk_map, 1)
        row1.addWidget(self._map_panel, 3)

        self._content_lo.addLayout(row1)

        # ── Municipality risk rate list (full-width below row 1) ──────
        self._muni_panel = _panel(min_height=200)
        self._muni_panel.layout().addLayout(
            _panel_header(
                "Municipality Risk Rate",
                hint="At-risk % · Distance from CTU  (≥5 students shown)"))
        self._muni_host_lo = QVBoxLayout()
        self._muni_host_lo.setSpacing(4)
        self._muni_panel.layout().addLayout(self._muni_host_lo)
        self._content_lo.addWidget(self._muni_panel)

        # ── Row 2: HS Type + Income ───────────────────────────────────
        row2 = QHBoxLayout(); row2.setSpacing(16)

        self._hs_panel = _panel(min_height=260)
        self._hs_panel.layout().addLayout(
            _panel_header("HS Type vs Risk", hint="Public vs Private high school"))
        self._hs_host = QFrame()
        self._hs_host.setStyleSheet("background:transparent;")
        QVBoxLayout(self._hs_host).setContentsMargins(0, 0, 0, 0)
        self._hs_panel.layout().addWidget(self._hs_host, 1)
        row2.addWidget(self._hs_panel, 1)

        self._income_panel = _panel(min_height=260)
        self._income_panel.layout().addLayout(
            _panel_header("Income Bracket vs Risk",
                          hint="At-risk rate per family income band"))
        self._income_host = QFrame()
        self._income_host.setStyleSheet("background:transparent;")
        QVBoxLayout(self._income_host).setContentsMargins(0, 0, 0, 0)
        self._income_panel.layout().addWidget(self._income_host, 1)
        row2.addWidget(self._income_panel, 1)
        self._content_lo.addLayout(row2)

        # ── Row 3: Term comparison + Intervention coverage ────────────
        row3 = QHBoxLayout(); row3.setSpacing(16)

        self._term_panel = _panel(min_height=260)
        self._term_panel.layout().addLayout(
            _panel_header("Semester Comparison",
                          hint="High / Moderate / Low count per term"))
        self._term_host = QFrame()
        self._term_host.setStyleSheet("background:transparent;")
        QVBoxLayout(self._term_host).setContentsMargins(0, 0, 0, 0)
        self._term_panel.layout().addWidget(self._term_host, 1)
        row3.addWidget(self._term_panel, 3)

        self._cov_panel = _panel(min_height=260)
        self._cov_panel.layout().addLayout(
            _panel_header("Intervention Coverage",
                          hint="Most recent term with predictions"))
        self._cov_host_lo = QVBoxLayout()
        self._cov_host_lo.setSpacing(10)
        self._cov_panel.layout().addLayout(self._cov_host_lo)
        row3.addWidget(self._cov_panel, 1)
        self._content_lo.addLayout(row3)

        self._content_lo.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        self._show_empty_all()

    # ══════════════════════════════════════════════════════════════════
    # Term loading
    # ══════════════════════════════════════════════════════════════════

    def _load_terms(self):
        # Guard: don't start a second term-loader if one is already running
        if self._term_loader is not None:
            return
        self._term_loader = AnalyticsTermLoader()
        # Keep cleanup LAST so _on_terms_loaded fully executes before deleteLater
        self._term_loader.finished.connect(self._on_terms_loaded)
        self._term_loader.error.connect(self._on_terms_error)
        self._term_loader.start()

    def _clear_term_loader(self):
        w = self._term_loader
        self._term_loader = None
        if w is not None:
            try:
                w.quit()
                w.wait(500)
                w.deleteLater()
            except RuntimeError:
                pass

    def _on_terms_error(self, e: str):
        self._status_lbl.setText(f"⚠ {e}")
        self._clear_term_loader()

    def _on_terms_loaded(self, terms: list):
        self._ay_combo.blockSignals(True)
        self._ay_combo.clear()
        self._ay_combo.addItem("All Terms", userData=("", 0))
        seen = []
        for ay, sem in terms:
            if ay not in seen:
                seen.append(ay)
                self._ay_combo.addItem(ay, userData=(ay, 0))
        self._ay_combo.blockSignals(False)
        self._load_btn.setEnabled(True)
        self._status_lbl.setText(
            f"{len(terms)} term(s) available — select a term and click Load"
            if terms else "No prediction data yet")
        self._clear_term_loader()
        # Do NOT auto-load. Wait for the user to click Load or Refresh.

    # ══════════════════════════════════════════════════════════════════
    # Data loading
    # ══════════════════════════════════════════════════════════════════

    def _load(self):
        if self._loader is not None:
            try:
                self._loader.finished.disconnect()
                self._loader.error.disconnect()
                if self._loader.isRunning():
                    self._loader.quit()
                    self._loader.wait(1000)
                w = self._loader
                self._loader = None
                w.deleteLater()
            except RuntimeError:
                self._loader = None

        ay_data = self._ay_combo.currentData()
        ay      = ay_data[0] if ay_data else ""
        sem_idx = self._sem_combo.currentIndex()
        sem     = sem_idx if sem_idx > 0 else 0

        self._load_btn.setEnabled(False)
        self._refresh_btn.setEnabled(False)
        self._status_lbl.setText("Loading analytics…")

        self._loader = AnalyticsLoader(ay=ay, sem=sem)
        self._loader.finished.connect(self._on_data_loaded)
        self._loader.finished.connect(self._clear_loader)
        self._loader.error.connect(self._on_load_error)
        self._loader.error.connect(self._clear_loader)
        self._loader.start()

    def _on_load_error(self, e: str):
        self._status_lbl.setText(f"⚠ {e}")
        self._load_btn.setEnabled(True)
        self._refresh_btn.setEnabled(True)

    def _clear_loader(self):
        w = self._loader
        self._loader = None
        if w is not None:
            try:
                w.quit()
                w.wait(500)
                w.deleteLater()
            except RuntimeError:
                pass

    def _on_data_loaded(self, data: dict):
        self._data = data
        self._load_btn.setEnabled(True)
        self._refresh_btn.setEnabled(True)
        self._status_lbl.setText("Updated just now")

        self._render_summary_cards(data)
        self._render_factor_chart(data.get("primary_factor_freq", []))

        # Merge variant municipality names (e.g. Bogo / Bogo City) and
        # attach dist_km. Both the ranked list and the map use this
        # single merged dataset so counts/percentages stay consistent.
        muni_rows_norm = _normalise_muni_rows(data.get("municipality_risk", []))
        self._render_municipality(muni_rows_norm)
        self._risk_map._render(muni_rows_norm)

        self._render_hs_type(data.get("hs_type_risk", []))
        self._render_income(data.get("income_risk", []))
        self._render_term_comparison(data.get("term_comparison", []))
        self._render_coverage(data.get("intervention_coverage", {}))

    # ══════════════════════════════════════════════════════════════════
    # Summary cards
    # ══════════════════════════════════════════════════════════════════

    def _render_summary_cards(self, data: dict):
        tc       = data.get("term_comparison", [])
        total    = sum(r["total"]    for r in tc)
        high     = sum(r["high"]     for r in tc)
        moderate = sum(r["moderate"] for r in tc)
        at_risk  = high + moderate
        at_pct   = round(at_risk / max(total, 1) * 100, 1)

        self._card_total.update(
            f"{total:,}", f"Across {len(tc)} term(s)")
        self._card_atrisk.update(
            f"{at_risk:,}",
            f"{at_pct}% of cohort  ·  High: {high:,}  Mod: {moderate:,}")

        cov = data.get("intervention_coverage", {})
        if cov:
            pct = cov.get("coverage_pct", 0)
            n   = cov.get("intervened", 0)
            tot = cov.get("high_risk_total", 0)
            ay  = cov.get("term_ay", "")
            sem = cov.get("term_sem", "")
            sl  = "1st" if sem == 1 else "2nd" if sem == 2 else ""
            self._card_coverage.update(
                f"{pct:.0f}%",
                f"{n:,} / {tot:,} high-risk students  ·  {sl} Sem {ay}")
        else:
            self._card_coverage.update("—", "No intervention data yet")

    # ══════════════════════════════════════════════════════════════════
    # Chart 1 — Primary factor frequency
    # ══════════════════════════════════════════════════════════════════

    def _render_factor_chart(self, rows: list):
        _clear_host(self._factor_host)
        if not rows:
            self._factor_host.layout().addWidget(
                _empty_label("No primary factor data found.\nRun a prediction first."))
            return

        labels  = [r["factor"].replace("_", " ").title()[:28] for r in rows]
        counts  = [r["count"] for r in rows]

        bar_set = QBarSet("")
        bar_set.setColor(QColor("#4f8cff"))
        for c in counts:
            bar_set.append(c)
        bar_set.hovered.connect(
            lambda state, idx, lbl=labels, vals=counts:
            QToolTip.showText(QCursor.pos(),
                f"<b>{lbl[idx]}</b><br>{vals[idx]:,} students")
            if state else QToolTip.hideText()
        )

        series = QHorizontalBarSeries()
        series.append(bar_set)
        series.setLabelsVisible(False)

        chart = _base_chart()
        chart.addSeries(series)

        cat_ax = QBarCategoryAxis()
        cat_ax.append(labels)
        cat_ax.setLabelsColor(QColor("#a0aabe"))
        cat_ax.setLabelsFont(QFont("Segoe UI", 8))
        cat_ax.setGridLineColor(QColor(255, 255, 255, 0))
        chart.addAxis(cat_ax, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(cat_ax)

        val_ax = _value_axis(max(counts, default=1))
        chart.addAxis(val_ax, Qt.AlignmentFlag.AlignBottom)
        series.attachAxis(val_ax)

        lo = self._factor_host.layout()
        lo.setContentsMargins(0, 0, 0, 0)
        lo.addWidget(_chart_view(chart, min_h=max(200, len(labels) * 26)))

    # ══════════════════════════════════════════════════════════════════
    # Chart 2 — Municipality risk rate list  (Option 3: + Distance col)
    # ══════════════════════════════════════════════════════════════════

    def _render_municipality(self, norm_rows: list[dict]):
        """
        norm_rows — merged/normalised rows (variant municipality names like
        "Bogo" / "Bogo City" already collapsed into one), each with
        municipality, total, high_risk, moderate_risk, low_risk, dist_km.
        At-risk rate is computed here from the merged counts.
        """
        _clear_layout(self._muni_host_lo)

        if not norm_rows:
            self._muni_host_lo.addWidget(
                _empty_label(
                    "No municipality data.\n"
                    "Ensure home_municipality is populated in dim_student."))
            return

        # ── Column header ─────────────────────────────────────────────
        hdr = QHBoxLayout()
        for txt, stretch, fixed_w in [
            ("Municipality", 3, None),
            ("At-Risk %",    1, None),
            ("Count",        0, 40),
            ("Distance",     0, 80),   # ← Option 3 header
        ]:
            lbl = QLabel(txt)
            lbl.setStyleSheet(
                "color:rgba(255,255,255,0.30); font-size:10px; "
                "font-weight:700; background:transparent;")
            if fixed_w:
                lbl.setFixedWidth(fixed_w)
                hdr.addWidget(lbl)
            else:
                hdr.addWidget(lbl, stretch)
        self._muni_host_lo.addLayout(hdr)

        scroll_host = QWidget()
        scroll_host.setStyleSheet("background:transparent;")
        slo = QVBoxLayout(scroll_host)
        slo.setSpacing(4)
        slo.setContentsMargins(0, 0, 0, 0)

        for r in norm_rows:
            muni     = r["municipality"]
            total    = r["total"]
            high     = r["high_risk"]
            mod      = r["moderate_risk"]
            rate     = round((high + mod) / max(total, 1) * 100, 1)
            dist_km  = r.get("dist_km")
            d_color  = _dist_color(dist_km)
            dist_txt = f"{dist_km:.1f} km" if dist_km is not None else "—"

            row_widget = QWidget()
            row_widget.setStyleSheet("background:transparent;")
            rlo = QHBoxLayout(row_widget)
            rlo.setContentsMargins(0, 2, 0, 2)
            rlo.setSpacing(8)

            name = QLabel(muni[:24])
            name.setStyleSheet(
                "color:rgba(255,255,255,0.75); font-size:11px; background:transparent;")
            name.setMinimumWidth(110)

            risk_color = (
                "#ff5b5b" if rate >= 60 else
                "#f5b335" if rate >= 35 else "#4f8cff"
            )
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(int(rate))
            bar.setFixedHeight(8)
            bar.setTextVisible(False)
            bar.setStyleSheet(f"""
                QProgressBar {{
                    background:rgba(255,255,255,0.08);
                    border-radius:4px; border:none;
                }}
                QProgressBar::chunk {{
                    background:{risk_color}; border-radius:4px;
                }}
            """)
            bar.setToolTip(
                f"{muni}: {rate}% at-risk "
                f"({high} high + {mod} mod / {total} total)"
                + (f"  ·  📍 {dist_km:.1f} km from CTU"
                   if dist_km is not None else ""))

            pct_lbl = QLabel(f"{rate:.0f}%")
            pct_lbl.setStyleSheet(
                f"color:{risk_color}; font-size:11px; "
                f"font-weight:700; background:transparent;")
            pct_lbl.setFixedWidth(36)

            cnt_lbl = QLabel(str(total))
            cnt_lbl.setStyleSheet(
                "color:rgba(255,255,255,0.40); font-size:11px; background:transparent;")
            cnt_lbl.setFixedWidth(40)

            # ── Option 3: distance badge ───────────────────────────────
            dist_lbl = QLabel(dist_txt)
            dist_lbl.setStyleSheet(
                f"color:{d_color}; font-size:11px; "
                f"font-weight:600; background:transparent;")
            dist_lbl.setFixedWidth(80)
            dist_lbl.setToolTip(
                f"Straight-line distance from CTU-Daanbantayan to {muni}"
                if dist_km is not None
                else f"{muni} coordinates not in lookup table")

            rlo.addWidget(name, 3)
            rlo.addWidget(bar, 2)
            rlo.addWidget(pct_lbl)
            rlo.addWidget(cnt_lbl)
            rlo.addWidget(dist_lbl)   # ← Option 3
            slo.addWidget(row_widget)

        slo.addStretch()
        self._muni_host_lo.addWidget(scroll_host, 1)

    # ══════════════════════════════════════════════════════════════════
    # Chart 3 — HS Type vs Risk
    # ══════════════════════════════════════════════════════════════════

    def _render_hs_type(self, rows: list):
        _clear_host(self._hs_host)
        if not rows:
            self._hs_host.layout().addWidget(
                _empty_label(
                    "No HS type data.\n"
                    "Ensure hs_type is populated in dim_student."))
            return

        labels = [r["hs_type"][:20] for r in rows]
        highs  = [r["high"]     for r in rows]
        mods   = [r["moderate"] for r in rows]
        lows   = [
            r.get("low", max(0, r["total"] - r["high"] - r["moderate"]))
            for r in rows
        ]

        high_set = QBarSet("High");     high_set.setColor(QColor("#ff5b5b"))
        mod_set  = QBarSet("Moderate"); mod_set.setColor(QColor("#f5b335"))
        low_set  = QBarSet("Low");      low_set.setColor(QColor("#34d399"))

        for h, m, l in zip(highs, mods, lows):
            high_set.append(h); mod_set.append(m); low_set.append(l)

        for s, vals in [(high_set, highs), (mod_set, mods), (low_set, lows)]:
            s.hovered.connect(
                lambda state, idx, v=vals, lb=labels, n=s.label():
                QToolTip.showText(QCursor.pos(),
                    f"<b>{lb[idx]}</b><br>{n}: {v[idx]:,}")
                if state else QToolTip.hideText()
            )

        series = QBarSeries()
        series.append(high_set); series.append(mod_set); series.append(low_set)

        chart = _base_chart()
        chart.addSeries(series)
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignTop)
        chart.legend().setLabelColor(QColor("#c9d0e0"))
        chart.legend().setFont(QFont("Segoe UI", 8))

        cat_ax = _category_axis(labels, angle=-20 if len(labels) > 4 else 0)
        chart.addAxis(cat_ax, Qt.AlignmentFlag.AlignBottom)
        series.attachAxis(cat_ax)

        max_val = max(
            max(highs, default=0) + max(mods, default=0) + max(lows, default=0), 1)
        val_ax = _value_axis(max_val)
        chart.addAxis(val_ax, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(val_ax)

        lo = self._hs_host.layout()
        lo.setContentsMargins(0, 0, 0, 0)
        lo.addWidget(_chart_view(chart, min_h=220))

    # ══════════════════════════════════════════════════════════════════
    # Chart 4 — Income bracket vs risk
    # ══════════════════════════════════════════════════════════════════

    def _render_income(self, rows: list):
        _clear_host(self._income_host)
        if not rows:
            self._income_host.layout().addWidget(
                _empty_label(
                    "No income bracket data.\n"
                    "Ensure family_income_bracket is populated."))
            return

        labels = [r["bracket"][:20] for r in rows]
        highs  = [r["high"]     for r in rows]
        mods   = [r["moderate"] for r in rows]
        totals = [r["total"]    for r in rows]

        high_set = QBarSet("High");     high_set.setColor(QColor("#ff5b5b"))
        mod_set  = QBarSet("Moderate"); mod_set.setColor(QColor("#f5b335"))
        rest_set = QBarSet("Low");      rest_set.setColor(QColor("#34d399"))

        for h, m, t in zip(highs, mods, totals):
            high_set.append(h)
            mod_set.append(m)
            rest_set.append(max(0, t - h - m))

        for s, vals, n in [(high_set, highs, "High"), (mod_set, mods, "Moderate")]:
            s.hovered.connect(
                lambda state, idx, v=vals, lb=labels, name=n:
                QToolTip.showText(QCursor.pos(),
                    f"<b>{lb[idx]}</b><br>{name}: {v[idx]:,}")
                if state else QToolTip.hideText()
            )

        series = QHorizontalBarSeries()
        series.append(high_set); series.append(mod_set); series.append(rest_set)

        chart = _base_chart()
        chart.addSeries(series)
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignTop)
        chart.legend().setLabelColor(QColor("#c9d0e0"))
        chart.legend().setFont(QFont("Segoe UI", 8))

        cat_ax = QBarCategoryAxis()
        cat_ax.append(labels)
        cat_ax.setLabelsColor(QColor("#a0aabe"))
        cat_ax.setLabelsFont(QFont("Segoe UI", 8))
        cat_ax.setGridLineColor(QColor(255, 255, 255, 0))
        chart.addAxis(cat_ax, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(cat_ax)

        val_ax = _value_axis(max(totals, default=1))
        chart.addAxis(val_ax, Qt.AlignmentFlag.AlignBottom)
        series.attachAxis(val_ax)

        lo = self._income_host.layout()
        lo.setContentsMargins(0, 0, 0, 0)
        lo.addWidget(_chart_view(chart, min_h=max(180, len(labels) * 28)))

    # ══════════════════════════════════════════════════════════════════
    # Chart 5 — Term comparison
    # ══════════════════════════════════════════════════════════════════

    def _render_term_comparison(self, rows: list):
        _clear_host(self._term_host)
        if not rows:
            self._term_host.layout().addWidget(
                _empty_label(
                    "No multi-term data yet.\n"
                    "Run predictions across multiple semesters."))
            return

        labels = [r["term_label"] for r in rows]
        highs  = [r["high"]     for r in rows]
        mods   = [r["moderate"] for r in rows]
        lows   = [r["low"]      for r in rows]

        high_set = QBarSet("High");     high_set.setColor(QColor("#ff5b5b"))
        mod_set  = QBarSet("Moderate"); mod_set.setColor(QColor("#f5b335"))
        low_set  = QBarSet("Low");      low_set.setColor(QColor("#34d399"))

        for h, m, l in zip(highs, mods, lows):
            high_set.append(h); mod_set.append(m); low_set.append(l)

        for s, vals, n in [
            (high_set, highs, "High"),
            (mod_set,  mods,  "Moderate"),
            (low_set,  lows,  "Low"),
        ]:
            s.hovered.connect(
                lambda state, idx, v=vals, lb=labels, name=n:
                QToolTip.showText(QCursor.pos(),
                    f"<b>{lb[idx]}</b><br>{name}: {v[idx]:,}")
                if state else QToolTip.hideText()
            )

        series = QBarSeries()
        series.append(high_set); series.append(mod_set); series.append(low_set)

        chart = _base_chart()
        chart.addSeries(series)
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignTop)
        chart.legend().setLabelColor(QColor("#c9d0e0"))
        chart.legend().setFont(QFont("Segoe UI", 9))

        cat_ax = _category_axis(labels, angle=-20 if len(labels) > 3 else 0)
        chart.addAxis(cat_ax, Qt.AlignmentFlag.AlignBottom)
        series.attachAxis(cat_ax)

        max_val = max(max(highs, default=0) + max(mods, default=0), 1)
        val_ax  = _value_axis(max_val)
        chart.addAxis(val_ax, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(val_ax)

        lo = self._term_host.layout()
        lo.setContentsMargins(0, 0, 0, 0)
        lo.addWidget(_chart_view(chart, min_h=220))

    # ══════════════════════════════════════════════════════════════════
    # Panel 6 — Intervention coverage
    # ══════════════════════════════════════════════════════════════════

    def _render_coverage(self, cov: dict):
        _clear_layout(self._cov_host_lo)

        if not cov:
            self._cov_host_lo.addWidget(
                _empty_label(
                    "No intervention data.\n"
                    "Run batch analysis on the Interventions page."))
            return

        pct   = cov.get("coverage_pct", 0)
        n     = cov.get("intervened", 0)
        total = cov.get("high_risk_total", 0)
        ay    = cov.get("term_ay", "—")
        sem   = cov.get("term_sem", 0)
        sem_s = "1st Semester" if sem == 1 else "2nd Semester" if sem == 2 else ""

        color = ("#34d399" if pct >= 75 else
                 "#f5b335" if pct >= 40 else "#ff5b5b")

        term_lbl = QLabel(f"{sem_s}  ·  AY {ay}" if sem_s else ay)
        term_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.40); font-size:11px; background:transparent;")

        big_pct = QLabel(f"{pct:.0f}%")
        big_pct.setStyleSheet(
            f"color:{color}; font-size:48px; font-weight:800; background:transparent;")
        big_pct.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sub = QLabel("of high-risk students received an AI intervention")
        sub.setWordWrap(True)
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet(
            "color:rgba(255,255,255,0.40); font-size:12px; background:transparent;")

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(int(pct))
        bar.setFixedHeight(10)
        bar.setTextVisible(False)
        bar.setStyleSheet(f"""
            QProgressBar {{
                background:rgba(255,255,255,0.08);
                border-radius:5px; border:none;
            }}
            QProgressBar::chunk {{
                background:{color}; border-radius:5px;
            }}
        """)

        detail = QLabel(f"{n:,} intervened  /  {total:,} high-risk")
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail.setStyleSheet(
            "color:rgba(255,255,255,0.55); font-size:12px; background:transparent;")

        gap = total - n
        if gap > 0:
            gap_lbl = QLabel(f"⚠  {gap:,} students not yet analyzed")
            gap_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            gap_lbl.setStyleSheet(
                "color:#f5b335; font-size:11px; background:transparent;")
            self._cov_host_lo.addWidget(gap_lbl)

        self._cov_host_lo.addWidget(term_lbl)
        self._cov_host_lo.addStretch()
        self._cov_host_lo.addWidget(big_pct)
        self._cov_host_lo.addWidget(sub)
        self._cov_host_lo.addSpacing(8)
        self._cov_host_lo.addWidget(bar)
        self._cov_host_lo.addWidget(detail)
        self._cov_host_lo.addStretch()

    # ══════════════════════════════════════════════════════════════════
    # Empty state
    # ══════════════════════════════════════════════════════════════════

    def _show_empty_all(self):
        msg = "Select a term and click Load, or wait for auto-load."
        for host in (self._factor_host, self._hs_host,
                     self._income_host, self._term_host):
            _clear_host(host)
            lo = host.layout()
            if lo:
                lo.addWidget(_empty_label(msg))

        for lo in (self._muni_host_lo, self._cov_host_lo):
            _clear_layout(lo)               # ← was missing; caused widget buildup
            lo.addWidget(_empty_label(msg))

        self._risk_map.show_empty()

    # ══════════════════════════════════════════════════════════════════
    # Lifecycle
    # ══════════════════════════════════════════════════════════════════

    def showEvent(self, event):
        super().showEvent(event)
        if self._first_show:
            # First visit: kick off term-list fetch so the combo populates.
            # Do NOT auto-load data — wait for the user to click Load.
            self._first_show = False
            return
        # Subsequent visits: re-fetch the term list so the combo stays
        # fresh, but still do NOT auto-load chart data.
        if self._term_loader is None:
            QTimer.singleShot(0, self._load_terms)

    def closeEvent(self, event):
        self._risk_map.cleanup()
        for attr in ("_loader", "_term_loader"):
            w = getattr(self, attr, None)
            if w is None:
                continue
            setattr(self, attr, None)
            try:
                w.finished.disconnect()
                w.error.disconnect()
            except RuntimeError:
                pass
            try:
                if w.isRunning():
                    w.quit()
                    w.wait(2000)
                w.deleteLater()
            except RuntimeError:
                pass
        super().closeEvent(event)