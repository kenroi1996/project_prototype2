from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QProgressBar,
    QGraphicsOpacityEffect,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QScrollArea,
    QTextEdit,
    QStackedWidget,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPropertyAnimation, QEasingCurve, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen

from services.data_store import DataStore
from services.merge_engine import MergeEngine, UNIFIED_COLUMNS
from ui.mixins.prediction_mixin import PredictionMixin
from ui.components.loading_overlay import LoadingOverlay


# =====================================
# MERGE WORKER THREAD
# =====================================

class _MergeWorker(QThread):
    finished = pyqtSignal(object)
    error    = pyqtSignal(str)

    def run(self):
        try:
            store  = DataStore.get()
            result = MergeEngine.merge(store.portals)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


# =====================================
# SPINNER WIDGET (from preview dialog)
# =====================================

class _MergeSpinner(QWidget):
    def __init__(self, size=40, color="#4f8cff", parent=None):
        super().__init__(parent)
        self._angle = 0
        self._color = QColor(color)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def start(self):
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._rotate)
        self._timer.start(16)
        self.show()

    def stop(self):
        if hasattr(self, '_timer'):
            self._timer.stop()
        self.hide()

    def _rotate(self):
        self._angle = (self._angle + 8) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        margin = 6
        rect = self.rect().adjusted(margin, margin, -margin, -margin)

        # Track
        track = QPen(QColor(255, 255, 255, 15))
        track.setWidth(3)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track)
        painter.drawEllipse(rect)

        # Arc
        arc = QPen(self._color)
        arc.setWidth(3)
        arc.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc)
        painter.drawArc(rect, -self._angle * 16, -120 * 16)


# =====================================
# HELPERS
# =====================================

def _divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: rgba(255,255,255,0.06); margin: 0;")
    return line


