from pathlib import Path

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
    QMessageBox,
    QFileDialog, 
    QLineEdit,
    QDialog
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPropertyAnimation, QEasingCurve, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen

from services.data_store import DataStore
from services.merge_engine import MergeEngine, UNIFIED_COLUMNS
from services.pipeline_orchestrator import PipelineOrchestrator
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
# FULL DATASET VIEWER DIALOG
# =====================================

class FullDatasetDialog(QDialog):
    """Full-screen modal dialog for viewing the complete unified dataset."""

    def __init__(self, headers, rows, parent=None):
        super().__init__(parent)
        self._headers = headers
        self._all_rows = list(rows)  # ← FORCE TO LIST
        self._filtered_rows = list(rows)  # ← FORCE TO LIST

        # Full-screen, frameless, modal
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)

        # Match parent window size
        if parent:
            self.resize(parent.window().size())
        else:
            self.resize(1400, 900)

        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        # Dark card container (the actual visible part)
        self._container = QFrame(self)  # ← CHANGED: container → self._container
        self._container.setObjectName("datasetDialogContainer")
        self._container.setGeometry(self.rect().adjusted(40, 40, -40, -40))

        layout = QVBoxLayout(self._container)  # ← CHANGED: container → self._container
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        # ── Header row ─────────────────────────────────────────────
        header_row = QHBoxLayout()
        header_row.setSpacing(16)

        title = QLabel("📋 Unified Dataset")
        title.setObjectName("datasetDialogTitle")
        self._title_lbl = title   # stored so _setup_title() can update it

        self._row_count_lbl = QLabel(f"{len(self._all_rows):,} rows")
        self._row_count_lbl.setObjectName("datasetDialogCount")

        self._search = QLineEdit()
        self._search.setObjectName("datasetDialogSearch")
        self._search.setPlaceholderText("🔍 Search by any column…")
        self._search.textChanged.connect(self._on_search)

        header_row.addWidget(title)
        header_row.addWidget(self._row_count_lbl)
        header_row.addStretch()
        header_row.addWidget(self._search, 1)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("datasetDialogCloseBtn")
        close_btn.setFixedSize(36, 36)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.close)

        header_row.addWidget(close_btn)
        layout.addLayout(header_row)

        # ── Table ──────────────────────────────────────────────────
        self._table = QTableWidget()
        self._table.setObjectName("datasetDialogTable")
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setHighlightSections(False)
        self._table.setShowGrid(False)
        self._table.setColumnCount(len(self._headers))
        self._table.setHorizontalHeaderLabels(self._headers)

        self._populate_table(self._filtered_rows)
        layout.addWidget(self._table, 1)

        # ── Footer ─────────────────────────────────────────────────
        footer = QHBoxLayout()
        self._status_lbl = QLabel(f"Showing all {len(self._all_rows):,} rows")
        self._status_lbl.setObjectName("datasetDialogStatus")
        footer.addWidget(self._status_lbl)
        footer.addStretch()

        export_btn = QPushButton("⬇ Export Filtered CSV")
        export_btn.setObjectName("datasetDialogExportBtn")
        export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        export_btn.clicked.connect(self._export_csv)
        footer.addWidget(export_btn)

        layout.addLayout(footer)

    def resizeEvent(self, event):
        """Keep container centered on resize."""
        super().resizeEvent(event)
        if hasattr(self, '_container') and self._container:
            self._container.setGeometry(self.rect().adjusted(40, 40, -40, -40))

    def _setup_title(self, title: str):
        """Update the dialog title label after construction."""
        # The title QLabel is the first widget in the header_row layout
        # which is the first layout item inside self._container's layout
        try:
            container_layout = self._container.layout()
            header_layout = container_layout.itemAt(0).layout()
            title_lbl = header_layout.itemAt(0).widget()
            if isinstance(title_lbl, QLabel):
                title_lbl.setText(title)
        except Exception:
            pass  # title update is cosmetic — never crash for this

    def _populate_table(self, rows):
        self._table.setRowCount(len(rows))
        for row_i, row in enumerate(rows):
            for col_i, cell in enumerate(row):
                value = str(cell).strip()
                is_empty = value == ""
                item = QTableWidgetItem("—" if is_empty else value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                if is_empty:
                    item.setForeground(QColor("#f5b335"))
                    item.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
                self._table.setItem(row_i, col_i, item)

        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)

    def _on_search(self, text: str):
        try:
            text = text.lower().strip()
            print(f">>> Search text: '{text}'")
            
            if not text:
                self._filtered_rows = list(self._all_rows)  # force list copy
            else:
                self._filtered_rows = []
                for row_idx, row in enumerate(self._all_rows):
                    try:
                        # Defensive: ensure row is iterable
                        if not hasattr(row, '__iter__'):
                            print(f"  ✗ Row {row_idx} is not iterable: {type(row)} = {row}")
                            continue
                        
                        row_matches = any(
                            text in str(cell).lower() 
                            for cell in row
                        )
                        if row_matches:
                            self._filtered_rows.append(row)
                    except Exception as row_e:
                        print(f"  ✗ Row {row_idx} failed: {row_e}")
                        print(f"     Row type: {type(row)}, content: {row[:5] if hasattr(row, '__getitem__') else row}")
                        continue

            print(f"  ✓ Filtered: {len(self._filtered_rows)} rows")
            self._populate_table(self._filtered_rows)
            self._row_count_lbl.setText(f"{len(self._filtered_rows):,} rows")
            self._status_lbl.setText(
                f"Showing {len(self._filtered_rows):,} of {len(self._all_rows):,} rows"
                if text else f"Showing all {len(self._all_rows):,} rows"
            )
        except Exception as e:
            print(f">>> SEARCH CRASH: {e}")
            import traceback
            traceback.print_exc()

    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Filtered Dataset",
            "filtered_dataset.csv",
            "CSV Files (*.csv)",
        )
        if not path:
            return
        try:
            import csv
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(self._headers)
                writer.writerows(self._filtered_rows)
            self._status_lbl.setText(f"✓ Exported to {path.split('/')[-1]}")
        except Exception as e:
            self._status_lbl.setText(f"Export failed: {e}")

    def _apply_styles(self):
        self.setStyleSheet("""
            #datasetDialogContainer {
                background-color: #13172a;
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 16px;
            }
            #datasetDialogTitle {
                color: #e8eaf0;
                font-size: 18px;
                font-weight: bold;
                background: transparent;
            }
            #datasetDialogCount {
                color: rgba(255,255,255,0.4);
                font-size: 13px;
                background: transparent;
            }
            #datasetDialogSearch {
                background-color: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 10px;
                color: #e8eaf0;
                font-size: 13px;
                padding: 10px 14px;
            }
            #datasetDialogSearch:focus {
                border-color: rgba(79,140,255,0.5);
            }
            #datasetDialogCloseBtn {
                background-color: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 10px;
                color: rgba(255,255,255,0.6);
                font-size: 16px;
                font-weight: bold;
            }
            #datasetDialogCloseBtn:hover {
                background-color: rgba(255,91,91,0.15);
                border-color: rgba(255,91,91,0.3);
                color: #ff5b5b;
            }
            #datasetDialogTable {
                background-color: transparent;
                border: none;
                color: rgba(255,255,255,0.85);
                font-size: 12px;
                alternate-background-color: rgba(255,255,255,0.02);
                selection-background-color: rgba(79,140,255,0.15);
                selection-color: white;
            }
            #datasetDialogTable QHeaderView::section {
                background-color: rgba(255,255,255,0.06);
                color: rgba(255,255,255,0.5);
                font-size: 11px;
                font-weight: bold;
                border: none;
                border-right: 1px solid rgba(255,255,255,0.06);
                padding: 10px 12px;
            }
            #datasetDialogTable QHeaderView::section:last {
                border-right: none;
            }
            #datasetDialogStatus {
                color: rgba(255,255,255,0.35);
                font-size: 12px;
                background: transparent;
            }
            #datasetDialogExportBtn {
                background-color: rgba(79,140,255,0.12);
                border: 1px solid rgba(79,140,255,0.30);
                border-radius: 8px;
                color: #6eb5ff;
                font-size: 12px;
                font-weight: 600;
                padding: 9px 18px;
            }
            #datasetDialogExportBtn:hover {
                background-color: rgba(79,140,255,0.22);
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255,255,255,0.12);
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255,255,255,0.22);
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0;
            }
        """)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        super().keyPressEvent(event)


