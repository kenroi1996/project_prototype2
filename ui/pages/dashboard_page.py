from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QSizePolicy, QGraphicsOpacityEffect, QProgressBar,
    QScrollArea, QGridLayout, QToolTip,
)
from PyQt6.QtCore import (
    QTimer, Qt, QPropertyAnimation, QEasingCurve, QMargins,
)
from PyQt6.QtGui import (
    QColor, QFont, QLinearGradient, QPainter, QBrush,
    QPixmap, QIcon, QCursor,
)
from PyQt6.QtCharts import (
    QChart, QChartView, QPieSeries, QBarSet, QStackedBarSeries,
    QBarCategoryAxis, QValueAxis, QLineSeries, QSplineSeries,
    QDateTimeAxis, QScatterSeries,
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
from services.system_config import SystemConfig


class DashboardPage(PredictionMixin, QWidget):
    """Dashboard page with interactive analytics and cross-filtering."""

    def __init__(self):
        super().__init__()
        # Cross-filter state
        self._active_risk_filter:    str = ""   # "" = all
        self._active_college_filter: str = ""
        self._active_shap_feature:   str = ""
        self._shap_widgets:          list[ShapFactor] = []
        self._all_predictions:       list[dict] = []

        self.setup_ui()
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
                self._show_empty_state()
        if key in ("predictions", "last_prediction_run", "all"):
            self._refresh_prediction_status()
        if key in ("mis", "sao", "guidance", "registrar", "all"):
            self._refresh_coverage()
        if key in ("system_config", "all"):
            self._refresh_term_label()

    def _refresh_term_label(self):
        """Update the semester pill in the header when settings change."""
        if hasattr(self, "_model_semester_lbl"):
            self._model_semester_lbl.setText(SystemConfig.term_label())

    # ------------------------------------------------------------------
    # Prediction results → update UI
    # ------------------------------------------------------------------

    def _apply_predictions(self, result):
        s = result.summary
        self._all_predictions = result.predictions

        self._metric_1.update_values(
            value   = f"{s.avg_score}%",
            status  = "High Risk" if s.avg_score >= 70 else "Moderate Risk",
            remarks = f"Average across {s.total:,} students",
        )
        self._metric_2.update_values(
            value   = f"{s.high_risk + s.moderate_risk:,}",
            status  = "High Risk" if s.high_risk_pct >= 30 else "Moderate Risk",
            remarks = f"{s.high_risk_pct}% of total cohort",
        )
        self._metric_3.update_values(
            value   = f"{s.high_risk:,}",
            status  = "High Risk",
            remarks = (
                f"{round(s.high_risk / s.total * 100, 1)}% flagged this run"
                if s.total else "—"
            ),
        )

        self._risk_distribution_chart.update_chart(
            high_risk=s.high_risk,
            moderate_risk=s.moderate_risk,
            low_risk=s.low_risk,
        )
        self._risk_analytics_chart.update_chart(by_college=s.by_college)
        self._update_shap_factors(result.predictions)
        self._refresh_alerts_panel(result.predictions)
        self._update_program_heatmap(result.predictions)
        self._load_trend_from_db()

        # Reset cross-filters when new prediction loads
        self._active_risk_filter    = ""
        self._active_college_filter = ""
        self._active_shap_feature   = ""
        self._update_filter_badge()

    # ------------------------------------------------------------------
    # CROSS-FILTERING
    # ------------------------------------------------------------------

    def _on_risk_filter_changed(self, category: str):
        """Called when a donut slice is clicked."""
        self._active_risk_filter = category
        self._active_college_filter = ""   # risk filter clears college filter
        self._update_filter_badge()
        self._apply_cross_filter()

    def _on_college_clicked(self, college: str):
        """Called when a bar column is clicked."""
        if self._active_college_filter == college:
            self._active_college_filter = ""
        else:
            self._active_college_filter = college
        self._update_filter_badge()
        self._apply_cross_filter()

    def _on_shap_clicked(self, feature: str):
        """Called when a SHAP row is clicked — highlight affected students."""
        if self._active_shap_feature == feature:
            self._active_shap_feature = ""
            # Deselect all shap widgets
            for w in self._shap_widgets:
                w.deselect()
        else:
            self._active_shap_feature = feature
        self._apply_cross_filter()

    def _on_heatmap_row_clicked(self, program: str):
        """Called when a program row in the heatmap is clicked."""
        # Filter alerts to this program
        preds = [p for p in self._all_predictions if p.get("program") == program]
        self._refresh_alerts_panel(preds, title=f"🎯  {program} — At-Risk Students")
        self._set_filter_banner(f"Program: {program}")

    def _apply_cross_filter(self):
        """Apply all active filters to the alerts panel."""
        preds = self._all_predictions

        if self._active_risk_filter:
            preds = [p for p in preds
                     if p.get("category") == self._active_risk_filter]

        if self._active_college_filter:
            preds = [p for p in preds
                     if p.get("college") == self._active_college_filter]

        # SHAP filter: students where this feature is in their top 3 factors
        if self._active_shap_feature:
            def _has_feature(pred):
                for entry in pred.get("shap_factors", []):
                    if len(entry) >= 1 and entry[0] == self._active_shap_feature:
                        return True
                return False
            preds = [p for p in preds if _has_feature(p)]

        # Build banner text
        parts = []
        if self._active_risk_filter:
            label = {
                "high_risk":     "High Risk",
                "moderate_risk": "Moderate Risk",
                "low_risk":      "Low Risk",
            }.get(self._active_risk_filter, "")
            parts.append(label)
        if self._active_college_filter:
            parts.append(self._active_college_filter)
        if self._active_shap_feature:
            parts.append(f"Factor: {self._active_shap_feature.replace('_',' ')}")

        title = "  ›  ".join(parts) if parts else None
        self._refresh_alerts_panel(preds, title=title)

        if parts:
            self._set_filter_banner(
                f"Filtered by: {' · '.join(parts)}  ·  {len(preds):,} students"
            )
        else:
            self._clear_filter_banner()

    def _update_filter_badge(self):
        active = sum([
            bool(self._active_risk_filter),
            bool(self._active_college_filter),
            bool(self._active_shap_feature),
        ])
        if active:
            self._filter_clear_btn.setText(f"✕  Clear {active} filter{'s' if active>1 else ''}")
            self._filter_clear_btn.show()
        else:
            self._filter_clear_btn.hide()

    def _clear_all_filters(self):
        self._active_risk_filter    = ""
        self._active_college_filter = ""
        self._active_shap_feature   = ""
        for w in self._shap_widgets:
            w.deselect()
        self._risk_distribution_chart._reset_slice_opacity()
        self._filter_clear_btn.hide()
        self._clear_filter_banner()
        if self._all_predictions:
            self._refresh_alerts_panel(self._all_predictions)

    def _set_filter_banner(self, text: str):
        self._filter_banner.setText(f"🔍  {text}")
        self._filter_banner.show()

    def _clear_filter_banner(self):
        self._filter_banner.hide()

    # ------------------------------------------------------------------
    # SHAP factors
    # ------------------------------------------------------------------

    def _update_shap_factors(self, predictions: list) -> None:
        while self._shap_factors_layout.count():
            item = self._shap_factors_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._shap_widgets.clear()

        factor_data: dict = {}
        for pred in predictions:
            for entry in pred.get("shap_factors", []):
                if len(entry) == 4:
                    feature_name, human_label, formatted_value, pct = entry
                elif len(entry) == 2:
                    feature_name, pct = entry
                    human_label = feature_name.replace("_", " ").title()
                else:
                    continue
                if human_label not in factor_data:
                    factor_data[human_label] = {
                        "pcts": [], "feature_name": feature_name
                    }
                factor_data[human_label]["pcts"].append(pct)

        avg_factors = [
            (label, round(sum(d["pcts"]) / len(d["pcts"]), 1), d["feature_name"])
            for label, d in factor_data.items()
        ]
        avg_factors.sort(key=lambda x: x[1], reverse=True)

        color_map = {
            "Entrance Exam Score":          "#ff5b5b",
            "High School GPA":              "#ff5b5b",
            "Financial Stress Index":       "#f5b335",
            "First-Generation Student":     "#f5b335",
            "Gap Years Before College":     "#f5b335",
            "Distance from Campus":         "#4f8cff",
            "SHS Strand–Program Alignment": "#4f8cff",
            "Has Scholarship":              "#34d399",
            "Graduated with HS Honors":     "#34d399",
            "Attended Private High School": "#34d399",
            "Age at Enrollment":            "#4f8cff",
        }

        def get_color(label):
            for key, color in color_map.items():
                if key.lower() in label.lower():
                    return color
            return "#4f8cff"

        for label, avg_pct, feat_name in avg_factors[:8]:
            w = ShapFactor(
                label_text   = label,
                percentage   = int(avg_pct),
                color        = get_color(label),
                feature_name = feat_name,
            )
            w.clicked.connect(self._on_shap_clicked)
            self._shap_widgets.append(w)
            self._shap_factors_layout.addWidget(w)

        self._shap_factors_layout.addStretch()

    # ------------------------------------------------------------------
    # Recent alerts panel
    # ------------------------------------------------------------------

    def _refresh_alerts_panel(self, predictions: list,
                               title: str = None) -> None:
        while self._alerts_content_layout.count():
            item = self._alerts_content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Panel title
        if title:
            t = QLabel(title)
            t.setStyleSheet(
                "color: #4f8cff; font-size:11px; font-weight:600; "
                "background:transparent; padding-bottom:4px;"
            )
            self._alerts_content_layout.addWidget(t)

        # Show top 5 at-risk
        at_risk = [p for p in predictions
                   if p.get("category") in ("high_risk", "moderate_risk")]
        at_risk.sort(key=lambda p: p.get("score", 0), reverse=True)

        if not at_risk:
            lbl = QLabel("No at-risk students match the current filter.")
            lbl.setStyleSheet(
                "color: rgba(255,255,255,0.35); font-size:12px; background:transparent;"
            )
            lbl.setWordWrap(True)
            self._alerts_content_layout.addWidget(lbl)
            self._alerts_content_layout.addStretch()
            return

        for pred in at_risk[:5]:
            card = self._build_mini_alert_card(pred)
            self._alerts_content_layout.addWidget(card)

        if len(at_risk) > 5:
            more = QLabel(
                f"+ {len(at_risk) - 5:,} more — go to Risk Alerts page"
            )
            more.setStyleSheet(
                "color: rgba(255,255,255,0.30); font-size:11px; "
                "background:transparent; padding-top:4px;"
            )
            self._alerts_content_layout.addWidget(more)

        self._alerts_content_layout.addStretch()

    def _build_mini_alert_card(self, pred: dict) -> QFrame:
        cat   = pred.get("category", "low_risk")
        color = {"high_risk":"#ff5b5b","moderate_risk":"#f5b335"}.get(cat,"#34d399")
        score = pred.get("score", 0)

        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: rgba(255,255,255,0.03);
                border-left: 3px solid {color};
                border-radius: 6px;
                margin-bottom: 2px;
            }}
        """)
        lo = QHBoxLayout(card)
        lo.setContentsMargins(10, 8, 10, 8)
        lo.setSpacing(8)

        info = QVBoxLayout()
        info.setSpacing(2)

        name_lbl = QLabel(pred.get("name", "—"))
        name_lbl.setStyleSheet(
            "color: #e8eaf0; font-size:12px; font-weight:600; background:transparent;"
        )

        meta = QLabel(
            f"{pred.get('program','—')}  ·  "
            f"{pred.get('college','—')}"
        )
        meta.setStyleSheet(
            "color: rgba(255,255,255,0.40); font-size:10px; background:transparent;"
        )

        info.addWidget(name_lbl)
        info.addWidget(meta)

        score_lbl = QLabel(f"{score:.1f}%")
        score_lbl.setStyleSheet(
            f"color: {color}; font-size:13px; font-weight:bold; background:transparent;"
        )
        score_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        lo.addLayout(info, 1)
        lo.addWidget(score_lbl)
        return card

    # ------------------------------------------------------------------
    # Program Risk Heatmap — clickable rows
    # ------------------------------------------------------------------

    def _update_program_heatmap(self, predictions: list) -> None:
        program_counts: dict[str, dict] = {}
        for pred in predictions:
            prog = pred.get("program", "—") or "—"
            if prog not in program_counts:
                program_counts[prog] = {
                    "high":0, "moderate":0, "low":0, "total":0
                }
            cat = pred.get("category", "low_risk")
            if cat == "high_risk":
                program_counts[prog]["high"] += 1
            elif cat == "moderate_risk":
                program_counts[prog]["moderate"] += 1
            else:
                program_counts[prog]["low"] += 1
            program_counts[prog]["total"] += 1

        sorted_programs = sorted(
            program_counts.items(),
            key=lambda x: x[1]["high"] + x[1]["moderate"],
            reverse=True,
        )[:10]

        while self._heatmap_grid.count():
            item = self._heatmap_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not sorted_programs:
            placeholder = QLabel("No program data available.")
            placeholder.setObjectName("analyticsText")
            self._heatmap_grid.addWidget(placeholder, 0, 0, 1, 5)
            return

        # Header row
        for col, text in enumerate(["Program", "High", "Mod", "Low", "Total"]):
            lbl = QLabel(text)
            lbl.setStyleSheet(
                "color: rgba(255,255,255,0.30); font-size:10px; "
                "font-weight:bold; background:transparent; padding:2px 4px;"
            )
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._heatmap_grid.addWidget(lbl, 0, col)

        for row_idx, (prog, counts) in enumerate(sorted_programs, 1):
            total = max(counts["total"], 1)

            # Clickable program name
            prog_btn = QPushButton(prog)
            prog_btn.setFlat(True)
            prog_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            prog_btn.setToolTip(f"Click to filter alerts to {prog}")
            prog_btn.setStyleSheet("""
                QPushButton {
                    color: rgba(255,255,255,0.75);
                    font-size: 11px; text-align: left;
                    background: transparent; border: none;
                    padding: 3px 4px;
                }
                QPushButton:hover {
                    color: #4f8cff;
                    background: rgba(79,140,255,0.06);
                    border-radius: 4px;
                }
            """)
            prog_btn.clicked.connect(
                lambda _, p=prog: self._on_heatmap_row_clicked(p)
            )
            self._heatmap_grid.addWidget(prog_btn, row_idx, 0)

            # High cell
            high_pct = counts["high"] / total
            opacity  = max(0.10, min(0.85, high_pct * 2.5))
            high_lbl = QLabel(str(counts["high"]))
            high_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            high_lbl.setStyleSheet(
                f"background:rgba(255,91,91,{opacity:.2f}); "
                f"color:{'#ff5b5b' if counts['high'] > 0 else 'rgba(255,255,255,0.2)'}; "
                "border-radius:4px; font-size:11px; font-weight:600; padding:3px 6px;"
            )
            high_lbl.setToolTip(
                f"{counts['high']} high-risk students in {prog} "
                f"({high_pct*100:.1f}% of program)"
            )
            self._heatmap_grid.addWidget(high_lbl, row_idx, 1)

            # Moderate cell
            mod_pct = counts["moderate"] / total
            opacity_m = max(0.08, min(0.70, mod_pct * 2.5))
            mod_lbl = QLabel(str(counts["moderate"]))
            mod_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            mod_lbl.setStyleSheet(
                f"background:rgba(245,179,53,{opacity_m:.2f}); "
                f"color:{'#f5b335' if counts['moderate'] > 0 else 'rgba(255,255,255,0.2)'}; "
                "border-radius:4px; font-size:11px; font-weight:600; padding:3px 6px;"
            )
            mod_lbl.setToolTip(
                f"{counts['moderate']} moderate-risk students in {prog}"
            )
            self._heatmap_grid.addWidget(mod_lbl, row_idx, 2)

            # Low cell
            low_lbl = QLabel(str(counts["low"]))
            low_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            low_lbl.setStyleSheet(
                "color:rgba(52,211,153,0.60); font-size:11px; padding:3px 6px;"
            )
            self._heatmap_grid.addWidget(low_lbl, row_idx, 3)

            # Total cell with at-risk %
            at_risk_pct = (counts["high"] + counts["moderate"]) / total * 100
            total_lbl = QLabel(f"{counts['total']}  ({at_risk_pct:.0f}%↑)")
            total_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            total_lbl.setStyleSheet(
                "color:rgba(255,255,255,0.40); font-size:11px; padding:3px 6px;"
            )
            total_lbl.setToolTip(
                f"{at_risk_pct:.1f}% of {prog} students are at risk"
            )
            self._heatmap_grid.addWidget(total_lbl, row_idx, 4)

    # ------------------------------------------------------------------
    # Risk Trend
    # ------------------------------------------------------------------

    def _load_trend_from_db(self) -> None:
        conn = DataStore.get().db_conn
        if not conn:
            self._show_trend_placeholder(
                "Connect to a database to view trend data."
            )
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        COALESCE(t.term_label,
                                 t.academic_year || ' Sem ' || t.semester::text)
                                                                AS term_label,
                        t.academic_year, t.semester,
                        COUNT(*) FILTER (
                            WHERE rl.risk_label ILIKE '%%high%%'
                        )                                       AS high_risk,
                        COUNT(*) FILTER (
                            WHERE rl.risk_label ILIKE '%%moderate%%'
                               OR rl.risk_label ILIKE '%%medium%%'
                        )                                       AS moderate_risk,
                        COUNT(*)                                AS total
                    FROM   public.fact_student_academic_risk fsr
                    JOIN   public.dim_academic_term  t
                           ON t.term_key      = fsr.term_key
                    LEFT JOIN public.dim_risk_level rl
                           ON rl.risk_level_id = fsr.risk_level_id
                    GROUP  BY t.term_key, t.term_label,
                              t.academic_year, t.semester
                    ORDER  BY t.term_key
                """)
                rows = cur.fetchall()
        except Exception as exc:
            print(f"[DashboardPage] Trend query failed: {exc}")
            self._show_trend_placeholder("Trend data not yet available.")
            return

        if not rows:
            self._show_trend_placeholder(
                "No historical data yet.\nRun predictions across multiple "
                "semesters to build the trend line."
            )
            return
        self._build_trend_chart(rows)

    def _build_trend_chart(self, rows: list) -> None:
        labels    = []
        high_vals = []
        mod_vals  = []

        for term_label, ay, sem, high, mod, total in rows:
            labels.append(term_label or f"{ay} S{sem}")
            high_vals.append(int(high or 0))
            mod_vals.append(int(mod or 0))

        while self._trend_chart_host.layout().count():
            item = self._trend_chart_host.layout().takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        high_series = QSplineSeries()
        high_series.setName("High Risk")
        high_series.setColor(QColor("#ff5b5b"))

        mod_series = QSplineSeries()
        mod_series.setName("Moderate Risk")
        mod_series.setColor(QColor("#f5b335"))

        for i, (h, m) in enumerate(zip(high_vals, mod_vals)):
            high_series.append(i, h)
            mod_series.append(i, m)

        # Hover tooltips on data points
        high_series.hovered.connect(
            lambda pt, state: self._on_trend_hovered(
                pt, state, labels, high_vals, "High Risk"
            )
        )
        mod_series.hovered.connect(
            lambda pt, state: self._on_trend_hovered(
                pt, state, labels, mod_vals, "Moderate Risk"
            )
        )

        chart = QChart()
        chart.addSeries(high_series)
        chart.addSeries(mod_series)
        chart.setBackgroundVisible(False)
        chart.setPlotAreaBackgroundVisible(False)
        chart.setMargins(QMargins(0, 0, 0, 0))
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignTop)
        chart.legend().setLabelColor(QColor("#c9d0e0"))
        chart.legend().setFont(QFont("Segoe UI", 9))

        ax = QBarCategoryAxis()
        ax.append(labels)
        ax.setLabelsColor(QColor("#a0aabe"))
        ax.setLabelsFont(QFont("Segoe UI", 8))
        ax.setGridLineColor(QColor(255,255,255,8))
        chart.addAxis(ax, Qt.AlignmentFlag.AlignBottom)
        high_series.attachAxis(ax)
        mod_series.attachAxis(ax)

        max_val = max(max(high_vals, default=0), max(mod_vals, default=0), 1)
        ay = QValueAxis()
        ay.setRange(0, max_val * 1.25)
        ay.setTickCount(5)
        ay.setLabelsColor(QColor("#a0aabe"))
        ay.setLabelsFont(QFont("Segoe UI", 8))
        ay.setGridLineColor(QColor(255,255,255,8))
        ay.setLabelFormat("%d")
        chart.addAxis(ay, Qt.AlignmentFlag.AlignLeft)
        high_series.attachAxis(ay)
        mod_series.attachAxis(ay)

        view = QChartView(chart)
        view.setRenderHint(QPainter.RenderHint.Antialiasing)
        view.setStyleSheet("background: transparent; border: none;")
        view.setMinimumHeight(200)
        self._trend_chart_host.layout().addWidget(view)

    def _on_trend_hovered(self, point, state: bool,
                           labels: list, vals: list, series_name: str):
        if state:
            idx = round(point.x())
            if 0 <= idx < len(labels) and idx < len(vals):
                QToolTip.showText(
                    QCursor.pos(),
                    f"<b>{labels[idx]}</b><br>"
                    f"{series_name}: <b>{vals[idx]:,}</b> students",
                )
        else:
            QToolTip.hideText()

    def _show_trend_placeholder(self, message: str) -> None:
        while self._trend_chart_host.layout().count():
            item = self._trend_chart_host.layout().takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        lbl = QLabel(message)
        lbl.setObjectName("analyticsText")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setWordWrap(True)
        self._trend_chart_host.layout().addWidget(lbl)

    # ------------------------------------------------------------------
    # Data Source Coverage
    # ------------------------------------------------------------------

    def _refresh_coverage(self) -> None:
        store = DataStore.get()
        mis   = store.get_portal("mis")
        master_rows = mis["row_count"] if mis else 0

        def _rows(p):  return p["row_count"] if p else 0
        def _ts(p):    return p.get("timestamp") if p else None

        portal_data = {
            "mis":       mis,
            "sao":       store.get_portal("sao"),
            "guidance":  store.get_portal("guidance"),
            "registrar": store.get_portal("registrar"),
        }

        for key, portal in portal_data.items():
            w    = self._coverage_cards[key]
            rows = _rows(portal)
            pct  = int(rows / max(master_rows, 1) * 100) if master_rows else (
                100 if rows > 0 else 0
            )
            pct  = min(pct, 100)

            dot_color = (
                "rgba(255,255,255,0.2)" if portal is None else
                "#34d399" if pct >= 100 else
                "#f5b335" if pct >= 75 else "#ff5b5b"
            )
            w["dot"].setStyleSheet(
                f"color:{dot_color}; font-size:9px; background:transparent;"
            )
            w["count"].setText(
                f"{rows:,} / {master_rows:,}" if master_rows else
                (f"{rows:,}" if rows else "—")
            )

            bar_color = (
                "#34d399" if pct >= 75 else
                "#f5b335" if pct >= 40 or rows > 0 else "#ff5b5b"
            )
            w["bar"].setValue(pct)
            w["bar"].setStyleSheet(f"""
                QProgressBar {{
                    background-color: rgba(255,255,255,0.08);
                    border-radius:3px; border:none;
                }}
                QProgressBar::chunk {{
                    background-color: {bar_color}; border-radius:3px;
                }}
            """)

            ts = _ts(portal)
            if rows == 0:
                status_text, status_color = "Not uploaded", "rgba(255,255,255,0.25)"
            elif pct >= 100:
                status_text, status_color = f"Complete · {ts or '—'}", "#34d399"
            else:
                status_text  = f"{pct}% · {master_rows - rows:,} missing"
                status_color = "#f5b335"

            w["status"].setText(status_text)
            w["status"].setStyleSheet(
                f"color:{status_color}; font-size:11px; background:transparent;"
            )

        ready   = store.ready_count()
        total_r = sum(_rows(portal_data[k]) for k in portal_data)
        self._coverage_summary_lbl.setText(
            f"{ready}/4 portals uploaded  ·  {total_r:,} total records"
        )
        color = (
            "#34d399" if ready == 4 else
            "#f5b335" if ready >= 2 else "rgba(255,255,255,0.4)"
        )
        self._coverage_summary_lbl.setStyleSheet(
            f"color:{color}; font-size:12px; background:transparent;"
        )

    # ------------------------------------------------------------------
    # Empty states
    # ------------------------------------------------------------------

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _show_empty_shap(self) -> None:
        self._clear_layout(self._shap_factors_layout)
        lbl = QLabel(
            "Run a prediction to see which factors drive risk\n"
            "across the student cohort."
        )
        lbl.setObjectName("analyticsText")
        lbl.setWordWrap(True)
        self._shap_factors_layout.addWidget(lbl)
        self._shap_factors_layout.addStretch()

    def _show_empty_alerts(self) -> None:
        self._clear_layout(self._alerts_content_layout)
        lbl = QLabel(
            "No alerts yet. Run a prediction to identify\n"
            "at-risk students."
        )
        lbl.setObjectName("analyticsText")
        lbl.setWordWrap(True)
        self._alerts_content_layout.addWidget(lbl)
        self._alerts_content_layout.addStretch()

    def _reset_metric_cards(self) -> None:
        for card in (self._metric_1, self._metric_2, self._metric_3):
            card.update_values(value="—", status="Pending",
                               remarks="Run prediction to update")

    def _show_empty_state(self) -> None:
        self._reset_metric_cards()
        self._risk_distribution_chart.show_empty()
        self._risk_analytics_chart.show_empty()
        self._show_empty_shap()
        self._show_empty_alerts()

    # ------------------------------------------------------------------
    # Setup UI
    # ------------------------------------------------------------------

    def setup_ui(self):
        self.setObjectName("page")
        self.overlay = LoadingOverlay(self)
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(30, 30, 30, 30)
        self.main_layout.setSpacing(20)

        # ── Header ────────────────────────────────────────────────────
        self.fixed_header_container = QFrame()
        self.fixed_header_container.setObjectName("fixedHeaderContainer")
        fixed_header_layout = QVBoxLayout()
        fixed_header_layout.setContentsMargins(20, 20, 20, 20)
        fixed_header_layout.setSpacing(14)

        header_row = QHBoxLayout()
        header_row.setSpacing(15)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        header = QLabel("DASHBOARD")
        header.setObjectName("header")
        subHeader = QLabel("AI-powered student risk monitoring overview")
        subHeader.setObjectName("subHeader")
        self._last_run_lbl = QLabel("Last prediction run: Not yet run")
        self._last_run_lbl.setObjectName("subHeader")
        text_col.addWidget(header)
        text_col.addWidget(subHeader)
        text_col.addWidget(self._last_run_lbl)
        header_row.addLayout(text_col)
        header_row.addStretch()

        model_card = QFrame()
        model_card.setObjectName("modelCard")
        model_layout = QHBoxLayout()
        model_layout.setContentsMargins(20, 15, 20, 15)

        self.model_status = QLabel("Model Active")
        self.model_status.setObjectName("modelStatus")
        self.model_status.setStyleSheet(
            "#modelStatus { color:#2ecc71; font-weight:bold; font-size:12px; }"
        )
        self.opacity_effect = QGraphicsOpacityEffect(self.model_status)
        self.model_status.setGraphicsEffect(self.opacity_effect)
        anim = QPropertyAnimation(self.opacity_effect, b"opacity")
        anim.setDuration(1200)
        anim.setStartValue(1.0)
        anim.setKeyValueAt(0.5, 0.3)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        anim.setLoopCount(-1)
        anim.start()
        self.status_animation = anim

        self._model_semester_lbl = QLabel(SystemConfig.term_label())
        self._model_semester_lbl.setObjectName("modelSemester")

        run_button = QPushButton("Run Prediction")
        run_button.setObjectName("runButton")
        run_button.setCursor(Qt.CursorShape.PointingHandCursor)
        run_button.setIcon(QIcon("assets/icons/play.svg"))
        run_button.clicked.connect(self.on_run_prediction)
        run_button.setFixedWidth(120)
        # Hide for counselors — they load data via the term selector
        from services.auth_service import AuthService
        if (AuthService.current_role() or "").strip().lower() == "counselor":
            run_button.hide()

        model_layout.addWidget(self.model_status)
        model_layout.addWidget(self._model_semester_lbl)
        model_layout.addSpacing(5)
        model_layout.addWidget(run_button)
        model_card.setLayout(model_layout)
        header_row.addWidget(model_card)
        fixed_header_layout.addLayout(header_row)

        # ── Active filter banner + clear button ───────────────────────
        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)

        self._filter_banner = QLabel("")
        self._filter_banner.setStyleSheet(
            "color: #4f8cff; font-size:11px; background:rgba(79,140,255,0.08); "
            "border:1px solid rgba(79,140,255,0.20); border-radius:6px; "
            "padding:5px 10px;"
        )
        self._filter_banner.hide()

        self._filter_clear_btn = QPushButton("✕  Clear filters")
        self._filter_clear_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 6px; color: rgba(255,255,255,0.55);
                font-size: 11px; padding: 4px 12px;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.09);
                color: #e8eaf0;
            }
        """)
        self._filter_clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._filter_clear_btn.clicked.connect(self._clear_all_filters)
        self._filter_clear_btn.hide()

        filter_row.addWidget(self._filter_banner)
        filter_row.addStretch()
        filter_row.addWidget(self._filter_clear_btn)
        fixed_header_layout.addLayout(filter_row)

        self.fixed_header_container.setLayout(fixed_header_layout)
        self.main_layout.addWidget(self.fixed_header_container)

        # ── Activity log ──────────────────────────────────────────────
        self._activity_log = ActivityLogPanel()
        self.main_layout.addWidget(self._activity_log)

        # ── Metric cards ──────────────────────────────────────────────
        self.main_layout.addLayout(self._build_metric_cards())

        # ── Row 1: Donut + Bar ────────────────────────────────────────
        row1 = QHBoxLayout()
        row1.setSpacing(20)

        dist_panel = QFrame()
        dist_panel.setObjectName("analyticsPanel")
        dist_layout = QVBoxLayout(dist_panel)
        dist_layout.setContentsMargins(20, 16, 20, 16)
        dist_layout.setSpacing(8)

        dist_header = QHBoxLayout()
        dist_title = QLabel("Risk Distribution")
        dist_title.setObjectName("cardTitle")
        dist_hint  = QLabel("Click a slice to filter")
        dist_hint.setStyleSheet(
            "color:rgba(255,255,255,0.25); font-size:10px; background:transparent;"
        )
        dist_header.addWidget(dist_title)
        dist_header.addStretch()
        dist_header.addWidget(dist_hint)
        dist_layout.addLayout(dist_header)

        self._risk_distribution_chart = RiskDistributionChart()
        self._risk_distribution_chart.risk_filter_changed.connect(
            self._on_risk_filter_changed
        )
        dist_layout.addWidget(self._risk_distribution_chart)

        bar_panel = QFrame()
        bar_panel.setObjectName("analyticsPanel")
        bar_layout = QVBoxLayout(bar_panel)
        bar_layout.setContentsMargins(20, 16, 20, 16)
        bar_layout.setSpacing(8)

        bar_header = QHBoxLayout()
        bar_title = QLabel("Risk by College")
        bar_title.setObjectName("cardTitle")
        bar_hint  = QLabel("Click a bar to filter")
        bar_hint.setStyleSheet(
            "color:rgba(255,255,255,0.25); font-size:10px; background:transparent;"
        )
        bar_header.addWidget(bar_title)
        bar_header.addStretch()
        bar_header.addWidget(bar_hint)
        bar_layout.addLayout(bar_header)

        self._risk_analytics_chart = RiskAnalyticsChart()
        self._risk_analytics_chart.college_clicked.connect(
            self._on_college_clicked
        )
        bar_layout.addWidget(self._risk_analytics_chart)

        row1.addWidget(dist_panel, 1)
        row1.addWidget(bar_panel, 2)
        self.main_layout.addLayout(row1)

        # ── Row 2: SHAP + Alerts ──────────────────────────────────────
        row2 = QHBoxLayout()
        row2.setSpacing(20)

        shap_panel = QFrame()
        shap_panel.setObjectName("analyticsPanel")
        shap_layout = QVBoxLayout(shap_panel)
        shap_layout.setContentsMargins(20, 16, 20, 16)
        shap_layout.setSpacing(10)

        shap_header = QHBoxLayout()
        shap_title = QLabel("Risk Drivers — This Cohort")
        shap_title.setObjectName("cardTitle")
        shap_hint  = QLabel("Click a factor to filter")
        shap_hint.setStyleSheet(
            "color:rgba(255,255,255,0.25); font-size:10px; background:transparent;"
        )
        shap_header.addWidget(shap_title)
        shap_header.addStretch()
        shap_header.addWidget(shap_hint)
        shap_layout.addLayout(shap_header)

        shap_sub = QLabel(
            "Weighted average contribution across all scored students. "
            "Click any row to filter the alerts panel."
        )
        shap_sub.setWordWrap(True)
        shap_sub.setObjectName("analyticsText")
        shap_layout.addWidget(shap_sub)

        self._shap_factors_layout = QVBoxLayout()
        self._shap_factors_layout.setSpacing(4)
        self._show_empty_shap()
        shap_layout.addLayout(self._shap_factors_layout)

        alerts_panel = QFrame()
        alerts_panel.setObjectName("analyticsPanel")
        alerts_layout = QVBoxLayout(alerts_panel)
        alerts_layout.setContentsMargins(20, 16, 20, 16)
        alerts_layout.setSpacing(10)

        alerts_header = QHBoxLayout()
        alerts_title = QLabel("At-Risk Alerts")
        alerts_title.setObjectName("cardTitle")
        alerts_count = QLabel("")
        alerts_count.setStyleSheet(
            "color:rgba(255,255,255,0.30); font-size:11px; background:transparent;"
        )
        alerts_header.addWidget(alerts_title)
        alerts_header.addStretch()
        alerts_header.addWidget(alerts_count)
        alerts_layout.addLayout(alerts_header)

        alerts_sub = QLabel(
            "Top at-risk students. Updates as you filter the charts."
        )
        alerts_sub.setWordWrap(True)
        alerts_sub.setObjectName("analyticsText")
        alerts_layout.addWidget(alerts_sub)

        self._alerts_content_layout = QVBoxLayout()
        self._alerts_content_layout.setSpacing(6)
        self._show_empty_alerts()
        alerts_layout.addLayout(self._alerts_content_layout)

        row2.addWidget(shap_panel, 1)
        row2.addWidget(alerts_panel, 1)
        self.main_layout.addLayout(row2)

        # ── Row 3: Trend + Heatmap ────────────────────────────────────
        row3 = QHBoxLayout()
        row3.setSpacing(20)

        trend_panel = QFrame()
        trend_panel.setObjectName("analyticsPanel")
        trend_panel.setMinimumHeight(280)
        trend_layout = QVBoxLayout(trend_panel)
        trend_layout.setContentsMargins(20, 16, 20, 16)
        trend_layout.setSpacing(10)

        trend_header = QHBoxLayout()
        trend_title = QLabel("Risk Trend")
        trend_title.setObjectName("cardTitle")
        trend_sub   = QLabel("Semester-over-semester at-risk count")
        trend_sub.setObjectName("analyticsText")
        trend_header.addWidget(trend_title)
        trend_header.addStretch()
        trend_header.addWidget(trend_sub)
        trend_layout.addLayout(trend_header)

        self._trend_chart_host = QFrame()
        host_lo = QVBoxLayout(self._trend_chart_host)
        host_lo.setContentsMargins(0, 0, 0, 0)
        self._show_trend_placeholder(
            "No historical data yet.\n"
            "Run predictions across multiple semesters to build the trend line."
        )
        trend_layout.addWidget(self._trend_chart_host, 1)

        heatmap_panel = QFrame()
        heatmap_panel.setObjectName("analyticsPanel")
        heatmap_panel.setMinimumHeight(280)
        heatmap_layout = QVBoxLayout(heatmap_panel)
        heatmap_layout.setContentsMargins(20, 16, 20, 16)
        heatmap_layout.setSpacing(10)

        heatmap_header = QHBoxLayout()
        heatmap_title = QLabel("Program Risk Breakdown")
        heatmap_title.setObjectName("cardTitle")
        heatmap_hint  = QLabel("Click a program to filter alerts")
        heatmap_hint.setStyleSheet(
            "color:rgba(255,255,255,0.25); font-size:10px; background:transparent;"
        )
        heatmap_header.addWidget(heatmap_title)
        heatmap_header.addStretch()
        heatmap_header.addWidget(heatmap_hint)
        heatmap_layout.addLayout(heatmap_header)

        heatmap_scroll = QScrollArea()
        heatmap_scroll.setWidgetResizable(True)
        heatmap_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        heatmap_scroll.setFrameShape(QFrame.Shape.NoFrame)
        heatmap_scroll.setStyleSheet(
            "QScrollArea { background:transparent; border:none; }"
        )

        heatmap_host = QWidget()
        heatmap_host.setStyleSheet("background:transparent;")
        heatmap_host_lo = QVBoxLayout(heatmap_host)
        heatmap_host_lo.setContentsMargins(0, 0, 0, 0)

        self._heatmap_grid = QGridLayout()
        self._heatmap_grid.setSpacing(6)
        self._heatmap_grid.setColumnStretch(0, 3)
        self._heatmap_grid.setColumnStretch(1, 1)
        self._heatmap_grid.setColumnStretch(2, 1)
        self._heatmap_grid.setColumnStretch(3, 1)
        self._heatmap_grid.setColumnStretch(4, 1)

        ph = QLabel("Run a prediction to populate program breakdown.")
        ph.setObjectName("analyticsText")
        ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._heatmap_grid.addWidget(ph, 0, 0, 1, 5)

        heatmap_host_lo.addLayout(self._heatmap_grid)
        heatmap_host_lo.addStretch()
        heatmap_scroll.setWidget(heatmap_host)
        heatmap_layout.addWidget(heatmap_scroll, 1)

        row3.addWidget(trend_panel, 1)
        row3.addWidget(heatmap_panel, 1)
        self.main_layout.addLayout(row3)

        # ── Data Source Coverage ──────────────────────────────────────
        coverage_card = QFrame()
        coverage_card.setObjectName("coverageCard")
        coverage_outer = QVBoxLayout(coverage_card)
        coverage_outer.setContentsMargins(25, 20, 25, 20)
        coverage_outer.setSpacing(14)

        cov_title_row = QHBoxLayout()
        cov_title = QLabel("DATA SOURCE COVERAGE")
        cov_title.setObjectName("coverageTitle")
        cov_title_row.addWidget(cov_title)
        cov_title_row.addStretch()
        self._coverage_summary_lbl = QLabel("0/4 portals uploaded  ·  0 total records")
        self._coverage_summary_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.4); font-size:12px; background:transparent;"
        )
        cov_title_row.addWidget(self._coverage_summary_lbl)
        coverage_outer.addLayout(cov_title_row)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(14)
        portal_meta = [
            ("mis",      "MIS",      "Academic Records"),
            ("sao",      "SAO",      "Student Affairs"),
            ("guidance", "Guidance", "Psych & Counseling"),
            ("registrar","Registrar","Biographical Data"),
        ]
        self._coverage_cards: dict = {}

        for key, title, subtitle in portal_meta:
            pcard = QFrame()
            pcard.setObjectName("coveragePortalCard")
            pcard.setStyleSheet("""
                QFrame#coveragePortalCard {
                    background-color: rgba(255,255,255,0.03);
                    border: 1px solid rgba(255,255,255,0.08);
                    border-radius: 10px;
                }
            """)
            pcard_lo = QVBoxLayout(pcard)
            pcard_lo.setContentsMargins(16, 14, 16, 14)
            pcard_lo.setSpacing(7)

            top = QHBoxLayout()
            top.setSpacing(8)
            dot_lbl = QLabel("●")
            dot_lbl.setFixedWidth(14)
            dot_lbl.setStyleSheet(
                "color:rgba(255,255,255,0.2); font-size:9px; background:transparent;"
            )
            title_lbl = QLabel(title)
            title_lbl.setStyleSheet(
                "color:#e8eaf0; font-size:13px; font-weight:bold; background:transparent;"
            )
            count_lbl = QLabel("—")
            count_lbl.setStyleSheet(
                "color:rgba(255,255,255,0.4); font-size:11px; background:transparent;"
            )
            count_lbl.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            top.addWidget(dot_lbl)
            top.addWidget(title_lbl, 1)
            top.addWidget(count_lbl)
            pcard_lo.addLayout(top)

            sub_lbl = QLabel(subtitle)
            sub_lbl.setStyleSheet(
                "color:rgba(255,255,255,0.3); font-size:11px; background:transparent;"
            )
            pcard_lo.addWidget(sub_lbl)

            bar = QProgressBar()
            bar.setValue(0)
            bar.setTextVisible(False)
            bar.setFixedHeight(5)
            bar.setStyleSheet("""
                QProgressBar { background-color:rgba(255,255,255,0.08);
                    border-radius:3px; border:none; }
                QProgressBar::chunk { background-color:rgba(255,255,255,0.2);
                    border-radius:3px; }
            """)
            pcard_lo.addWidget(bar)

            status_lbl = QLabel("Not uploaded")
            status_lbl.setWordWrap(True)
            status_lbl.setStyleSheet(
                "color:rgba(255,255,255,0.25); font-size:11px; background:transparent;"
            )
            pcard_lo.addWidget(status_lbl)

            cards_row.addWidget(pcard, 1)
            self._coverage_cards[key] = {
                "dot":    dot_lbl,
                "count":  count_lbl,
                "bar":    bar,
                "status": status_lbl,
            }

        coverage_outer.addLayout(cards_row)
        self.main_layout.addWidget(coverage_card)

        self.setLayout(self.main_layout)
        self.init_prediction()
        self._refresh_prediction_status()
        self._refresh_coverage()

        existing = DataStore.get().predictions
        if existing and existing.success:
            self._apply_predictions(existing)
        self._load_trend_from_db()

    def _refresh_prediction_status(self):
        last_run = DataStore.get().last_prediction_run
        self._last_run_lbl.setText(
            f"Last prediction run: {last_run}" if last_run
            else "Last prediction run: Not yet run"
        )

    def _build_metric_cards(self) -> QHBoxLayout:
        lo = QHBoxLayout()
        self._metric_1 = MetricCard(
            "Overall Risk Score", "—", "Pending", "Run prediction to update")
        self._metric_2 = MetricCard(
            "At-Risk Students",   "—", "Pending", "Run prediction to update")
        self._metric_3 = MetricCard(
            "High-Risk Students", "—", "Pending", "Run prediction to update")
        self._metric_4 = MetricCard(
            "Intervention Rate",  "78%", "Moderate Risk",
            "↑ 10% from last semester")
        lo.addWidget(self._metric_1)
        lo.addWidget(self._metric_2)
        lo.addWidget(self._metric_3)
        lo.addWidget(self._metric_4)
        return lo

    def closeEvent(self, event):
        DataStore.get().remove_listener(self._on_store_updated)
        super().closeEvent(event)