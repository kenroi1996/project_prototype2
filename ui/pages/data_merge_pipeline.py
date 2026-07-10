"""
ui/pages/data_merge_pipeline_page.py
======================================
Combined Stage 2 + Stage 3 page.

Section A — Data Merge Center
    Unify all four portal datasets into one clean dataset.

Section B — Data Pipeline
    Preprocess the unified dataset and train the ML model.
    The 'Run Pipeline' button is locked until the merge is complete.

Workers, the full-dataset dialog, the spinner widget, and shared render
helpers used by this page have been split out into their own modules to
keep this file focused on the page itself:
  workers/data_merge_workers.py         -> _MergeWorker, PipelineWorker
  ui/dialogs/full_dataset_dialog.py     -> FullDatasetDialog
  ui/widgets/merge_spinner.py           -> _MergeSpinner
  ui/helpers/merge_pipeline_render.py   -> _divider, _quality_badge, _stat_tile

No logic changes — only relocation and import wiring.

Dialog styling fix
--------------------
Every QMessageBox in this page (pipeline complete/error, save errors, empty
states) previously used the raw PyQt6 QMessageBox, which renders with the
default OS theme and looks out of place against the rest of the dark UI.
All 8 call sites now go through the app's own show_info / show_warning /
show_error dialogs (ui/dialogs/confirmation_dialog.py), matching the styled
dialogs already used throughout the rest of the app.
"""
from __future__ import annotations

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
    QTextEdit,
    QStackedWidget,
    QGridLayout,
    QFileDialog,
)
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QFont

from services.data_store import DataStore
from services.merge_engine import MergeEngine, UNIFIED_COLUMNS
from ui.mixins.prediction_mixin import PredictionMixin
from ui.components.loading_overlay import LoadingOverlay
from services.system_config import SystemConfig
from ui.dialogs.confirmation_dialog import show_error, show_info, show_warning

from workers.data_merge_workers import _MergeWorker, PipelineWorker
from ui.dialogs.full_dataset_dialog import FullDatasetDialog
from ui.widgets.merge_spinner import _MergeSpinner
from ui.helpers.merge_pipeline_render import _divider, _quality_badge, _stat_tile
from ui.styles.merge_pipeline_styles import MERGE_PIPELINE_STYLESHEET


# =====================================
# COMBINED PAGE
# =====================================