# =====================================
# PIPELINE WORKER THREAD
# =====================================

class PipelineWorker(QThread):
    """Background worker for the full ML pipeline."""

    step_started     = pyqtSignal(str, str)   # step_name, message
    step_progress    = pyqtSignal(int)         # 0-100
    finished_success = pyqtSignal(dict)        # results
    finished_error   = pyqtSignal(str)         # error message

    def __init__(self, parent=None):
        super().__init__(parent)
        self.orchestrator = PipelineOrchestrator()

    def run(self):
        try:
            store = DataStore.get()

            # Use the already-merged unified dataset
            unified = store.unified_dataset
            if unified is None:
                raise ValueError(
                    "No unified dataset found. "
                    "Please run the Data Merge step first."
                )

            temp_path = Path("outputs/_unified_temp.csv")
            temp_path.parent.mkdir(parents=True, exist_ok=True)

            # unified_dataset may be a dict {"headers": [...], "rows": [...]}
            # or a DataFrame — handle both
            import pandas as pd
            if isinstance(unified, dict):
                df = pd.DataFrame(unified["rows"], columns=unified["headers"])
            else:
                df = unified
            df.to_csv(temp_path, index=False)

            def on_step(step, msg):
                self.step_started.emit(step, msg)

            results = self.orchestrator.run(
                excel_path=str(temp_path),
                required_columns=None,
                target_column="risk_label",
                risk_based_on=None,
                model_type="random_forest",
                save_path="outputs",
                on_step=on_step,
            )
            self.finished_success.emit(results)
        except Exception as e:
            self.finished_error.emit(str(e))