def _section_header(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("mergeSectionHeader")
    return lbl


def _stat_tile(value: str, label: str, accent: str = "rgba(255,255,255,0.75)") -> QFrame:
    tile = QFrame()
    tile.setObjectName("mergeStatTile")
    tile.setStyleSheet("""
        #mergeStatTile {
            background-color: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 10px;
        }
    """)
    layout = QVBoxLayout(tile)
    layout.setContentsMargins(16, 12, 16, 12)
    layout.setSpacing(4)

    val = QLabel(value)
    val.setStyleSheet(f"color: {accent}; font-size: 18px; font-weight: bold;")

    lbl = QLabel(label)
    lbl.setStyleSheet("color: rgba(255,255,255,0.35); font-size: 11px;")

    layout.addWidget(val)
    layout.addWidget(lbl)
    return tile


def _quality_badge(text: str, level: str = "pending") -> QLabel:
    colors = {
        "pending": ("rgba(255,255,255,0.28)", "rgba(255,255,255,0.06)", "rgba(255,255,255,0.10)"),
        "ready":   ("#34d399", "rgba(52,211,153,0.12)", "rgba(52,211,153,0.30)"),
        "warning": ("#f5b335", "rgba(245,179,53,0.12)", "rgba(245,179,53,0.30)"),
        "error":   ("#ff5b5b", "rgba(255,91,91,0.12)", "rgba(255,91,91,0.30)"),
    }
    fg, bg, border = colors.get(level, colors["pending"])

    badge = QLabel(text)
    badge.setStyleSheet(f"""
        color: {fg};
        background-color: {bg};
        border: 1px solid {border};
        border-radius: 10px;
        font-size: 11px;
        font-weight: 600;
        padding: 3px 10px;
    """)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    badge.setFixedWidth(76)
    return badge


# =====================================
# DATA MERGE PAGE
# =====================================

class DataMergePage(PredictionMixin, QWidget):
    """
    Stage 2 — Merge all four portal datasets into one unified dataset.
    """

    def __init__(self):
        super().__init__()
        self._merge_result = None
        self._accent = "#4f8cff"
        self.setup_ui()
        self.overlay = LoadingOverlay(self)

        DataStore.get().add_listener(self._on_store_updated)
        self._refresh_source_panel()

    # ------------------------------------------------------------------
    # UI Build
    # ------------------------------------------------------------------

    def setup_ui(self):
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(30, 30, 30, 30)
        self.main_layout.setSpacing(24)

        # ── Header ───────────────────────────────────────────────────
        self.main_layout.addWidget(self._build_header())

        # ── Source readiness row ──────────────────────────────────────
        self.main_layout.addWidget(self._build_source_panel())

        # ── Merge config + run ────────────────────────────────────────
        self.main_layout.addWidget(self._build_merge_config_card())

        # ── Results (hidden until merge runs) ─────────────────────────
        self._results_stack = QStackedWidget()
        self._results_stack.addWidget(QWidget())  # empty placeholder
        self._results_stack.addWidget(self._build_results_section())
        self._results_stack.setCurrentIndex(0)
        self.main_layout.addWidget(self._results_stack, 1)

        self.main_layout.addStretch()
        self.setLayout(self.main_layout)
        self.init_prediction()
        self._apply_styles()

    # ── Header ───────────────────────────────────────────────────────

    def _build_header(self) -> QFrame:
        container = QFrame()
        container.setObjectName("mergeHeaderCard")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(0)

        row = QHBoxLayout()
        row.setSpacing(20)

        title_col = QVBoxLayout()
        title_col.setSpacing(4)

        title = QLabel("Data Merge Center")
        title.setObjectName("mergeTitle")

        sub = QLabel("Unify all portal datasets into one clean dataset for model training")
        sub.setObjectName("mergeSubtitle")

        title_col.addWidget(title)
        title_col.addWidget(sub)

        row.addLayout(title_col, 1)

        # Model status pill
        model_pill = QFrame()
        model_pill.setObjectName("mergeModelPill")
        pill_layout = QHBoxLayout(model_pill)
        pill_layout.setContentsMargins(16, 10, 16, 10)
        pill_layout.setSpacing(10)

        dot = QLabel("●")
        dot.setStyleSheet("color: #34d399; font-size: 10px; background: transparent;")

        status_lbl = QLabel("Model Active")
        status_lbl.setStyleSheet("color: #e8eaf0; font-size: 12px; font-weight: 600; background: transparent;")

        # Pulsing dot effect
        opacity = QGraphicsOpacityEffect(dot)
        dot.setGraphicsEffect(opacity)
        anim = QPropertyAnimation(opacity, b"opacity")
        anim.setDuration(1500)
        anim.setStartValue(1.0)
        anim.setKeyValueAt(0.5, 0.3)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        anim.setLoopCount(-1)
        anim.start()
        self._status_anim = anim

        run_btn = QPushButton("Run Prediction")
        run_btn.setObjectName("mergeRunPredBtn")
        run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        run_btn.setFixedWidth(130)
        run_btn.clicked.connect(self.on_run_prediction)

        pill_layout.addWidget(dot)
        pill_layout.addWidget(status_lbl)
        pill_layout.addSpacing(8)
        pill_layout.addWidget(run_btn)

        row.addWidget(model_pill)
        layout.addLayout(row)
        return container

    # ── Source panel ─────────────────────────────────────────────────

    def _build_source_panel(self) -> QFrame:
        card = QFrame()
        card.setObjectName("mergeCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Title bar
        title_bar = QFrame()
        title_bar.setObjectName("mergeCardTitleBar")
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(24, 16, 24, 16)
        title_layout.setSpacing(12)

        icon = QLabel("🔗")
        icon.setStyleSheet("font-size: 16px;")

        title = QLabel("Data Sources")
        title.setObjectName("mergeCardTitle")

        self._source_prog_lbl = QLabel("0 / 4 sources ready")
        self._source_prog_lbl.setObjectName("mergeCardSubtitle")

        title_layout.addWidget(icon)
        title_layout.addWidget(title)
        title_layout.addStretch()
        title_layout.addWidget(self._source_prog_lbl)

        layout.addWidget(title_bar)
        layout.addWidget(_divider())

        # Portal rows
        rows_container = QWidget()
        rows_container.setObjectName("mergeRowsContainer")
        rows_layout = QVBoxLayout(rows_container)
        rows_layout.setContentsMargins(24, 16, 24, 16)
        rows_layout.setSpacing(8)

        self._source_rows: dict = {}
        portal_meta = {
            "mis":       ("MIS",       "Academic Records",   "#4f8cff"),
            "sao":       ("SAO",       "Student Affairs",    "#34d399"),
            "guidance":  ("Guidance",  "Psych & Counseling", "#f59e0b"),
            "registrar": ("Registrar", "Biographical Data",  "#a78bfa"),
        }

        for key, (short, desc, color) in portal_meta.items():
            row_widget = QFrame()
            row_widget.setObjectName("mergeSourceRow")
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(14)

            # Color indicator dot
            dot_frame = QFrame()
            dot_frame.setFixedSize(8, 8)
            dot_frame.setStyleSheet(f"""
                background-color: rgba(255,255,255,0.15);
                border-radius: 4px;
            """)

            name_col = QVBoxLayout()
            name_col.setSpacing(2)

            name = QLabel(f"{short}")
            name.setStyleSheet(f"color: {color}; font-size: 13px; font-weight: bold; background: transparent;")

            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet("color: rgba(255,255,255,0.35); font-size: 11px; background: transparent;")

            name_col.addWidget(name)
            name_col.addWidget(desc_lbl)

            id_col_lbl = QLabel("ID col: —")
            id_col_lbl.setObjectName("mergeSourceMeta")

            rows_lbl = QLabel("—")
            rows_lbl.setObjectName("mergeSourceMeta")
            rows_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)

            badge = _quality_badge("Pending", "pending")

            row_layout.addWidget(dot_frame)
            row_layout.addLayout(name_col, 1)
            row_layout.addWidget(id_col_lbl)
            row_layout.addWidget(rows_lbl)
            row_layout.addWidget(badge)

            rows_layout.addWidget(row_widget)

            self._source_rows[key] = {
                "dot":        dot_frame,
                "id_col_lbl": id_col_lbl,
                "rows_lbl":   rows_lbl,
                "badge":      badge,
                "color":      color,
            }

        layout.addWidget(rows_container)

        # Progress bar
        layout.addWidget(_divider())

        prog_container = QWidget()
        prog_container.setObjectName("mergeProgContainer")
        prog_layout = QHBoxLayout(prog_container)
        prog_layout.setContentsMargins(24, 14, 24, 14)
        prog_layout.setSpacing(16)

        self._source_progress = QProgressBar()
        self._source_progress.setObjectName("mergeSourceProgress")
        self._source_progress.setRange(0, 4)
        self._source_progress.setValue(0)
        self._source_progress.setTextVisible(False)
        self._source_progress.setFixedHeight(6)

        prog_layout.addWidget(self._source_progress, 1)

        # Mini spinner for loading states
        self._source_spinner = _MergeSpinner(size=20, color="#4f8cff")
        self._source_spinner.hide()
        prog_layout.addWidget(self._source_spinner)

        layout.addWidget(prog_container)

        return card

    # ── Merge config card ─────────────────────────────────────────────

    def _build_merge_config_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("mergeCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Title bar
        title_bar = QFrame()
        title_bar.setObjectName("mergeCardTitleBar")
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(24, 16, 24, 16)
        title_layout.setSpacing(12)

        icon = QLabel("⚙️")
        icon.setStyleSheet("font-size: 16px;")

        title = QLabel("Merge Configuration")
        title.setObjectName("mergeCardTitle")

        title_layout.addWidget(icon)
        title_layout.addWidget(title)
        title_layout.addStretch()

        layout.addWidget(title_bar)
        layout.addWidget(_divider())

        # Config body
        body = QWidget()
        body.setObjectName("mergeConfigBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(24, 20, 24, 20)
        body_layout.setSpacing(20)

        # Strategy grid
        strategy_grid = QHBoxLayout()
        strategy_grid.setSpacing(24)

        for label, value, icon_text in [
            ("Join Type",     "Left Join",    "⊕"),
            ("Master Source", "MIS Portal",   "★"),
            ("Join Key",      "Student ID",   "🔑"),
            ("Output Cols",   str(len(UNIFIED_COLUMNS)), "📊"),
        ]:
            tile = QFrame()
            tile.setObjectName("mergeConfigTile")
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(16, 12, 16, 12)
            tile_layout.setSpacing(4)

            lbl = QLabel(f"{icon_text}  {label}")
            lbl.setStyleSheet("color: rgba(255,255,255,0.35); font-size: 10px; font-weight: bold; letter-spacing: 0.8px; background: transparent;")

            val = QLabel(value)
            val.setStyleSheet("color: #e8eaf0; font-size: 14px; font-weight: 600; background: transparent;")

            tile_layout.addWidget(lbl)
            tile_layout.addWidget(val)
            strategy_grid.addWidget(tile)

        strategy_grid.addStretch()
        body_layout.addLayout(strategy_grid)

        body_layout.addWidget(_divider())

        # Expected output + run button row
        action_row = QHBoxLayout()
        action_row.setSpacing(16)

        self._expected_lbl = QLabel("Upload all 4 portal datasets to enable merge.")
        self._expected_lbl.setObjectName("mergeExpectedLabel")
        action_row.addWidget(self._expected_lbl, 1)

        self._run_merge_btn = QPushButton("▶  Run Merge")
        self._run_merge_btn.setObjectName("mergeRunBtnLocked")
        self._run_merge_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._run_merge_btn.setFixedHeight(42)
        self._run_merge_btn.setEnabled(False)
        self._run_merge_btn.clicked.connect(self._on_run_merge)

        action_row.addWidget(self._run_merge_btn)
        body_layout.addLayout(action_row)

        layout.addWidget(body)
        return card

    # ── Results section ───────────────────────────────────────────────

    def _build_results_section(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)

        # ── Quality score bar ─────────────────────────────────────────
        layout.addWidget(self._build_quality_bar())

        # ── Stats row ─────────────────────────────────────────────────
        self._stats_row = QHBoxLayout()
        self._stats_row.setSpacing(12)
        stats_wrap = QWidget()
        stats_wrap.setLayout(self._stats_row)
        layout.addWidget(stats_wrap)

        # ── Two column: preview + log ─────────────────────────────────
        bottom = QHBoxLayout()
        bottom.setSpacing(16)
        bottom.addWidget(self._build_preview_card(), 3)
        bottom.addWidget(self._build_log_card(), 1)
        layout.addLayout(bottom, 1)

        # ── Action footer ─────────────────────────────────────────────
        layout.addWidget(self._build_result_footer())

        return container

    def _build_quality_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("mergeQualityBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(20)

        # Quality cards
        self._result_qcards = QHBoxLayout()
        self._result_qcards.setSpacing(12)
        layout.addLayout(self._result_qcards, 1)

        # Quality score
        score_col = QVBoxLayout()
        score_col.setSpacing(6)

        score_header = QHBoxLayout()
        score_lbl = QLabel("DATA QUALITY SCORE")
        score_lbl.setObjectName("mergeQualityLabel")
        self._quality_pct = QLabel("—%")
        self._quality_pct.setObjectName("mergeQualityPct")
        score_header.addWidget(score_lbl)
        score_header.addStretch()
        score_header.addWidget(self._quality_pct)

        self._quality_bar = QProgressBar()
        self._quality_bar.setObjectName("mergeQualityProgress")
        self._quality_bar.setFixedHeight(8)
        self._quality_bar.setTextVisible(False)
        self._quality_bar.setRange(0, 100)

        score_col.addLayout(score_header)
        score_col.addWidget(self._quality_bar)

        score_wrap = QWidget()
        score_wrap.setFixedWidth(240)
        score_wrap.setLayout(score_col)
        layout.addWidget(score_wrap)

        return bar

    def _build_preview_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("mergeCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Title bar
        title_bar = QFrame()
        title_bar.setObjectName("mergeCardTitleBar")
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(24, 14, 24, 14)
        title_layout.setSpacing(12)

        icon = QLabel("📋")
        icon.setStyleSheet("font-size: 15px;")

        title = QLabel("Unified Dataset Preview")
        title.setObjectName("mergeCardTitle")

        self._preview_meta = QLabel("")
        self._preview_meta.setObjectName("mergeCardSubtitle")

        title_layout.addWidget(icon)
        title_layout.addWidget(title)
        title_layout.addStretch()
        title_layout.addWidget(self._preview_meta)

        layout.addWidget(title_bar)
        layout.addWidget(_divider())

        # Table
        table_container = QWidget()
        table_container.setObjectName("mergeTableContainer")
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(24, 16, 24, 16)
        table_layout.setSpacing(0)

        self._preview_table = QTableWidget()
        self._preview_table.setObjectName("mergeTable")
        self._preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._preview_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._preview_table.setAlternatingRowColors(True)
        self._preview_table.verticalHeader().setVisible(False)
        self._preview_table.horizontalHeader().setHighlightSections(False)
        self._preview_table.setShowGrid(False)
        self._preview_table.setMinimumHeight(300)

        table_layout.addWidget(self._preview_table, 1)
        layout.addWidget(table_container, 1)

        return card

    def _build_log_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("mergeCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Title bar
        title_bar = QFrame()
        title_bar.setObjectName("mergeCardTitleBar")
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(24, 14, 24, 14)

        icon = QLabel("📝")
        icon.setStyleSheet("font-size: 15px;")

        title = QLabel("Merge Log")
        title.setObjectName("mergeCardTitle")

        title_layout.addWidget(icon)
        title_layout.addWidget(title)
        title_layout.addStretch()

        layout.addWidget(title_bar)
        layout.addWidget(_divider())

        # Log body
        log_container = QWidget()
        log_container.setObjectName("mergeLogContainer")
        log_layout = QVBoxLayout(log_container)
        log_layout.setContentsMargins(24, 16, 24, 16)
        log_layout.setSpacing(12)

        self._log = QTextEdit()
        self._log.setObjectName("mergeLog")
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(200)

        log_layout.addWidget(self._log, 1)

        # Unmatched summary
        self._unmatched_lbl = QLabel("")
        self._unmatched_lbl.setObjectName("mergeUnmatchedLabel")
        self._unmatched_lbl.setWordWrap(True)
        log_layout.addWidget(self._unmatched_lbl)

        layout.addWidget(log_container, 1)

        return card

    def _build_result_footer(self) -> QWidget:
        footer = QFrame()
        footer.setObjectName("mergeResultFooter")
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(12)

        self._result_note = QLabel("")
        self._result_note.setObjectName("mergeResultNote")

        self._save_btn = QPushButton("💾  Save Unified Dataset")
        self._save_btn.setObjectName("mergeSaveBtn")
        self._save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_btn.clicked.connect(self._on_save)

        self._proceed_btn = QPushButton("Proceed to Model Training  →")
        self._proceed_btn.setObjectName("mergeProceedBtn")
        self._proceed_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._proceed_btn.clicked.connect(self._on_proceed)

        layout.addWidget(self._result_note)
        layout.addStretch()
        layout.addWidget(self._save_btn)
        layout.addWidget(self._proceed_btn)

        return footer

    # ------------------------------------------------------------------
    # Refresh source panel
    # ------------------------------------------------------------------

    def _refresh_source_panel(self):
        store     = DataStore.get()
        readiness = store.get_readiness()
        ready     = store.ready_count()
        all_ok    = store.all_portals_ready()

        id_cols = MergeEngine.detect_id_columns(store.portals)

        for key, is_ready in readiness.items():
            w    = self._source_rows[key]
            data = store.get_portal(key)

            if is_ready and data:
                w["dot"].setStyleSheet(f"""
                    background-color: {w['color']};
                    border-radius: 4px;
                    box-shadow: 0 0 8px {w['color']};
                """)
                w["rows_lbl"].setText(f"{data['row_count']:,} rows")
                w["rows_lbl"].setStyleSheet(
                    f"color: {w['color']}; font-size: 12px; font-weight: 600; background: transparent;"
                )
                id_c = id_cols.get(key) or "—"
                w["id_col_lbl"].setText(f"ID: {id_c}")
                w["id_col_lbl"].setStyleSheet(
                    f"color: rgba(255,255,255,0.5); font-size: 11px; background: transparent;"
                )

                # Replace badge widget
                old_badge = w["badge"]
                new_badge = _quality_badge("✓ Ready", "ready")
                parent = old_badge.parentWidget().layout()
                parent.replaceWidget(old_badge, new_badge)
                old_badge.deleteLater()
                w["badge"] = new_badge

            else:
                w["dot"].setStyleSheet("background-color: rgba(255,255,255,0.12); border-radius: 4px;")
                w["rows_lbl"].setText("—")
                w["rows_lbl"].setStyleSheet(
                    "color: rgba(255,255,255,0.25); font-size: 12px; background: transparent;"
                )
                w["id_col_lbl"].setText("ID: —")
                w["id_col_lbl"].setStyleSheet(
                    "color: rgba(255,255,255,0.25); font-size: 11px; background: transparent;"
                )

                old_badge = w["badge"]
                new_badge = _quality_badge("Pending", "pending")
                parent = old_badge.parentWidget().layout()
                parent.replaceWidget(old_badge, new_badge)
                old_badge.deleteLater()
                w["badge"] = new_badge

        self._source_progress.setValue(ready)
        self._source_prog_lbl.setText(f"{ready} / 4 sources ready")

        # Update expected label
        if all_ok:
            total = sum(p["row_count"] for p in store.portals.values() if p)
            self._expected_lbl.setText(
                f"✓ All sources ready  ·  Expected: ~{store.portals['mis']['row_count']:,} rows  "
                f"·  {len(UNIFIED_COLUMNS)} columns  ·  {total:,} source records"
            )
            self._expected_lbl.setStyleSheet(
                "color: #34d399; font-size: 12px; background: transparent;"
            )
            self._run_merge_btn.setEnabled(True)
            self._run_merge_btn.setObjectName("mergeRunBtnReady")
            self._run_merge_btn.setStyleSheet("""
                QPushButton#mergeRunBtnReady {
                    background-color: #4f8cff;
                    border: none;
                    border-radius: 8px;
                    color: white;
                    font-size: 13px;
                    font-weight: 600;
                    padding: 0 28px;
                }
                QPushButton#mergeRunBtnReady:hover {
                    background-color: rgba(79,140,255,0.85);
                }
            """)
        else:
            missing = [k for k, v in readiness.items() if not v]
            self._expected_lbl.setText(
                f"Waiting for: {', '.join(missing)}"
            )
            self._expected_lbl.setStyleSheet(
                "color: rgba(255,255,255,0.4); font-size: 12px; background: transparent;"
            )
            self._run_merge_btn.setEnabled(False)
            self._run_merge_btn.setObjectName("mergeRunBtnLocked")
            self._run_merge_btn.setStyleSheet("""
                QPushButton#mergeRunBtnLocked {
                    background-color: rgba(255,255,255,0.04);
                    border: 1px solid rgba(255,255,255,0.10);
                    border-radius: 8px;
                    color: rgba(255,255,255,0.25);
                    font-size: 13px;
                    padding: 0 28px;
                }
            """)

    # ------------------------------------------------------------------
    # Merge execution
    # ------------------------------------------------------------------

    def _on_run_merge(self):
        self._log.clear()
        self._log_line("Starting merge…", "info")
        self._log_line("Join type: Left Join on Student ID", "info")
        self._log_line("Master source: MIS Portal", "info")

        self.overlay.set_message("Merging datasets…", "Joining on Student ID")
        self.overlay.show()

        self._worker = _MergeWorker()
        self._worker.finished.connect(self._on_merge_finished)
        self._worker.error.connect(self._on_merge_error)
        self._worker.start()

    def _on_merge_finished(self, result):
        self.overlay.hide()
        self._merge_result = result

        if not result.success:
            for err in result.report.errors:
                self._log_line(f"ERROR: {err}", "error")
            return

        report = result.report
        self._log_line(f"MIS records loaded: {report.total_master:,}", "success")
        for key, count in report.unmatched.items():
            level = "warning" if count > 0 else "success"
            self._log_line(f"{key.capitalize()} unmatched: {count:,} students", level)
        self._log_line(f"Unified dataset: {report.total_merged:,} rows × {len(result.headers)} columns", "success")
        self._log_line("Merge complete ✅", "success")

        DataStore.get().set_unified_dataset({
            "headers": result.headers,
            "rows":    result.rows,
        })

        # Quality score
        total_cells = report.total_merged * len(result.headers)
        bad = report.total_master - report.total_merged  # simplified metric
        score = max(0, min(100, int(100 * (1 - sum(report.unmatched.values()) / max(report.total_master, 1)))))
        self._quality_bar.setValue(score)
        self._quality_pct.setText(f"{score}%")

        color = "#34d399" if score >= 85 else "#f5b335" if score >= 60 else "#ff5b5b"
        self._quality_bar.setStyleSheet(f"""
            QProgressBar#mergeQualityProgress {{
                background-color: rgba(255,255,255,0.08);
                border-radius: 4px;
                border: none;
            }}
            QProgressBar#mergeQualityProgress::chunk {{
                background-color: {color};
                border-radius: 4px;
            }}
        """)
        self._quality_pct.setStyleSheet(
            f"color: {color}; font-size: 14px; font-weight: bold; background: transparent;"
        )

        # Unmatched summary
        unmatched_parts = [
            f"{k.upper()}: {v:,} unmatched"
            for k, v in report.unmatched.items() if v > 0
        ]
        self._unmatched_lbl.setText(
            "  ·  ".join(unmatched_parts) if unmatched_parts
            else "✓ All students matched across all portals."
        )
        self._unmatched_lbl.setStyleSheet(
            f"color: {'#f5b335' if unmatched_parts else '#34d399'}; "
            f"font-size: 12px; background: transparent;"
        )

        # Quality cards
        while self._result_qcards.count():
            item = self._result_qcards.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for value, label, color in [
            (f"{report.total_merged:,}", "Unified Rows",   "#4f8cff"),
            (str(len(result.headers)),     "Columns",        "#4f8cff"),
            (f"{report.coverage_pct}%",   "Coverage",       "#34d399" if report.coverage_pct >= 90 else "#f5b335"),
            (str(sum(report.unmatched.values())), "Unmatched", "#f5b335" if sum(report.unmatched.values()) > 0 else "#34d399"),
        ]:
            self._result_qcards.addWidget(_stat_tile(value, label, color))
        self._result_qcards.addStretch()

        # Preview table
        self._populate_preview(result.headers, result.rows[:100])

        self._preview_meta.setText(
            f"Showing {min(100, len(result.rows)):,} of {len(result.rows):,} rows  ·  {len(result.headers)} columns"
        )
        self._result_note.setText(
            f"✓  {report.total_merged:,} rows  ·  {len(result.headers)} columns  ·  merge complete"
        )
        self._result_note.setStyleSheet("color: #34d399; font-size: 12px; background: transparent;")

        # Switch to results view
        self._results_stack.setCurrentIndex(1)

    def _on_merge_error(self, error_msg: str):
        self.overlay.hide()
        self._log_line(f"Merge failed: {error_msg}", "error")

    # ------------------------------------------------------------------
    # Preview table
    # ------------------------------------------------------------------

    def _populate_preview(self, headers: list, rows: list):
        self._preview_table.clear()
        self._preview_table.setColumnCount(len(headers))
        self._preview_table.setRowCount(len(rows))
        self._preview_table.setHorizontalHeaderLabels(headers)

        for row_i, row in enumerate(rows):
            for col_i, cell in enumerate(row):
                value    = str(cell).strip()
                is_empty = value == ""
                item     = QTableWidgetItem("—" if is_empty else value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                if is_empty:
                    item.setForeground(QColor("#f5b335"))
                    item.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
                self._preview_table.setItem(row_i, col_i, item)

        self._preview_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._preview_table.horizontalHeader().setStretchLastSection(True)

    # ------------------------------------------------------------------
    # Log helper
    # ------------------------------------------------------------------

    def _log_line(self, message: str, level: str = "info"):
        colors = {
            "info":    "#b8bcc8",
            "success": "#34d399",
            "warning": "#f5b335",
            "error":   "#ff5b5b",
        }
        color = colors.get(level, "#b8bcc8")
        timestamp = QTimer().currentTime().toString("hh:mm:ss") if hasattr(QTimer, 'currentTime') else ""
        self._log.append(
            f'<span style="color:rgba(255,255,255,0.25); font-size:11px;">[{timestamp}]</span> '
            f'<span style="color:{color}; font-family: Consolas, monospace; font-size: 12px;">{message}</span>'
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_save(self):
        from PyQt6.QtWidgets import QFileDialog
        import csv

        if not self._merge_result or not self._merge_result.success:
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Unified Dataset", "unified_dataset.csv",
            "CSV Files (*.csv);;All Files (*)"
        )
        if not path:
            return

        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(self._merge_result.headers)
                writer.writerows(self._merge_result.rows)
            self._result_note.setText(f"✓  Saved: {path.replace(chr(92), '/').split('/')[-1]}")
            self._refresh_log(f"Dataset saved to {path}", "success")
        except Exception as e:
            self._result_note.setText(f"Save failed: {e}")
            self._log_line(f"Save failed: {e}", "error")

    def _on_proceed(self):
        print("[DataMergePage] Proceeding to Model Training.")

    # ------------------------------------------------------------------
    # DataStore listener
    # ------------------------------------------------------------------

    def _on_store_updated(self, key: str):
        self._refresh_source_panel()

    # ------------------------------------------------------------------
    # Styles
    # ------------------------------------------------------------------

    def _apply_styles(self):
        self.setStyleSheet("""
            /* Cards */
            #mergeHeaderCard {
                background-color: #13172a;
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 16px;
            }
            #mergeCard {
                background-color: #13172a;
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 16px;
            }

            /* Card title bars */
            #mergeCardTitleBar {
                background: transparent;
            }
            #mergeCardTitle {
                color: #e8eaf0;
                font-size: 14px;
                font-weight: bold;
                background: transparent;
            }
            #mergeCardSubtitle {
                color: rgba(255,255,255,0.4);
                font-size: 12px;
                background: transparent;
            }

            /* Header */
            #mergeTitle {
                color: #e8eaf0;
                font-size: 18px;
                font-weight: bold;
                background: transparent;
            }
            #mergeSubtitle {
                color: rgba(255,255,255,0.4);
                font-size: 12px;
                background: transparent;
            }

            /* Model pill */
            #mergeModelPill {
                background-color: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 12px;
            }

            /* Section headers */
            #mergeSectionHeader {
                color: rgba(255,255,255,0.35);
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1.2px;
                background: transparent;
            }

            /* Source rows */
            #mergeSourceRow {
                background-color: rgba(255,255,255,0.02);
                border: 1px solid rgba(255,255,255,0.05);
                border-radius: 10px;
                padding: 12px 16px;
            }
            #mergeSourceMeta {
                color: rgba(255,255,255,0.4);
                font-size: 11px;
                background: transparent;
                min-width: 80px;
            }

            /* Progress */
            #mergeSourceProgress {
                background-color: rgba(255,255,255,0.08);
                border-radius: 3px;
                border: none;
            }
            #mergeSourceProgress::chunk {
                background-color: #4f8cff;
                border-radius: 3px;
            }

            /* Config tiles */
            #mergeConfigTile {
                background-color: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
            }

            /* Expected label */
            #mergeExpectedLabel {
                color: rgba(255,255,255,0.4);
                font-size: 12px;
                background: transparent;
            }

            /* Run prediction button */
            #mergeRunPredBtn {
                background-color: rgba(79,140,255,0.15);
                border: 1px solid rgba(79,140,255,0.30);
                border-radius: 8px;
                color: #6eb5ff;
                font-size: 12px;
                font-weight: 600;
                padding: 8px 16px;
            }
            #mergeRunPredBtn:hover {
                background-color: rgba(79,140,255,0.25);
            }

            /* Quality bar */
            #mergeQualityBar {
                background-color: rgba(0,0,0,0.15);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 12px;
            }
            #mergeQualityLabel {
                color: rgba(255,255,255,0.35);
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1px;
                background: transparent;
            }
            #mergeQualityPct {
                font-size: 14px;
                font-weight: bold;
                background: transparent;
            }
            #mergeQualityProgress {
                background-color: rgba(255,255,255,0.08);
                border-radius: 4px;
                border: none;
            }
            #mergeQualityProgress::chunk {
                border-radius: 4px;
            }

            /* Table */
            #mergeTableContainer {
                background: transparent;
            }
            #mergeTable {
                background-color: transparent;
                border: none;
                gridline-color: transparent;
                color: rgba(255,255,255,0.80);
                font-size: 12px;
                alternate-background-color: rgba(255,255,255,0.025);
                selection-background-color: rgba(79,140,255,0.18);
                selection-color: white;
            }
            #mergeTable QHeaderView::section {
                background-color: rgba(255,255,255,0.05);
                color: rgba(255,255,255,0.45);
                font-size: 11px;
                font-weight: bold;
                border: none;
                border-right: 1px solid rgba(255,255,255,0.06);
                padding: 8px 10px;
            }
            #mergeTable QHeaderView::section:last {
                border-right: none;
            }
            #mergeTable QScrollBar:vertical {
                background: transparent;
                width: 8px;
            }
            #mergeTable QScrollBar::handle:vertical {
                background: rgba(255,255,255,0.15);
                border-radius: 4px;
                min-height: 30px;
            }
            #mergeTable QScrollBar::handle:vertical:hover {
                background: rgba(255,255,255,0.28);
            }
            #mergeTable QScrollBar:horizontal {
                background: transparent;
                height: 8px;
            }
            #mergeTable QScrollBar::handle:horizontal {
                background: rgba(255,255,255,0.15);
                border-radius: 4px;
            }
            #mergeTable QScrollBar::add-line:vertical,
            #mergeTable QScrollBar::sub-line:vertical,
            #mergeTable QScrollBar::add-line:horizontal,
            #mergeTable QScrollBar::sub-line:horizontal {
                height: 0;
                width: 0;
            }

            /* Log */
            #mergeLogContainer {
                background: transparent;
            }
            #mergeLog {
                background-color: rgba(0,0,0,0.25);
                border: 1px solid rgba(255,255,255,0.07);
                border-radius: 8px;
                color: #b8bcc8;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
                padding: 10px;
            }
            #mergeUnmatchedLabel {
                color: rgba(255,255,255,0.5);
                font-size: 12px;
                background: transparent;
            }

            /* Result footer */
            #mergeResultFooter {
                background-color: rgba(0,0,0,0.12);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 12px;
            }
            #mergeResultNote {
                color: rgba(255,255,255,0.5);
                font-size: 12px;
                background: transparent;
            }

            /* Buttons */
            #mergeSaveBtn {
                background-color: rgba(52,211,153,0.10);
                border: 1px solid rgba(52,211,153,0.35);
                border-radius: 8px;
                color: #34d399;
                font-size: 12px;
                font-weight: 600;
                padding: 9px 20px;
            }
            #mergeSaveBtn:hover {
                background-color: rgba(52,211,153,0.20);
            }
            #mergeProceedBtn {
                background-color: #4f8cff;
                border: none;
                border-radius: 8px;
                color: white;
                font-size: 12px;
                font-weight: 600;
                padding: 9px 24px;
            }
            #mergeProceedBtn:hover {
                background-color: rgba(79,140,255,0.85);
            }

            /* Stat tiles */
            #mergeStatTile {
                background-color: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
            }

            /* Global scrollbars */
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255,255,255,0.10);
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255,255,255,0.20);
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0;
            }
        """)