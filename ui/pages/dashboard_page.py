"""
ui/pages/dashboard_page.py
=============================
Admin dashboard with cross-filtering, term selector, and live refresh.

The one background worker used here has been split out to keep this file
focused on the page itself:
  workers/dashboard_workers.py -> _InterventionRateLoader

No logic changes — only relocation and import wiring. Everything else
(cross-filtering, SHAP factor aggregation, alerts panel, program heatmap,
trend chart, coverage refresh, setup_ui/_build_* methods) stays here since
it's tightly coupled to this page's own widgets (self._metric_1,
self._shap_factors_layout, self._heatmap_grid, etc.) — splitting it further
would mean introducing a controller/presenter layer, which is a design
change, not a relocation.

Term selector staleness fix
-----------------------------
The AY/Semester term combo built in _build_term_bar() is only populated
once, 200ms after this page opens (via _load_on_open -> load_term_list()).
It previously had no way to learn about term changes that happened after
that point, in either direction:

  - Deletion: a term deleted elsewhere (Prediction History page's "Delete
    Term" button) kept showing in this combo — clicking "Load Term"
    against it silently returned zero rows instead of the combo ever
    showing "No data". Fixed via DataStore._notify("terms_changed"),
    broadcast by PredictionHistoryPage after a successful delete.

  - Creation: a brand-new prediction saved to the DB (e.g. the very first
    one, when this combo started out showing "No saved predictions in
    database") never appeared in the combo until the app was restarted.
    Fixed by reloading the term list on every "predictions" /
    "last_prediction_run" DataStore event, since both fire right after a
    prediction run completes and is persisted.

Both paths call self._term_svc.load_term_list() (idempotent / safe to
call repeatedly — it no-ops if a list load is already in flight), and the
deletion path additionally re-runs self._refresh_svc.refresh() so
metrics/charts clear correctly if the deleted term was the last data in
the database.
"""
from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QSizePolicy, QGraphicsOpacityEffect, QProgressBar,
    QScrollArea, QGridLayout, QToolTip, QComboBox,
)
from PyQt6.QtCore import (
    QTimer, Qt, QPropertyAnimation, QEasingCurve, QMargins,
)
from PyQt6.QtGui import (
    QColor, QFont, QPainter, QIcon, QCursor,
)
from PyQt6.QtCharts import (
    QChart, QChartView, QBarSet, QStackedBarSeries,
    QBarCategoryAxis, QValueAxis, QSplineSeries,
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
from services.dashboard_refresh_service import DashboardRefreshService
from services.dashboard_term_service import DashboardTermService
from services.prediction_engine import RISK_HIGH_LABEL, RISK_MODERATE_LABEL, RISK_LOW_LABEL

from workers.dashboard_workers import _InterventionRateLoader


# ── Dashboard Page ────────────────────────────────────────────────────────────

class DashboardPage(PredictionMixin, QWidget):
    """Admin dashboard with cross-filtering, term selector, and live refresh."""

    def __init__(self):
        super().__init__()

        # Cross-filter state
        self._active_risk_filter:    str        = ""
        self._active_college_filter: str        = ""
        self._active_shap_feature:   str        = ""
        self._shap_widgets:          list       = []
        self._all_predictions:       list[dict] = []

        # ── Services — must be created BEFORE setup_ui() ──────────────
        self._refresh_svc = DashboardRefreshService(self)
        self._refresh_svc.data_available.connect(self._apply_predictions)
        self._refresh_svc.data_cleared.connect(self._on_refresh_data_cleared)
        self._refresh_svc.error.connect(self._on_refresh_error)
        self._refresh_svc.busy_changed.connect(self._on_refresh_busy_changed)

        self._term_svc = DashboardTermService(self)
        self._term_svc.terms_loaded.connect(self._on_terms_loaded)
        self._term_svc.terms_error.connect(self._on_terms_error)
        self._term_svc.result_ready.connect(self._apply_predictions)
        self._term_svc.result_error.connect(self._on_term_result_error)
        self._term_svc.busy_changed.connect(self._on_term_busy_changed)

        self.setup_ui()

        self._interv_loader: _InterventionRateLoader | None = None

        DataStore.get().add_listener(self._on_store_updated)
        QTimer.singleShot(200, self._load_on_open)

    # ── DataStore listener ────────────────────────────────────────────────────

    def _on_store_updated(self, key: str) -> None:
        if key in ("predictions", "all"):
            result = DataStore.get().predictions
            if result and getattr(result, "success", False):
                from services.auth_service import AuthService
                role   = (AuthService.current_role() or "").strip().lower()
                source = getattr(result, "_source", "admin")
                if (role == "counselor" and source == "counselor") or \
                   (role != "counselor" and source != "counselor"):
                    self._apply_predictions(result)
            else:
                self._show_empty_state()

        if key in ("predictions", "last_prediction_run", "all"):
            self._refresh_prediction_status()
            # A new prediction run may have just been saved to the DB,
            # introducing a term that didn't exist when this combo was
            # first populated at startup. Reload so it stops showing
            # "No data" / a stale list. load_term_list() is a no-op if a
            # list load is already in flight, so this is safe to call on
            # every predictions/last_prediction_run event.
            self._term_svc.load_term_list()

        if key in ("mis", "sao", "guidance", "registrar", "all"):
            self._refresh_coverage()

        if key in ("system_config", "all"):
            self._refresh_term_label()

        if key in ("db_connected", "all"):
            self._load_intervention_rate()

        if key in ("terms_changed", "all"):
            # A term was deleted elsewhere (Prediction History page). The
            # term selector combo here is only populated once at startup,
            # so it goes stale otherwise — reload it. Also re-run the
            # existing empty/available check so metrics and charts clear
            # correctly if the deleted term was the last data in the DB,
            # instead of silently showing 0%/empty values for a term that
            # no longer exists.
            self._term_svc.load_term_list()
            self._refresh_svc.refresh()

    # ── Deferred startup ──────────────────────────────────────────────────────

    def _load_on_open(self) -> None:
        """Called 200 ms after window is shown to avoid blocking __init__."""
        existing = DataStore.get().predictions
        if existing and getattr(existing, "success", False):
            self._apply_predictions(existing)
        else:
            self._load_intervention_rate()
        self._term_svc.load_term_list()

    # ── Intervention metric ───────────────────────────────────────────────────

    def _load_intervention_rate(self, _at_risk_total: int = 0) -> None:
        self._interv_loader = _InterventionRateLoader()
        self._interv_loader.finished.connect(self._on_intervention_rate_loaded)
        self._interv_loader.error.connect(lambda _: None)
        self._interv_loader.finished.connect(self._interv_loader.deleteLater)
        self._interv_loader.error.connect(self._interv_loader.deleteLater)
        self._interv_loader.start()

    def _on_intervention_rate_loaded(self, total: int, per_student: int) -> None:
        cohort = total - per_student
        if total == 0:
            status  = "No logs yet"
            remarks = "Counselor has not logged any interventions"
        elif cohort > 0:
            status  = "Active"
            remarks = f"{per_student:,} per-student  ·  {cohort:,} cohort  (all terms)"
        else:
            status  = "Active"
            remarks = f"{per_student:,} per-student  (all terms)"

        self._metric_4.update_values(
            value=f"{total:,}", status=status, remarks=remarks)

    # ── Term label pill ───────────────────────────────────────────────────────

    def _refresh_term_label(self) -> None:
        if hasattr(self, "_model_semester_lbl"):
            self._model_semester_lbl.setText(SystemConfig.term_label())

    # ── Apply predictions → update all panels ─────────────────────────────────

    def _apply_predictions(self, result) -> None:
        s = result.summary
        self._all_predictions = result.predictions

        self._metric_1.update_values(
            value   = f"{s.avg_score}%",
            status  = RISK_HIGH_LABEL if s.avg_score >= 70 else RISK_MODERATE_LABEL,
            remarks = f"Average across {s.total:,} students",
        )
        self._metric_2.update_values(
            value   = f"{s.high_risk + s.moderate_risk:,}",
            status  = RISK_HIGH_LABEL if s.high_risk_pct >= 30 else RISK_MODERATE_LABEL,
            remarks = f"{s.high_risk_pct}% of total cohort",
        )
        self._metric_3.update_values(
            value   = f"{s.high_risk:,}",
            status  = RISK_HIGH_LABEL,
            remarks = (
                f"{round(s.high_risk / s.total * 100, 1)}% flagged this run"
                if s.total else "—"
            ),
        )

        self._load_intervention_rate()

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

        # Reset cross-filters on new data
        self._active_risk_filter    = ""
        self._active_college_filter = ""
        self._active_shap_feature   = ""
        self._update_filter_badge()

    # ── Refresh service slots ─────────────────────────────────────────────────

    def _on_refresh_busy_changed(self, is_busy: bool) -> None:
        self._refresh_btn.setEnabled(not is_busy)
        self._refresh_btn.setText("↻  Refreshing…" if is_busy else "↻  Refresh")

    def _on_refresh_data_cleared(self) -> None:
        self._show_empty_state()
        self._show_trend_placeholder(
            "No prediction data found in the database.\n"
            "Run a new prediction to populate the dashboard."
        )
        self._last_run_lbl.setText("Last prediction run: Not yet run")

    def _on_refresh_error(self, message: str) -> None:
        self._set_filter_banner(f"⚠  Refresh failed: {message}")
        QTimer.singleShot(4000, self._clear_filter_banner)

    # ── Term service slots ────────────────────────────────────────────────────

    def _on_terms_loaded(self, terms: list) -> None:
        self._term_ay_combo.blockSignals(True)
        self._term_ay_combo.clear()

        if not terms:
            self._term_ay_combo.addItem("No data")
            self._term_ay_combo.setEnabled(False)
            self._term_load_btn.setEnabled(False)
            self._term_status_lbl.setText("No saved predictions in database.")
            self._term_ay_combo.blockSignals(False)
            return

        seen = []
        for ay, _ in terms:
            if ay not in seen:
                seen.append(ay)
        self._term_ay_combo.addItems(seen)

        ay, sem = terms[0]
        self._term_ay_combo.setCurrentText(ay)
        self._term_sem_combo.setCurrentIndex(sem - 1)

        self._term_ay_combo.setEnabled(True)
        self._term_load_btn.setEnabled(True)
        self._term_status_lbl.setText(f"{len(terms)} term(s) available")
        self._term_ay_combo.blockSignals(False)

    def _on_terms_error(self, message: str) -> None:
        self._term_ay_combo.clear()
        self._term_ay_combo.addItem("Error")
        self._term_ay_combo.setEnabled(False)
        self._term_load_btn.setEnabled(False)
        self._term_status_lbl.setText(f"⚠  {message}")

    def _on_term_busy_changed(self, is_busy: bool) -> None:
        self._term_load_btn.setEnabled(not is_busy)
        self._term_load_btn.setText("Loading…" if is_busy else "Load Term")

    def _on_term_load_clicked(self) -> None:
        ay  = self._term_ay_combo.currentText().strip()
        sem = self._term_sem_combo.currentIndex() + 1
        self._term_svc.load_term_data(ay, sem)

    def _on_term_result_error(self, message: str) -> None:
        self._term_status_lbl.setText(f"⚠  Load failed: {message}")

    # ── Cross-filtering ───────────────────────────────────────────────────────

    def _on_risk_filter_changed(self, category: str) -> None:
        self._active_risk_filter    = category
        self._active_college_filter = ""
        self._update_filter_badge()
        self._apply_cross_filter()

    def _on_college_clicked(self, college: str) -> None:
        self._active_college_filter = (
            "" if self._active_college_filter == college else college
        )
        self._update_filter_badge()
        self._apply_cross_filter()

    def _on_shap_clicked(self, feature: str) -> None:
        if self._active_shap_feature == feature:
            self._active_shap_feature = ""
            for w in self._shap_widgets:
                w.deselect()
        else:
            self._active_shap_feature = feature
        self._apply_cross_filter()

    def _on_heatmap_row_clicked(self, program: str) -> None:
        preds = [p for p in self._all_predictions if p.get("program") == program]
        self._refresh_alerts_panel(preds, title=f"🎯  {program} — At-Risk Students")
        self._set_filter_banner(f"Program: {program}")

    def _apply_cross_filter(self) -> None:
        preds = self._all_predictions

        if self._active_risk_filter:
            preds = [p for p in preds
                     if p.get("category") == self._active_risk_filter]

        if self._active_college_filter:
            preds = [p for p in preds
                     if p.get("college") == self._active_college_filter]

        if self._active_shap_feature:
            def _has_feature(pred):
                for entry in pred.get("shap_factors", []):
                    if len(entry) >= 1 and entry[0] == self._active_shap_feature:
                        return True
                return False
            preds = [p for p in preds if _has_feature(p)]

        parts = []
        if self._active_risk_filter:
            label = {
                "high_risk":     RISK_HIGH_LABEL,
                "moderate_risk": RISK_MODERATE_LABEL,
                "low_risk":      RISK_LOW_LABEL,
            }.get(self._active_risk_filter, "")
            parts.append(label)
        if self._active_college_filter:
            parts.append(self._active_college_filter)
        if self._active_shap_feature:
            parts.append(f"Factor: {self._active_shap_feature.replace('_', ' ')}")

        self._refresh_alerts_panel(
            preds,
            title="  ›  ".join(parts) if parts else None,
        )

        if parts:
            self._set_filter_banner(
                f"Filtered by: {' · '.join(parts)}  ·  {len(preds):,} students"
            )
        else:
            self._clear_filter_banner()

    def _update_filter_badge(self) -> None:
        active = sum([
            bool(self._active_risk_filter),
            bool(self._active_college_filter),
            bool(self._active_shap_feature),
        ])
        if active:
            self._filter_clear_btn.setText(
                f"✕  Clear {active} filter{'s' if active > 1 else ''}")
            self._filter_clear_btn.show()
        else:
            self._filter_clear_btn.hide()

    def _clear_all_filters(self) -> None:
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

    def _set_filter_banner(self, text: str) -> None:
        self._filter_banner.setText(f"🔍  {text}")
        self._filter_banner.show()

    def _clear_filter_banner(self) -> None:
        self._filter_banner.hide()

    # ── SHAP factors ──────────────────────────────────────────────────────────

    _SHAP_COLOR_MAP = {
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

    def _shap_color(self, label: str) -> str:
        for key, color in self._SHAP_COLOR_MAP.items():
            if key.lower() in label.lower():
                return color
        return "#4f8cff"

    def _update_shap_factors(self, predictions: list) -> None:
        self._clear_layout(self._shap_factors_layout)
        self._shap_widgets.clear()

        factor_data: dict = {}
        for pred in predictions:
            for entry in pred.get("shap_factors", []):
                if len(entry) == 4:
                    feature_name, human_label, _fmt, pct = entry
                elif len(entry) == 2:
                    feature_name, pct = entry
                    human_label = feature_name.replace("_", " ").title()
                else:
                    continue
                if human_label not in factor_data:
                    factor_data[human_label] = {"pcts": [], "feature_name": feature_name}
                factor_data[human_label]["pcts"].append(pct)

        avg_factors = sorted(
            [
                (label, round(sum(d["pcts"]) / len(d["pcts"]), 1), d["feature_name"])
                for label, d in factor_data.items()
            ],
            key=lambda x: x[1],
            reverse=True,
        )

        for label, avg_pct, feat_name in avg_factors[:8]:
            w = ShapFactor(
                label_text   = label,
                percentage   = int(avg_pct),
                color        = self._shap_color(label),
                feature_name = feat_name,
            )
            w.clicked.connect(self._on_shap_clicked)
            self._shap_widgets.append(w)
            self._shap_factors_layout.addWidget(w)

        self._shap_factors_layout.addStretch()

    # ── Alerts panel ──────────────────────────────────────────────────────────

    def _refresh_alerts_panel(self, predictions: list,
                               title: str = None) -> None:
        self._clear_layout(self._alerts_content_layout)

        if title:
            t = QLabel(title)
            t.setStyleSheet(
                "color:#4f8cff; font-size:11px; font-weight:600; "
                "background:transparent; padding-bottom:4px;"
            )
            self._alerts_content_layout.addWidget(t)

        at_risk = sorted(
            [p for p in predictions
             if p.get("category") in ("high_risk", "moderate_risk")],
            key=lambda p: p.get("score", 0),
            reverse=True,
        )

        if not at_risk:
            lbl = QLabel("No at-risk students match the current filter.")
            lbl.setStyleSheet(
                "color:rgba(255,255,255,0.35); font-size:12px; background:transparent;")
            lbl.setWordWrap(True)
            self._alerts_content_layout.addWidget(lbl)
            self._alerts_content_layout.addStretch()
            return

        for pred in at_risk[:5]:
            self._alerts_content_layout.addWidget(
                self._build_mini_alert_card(pred))

        if len(at_risk) > 5:
            more = QLabel(f"+ {len(at_risk) - 5:,} more — go to Risk Alerts page")
            more.setStyleSheet(
                "color:rgba(255,255,255,0.30); font-size:11px; "
                "background:transparent; padding-top:4px;"
            )
            self._alerts_content_layout.addWidget(more)

        self._alerts_content_layout.addStretch()

    def _build_mini_alert_card(self, pred: dict) -> QFrame:
        cat   = pred.get("category", "low_risk")
        color = {"high_risk": "#ff5b5b", "moderate_risk": "#f5b335"}.get(cat, "#34d399")
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
            "color:#e8eaf0; font-size:12px; font-weight:600; background:transparent;")

        meta = QLabel(f"{pred.get('program', '—')}  ·  {pred.get('college', '—')}")
        meta.setStyleSheet(
            "color:rgba(255,255,255,0.40); font-size:10px; background:transparent;")

        info.addWidget(name_lbl)
        info.addWidget(meta)

        score_lbl = QLabel(f"{score:.1f}%")
        score_lbl.setStyleSheet(
            f"color:{color}; font-size:13px; font-weight:bold; background:transparent;")
        score_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        lo.addLayout(info, 1)
        lo.addWidget(score_lbl)
        return card

    # ── Program heatmap ───────────────────────────────────────────────────────

    def _update_program_heatmap(self, predictions: list) -> None:
        program_counts: dict[str, dict] = {}
        for pred in predictions:
            prog = pred.get("program", "—") or "—"
            if prog not in program_counts:
                program_counts[prog] = {"high": 0, "moderate": 0, "low": 0, "total": 0}
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

        self._clear_grid(self._heatmap_grid)

        if not sorted_programs:
            ph = QLabel("No program data available.")
            ph.setObjectName("analyticsText")
            self._heatmap_grid.addWidget(ph, 0, 0, 1, 5)
            return

        for col, text in enumerate(["Program", "High", "Mod", "Low", "Total"]):
            lbl = QLabel(text)
            lbl.setStyleSheet(
                "color:rgba(255,255,255,0.30); font-size:10px; "
                "font-weight:bold; background:transparent; padding:2px 4px;"
            )
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._heatmap_grid.addWidget(lbl, 0, col)

        for row_idx, (prog, counts) in enumerate(sorted_programs, 1):
            total = max(counts["total"], 1)

            prog_btn = QPushButton(prog)
            prog_btn.setFlat(True)
            prog_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            prog_btn.setToolTip(f"Click to filter alerts to {prog}")
            prog_btn.setStyleSheet("""
                QPushButton {
                    color:rgba(255,255,255,0.75); font-size:11px;
                    text-align:left; background:transparent; border:none; padding:3px 4px;
                }
                QPushButton:hover {
                    color:#4f8cff; background:rgba(79,140,255,0.06); border-radius:4px;
                }
            """)
            prog_btn.clicked.connect(
                lambda _, p=prog: self._on_heatmap_row_clicked(p))
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
                f"{counts['high']} high-risk in {prog} ({high_pct * 100:.1f}%)")
            self._heatmap_grid.addWidget(high_lbl, row_idx, 1)

            # Moderate cell
            mod_pct   = counts["moderate"] / total
            opacity_m = max(0.08, min(0.70, mod_pct * 2.5))
            mod_lbl   = QLabel(str(counts["moderate"]))
            mod_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            mod_lbl.setStyleSheet(
                f"background:rgba(245,179,53,{opacity_m:.2f}); "
                f"color:{'#f5b335' if counts['moderate'] > 0 else 'rgba(255,255,255,0.2)'}; "
                "border-radius:4px; font-size:11px; font-weight:600; padding:3px 6px;"
            )
            mod_lbl.setToolTip(f"{counts['moderate']} moderate-risk in {prog}")
            self._heatmap_grid.addWidget(mod_lbl, row_idx, 2)

            # Low cell
            low_lbl = QLabel(str(counts["low"]))
            low_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            low_lbl.setStyleSheet(
                "color:rgba(52,211,153,0.60); font-size:11px; padding:3px 6px;")
            self._heatmap_grid.addWidget(low_lbl, row_idx, 3)

            # Total cell
            at_risk_pct = (counts["high"] + counts["moderate"]) / total * 100
            total_lbl   = QLabel(f"{counts['total']}  ({at_risk_pct:.0f}%↑)")
            total_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            total_lbl.setStyleSheet(
                "color:rgba(255,255,255,0.40); font-size:11px; padding:3px 6px;")
            total_lbl.setToolTip(
                f"{at_risk_pct:.1f}% of {prog} students are at risk")
            self._heatmap_grid.addWidget(total_lbl, row_idx, 4)

    # ── Risk trend chart ──────────────────────────────────────────────────────

    def _load_trend_from_db(self) -> None:
        conn = DataStore.get().db_conn
        if not conn:
            self._show_trend_placeholder("Connect to a database to view trend data.")
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
                    JOIN   public.dim_academic_term t
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
                "No historical data yet.\n"
                "Run predictions across multiple semesters to build the trend line."
            )
            return
        self._build_trend_chart(rows)

    def _build_trend_chart(self, rows: list) -> None:
        labels, high_vals, mod_vals = [], [], []
        for term_label, ay, sem, high, mod, _total in rows:
            labels.append(term_label or f"{ay} S{sem}")
            high_vals.append(int(high or 0))
            mod_vals.append(int(mod or 0))

        self._clear_host(self._trend_chart_host)

        high_series = QSplineSeries()
        high_series.setName(RISK_HIGH_LABEL)
        high_series.setColor(QColor("#ff5b5b"))

        mod_series = QSplineSeries()
        mod_series.setName(RISK_MODERATE_LABEL)
        mod_series.setColor(QColor("#f5b335"))

        for i, (h, m) in enumerate(zip(high_vals, mod_vals)):
            high_series.append(i, h)
            mod_series.append(i, m)

        high_series.hovered.connect(
            lambda pt, state, lbl=labels, vals=high_vals:
            self._on_trend_hovered(pt, state, lbl, vals, RISK_HIGH_LABEL)
        )
        mod_series.hovered.connect(
            lambda pt, state, lbl=labels, vals=mod_vals:
            self._on_trend_hovered(pt, state, lbl, vals, RISK_MODERATE_LABEL)
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
        ax.setGridLineColor(QColor(255, 255, 255, 8))
        chart.addAxis(ax, Qt.AlignmentFlag.AlignBottom)
        high_series.attachAxis(ax)
        mod_series.attachAxis(ax)

        max_val = max(max(high_vals, default=0), max(mod_vals, default=0), 1)
        ay_axis = QValueAxis()
        ay_axis.setRange(0, max_val * 1.25)
        ay_axis.setTickCount(5)
        ay_axis.setLabelsColor(QColor("#a0aabe"))
        ay_axis.setLabelsFont(QFont("Segoe UI", 8))
        ay_axis.setGridLineColor(QColor(255, 255, 255, 8))
        ay_axis.setLabelFormat("%d")
        chart.addAxis(ay_axis, Qt.AlignmentFlag.AlignLeft)
        high_series.attachAxis(ay_axis)
        mod_series.attachAxis(ay_axis)

        view = QChartView(chart)
        view.setRenderHint(QPainter.RenderHint.Antialiasing)
        view.setStyleSheet("background:transparent; border:none;")
        view.setMinimumHeight(200)
        self._trend_chart_host.layout().addWidget(view)

    def _on_trend_hovered(self, point, state: bool,
                           labels: list, vals: list, series_name: str) -> None:
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
        self._clear_host(self._trend_chart_host)
        lbl = QLabel(message)
        lbl.setObjectName("analyticsText")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setWordWrap(True)
        self._trend_chart_host.layout().addWidget(lbl)

    # ── Data Source Coverage ──────────────────────────────────────────────────

    def _refresh_coverage(self) -> None:
        store = DataStore.get()
        mis   = store.get_portal("mis")
        master_rows = mis["row_count"] if mis else 0

        def _rows(p): return p["row_count"] if p else 0
        def _ts(p):   return p.get("timestamp") if p else None

        portal_data = {
            "mis":       mis,
            "sao":       store.get_portal("sao"),
            "guidance":  store.get_portal("guidance"),
            "registrar": store.get_portal("registrar"),
        }

        for key, portal in portal_data.items():
            w    = self._coverage_cards[key]
            rows = _rows(portal)
            pct  = min(
                int(rows / max(master_rows, 1) * 100) if master_rows else
                (100 if rows > 0 else 0),
                100,
            )

            dot_color = (
                "rgba(255,255,255,0.2)" if portal is None else
                "#34d399" if pct >= 100 else
                "#f5b335" if pct >= 75 else "#ff5b5b"
            )
            w["dot"].setStyleSheet(
                f"color:{dot_color}; font-size:9px; background:transparent;")
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
                    background-color:rgba(255,255,255,0.08);
                    border-radius:3px; border:none;
                }}
                QProgressBar::chunk {{
                    background-color:{bar_color}; border-radius:3px;
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
                f"color:{status_color}; font-size:11px; background:transparent;")

        ready   = store.ready_count()
        total_r = sum(_rows(portal_data[k]) for k in portal_data)
        color   = (
            "#34d399" if ready == 4 else
            "#f5b335" if ready >= 2 else "rgba(255,255,255,0.4)"
        )
        self._coverage_summary_lbl.setText(
            f"{ready}/4 portals uploaded  ·  {total_r:,} total records")
        self._coverage_summary_lbl.setStyleSheet(
            f"color:{color}; font-size:12px; background:transparent;")

    # ── Empty states ──────────────────────────────────────────────────────────

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
        lbl = QLabel("No alerts yet. Run a prediction to identify\nat-risk students.")
        lbl.setObjectName("analyticsText")
        lbl.setWordWrap(True)
        self._alerts_content_layout.addWidget(lbl)
        self._alerts_content_layout.addStretch()

    def _reset_metric_cards(self) -> None:
        self._metric_4.update_values(
            value="—", status="No data", remarks="Load predictions first")
        for card in (self._metric_1, self._metric_2, self._metric_3):
            card.update_values(
                value="—", status="Pending", remarks="Run prediction to update")

    def _show_empty_state(self) -> None:
        self._reset_metric_cards()
        self._risk_distribution_chart.show_empty()
        self._risk_analytics_chart.show_empty()
        self._show_empty_shap()
        self._show_empty_alerts()
        # ── also clear heatmap and trend ──────────────────────────────
        self._clear_grid(self._heatmap_grid)
        ph = QLabel("Run a prediction to populate program breakdown.")
        ph.setObjectName("analyticsText")
        ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._heatmap_grid.addWidget(ph, 0, 0, 1, 5)
        self._show_trend_placeholder(
            "No historical data yet.\n"
            "Run predictions across multiple semesters to build the trend line."
        )

    # ── Layout helpers ────────────────────────────────────────────────────────

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _clear_host(self, host: QFrame) -> None:
        """Clear a QFrame that acts as a chart host."""
        while host.layout().count():
            item = host.layout().takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _clear_grid(self, grid: QGridLayout) -> None:
        while grid.count():
            item = grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ── Prediction status ─────────────────────────────────────────────────────

    def _refresh_prediction_status(self) -> None:
        last_run = DataStore.get().last_prediction_run
        self._last_run_lbl.setText(
            f"Last prediction run: {last_run}" if last_run
            else "Last prediction run: Not yet run"
        )

    # =========================================================================
    # Setup UI
    # =========================================================================

    def setup_ui(self) -> None:
        self.setObjectName("page")
        self.overlay    = LoadingOverlay(self)
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(30, 30, 30, 30)
        self.main_layout.setSpacing(20)

        self.main_layout.addWidget(self._build_header())
        self.main_layout.addWidget(self._activity_log_widget())
        self.main_layout.addLayout(self._build_metric_cards())
        self.main_layout.addLayout(self._build_row1())
        self.main_layout.addLayout(self._build_row2())
        self.main_layout.addLayout(self._build_row3())
        self.main_layout.addWidget(self._build_coverage_card())

        self.setLayout(self.main_layout)
        self.init_prediction()
        self._refresh_prediction_status()
        self._refresh_coverage()

        existing = DataStore.get().predictions
        if existing and existing.success:
            self._apply_predictions(existing)
        self._load_trend_from_db()

    # ── Header ────────────────────────────────────────────────────────────────

    def _build_header(self) -> QFrame:
        container = QFrame()
        container.setObjectName("fixedHeaderContainer")
        self.fixed_header_container = container

        outer = QVBoxLayout(container)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(10)

        # ── Row 1: title + model card ──────────────────────────────────
        header_row = QHBoxLayout()
        header_row.setSpacing(15)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)

        lbl_header = QLabel("DASHBOARD")
        lbl_header.setObjectName("header")

        lbl_sub = QLabel("AI-powered student risk monitoring overview")
        lbl_sub.setObjectName("subHeader")

        self._last_run_lbl = QLabel("Last prediction run: Not yet run")
        self._last_run_lbl.setObjectName("subHeader")

        text_col.addWidget(lbl_header)
        text_col.addWidget(lbl_sub)
        text_col.addWidget(self._last_run_lbl)
        header_row.addLayout(text_col)
        header_row.addStretch()
        header_row.addWidget(self._build_model_card())
        outer.addLayout(header_row)

        # ── Row 2: term selector bar ───────────────────────────────────
        outer.addWidget(self._build_term_bar())

        # ── Row 3: filter banner ───────────────────────────────────────
        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)

        self._filter_banner = QLabel("")
        self._filter_banner.setStyleSheet(
            "color:#4f8cff; font-size:11px; "
            "background:rgba(79,140,255,0.08); "
            "border:1px solid rgba(79,140,255,0.20); "
            "border-radius:6px; padding:5px 10px;"
        )
        self._filter_banner.hide()

        self._filter_clear_btn = QPushButton("✕  Clear filters")
        self._filter_clear_btn.setStyleSheet("""
            QPushButton {
                background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.12);
                border-radius:6px; color:rgba(255,255,255,0.55);
                font-size:11px; padding:4px 12px;
            }
            QPushButton:hover {
                background:rgba(255,255,255,0.09); color:#e8eaf0;
            }
        """)
        self._filter_clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._filter_clear_btn.clicked.connect(self._clear_all_filters)
        self._filter_clear_btn.hide()

        filter_row.addWidget(self._filter_banner)
        filter_row.addStretch()
        filter_row.addWidget(self._filter_clear_btn)
        outer.addLayout(filter_row)

        return container

    def _build_model_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("modelCard")
        lo = QHBoxLayout(card)
        lo.setContentsMargins(16, 12, 16, 12)
        lo.setSpacing(10)

        # Pulsing "Model Active" label
        self.model_status = QLabel("Model Active")
        self.model_status.setObjectName("modelStatus")
        self.model_status.setStyleSheet(
            "#modelStatus { color:#2ecc71; font-weight:bold; font-size:12px; }")
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

        self._refresh_btn = QPushButton("↻  Refresh")
        self._refresh_btn.setObjectName("refreshButton")
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.setToolTip(
            "Re-check the database. Clears charts if data was deleted.")
        self._refresh_btn.setFixedWidth(100)
        self._refresh_btn.setStyleSheet("""
            QPushButton#refreshButton {
                background-color:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.12);
                border-radius:6px; color:rgba(255,255,255,0.65);
                font-size:12px; padding:5px 10px;
            }
            QPushButton#refreshButton:hover {
                background-color:rgba(79,140,255,0.12);
                border-color:rgba(79,140,255,0.35); color:#4f8cff;
            }
            QPushButton#refreshButton:disabled {
                color:rgba(255,255,255,0.25);
                border-color:rgba(255,255,255,0.06);
            }
        """)
        self._refresh_btn.clicked.connect(self._refresh_svc.refresh)

        run_button = QPushButton("Run Prediction")
        run_button.setObjectName("runButton")
        run_button.setCursor(Qt.CursorShape.PointingHandCursor)
        run_button.setIcon(QIcon("assets/icons/play.svg"))
        run_button.clicked.connect(self.on_run_prediction)
        run_button.setFixedWidth(120)
        from services.auth_service import AuthService
        if (AuthService.current_role() or "").strip().lower() == "counselor":
            run_button.hide()

        lo.addWidget(self.model_status)
        lo.addWidget(self._model_semester_lbl)
        lo.addSpacing(4)
        lo.addWidget(self._refresh_btn)
        lo.addWidget(run_button)

        return card

    def _build_term_bar(self) -> QFrame:
        """
        Dedicated row for term selection — lives below the model card so it
        never forces the header wider than the screen.
        """
        bar = QFrame()
        bar.setObjectName("dashTermBar")
        bar.setStyleSheet("""
            QFrame#dashTermBar {
                background-color: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.07);
                border-radius: 8px;
            }
        """)

        lo = QHBoxLayout(bar)
        lo.setContentsMargins(16, 10, 16, 10)
        lo.setSpacing(10)

        # Label
        term_lbl = QLabel("Academic Term:")
        term_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.45); font-size:12px; background:transparent;")

        # Shared combo style
        _combo_style = """
            QComboBox#dashTermCombo {
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 6px; color: #e8eaf0;
                font-size: 12px; padding: 4px 10px; min-height: 28px;
            }
            QComboBox#dashTermCombo:hover {
                border-color: rgba(79,140,255,0.35);
            }
            QComboBox#dashTermCombo::drop-down { border: none; width: 16px; }
            QComboBox#dashTermCombo QAbstractItemView {
                background: #1a1f35;
                border: 1px solid rgba(255,255,255,0.12);
                color: #e8eaf0;
                selection-background-color: rgba(79,140,255,0.18);
            }
        """

        self._term_ay_combo = QComboBox()
        self._term_ay_combo.setObjectName("dashTermCombo")
        self._term_ay_combo.setMinimumWidth(120)
        self._term_ay_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._term_ay_combo.addItem("Load Term")
        self._term_ay_combo.setEnabled(False)
        self._term_ay_combo.setStyleSheet(_combo_style)

        self._term_sem_combo = QComboBox()
        self._term_sem_combo.setObjectName("dashTermCombo")
        self._term_sem_combo.addItems(["1st Semester", "2nd Semester"])
        self._term_sem_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._term_sem_combo.setStyleSheet(_combo_style)

        self._term_load_btn = QPushButton("Load Term")
        self._term_load_btn.setObjectName("termLoadBtn")
        self._term_load_btn.setFixedHeight(30)
        self._term_load_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._term_load_btn.setEnabled(False)
        self._term_load_btn.setStyleSheet("""
            QPushButton#termLoadBtn {
                background: rgba(79,140,255,0.15);
                border: 1px solid rgba(79,140,255,0.30);
                border-radius: 6px; color: #4f8cff;
                font-size: 12px; font-weight: 600; padding: 0 16px;
            }
            QPushButton#termLoadBtn:hover {
                background: rgba(79,140,255,0.28);
                border-color: rgba(79,140,255,0.55);
            }
            QPushButton#termLoadBtn:disabled {
                background: rgba(255,255,255,0.04);
                border-color: rgba(255,255,255,0.08);
                color: rgba(255,255,255,0.20);
            }
        """)
        self._term_load_btn.clicked.connect(self._on_term_load_clicked)

        self._term_status_lbl = QLabel("Connecting to database…")
        self._term_status_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;")

        lo.addWidget(term_lbl)
        lo.addWidget(self._term_ay_combo)
        lo.addWidget(self._term_sem_combo)
        lo.addWidget(self._term_load_btn)
        lo.addWidget(self._term_status_lbl)
        lo.addStretch()

    # ── Hide for counselors — they have their own term selector
    #    in the counselor portal header bar.
        from services.auth_service import AuthService
        if (AuthService.current_role() or "").strip().lower() == "counselor":
            bar.hide()

        return bar

    # ── Activity log ──────────────────────────────────────────────────────────

    def _activity_log_widget(self) -> QWidget:
        self._activity_log = ActivityLogPanel()
        return self._activity_log

    # ── Metric cards ──────────────────────────────────────────────────────────

    def _build_metric_cards(self) -> QHBoxLayout:
        lo = QHBoxLayout()
        self._metric_1 = MetricCard(
            "Overall Risk Score", "—", "Pending",
            "Run prediction to update", accent="#4f8cff")
        self._metric_2 = MetricCard(
            "At-Risk Students", "—", "Pending",
            "Run prediction to update", accent="#f5b335")
        self._metric_3 = MetricCard(
            "High-Risk Students", "—", "Pending",
            "Run prediction to update", accent="#ff5b5b")
        self._metric_4 = MetricCard(
            "Interventions Logged", "—", "No logs yet",
            "Counselor activity this term", accent="#34d399")
        for card in (self._metric_1, self._metric_2,
                     self._metric_3, self._metric_4):
            lo.addWidget(card)
        return lo

    # ── Row 1: Risk Distribution + Risk by College ────────────────────────────

    def _build_row1(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(20)

        # Donut chart
        dist_panel  = self._analytics_panel()
        dist_layout = dist_panel.layout()
        dist_layout.addLayout(
            self._panel_header("Risk Distribution", hint="Click a slice to filter"))

        self._risk_distribution_chart = RiskDistributionChart()
        self._risk_distribution_chart.risk_filter_changed.connect(
            self._on_risk_filter_changed)
        dist_layout.addWidget(self._risk_distribution_chart)

        # Bar chart
        bar_panel  = self._analytics_panel()
        bar_layout = bar_panel.layout()
        bar_layout.addLayout(
            self._panel_header("Risk by College", hint="Click a bar to filter"))

        self._risk_analytics_chart = RiskAnalyticsChart()
        self._risk_analytics_chart.college_clicked.connect(self._on_college_clicked)
        bar_layout.addWidget(self._risk_analytics_chart)

        row.addWidget(dist_panel, 1)
        row.addWidget(bar_panel, 2)
        return row

    # ── Row 2: SHAP factors + At-Risk Alerts ─────────────────────────────────

    def _build_row2(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(20)

        # SHAP panel
        shap_panel  = self._analytics_panel()
        shap_layout = shap_panel.layout()
        shap_layout.addLayout(
            self._panel_header("Risk Drivers — This Cohort",
                               hint="Click a factor to filter"))

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

        # Alerts panel
        alerts_panel  = self._analytics_panel()
        alerts_layout = alerts_panel.layout()
        alerts_layout.addLayout(self._panel_header("At-Risk Alerts"))

        alerts_sub = QLabel("Top at-risk students. Updates as you filter the charts.")
        alerts_sub.setWordWrap(True)
        alerts_sub.setObjectName("analyticsText")
        alerts_layout.addWidget(alerts_sub)

        self._alerts_content_layout = QVBoxLayout()
        self._alerts_content_layout.setSpacing(6)
        self._show_empty_alerts()
        alerts_layout.addLayout(self._alerts_content_layout)

        row.addWidget(shap_panel, 1)
        row.addWidget(alerts_panel, 1)
        return row

    # ── Row 3: Trend + Program Heatmap ───────────────────────────────────────

    def _build_row3(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(20)

        # Trend
        trend_panel = self._analytics_panel(min_height=280)
        trend_layout = trend_panel.layout()
        trend_layout.addLayout(
            self._panel_header(
                "Risk Trend",
                hint="Semester-over-semester at-risk count",
            ))

        self._trend_chart_host = QFrame()
        QVBoxLayout(self._trend_chart_host).setContentsMargins(0, 0, 0, 0)
        self._show_trend_placeholder(
            "No historical data yet.\n"
            "Run predictions across multiple semesters to build the trend line."
        )
        trend_layout.addWidget(self._trend_chart_host, 1)

        # Heatmap
        heatmap_panel  = self._analytics_panel(min_height=280)
        heatmap_layout = heatmap_panel.layout()
        heatmap_layout.addLayout(
            self._panel_header(
                "Program Risk Breakdown",
                hint="Click a program to filter alerts",
            ))

        heatmap_scroll = QScrollArea()
        heatmap_scroll.setWidgetResizable(True)
        heatmap_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        heatmap_scroll.setFrameShape(QFrame.Shape.NoFrame)
        heatmap_scroll.setStyleSheet(
            "QScrollArea { background:transparent; border:none; }")

        heatmap_host = QWidget()
        heatmap_host.setStyleSheet("background:transparent;")
        heatmap_host_lo = QVBoxLayout(heatmap_host)
        heatmap_host_lo.setContentsMargins(0, 0, 0, 0)

        self._heatmap_grid = QGridLayout()
        self._heatmap_grid.setSpacing(6)
        self._heatmap_grid.setColumnStretch(0, 3)
        for c in range(1, 5):
            self._heatmap_grid.setColumnStretch(c, 1)

        ph = QLabel("Run a prediction to populate program breakdown.")
        ph.setObjectName("analyticsText")
        ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._heatmap_grid.addWidget(ph, 0, 0, 1, 5)

        heatmap_host_lo.addLayout(self._heatmap_grid)
        heatmap_host_lo.addStretch()
        heatmap_scroll.setWidget(heatmap_host)
        heatmap_layout.addWidget(heatmap_scroll, 1)

        row.addWidget(trend_panel, 1)
        row.addWidget(heatmap_panel, 1)
        return row

    # ── Coverage card ─────────────────────────────────────────────────────────

    def _build_coverage_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("coverageCard")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(25, 20, 25, 20)
        outer.setSpacing(14)

        title_row = QHBoxLayout()
        cov_title = QLabel("DATA SOURCE COVERAGE")
        cov_title.setObjectName("coverageTitle")
        title_row.addWidget(cov_title)
        title_row.addStretch()

        self._coverage_summary_lbl = QLabel("0/4 portals uploaded  ·  0 total records")
        self._coverage_summary_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.4); font-size:12px; background:transparent;")
        title_row.addWidget(self._coverage_summary_lbl)
        outer.addLayout(title_row)

        portal_meta = [
            ("mis",       "MIS",       "Academic Records"),
            ("sao",       "SAO",       "Student Affairs"),
            ("guidance",  "Guidance",  "Psych & Counseling"),
            ("registrar", "Registrar", "Biographical Data"),
        ]
        self._coverage_cards: dict = {}
        cards_row = QHBoxLayout()
        cards_row.setSpacing(14)

        for key, title, subtitle in portal_meta:
            pcard = QFrame()
            pcard.setObjectName("coveragePortalCard")
            pcard.setStyleSheet("""
                QFrame#coveragePortalCard {
                    background-color:rgba(255,255,255,0.03);
                    border:1px solid rgba(255,255,255,0.08);
                    border-radius:10px;
                }
            """)
            plo = QVBoxLayout(pcard)
            plo.setContentsMargins(16, 14, 16, 14)
            plo.setSpacing(7)

            top = QHBoxLayout()
            top.setSpacing(8)

            dot = QLabel("●")
            dot.setFixedWidth(14)
            dot.setStyleSheet(
                "color:rgba(255,255,255,0.2); font-size:9px; background:transparent;")

            title_lbl = QLabel(title)
            title_lbl.setStyleSheet(
                "color:#e8eaf0; font-size:13px; font-weight:bold; background:transparent;")

            count_lbl = QLabel("—")
            count_lbl.setStyleSheet(
                "color:rgba(255,255,255,0.4); font-size:11px; background:transparent;")
            count_lbl.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            top.addWidget(dot)
            top.addWidget(title_lbl, 1)
            top.addWidget(count_lbl)
            plo.addLayout(top)

            sub_lbl = QLabel(subtitle)
            sub_lbl.setStyleSheet(
                "color:rgba(255,255,255,0.3); font-size:11px; background:transparent;")
            plo.addWidget(sub_lbl)

            bar = QProgressBar()
            bar.setValue(0)
            bar.setTextVisible(False)
            bar.setFixedHeight(5)
            bar.setStyleSheet("""
                QProgressBar {
                    background-color:rgba(255,255,255,0.08);
                    border-radius:3px; border:none;
                }
                QProgressBar::chunk {
                    background-color:rgba(255,255,255,0.2); border-radius:3px;
                }
            """)
            plo.addWidget(bar)

            status_lbl = QLabel("Not uploaded")
            status_lbl.setWordWrap(True)
            status_lbl.setStyleSheet(
                "color:rgba(255,255,255,0.25); font-size:11px; background:transparent;")
            plo.addWidget(status_lbl)

            cards_row.addWidget(pcard, 1)
            self._coverage_cards[key] = {
                "dot":    dot,
                "count":  count_lbl,
                "bar":    bar,
                "status": status_lbl,
            }

        outer.addLayout(cards_row)
        return card

    # ── UI factory helpers ────────────────────────────────────────────────────

    @staticmethod
    def _analytics_panel(min_height: int = 0) -> QFrame:
        panel = QFrame()
        panel.setObjectName("analyticsPanel")
        if min_height:
            panel.setMinimumHeight(min_height)
        lo = QVBoxLayout(panel)
        lo.setContentsMargins(20, 16, 20, 16)
        lo.setSpacing(8)
        return panel

    @staticmethod
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

    # ── Close event ───────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        # Intervention loader
        worker = getattr(self, "_interv_loader", None)
        if worker is not None:
            try:
                worker.finished.disconnect()
                worker.error.disconnect()
                if worker.isRunning():
                    worker.quit()
                    worker.wait(2000)
            except (RuntimeError, Exception):
                pass

        self._refresh_svc.cleanup()
        self._term_svc.cleanup()

        DataStore.get().remove_listener(self._on_store_updated)
        super().closeEvent(event)