class DataMergePipelinePage(PredictionMixin, QWidget):
    """
    Combined Stage 2 + Stage 3 page.

    Section A — Data Merge Center
        Unify all four portal datasets into one clean dataset.

    Section B — Data Pipeline
        Preprocess the unified dataset and train the ML model.
        The 'Run Pipeline' button is locked until the merge is complete.
    """

    def __init__(self):
        super().__init__()
        self._merge_result                = None
        self._pipeline_worker: PipelineWorker | None = None
        self._accent                      = "#4f8cff"
        self._on_proceed_training         = None
        self._pipeline_engineered_dataset = None
        self.setup_ui()
        self.overlay = LoadingOverlay(self)

        DataStore.get().add_listener(self._on_store_updated)
        self._refresh_source_panel()
        self._refresh_pipeline_gate()

    # ==================================================================
    # TOP-LEVEL UI
    # ==================================================================

    def setup_ui(self):
        self.setObjectName("page")
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(30, 30, 30, 30)
        self.main_layout.setSpacing(28)

        self.fixed_header_container = self._build_shared_header()
        self.main_layout.addWidget(self.fixed_header_container)

        self.main_layout.addWidget(self._section_divider("🔀", "SECTION A", "Data Merge"))
        self.main_layout.addWidget(self._build_source_panel())
        self.main_layout.addWidget(self._build_merge_config_card())

        self._merge_results_stack = QStackedWidget()
        self._merge_results_stack.addWidget(QWidget())
        self._merge_results_stack.addWidget(self._build_merge_results_section())
        self._merge_results_stack.setCurrentIndex(0)
        self.main_layout.addWidget(self._merge_results_stack)

        self.main_layout.addWidget(self._section_divider("⚙️", "SECTION B", "Data Pipeline"))

        self._pipeline_gate_banner = self._build_pipeline_gate_banner()
        self.main_layout.addWidget(self._pipeline_gate_banner)

        self._pipeline_content = QWidget()
        pipeline_content_layout = QVBoxLayout(self._pipeline_content)
        pipeline_content_layout.setContentsMargins(0, 0, 0, 0)
        pipeline_content_layout.setSpacing(20)

        preprocess_row = QHBoxLayout()
        preprocess_row.setSpacing(16)

        preprocess_left = QVBoxLayout()
        preprocess_left.setSpacing(6)

        section_title = QLabel("Data Preprocessing Pipeline")
        section_title.setObjectName("pipelineSectionTitle")

        section_desc = QLabel(
            "Cleans, merges, and normalizes data from all four offices "
            "into one unified dataset for ML training"
        )
        section_desc.setObjectName("pipelineSectionDesc")
        section_desc.setWordWrap(True)

        preprocess_left.addWidget(section_title)
        preprocess_left.addWidget(section_desc)

        self._run_pipeline_btn = QPushButton("▶  Run Pipeline")
        self._run_pipeline_btn.setObjectName("pipelineRunBtn")
        self._run_pipeline_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._run_pipeline_btn.setFixedHeight(40)
        self._run_pipeline_btn.setEnabled(False)
        self._run_pipeline_btn.clicked.connect(self._run_pipeline)

        preprocess_row.addLayout(preprocess_left, 1)
        preprocess_row.addWidget(self._run_pipeline_btn, 0, Qt.AlignmentFlag.AlignTop)
        pipeline_content_layout.addLayout(preprocess_row)

        pipeline_content_layout.addWidget(self._create_pipeline_stages())

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(20)
        bottom_row.addWidget(self._create_quality_report_card(), 1)
        bottom_row.addWidget(self._create_dataset_preview_card(), 1)
        pipeline_content_layout.addLayout(bottom_row)

        pipeline_content_layout.addWidget(self._create_pipeline_log_card())

        self.main_layout.addWidget(self._pipeline_content)
        self.main_layout.addStretch()
        self.setLayout(self.main_layout)
        self.init_prediction()
        self._apply_styles()

    def _build_shared_header(self) -> QFrame:
        container = QFrame()
        container.setObjectName("mergeHeaderCard")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(0)

        row = QHBoxLayout()
        row.setSpacing(20)

        title_col = QVBoxLayout()
        title_col.setSpacing(4)

        title = QLabel("DATA MERGE & PIPELINE CENTER")
        title.setObjectName("mergeTitle")

        sub = QLabel(
            "Unify all portal datasets into one clean dataset, "
            "then run the preprocessing pipeline for model training"
        )
        sub.setObjectName("mergeSubtitle")

        title_col.addWidget(title)
        title_col.addWidget(sub)
        row.addLayout(title_col, 1)

        model_pill = QFrame()
        model_pill.setObjectName("mergeModelPill")
        pill_layout = QHBoxLayout(model_pill)
        pill_layout.setContentsMargins(16, 10, 16, 10)
        pill_layout.setSpacing(10)

        dot = QLabel("●")
        dot.setStyleSheet("color: #34d399; font-size: 10px; background: transparent;")

        status_lbl = QLabel("Model Active")
        status_lbl.setStyleSheet(
            "color: #e8eaf0; font-size: 12px; font-weight: 600; background: transparent;"
        )

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

        self._sem_pill_lbl = QLabel(f"{SystemConfig.term_label()}  ▾")
        self._sem_pill_lbl.setObjectName("pipelineSemesterPill")
        self._sem_pill_lbl.setObjectName("pipelineSemesterPill")

        #run_pred_btn = QPushButton("Run Prediction")
        #run_pred_btn.setObjectName("mergeRunPredBtn")
        #run_pred_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        #run_pred_btn.setFixedWidth(130)
        #run_pred_btn.clicked.connect(self.on_run_prediction)

        pill_layout.addWidget(dot)
        pill_layout.addWidget(status_lbl)
        pill_layout.addSpacing(8)
        pill_layout.addWidget(self._sem_pill_lbl)
        #pill_layout.addWidget(run_pred_btn)
        row.addWidget(model_pill)
        layout.addLayout(row)
        return container

    def _section_divider(self, icon: str, tag: str, title: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("sectionDividerFrame")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(12)

        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("font-size: 16px;")

        tag_lbl = QLabel(tag)
        tag_lbl.setObjectName("sectionTag")

        title_lbl = QLabel(title)
        title_lbl.setObjectName("sectionDividerTitle")

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: rgba(255,255,255,0.08);")

        layout.addWidget(icon_lbl)
        layout.addWidget(tag_lbl)
        layout.addWidget(title_lbl)
        layout.addWidget(line, 1)
        return frame

    # ==================================================================
    # SECTION A — DATA MERGE BUILDERS
    # ==================================================================

    def _build_source_panel(self) -> QFrame:
        card = QFrame()
        card.setObjectName("mergeCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

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

            dot_frame = QFrame()
            dot_frame.setFixedSize(8, 8)
            dot_frame.setStyleSheet(
                "background-color: rgba(255,255,255,0.15); border-radius: 4px;"
            )

            name_col = QVBoxLayout()
            name_col.setSpacing(2)

            name = QLabel(short)
            name.setStyleSheet(
                f"color: {color}; font-size: 13px; font-weight: bold; background: transparent;"
            )

            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet(
                "color: rgba(255,255,255,0.35); font-size: 11px; background: transparent;"
            )

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

        self._source_spinner = _MergeSpinner(size=20, color="#4f8cff")
        self._source_spinner.hide()
        prog_layout.addWidget(self._source_spinner)

        layout.addWidget(prog_container)
        return card

    def _build_merge_config_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("mergeCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

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

        body = QWidget()
        body.setObjectName("mergeConfigBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(24, 20, 24, 20)
        body_layout.setSpacing(20)

        strategy_grid = QHBoxLayout()
        strategy_grid.setSpacing(24)

        for label, value, icon_text in [
            ("Join Type",     "Left Join",               ""),
            ("Master Source", "MIS Portal",              ""),
            ("Join Key",      "Student ID",              ""),
            ("Output Cols",   str(len(UNIFIED_COLUMNS)), "📊"),
        ]:
            tile = QFrame()
            tile.setObjectName("mergeConfigTile")
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(16, 12, 16, 12)
            tile_layout.setSpacing(4)

            lbl = QLabel(f"{icon_text}  {label}")
            lbl.setStyleSheet(
                "color: rgba(255,255,255,0.35); font-size: 10px; "
                "font-weight: bold; letter-spacing: 0.8px; background: transparent;"
            )

            val = QLabel(value)
            val.setStyleSheet(
                "color: #e8eaf0; font-size: 14px; font-weight: 600; background: transparent;"
            )

            tile_layout.addWidget(lbl)
            tile_layout.addWidget(val)
            strategy_grid.addWidget(tile)

        strategy_grid.addStretch()
        body_layout.addLayout(strategy_grid)
        body_layout.addWidget(_divider())

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

    def _build_merge_results_section(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)

        layout.addWidget(self._build_quality_bar())

        self._stats_row = QHBoxLayout()
        self._stats_row.setSpacing(12)
        stats_wrap = QWidget()
        stats_wrap.setLayout(self._stats_row)
        layout.addWidget(stats_wrap)

        bottom = QHBoxLayout()
        bottom.setSpacing(16)
        bottom.addWidget(self._build_preview_card(), 3)
        bottom.addWidget(self._build_log_card(), 1)
        layout.addLayout(bottom, 1)

        layout.addWidget(self._build_result_footer())
        return container

    def _build_quality_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("mergeQualityBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(20)

        self._result_qcards = QHBoxLayout()
        self._result_qcards.setSpacing(12)
        layout.addLayout(self._result_qcards, 1)

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

        self._view_full_btn = QPushButton("👁 View Full Dataset")
        self._view_full_btn.setObjectName("mergeViewFullBtn")
        self._view_full_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._view_full_btn.clicked.connect(self._on_view_full_dataset)
        self._view_full_btn.setEnabled(False)

        print(f">>> View Full Dataset button created, enabled={self._view_full_btn.isEnabled()}")

        self._save_btn = QPushButton("Save Unified Dataset")
        self._save_btn.setObjectName("mergeSaveBtn")
        self._save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_btn.clicked.connect(self._on_save)

        self._proceed_btn = QPushButton("Proceed to Model Training  →")
        self._proceed_btn.setObjectName("mergeProceedBtn")
        self._proceed_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._proceed_btn.clicked.connect(self._on_proceed_training_clicked)

        layout.addWidget(self._result_note)
        layout.addStretch()
        layout.addWidget(self._view_full_btn)
        layout.addWidget(self._save_btn)
        layout.addWidget(self._proceed_btn)
        return footer

    def _on_proceed_training_clicked(self):
        if callable(self._on_proceed_training):
            self._on_proceed_training()

    # ==================================================================
    # SECTION B — DATA PIPELINE BUILDERS
    # ==================================================================

    def _build_pipeline_gate_banner(self) -> QFrame:
        banner = QFrame()
        banner.setObjectName("pipelineGateBanner")
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(14)

        icon = QLabel("🔒")
        icon.setStyleSheet("font-size: 18px;")

        msg = QLabel(
            "Run the <b>Data Merge</b> above first — "
            "the pipeline requires a unified dataset to proceed."
        )
        msg.setObjectName("pipelineGateMsg")
        msg.setTextFormat(Qt.TextFormat.RichText)

        layout.addWidget(icon)
        layout.addWidget(msg, 1)
        return banner

    def _create_pipeline_stages(self) -> QFrame:
        card = QFrame()
        card.setObjectName("pipelineCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(20)

        title = QLabel("PIPELINE STAGES")
        title.setObjectName("pipelineCardTitle")
        layout.addWidget(title)

        self._stages_row = QHBoxLayout()
        self._stages_row.setSpacing(0)

        self._stage_widgets = []
        self._stage_arrows  = []

        stages = [
            ("", "Ingest",     "read_excel"),
            ("", "Clean",      "handle_missing"),
            ("", "Merge",      "remove_duplicates"),
            ("", "Normalize",  "scale_numerical"),
            ("", "Ready",      "save_outputs"),
        ]

        for i, (icon, name, step_key) in enumerate(stages):
            stage = QFrame()
            stage.setObjectName("pipelineStage")
            stage.setMinimumHeight(72)
            stage_layout = QVBoxLayout(stage)
            stage_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            stage_layout.setSpacing(6)

            icon_lbl = QLabel(icon)
            icon_lbl.setObjectName("pipelineStageIcon")
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            name_lbl = QLabel(name)
            name_lbl.setObjectName("pipelineStageLabel")
            name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            stage_layout.addWidget(icon_lbl)
            stage_layout.addWidget(name_lbl)

            self._stages_row.addWidget(stage, 1)
            self._stage_widgets.append((stage, icon_lbl, name_lbl, step_key))

            if i < len(stages) - 1:
                arrow = QLabel("›")
                arrow.setObjectName("pipelineStageArrow")
                arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
                arrow.setFixedWidth(20)
                self._stages_row.addWidget(arrow)
                self._stage_arrows.append(arrow)

        layout.addLayout(self._stages_row)
        return card

    def _create_quality_report_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("pipelineCard")
        self._quality_layout = QVBoxLayout(card)
        self._quality_layout.setContentsMargins(24, 20, 24, 20)
        self._quality_layout.setSpacing(14)

        title = QLabel("DATA QUALITY REPORT")
        title.setObjectName("pipelineCardTitle")
        self._quality_layout.addWidget(title)

        self._metrics_container = QVBoxLayout()
        self._quality_layout.addLayout(self._metrics_container)

        self._quality_footer = QLabel(
            'Data quality score: <span id="pipelineQualityScore">--/100</span>'
        )
        self._quality_footer.setObjectName("pipelineQualityFooter")
        self._quality_footer.setTextFormat(Qt.TextFormat.RichText)
        self._quality_layout.addSpacing(8)
        self._quality_layout.addWidget(self._quality_footer)
        return card

    def _create_dataset_preview_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("pipelineCard")
        self._pipeline_preview_layout = QVBoxLayout(card)
        self._pipeline_preview_layout.setContentsMargins(24, 20, 24, 20)
        self._pipeline_preview_layout.setSpacing(14)

        title = QLabel("UNIFIED DATASET PREVIEW")
        title.setObjectName("pipelineCardTitle")
        self._pipeline_preview_layout.addWidget(title)

        self._pipeline_preview_meta = QLabel("No data loaded yet")
        self._pipeline_preview_meta.setObjectName("pipelinePreviewMeta")
        self._pipeline_preview_layout.addWidget(self._pipeline_preview_meta)

        self._tags_grid = QGridLayout()
        self._tags_grid.setHorizontalSpacing(8)
        self._tags_grid.setVerticalSpacing(8)
        self._tags_host = QWidget()
        self._tags_host.setLayout(self._tags_grid)
        self._pipeline_preview_layout.addWidget(self._tags_host)

        self._download_btn = QPushButton("↓  Download Unified CSV")
        self._download_btn.setObjectName("pipelineDownloadBtn")
        self._download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._download_btn.setEnabled(False)
        self._download_btn.clicked.connect(self._download_unified_csv)
        self._pipeline_preview_layout.addWidget(self._download_btn)
        return card

    def _create_pipeline_log_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("pipelineCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(12)

        title_row = QHBoxLayout()
        title_row.setSpacing(12)

        title = QLabel("PIPELINE LOG")
        title.setObjectName("pipelineCardTitle")
        title_row.addWidget(title)
        title_row.addStretch()

        self._view_pipeline_dataset_btn = QPushButton("👁  View Engineered Dataset")
        self._view_pipeline_dataset_btn.setObjectName("pipelineViewDatasetBtn")
        self._view_pipeline_dataset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._view_pipeline_dataset_btn.setEnabled(False)
        self._view_pipeline_dataset_btn.setFixedHeight(32)
        self._view_pipeline_dataset_btn.clicked.connect(self._on_view_pipeline_dataset)
        title_row.addWidget(self._view_pipeline_dataset_btn)

        layout.addLayout(title_row)

        self._pipeline_log = QTextEdit()
        self._pipeline_log.setReadOnly(True)
        self._pipeline_log.setPlaceholderText(
            "Complete the Data Merge above, then click 'Run Pipeline'…"
        )
        self._pipeline_log.setMaximumHeight(220)
        self._pipeline_log.setStyleSheet("""
            QTextEdit {
                background-color: rgba(0,0,0,0.2);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 8px;
                color: #b8bcc8;
                font-family: 'Consolas', monospace;
                font-size: 12px;
                padding: 10px;
            }
        """)
        layout.addWidget(self._pipeline_log)
        return card

    def _create_metric_row(self, label, value, color, display_value=None) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(12)

        name = QLabel(label)
        name.setObjectName("pipelineMetricLabel")
        name.setFixedWidth(160)

        bar = QProgressBar()
        bar.setValue(value)
        bar.setTextVisible(False)
        bar.setFixedHeight(8)
        bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: rgba(255, 255, 255, 0.08);
                border-radius: 4px;
                border: none;
            }}
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 4px;
            }}
        """)

        shown = display_value if display_value is not None else f"{value}%"
        pct = QLabel(shown)
        pct.setObjectName("pipelineMetricValue")
        pct.setFixedWidth(60)
        pct.setAlignment(Qt.AlignmentFlag.AlignRight)

        layout.addWidget(name)
        layout.addWidget(bar, 1)
        layout.addWidget(pct)
        return row

    # ==================================================================
    # SECTION A — LOGIC
    # ==================================================================

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
                w["dot"].setStyleSheet(
                    f"background-color: {w['color']}; border-radius: 4px;"
                )
                w["rows_lbl"].setText(f"{data['row_count']:,} rows")
                w["rows_lbl"].setStyleSheet(
                    f"color: {w['color']}; font-size: 12px; "
                    f"font-weight: 600; background: transparent;"
                )
                id_c = id_cols.get(key) or "—"
                w["id_col_lbl"].setText(f"ID: {id_c}")
                w["id_col_lbl"].setStyleSheet(
                    "color: rgba(255,255,255,0.5); font-size: 11px; background: transparent;"
                )
                old_badge = w["badge"]
                new_badge = _quality_badge("✓ Ready", "ready")
                parent = old_badge.parentWidget().layout()
                parent.replaceWidget(old_badge, new_badge)
                old_badge.deleteLater()
                w["badge"] = new_badge
            else:
                w["dot"].setStyleSheet(
                    "background-color: rgba(255,255,255,0.12); border-radius: 4px;"
                )
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

        if all_ok:
            total = sum(p["row_count"] for p in store.portals.values() if p)
            self._expected_lbl.setText(
                f"✓ All sources ready  ·  "
                f"Expected: ~{store.portals['mis']['row_count']:,} rows  ·  "
                f"{len(UNIFIED_COLUMNS)} columns  ·  {total:,} source records"
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
            self._expected_lbl.setText(f"Waiting for: {', '.join(missing)}")
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

    def _on_run_merge(self):
        self._log.clear()
        self._merge_log_line("Starting merge…", "info")
        self._merge_log_line("Join type: Left Join on Student ID", "info")
        self._merge_log_line("Master source: MIS Portal", "info")

        self.overlay.set_message("Merging datasets…", "Joining on Student ID")
        self.overlay.show()

        self._worker = _MergeWorker()
        self._worker.finished.connect(self._on_merge_finished)
        self._worker.error.connect(self._on_merge_error)
        self._worker.start()

    def _on_merge_finished(self, result):
        self.overlay.hide()
        self._merge_result = result
        self._view_full_btn.setEnabled(True)
        print(f">>> View Full Dataset button enabled={self._view_full_btn.isEnabled()}")

        if not result.success:
            for err in result.report.errors:
                self._merge_log_line(f"ERROR: {err}", "error")
            return

        report = result.report
        self._merge_log_line(f"MIS records loaded: {report.total_master:,}", "success")
        for key, count in report.unmatched.items():
            level = "warning" if count > 0 else "success"
            self._merge_log_line(
                f"{key.capitalize()} unmatched: {count:,} students", level
            )
        self._merge_log_line(
            f"Unified dataset: {report.total_merged:,} rows × "
            f"{len(result.headers)} columns",
            "success",
        )
        self._merge_log_line("Merge complete ✅", "success")

        # ── FIX 1: store raw merged data separately so retraining always ──────
        # has access to the original columns (including Final_Avg_GRD).
        # raw_merged_dataset is NEVER overwritten by the pipeline or prediction
        # flow — only by a new merge run.
        DataStore.get().set_raw_merged_dataset({
            "headers": result.headers,
            "rows":    result.rows,
        })
        DataStore.get().set_unified_dataset({
            "headers": result.headers,
            "rows":    result.rows,
        })

        # Quality score
        score = max(
            0,
            min(
                100,
                int(
                    100
                    * (
                        1
                        - sum(report.unmatched.values())
                        / max(report.total_master, 1)
                    )
                ),
            ),
        )
        self._quality_bar.setValue(score)
        self._quality_pct.setText(f"{score}%")

        color = (
            "#34d399" if score >= 85
            else "#f5b335" if score >= 60
            else "#ff5b5b"
        )
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
            f"color: {color}; font-size: 14px; "
            f"font-weight: bold; background: transparent;"
        )

        unmatched_parts = [
            f"{k.upper()}: {v:,} unmatched"
            for k, v in report.unmatched.items()
            if v > 0
        ]
        self._unmatched_lbl.setText(
            "  ·  ".join(unmatched_parts)
            if unmatched_parts
            else "✓ All students matched across all portals."
        )
        self._unmatched_lbl.setStyleSheet(
            f"color: {'#f5b335' if unmatched_parts else '#34d399'}; "
            f"font-size: 12px; background: transparent;"
        )

        while self._result_qcards.count():
            item = self._result_qcards.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for value, label, tile_color in [
            (f"{report.total_merged:,}",                  "Unified Rows", "#4f8cff"),
            (str(len(result.headers)),                    "Columns",      "#4f8cff"),
            (f"{report.coverage_pct}%",                   "Coverage",
             "#34d399" if report.coverage_pct >= 90 else "#f5b335"),
            (str(sum(report.unmatched.values())),          "Unmatched",
             "#f5b335" if sum(report.unmatched.values()) > 0 else "#34d399"),
        ]:
            self._result_qcards.addWidget(
                _stat_tile(value, label, tile_color)
            )
        self._result_qcards.addStretch()

        self._populate_preview(result.headers, result.rows[:100])
        self._preview_meta.setText(
            f"Showing {min(100, len(result.rows)):,} of "
            f"{len(result.rows):,} rows  ·  {len(result.headers)} columns"
        )
        self._result_note.setText(
            f"✓  {report.total_merged:,} rows  ·  "
            f"{len(result.headers)} columns  ·  merge complete"
        )
        self._result_note.setStyleSheet(
            "color: #34d399; font-size: 12px; background: transparent;"
        )

        self._merge_results_stack.setCurrentIndex(1)
        self._refresh_pipeline_gate()
        self._refresh_pipeline_quality()

    def _on_merge_error(self, error_msg: str):
        self.overlay.hide()
        self._merge_log_line(f"Merge failed: {error_msg}", "error")

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
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
                )
                if is_empty:
                    item.setForeground(QColor("#f5b335"))
                    item.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
                self._preview_table.setItem(row_i, col_i, item)

        self._preview_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._preview_table.horizontalHeader().setStretchLastSection(True)

    def _merge_log_line(self, message: str, level: str = "info"):
        colors = {
            "info":    "#b8bcc8",
            "success": "#34d399",
            "warning": "#f5b335",
            "error":   "#ff5b5b",
        }
        color = colors.get(level, "#b8bcc8")
        self._log.append(
            f'<span style="color:{color}; '
            f'font-family: Consolas, monospace; font-size: 12px;">'
            f"{message}</span>"
        )

    def _on_save(self):
        import csv

        if not self._merge_result or not self._merge_result.success:
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Unified Dataset",
            "unified_dataset.csv",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path:
            return

        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(self._merge_result.headers)
                writer.writerows(self._merge_result.rows)
            self._result_note.setText(
                f"✓  Saved: {path.replace(chr(92), '/').split('/')[-1]}"
            )
        except Exception as e:
            self._result_note.setText(f"Save failed: {e}")
            self._merge_log_line(f"Save failed: {e}", "error")

    def _on_view_full_dataset(self):
        if not self._merge_result or not self._merge_result.success:
            return

        dialog = FullDatasetDialog(
            self._merge_result.headers,
            self._merge_result.rows,
            parent=self,
        )
        dialog.exec()

    # ==================================================================
    # SECTION B — LOGIC
    # ==================================================================

    def _refresh_pipeline_gate(self):
        store      = DataStore.get()
        merge_done = store.unified_dataset is not None

        self._pipeline_gate_banner.setVisible(not merge_done)
        self._pipeline_content.setEnabled(merge_done)

        self._run_pipeline_btn.setEnabled(merge_done)
        if merge_done:
            self._run_pipeline_btn.setStyleSheet("""
                QPushButton#pipelineRunBtn {
                    background-color: #4f8cff;
                    border: none;
                    border-radius: 8px;
                    color: white;
                    font-size: 13px;
                    font-weight: 600;
                    padding: 0 24px;
                }
                QPushButton#pipelineRunBtn:hover {
                    background-color: rgba(79,140,255,0.85);
                }
            """)
            self._pipeline_log.setPlaceholderText(
                "Unified dataset ready. Click 'Run Pipeline' to start…"
            )
        else:
            self._run_pipeline_btn.setStyleSheet("""
                QPushButton#pipelineRunBtn {
                    background-color: rgba(255,255,255,0.04);
                    border: 1px solid rgba(255,255,255,0.10);
                    border-radius: 8px;
                    color: rgba(255,255,255,0.25);
                    font-size: 13px;
                    padding: 0 24px;
                }
            """)

    def _run_pipeline(self):
        store = DataStore.get()

        if store.unified_dataset is None:
            show_warning(
                self,
                "Merge Required",
                "Please complete the Data Merge step above before running the pipeline.",
            )
            return

        self._run_pipeline_btn.setEnabled(False)
        self._download_btn.setEnabled(False)
        self._pipeline_log.clear()

        self._pipeline_worker = PipelineWorker(self)
        self._pipeline_worker.step_started.connect(self._on_pipeline_step)
        self._pipeline_worker.step_progress.connect(self._on_pipeline_progress)
        self._pipeline_worker.finished_success.connect(self._on_pipeline_success)
        self._pipeline_worker.finished_error.connect(self._on_pipeline_error)
        self._pipeline_worker.start()

    def _on_pipeline_step(self, step: str, message: str):
        self._pipeline_log.append(f"[{step}] {message}")
        step_map = {
            "read_excel": 0,     "validate": 0,
            "remove_duplicates": 1, "handle_missing": 1,
            "encode_categorical": 2, "scale_numerical": 2,
            "generate_labels": 3,   "prepare_features": 3,
            "train_model": 3,       "save_outputs": 4,
        }
        self._highlight_stage(step_map.get(step, -1))

    def _on_pipeline_progress(self, progress: int):
        pass

    def _highlight_stage(self, active_idx: int):
        for i, (stage, icon, name, key) in enumerate(self._stage_widgets):
            if i == active_idx:
                stage.setStyleSheet("""
                    #pipelineStage {
                        background-color: rgba(79,140,255,0.15);
                        border: 1px solid rgba(79,140,255,0.4);
                        border-radius: 10px;
                    }
                """)
                icon.setStyleSheet("font-size: 24px; color: #4f8cff;")
            elif i < active_idx:
                stage.setStyleSheet("""
                    #pipelineStage {
                        background-color: rgba(52,211,153,0.10);
                        border: 1px solid rgba(52,211,153,0.3);
                        border-radius: 10px;
                    }
                """)
                icon.setStyleSheet("font-size: 24px; color: #34d399;")
            else:
                stage.setStyleSheet("""
                    #pipelineStage {
                        background-color: rgba(255,255,255,0.03);
                        border: 1px solid rgba(255,255,255,0.08);
                        border-radius: 10px;
                    }
                """)
                icon.setStyleSheet("font-size: 24px; color: rgba(255,255,255,0.4);")

    def _on_pipeline_success(self, results: dict):
        self._highlight_stage(4)

        store           = DataStore.get()
        training_result = results.get("training_result")
        metrics = {
            "accuracy": results.get("recall", 0) / 100,
            "cv_mean":  results.get("f1_score", 0),
            "cv_std":   0,
        }

        eng_headers = results.get("engineered_headers", [])
        eng_rows    = results.get("engineered_rows", [])
        if eng_headers and eng_rows:
            self._pipeline_engineered_dataset = {
                "headers": eng_headers,
                "rows":    eng_rows,
            }
            # ── FIX 2: do NOT overwrite unified_dataset with engineered output ─
            # raw_merged_dataset remains the training source (set in
            # _on_merge_finished and never touched here).
            # unified_dataset is also left as-is: the raw merged data set by
            # _on_merge_finished is still the correct input for prediction
            # (PredictionEngine runs feature engineering on demand).
            self._pipeline_log.append(
                f"📊 Engineered dataset: {len(eng_rows):,} rows "
                f"· {len(eng_headers)} features"
            )
        else:
            self._pipeline_engineered_dataset = None

        self._pipeline_log.append("✅ Pipeline completed successfully!")
        self._pipeline_log.append(
            f"Accuracy: {metrics.get('accuracy', 'N/A'):.4f}"
            if isinstance(metrics.get('accuracy'), float)
            else f"Accuracy: {metrics.get('accuracy', 'N/A')}"
        )
        self._pipeline_log.append(
            f"CV Score: {metrics.get('cv_mean', 'N/A')} "
            f"± {metrics.get('cv_std', 'N/A')}"
        )

        self._refresh_pipeline_quality()
        self._run_pipeline_btn.setEnabled(True)
        self._download_btn.setEnabled(True)
        self._view_pipeline_dataset_btn.setEnabled(
            self._pipeline_engineered_dataset is not None
        )

        # Use the app's own styled dialog instead of a raw QMessageBox.
        show_info(
            self,
            "Pipeline Complete",
            "Model trained successfully!",
            f"Recall: {results.get('recall', 'N/A')}%\n"
            f"F1: {results.get('f1_score', 'N/A')}  PR-AUC: {results.get('pr_auc', 'N/A')}\n"
            f"Features: {len(eng_headers) - 1} engineered columns\n"
            f"Rows: {len(eng_rows):,}\n\n"
            f"Click '👁 View Engineered Dataset' to inspect the result.",
        )

    def _on_pipeline_error(self, error: str):
        self._pipeline_log.append(f"❌ ERROR: {error}")
        self._refresh_pipeline_gate()
        show_error(self, "Pipeline Error", "The preprocessing pipeline failed.", error)

    def _on_view_pipeline_dataset(self):
        dataset = getattr(self, "_pipeline_engineered_dataset", None)

        if not dataset:
            show_info(
                self,
                "No Engineered Dataset",
                "Run the pipeline first to generate the engineered dataset.",
            )
            return

        headers = dataset.get("headers", [])
        rows    = dataset.get("rows", [])

        if not headers or not rows:
            show_info(self, "Empty Dataset", "The engineered dataset is empty.")
            return

        dialog = FullDatasetDialog(headers, rows, parent=self)
        dialog._setup_title(
            f"⚙️ Engineered Dataset  ·  {len(rows):,} rows  ·  "
            f"{len(headers)} features"
        )
        dialog.exec()

    def _download_unified_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Unified Dataset",
            "unified_dataset.csv",
            "CSV Files (*.csv)",
        )
        if not path:
            return

        store   = DataStore.get()
        unified = store.unified_dataset
        if unified is None:
            show_warning(self, "No Data", "No unified dataset available.")
            return

        try:
            import pandas as pd
            if isinstance(unified, dict):
                df = pd.DataFrame(unified["rows"], columns=unified["headers"])
            else:
                df = unified
            df.to_csv(path, index=False)
            show_info(self, "Saved", "Unified dataset saved successfully.", path)
        except Exception as e:
            show_error(self, "Save Error", "Could not save the unified dataset.", str(e))

    def _refresh_pipeline_quality(self):
        store = DataStore.get()

        while self._metrics_container.count():
            item = self._metrics_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        readiness = store.get_readiness()
        for portal, ready in readiness.items():
            data  = store.get_portal(portal)
            rows  = data["row_count"] if data else 0
            color = "#34d399" if ready else "#6b7280"
            self._metrics_container.addWidget(
                self._create_metric_row(
                    f"{portal.upper()} completeness",
                    100 if ready else 0,
                    color,
                    f"{rows:,} rows" if ready else "Missing",
                )
            )

        self._metrics_container.addWidget(
            self._create_metric_row("Duplicate rows", 0, "#6b7280", "0")
        )

        self._quality_footer.setText(
            f'Data quality score: <span id="pipelineQualityScore">'
            f"{store.ready_count() * 25}/100</span>"
            f" — {store.ready_count()}/4 portals ready"
        )

        while self._tags_grid.count():
            item = self._tags_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        all_columns = set()
        for portal in ["mis", "sao", "guidance", "registrar"]:
            data = store.get_portal(portal)
            if data:
                all_columns.update(data["headers"])

        portal_colors = {
            "mis": "blue", "sao": "green",
            "guidance": "orange", "registrar": "purple",
        }
        style_map = {
            "blue":   "pipelineFeatureBlue",
            "green":  "pipelineFeatureGreen",
            "orange": "pipelineFeatureOrange",
            "purple": "pipelineFeaturePurple",
        }

        for i, col in enumerate(sorted(all_columns)):
            color = "blue"
            for portal, data in store.portals.items():
                if data and col in data["headers"]:
                    color = portal_colors.get(portal, "blue")
                    break
            pill = QLabel(col)
            pill.setObjectName(style_map.get(color, "pipelineFeatureBlue"))
            pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._tags_grid.addWidget(pill, i // 4, i % 4)

        total_rows = sum(
            (d["row_count"] if d else 0) for d in store.portals.values()
        )
        self._pipeline_preview_meta.setText(
            f"{total_rows:,} records across {store.ready_count()} portals"
            f" · {len(all_columns)} features"
        )

        if store.ready_count() > 0:
            self._download_btn.setEnabled(store.unified_dataset is not None)

    # ==================================================================
    # DATASTORE LISTENER
    # ==================================================================

    def _on_store_updated(self, key: str):
        if key in ("system_config", "all"):
            if hasattr(self, "_sem_pill_lbl"):
                self._sem_pill_lbl.setText(f"{SystemConfig.term_label()}  ▾")
        self._refresh_source_panel()
        self._refresh_pipeline_gate()
        self._refresh_pipeline_quality()

    # ==================================================================
    # STYLES
    # ==================================================================

    def _apply_styles(self):
        self.setStyleSheet(MERGE_PIPELINE_STYLESHEET)

    # ==================================================================
    # CLEANUP
    # ==================================================================

    def closeEvent(self, event):
        DataStore.get().remove_listener(self._on_store_updated)
        super().closeEvent(event)