# =====================================
# SPINNER WIDGET
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
        if hasattr(self, "_timer"):
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

        track = QPen(QColor(255, 255, 255, 15))
        track.setWidth(3)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track)
        painter.drawEllipse(rect)

        arc = QPen(self._color)
        arc.setWidth(3)
        arc.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc)
        painter.drawArc(rect, -self._angle * 16, -120 * 16)


# =====================================
# SHARED HELPERS
# =====================================

def _divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: rgba(255,255,255,0.06); margin: 0;")
    return line


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
        self._pipeline_engineered_dataset = None   # set after pipeline success
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

        # ── Shared header ─────────────────────────────────────────────
        self.fixed_header_container = self._build_shared_header()
        self.main_layout.addWidget(self.fixed_header_container)

        # ══════════════════════════════════════════════════════════════
        # SECTION A — DATA MERGE
        # ══════════════════════════════════════════════════════════════
        self.main_layout.addWidget(self._section_divider("🔀", "SECTION A", "Data Merge"))

        # Source readiness
        self.main_layout.addWidget(self._build_source_panel())

        # Merge config + run
        self.main_layout.addWidget(self._build_merge_config_card())

        # Results (hidden until merge runs)
        self._merge_results_stack = QStackedWidget()
        self._merge_results_stack.addWidget(QWidget())          # placeholder
        self._merge_results_stack.addWidget(self._build_merge_results_section())
        self._merge_results_stack.setCurrentIndex(0)
        self.main_layout.addWidget(self._merge_results_stack)

        # ══════════════════════════════════════════════════════════════
        # SECTION B — DATA PIPELINE
        # ══════════════════════════════════════════════════════════════
        self.main_layout.addWidget(self._section_divider("⚙️", "SECTION B", "Data Pipeline"))

        # Gate banner (shown when merge not yet done)
        self._pipeline_gate_banner = self._build_pipeline_gate_banner()
        self.main_layout.addWidget(self._pipeline_gate_banner)

        # Pipeline content (stages, quality, log)
        self._pipeline_content = QWidget()
        pipeline_content_layout = QVBoxLayout(self._pipeline_content)
        pipeline_content_layout.setContentsMargins(0, 0, 0, 0)
        pipeline_content_layout.setSpacing(20)

        # Description + Run button row
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
        self._run_pipeline_btn.setEnabled(False)       # locked until merge done
        self._run_pipeline_btn.clicked.connect(self._run_pipeline)

        preprocess_row.addLayout(preprocess_left, 1)
        preprocess_row.addWidget(
            self._run_pipeline_btn, 0, Qt.AlignmentFlag.AlignTop
        )
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

    # ------------------------------------------------------------------
    # Shared header (replaces both pages' individual headers)
    # ------------------------------------------------------------------

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

        title = QLabel("Data Merge & Pipeline Center")
        title.setObjectName("mergeTitle")

        sub = QLabel(
            "Unify all portal datasets into one clean dataset, "
            "then run the preprocessing pipeline for model training"
        )
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

        semester_pill = QLabel("1st Semester 2024–25  ▾")
        semester_pill.setObjectName("pipelineSemesterPill")

        run_pred_btn = QPushButton("Run Prediction")
        run_pred_btn.setObjectName("mergeRunPredBtn")
        run_pred_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        run_pred_btn.setFixedWidth(130)
        run_pred_btn.clicked.connect(self.on_run_prediction)

        pill_layout.addWidget(dot)
        pill_layout.addWidget(status_lbl)
        pill_layout.addSpacing(8)
        pill_layout.addWidget(semester_pill)
        pill_layout.addWidget(run_pred_btn)
        row.addWidget(model_pill)
        layout.addLayout(row)
        return container

    # ------------------------------------------------------------------
    # Section divider (visual separator between A and B)
    # ------------------------------------------------------------------

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
            ("Join Type",     "Left Join",               "⊕"),
            ("Master Source", "MIS Portal",              "★"),
            ("Join Key",      "Student ID",              "🔑"),
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
        self._view_full_btn.setEnabled(False)  # enabled after merge

        print(f">>> View Full Dataset button created, enabled={self._view_full_btn.isEnabled()}")

        self._save_btn = QPushButton("💾  Save Unified Dataset")
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
        """
        Banner shown at the top of the pipeline section when the
        unified dataset is not yet available.
        """
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
            ("📥", "Ingest",     "read_excel"),
            ("🧹", "Clean",      "handle_missing"),
            ("🔗", "Merge",      "remove_duplicates"),
            ("📐", "Normalize",  "scale_numerical"),
            ("✅", "Ready",      "save_outputs"),
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

        # ── Title row with View Dataset button ────────────────────────
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

    # ------------------------------------------------------------------
    # Metric row helper (pipeline quality report)
    # ------------------------------------------------------------------

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

        # Rebuild quality cards
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

        # Show results section
        self._merge_results_stack.setCurrentIndex(1)

        # ── Unlock pipeline ──────────────────────────────────────────
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
        dialog.exec()  # blocks until closed — no GC issues


    # ==================================================================
    # SECTION B — LOGIC
    # ==================================================================

    def _refresh_pipeline_gate(self):
        """
        Show/hide the gate banner and enable/disable the Run Pipeline
        button depending on whether a unified dataset exists.
        """
        store        = DataStore.get()
        merge_done   = store.unified_dataset is not None

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
            QMessageBox.warning(
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
        pass  # extend if you add a global progress bar

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

        store      = DataStore.get()
        ml_service = results.get("model")
        if ml_service:
            store.set_trained_model(ml_service)

        metrics = results.get("training_metrics", {})

        # ── Store the engineered dataset for the view button ──────────────────
        eng_headers = results.get("engineered_headers", [])
        eng_rows    = results.get("engineered_rows", [])
        if eng_headers and eng_rows:
            # Keep a page-level reference so the view button always shows
            # the last pipeline run, independent of DataStore state
            self._pipeline_engineered_dataset = {
                "headers": eng_headers,
                "rows":    eng_rows,
            }
            # Also update DataStore so prediction engine gets engineered columns
            store.set_unified_dataset({
                "headers": eng_headers,
                "rows":    eng_rows,
            })
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

        QMessageBox.information(
            self,
            "Pipeline Complete",
            f"Model trained successfully!\n\n"
            f"Accuracy: {metrics.get('accuracy', 'N/A')}\n"
            f"Features: {len(eng_headers) - 1} engineered columns\n"
            f"Rows: {len(eng_rows):,}\n\n"
            f"Click '👁 View Engineered Dataset' to inspect the result.",
        )

    def _on_pipeline_error(self, error: str):
        self._pipeline_log.append(f"❌ ERROR: {error}")
        self._refresh_pipeline_gate()
        QMessageBox.critical(self, "Pipeline Error", error)

    def _on_view_pipeline_dataset(self):
        """Open the engineered dataset (post-pipeline) in the full viewer dialog."""
        dataset = getattr(self, "_pipeline_engineered_dataset", None)

        if not dataset:
            QMessageBox.information(
                self,
                "No Engineered Dataset",
                "Run the pipeline first to generate the engineered dataset.",
            )
            return

        headers = dataset.get("headers", [])
        rows    = dataset.get("rows", [])

        if not headers or not rows:
            QMessageBox.information(
                self, "Empty Dataset", "The engineered dataset is empty."
            )
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

        store = DataStore.get()
        unified = store.unified_dataset
        if unified is None:
            QMessageBox.warning(self, "No Data", "No unified dataset available.")
            return

        try:
            import pandas as pd
            if isinstance(unified, dict):
                df = pd.DataFrame(unified["rows"], columns=unified["headers"])
            else:
                df = unified
            df.to_csv(path, index=False)
            QMessageBox.information(self, "Saved", f"Saved to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def _refresh_pipeline_quality(self):
        """Rebuild the pipeline quality report from current DataStore state."""
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

        # Column tags
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
        self._refresh_source_panel()
        self._refresh_pipeline_gate()
        self._refresh_pipeline_quality()

    # ==================================================================
    # STYLES
    # ==================================================================

    def _apply_styles(self):
        self.setStyleSheet("""
            /* ── Cards ────────────────────────────────────────────────── */
            #mergeHeaderCard {
                background-color: #13172a;
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 16px;
            }
            #mergeCard, #pipelineCard {
                background-color: #13172a;
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 16px;
            }

            /* ── Card internals ────────────────────────────────────────── */
            #mergeCardTitleBar { background: transparent; }
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

            /* ── Header ────────────────────────────────────────────────── */
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
            #mergeModelPill {
                background-color: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 12px;
            }
            #pipelineSemesterPill {
                color: rgba(255,255,255,0.5);
                font-size: 12px;
                background: transparent;
            }

            /* ── Section divider ───────────────────────────────────────── */
            #sectionTag {
                color: rgba(255,255,255,0.25);
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1.4px;
                background: transparent;
            }
            #sectionDividerTitle {
                color: #e8eaf0;
                font-size: 13px;
                font-weight: bold;
                background: transparent;
            }

            /* ── Gate banner ───────────────────────────────────────────── */
            #pipelineGateBanner {
                background-color: rgba(245,179,53,0.07);
                border: 1px solid rgba(245,179,53,0.25);
                border-radius: 12px;
            }
            #pipelineGateMsg {
                color: rgba(255,255,255,0.65);
                font-size: 13px;
                background: transparent;
            }

            /* ── Source rows ───────────────────────────────────────────── */
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

            /* ── Progress bar ──────────────────────────────────────────── */
            #mergeSourceProgress {
                background-color: rgba(255,255,255,0.08);
                border-radius: 3px;
                border: none;
            }
            #mergeSourceProgress::chunk {
                background-color: #4f8cff;
                border-radius: 3px;
            }

            /* ── Merge config tiles ────────────────────────────────────── */
            #mergeConfigTile {
                background-color: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
            }
            #mergeExpectedLabel {
                color: rgba(255,255,255,0.4);
                font-size: 12px;
                background: transparent;
            }

            /* ── Run prediction button ─────────────────────────────────── */
            #mergeRunPredBtn {
                background-color: rgba(79,140,255,0.15);
                border: 1px solid rgba(79,140,255,0.30);
                border-radius: 8px;
                color: #6eb5ff;
                font-size: 12px;
                font-weight: 600;
                padding: 8px 16px;
            }
            #mergeRunPredBtn:hover { background-color: rgba(79,140,255,0.25); }

            /* ── Quality bar ───────────────────────────────────────────── */
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
            #mergeQualityProgress::chunk { border-radius: 4px; }

            /* ── Preview table ─────────────────────────────────────────── */
            #mergeTableContainer { background: transparent; }
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
            #mergeTable QHeaderView::section:last { border-right: none; }
            #mergeTable QScrollBar:vertical {
                background: transparent; width: 8px;
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
                background: transparent; height: 8px;
            }
            #mergeTable QScrollBar::handle:horizontal {
                background: rgba(255,255,255,0.15);
                border-radius: 4px;
            }
            #mergeTable QScrollBar::add-line:vertical,
            #mergeTable QScrollBar::sub-line:vertical,
            #mergeTable QScrollBar::add-line:horizontal,
            #mergeTable QScrollBar::sub-line:horizontal {
                height: 0; width: 0;
            }

            /* ── Merge log ─────────────────────────────────────────────── */
            #mergeLogContainer { background: transparent; }
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

            /* ── Result footer ─────────────────────────────────────────── */
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

            /* ── Buttons ───────────────────────────────────────────────── */
            #mergeSaveBtn {
                background-color: rgba(52,211,153,0.10);
                border: 1px solid rgba(52,211,153,0.35);
                border-radius: 8px;
                color: #34d399;
                font-size: 12px;
                font-weight: 600;
                padding: 9px 20px;
            }
            #mergeSaveBtn:hover { background-color: rgba(52,211,153,0.20); }

            #mergeProceedBtn {
                background-color: #4f8cff;
                border: none;
                border-radius: 8px;
                color: white;
                font-size: 12px;
                font-weight: 600;
                padding: 9px 20px;
            }
            #mergeProceedBtn:hover {
                background-color: rgba(79,140,255,0.85);
            }

            #pipelineDownloadBtn {
                background-color: rgba(79,140,255,0.10);
                border: 1px solid rgba(79,140,255,0.30);
                border-radius: 8px;
                color: #6eb5ff;
                font-size: 12px;
                font-weight: 600;
                padding: 9px 20px;
            }
            #pipelineDownloadBtn:hover { background-color: rgba(79,140,255,0.20); }
            #pipelineDownloadBtn:disabled {
                background-color: rgba(255,255,255,0.03);
                border-color: rgba(255,255,255,0.08);
                color: rgba(255,255,255,0.2);
            }

            #pipelineViewDatasetBtn {
                background-color: rgba(167,139,250,0.10);
                border: 1px solid rgba(167,139,250,0.30);
                border-radius: 8px;
                color: #a78bfa;
                font-size: 11px;
                font-weight: 600;
                padding: 0 14px;
            }
            #pipelineViewDatasetBtn:hover { background-color: rgba(167,139,250,0.20); }
            #pipelineViewDatasetBtn:disabled {
                background-color: rgba(255,255,255,0.03);
                border-color: rgba(255,255,255,0.08);
                color: rgba(255,255,255,0.2);
            }

            /* ── Stat tiles ────────────────────────────────────────────── */
            #mergeStatTile {
                background-color: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
            }

            /* ── Pipeline section titles ───────────────────────────────── */
            #pipelineSectionTitle {
                color: #e8eaf0;
                font-size: 15px;
                font-weight: bold;
                background: transparent;
            }
            #pipelineSectionDesc {
                color: rgba(255,255,255,0.4);
                font-size: 12px;
                background: transparent;
            }

            /* ── Pipeline card titles ──────────────────────────────────── */
            #pipelineCardTitle {
                color: rgba(255,255,255,0.35);
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1.2px;
                background: transparent;
            }

            /* ── Pipeline stages ───────────────────────────────────────── */
            #pipelineStage {
                background-color: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
            }
            #pipelineStageIcon {
                font-size: 24px;
                color: rgba(255,255,255,0.4);
                background: transparent;
            }
            #pipelineStageLabel {
                color: rgba(255,255,255,0.5);
                font-size: 11px;
                background: transparent;
            }
            #pipelineStageArrow {
                color: rgba(255,255,255,0.2);
                font-size: 20px;
                background: transparent;
            }

            /* ── Pipeline quality metrics ──────────────────────────────── */
            #pipelineMetricLabel {
                color: rgba(255,255,255,0.55);
                font-size: 12px;
                background: transparent;
            }
            #pipelineMetricValue {
                color: rgba(255,255,255,0.7);
                font-size: 12px;
                background: transparent;
            }
            #pipelineQualityFooter {
                color: rgba(255,255,255,0.4);
                font-size: 12px;
                background: transparent;
            }

            /* ── Pipeline dataset preview ──────────────────────────────── */
            #pipelinePreviewMeta {
                color: rgba(255,255,255,0.4);
                font-size: 12px;
                background: transparent;
            }
            #pipelineFeatureBlue {
                color: #4f8cff;
                background-color: rgba(79,140,255,0.10);
                border: 1px solid rgba(79,140,255,0.25);
                border-radius: 6px;
                font-size: 11px;
                padding: 3px 8px;
            }
            #pipelineFeatureGreen {
                color: #34d399;
                background-color: rgba(52,211,153,0.10);
                border: 1px solid rgba(52,211,153,0.25);
                border-radius: 6px;
                font-size: 11px;
                padding: 3px 8px;
            }
            #pipelineFeatureOrange {
                color: #f59e0b;
                background-color: rgba(245,158,11,0.10);
                border: 1px solid rgba(245,158,11,0.25);
                border-radius: 6px;
                font-size: 11px;
                padding: 3px 8px;
            }
            #pipelineFeaturePurple {
                color: #a78bfa;
                background-color: rgba(167,139,250,0.10);
                border: 1px solid rgba(167,139,250,0.25);
                border-radius: 6px;
                font-size: 11px;
                padding: 3px 8px;
            }

            /* ── Global scrollbars ─────────────────────────────────────── */
            QScrollBar:vertical { background: transparent; width: 8px; }
            QScrollBar::handle:vertical {
                background: rgba(255,255,255,0.10);
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255,255,255,0.20);
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical { height: 0; }
        """)

    # ==================================================================
    # CLEANUP
    # ==================================================================

    def closeEvent(self, event):
        DataStore.get().remove_listener(self._on_store_updated)
        super().closeEvent(event)