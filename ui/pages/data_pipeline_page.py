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
    QGridLayout,
    QScrollArea,
    QMessageBox,
    QTextEdit,
    QFileDialog
)
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QIcon

from ui.components.loading_overlay import LoadingOverlay
from ui.mixins.prediction_mixin import PredictionMixin
from services.data_store import DataStore
from ui.components.readiness_panel import ReadinessPanelWidget
from services.pipeline_service import PipelineOrchestrator


# =============================================================================
# BACKGROUND WORKER
# =============================================================================

class PipelineWorker(QThread):
    """Background worker for the full ML pipeline."""
    
    step_started = pyqtSignal(str, str)      # step_name, message
    step_progress = pyqtSignal(int)          # 0-100
    finished_success = pyqtSignal(dict)      # results
    finished_error = pyqtSignal(str)         # error message

    def __init__(self, parent=None):
        super().__init__(parent)
        self.orchestrator = PipelineOrchestrator()

    def run(self):
        try:
            import pandas as pd

            store = DataStore.get()
            unified = store.unified_dataset
            if unified is None:
                raise ValueError(
                    "No unified dataset found. "
                    "Complete Data Merge on the Data Merge & Pipeline page first."
                )

            temp_path = Path("outputs/_unified_temp.csv")
            temp_path.parent.mkdir(parents=True, exist_ok=True)
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
                on_step=on_step
            )
            self.finished_success.emit(results)
        except Exception as e:
            self.finished_error.emit(str(e))


# =============================================================================
# DATA PIPELINE PAGE
# =============================================================================

class DataPipelinePage(PredictionMixin, QWidget):
    """Data Pipeline page — live preprocessing workflow, quality report, and logs."""

    def __init__(self):
        super().__init__()
        self._pipeline_worker: PipelineWorker | None = None
        self.setup_ui()
        self.overlay = LoadingOverlay(self)
        self._refresh_from_store()

    # ------------------------------------------------------------------
    # UI BUILDERS
    # ------------------------------------------------------------------

    def _create_metric_row(self, label, value, color, display_value=None):
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
        pct.setFixedWidth(40)
        pct.setAlignment(Qt.AlignmentFlag.AlignRight)

        layout.addWidget(name)
        layout.addWidget(bar, 1)
        layout.addWidget(pct)
        return row

    def _create_pipeline_stages(self):
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
        self._stage_arrows = []

        stages = [
            ("📥", "Ingest", "read_excel"),
            ("🧹", "Clean", "handle_missing"),
            ("🔗", "Merge", "remove_duplicates"),
            ("📐", "Normalize", "scale_numerical"),
            ("✅", "Ready", "save_outputs"),
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

    def _create_quality_report_card(self):
        card = QFrame()
        card.setObjectName("pipelineCard")
        self._quality_layout = QVBoxLayout(card)
        self._quality_layout.setContentsMargins(24, 20, 24, 20)
        self._quality_layout.setSpacing(14)

        title = QLabel("DATA QUALITY REPORT")
        title.setObjectName("pipelineCardTitle")
        self._quality_layout.addWidget(title)

        # Dynamic metrics container
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

    def _create_dataset_preview_card(self):
        card = QFrame()
        card.setObjectName("pipelineCard")
        self._preview_layout = QVBoxLayout(card)
        self._preview_layout.setContentsMargins(24, 20, 24, 20)
        self._preview_layout.setSpacing(14)

        title = QLabel("UNIFIED DATASET PREVIEW")
        title.setObjectName("pipelineCardTitle")
        self._preview_layout.addWidget(title)

        self._preview_meta = QLabel("No data loaded yet")
        self._preview_meta.setObjectName("pipelinePreviewMeta")
        self._preview_layout.addWidget(self._preview_meta)

        # Tags grid
        self._tags_grid = QGridLayout()
        self._tags_grid.setHorizontalSpacing(8)
        self._tags_grid.setVerticalSpacing(8)
        self._tags_host = QWidget()
        self._tags_host.setLayout(self._tags_grid)
        self._preview_layout.addWidget(self._tags_host)

        self._download_btn = QPushButton("↓  Download Unified CSV")
        self._download_btn.setObjectName("pipelineDownloadBtn")
        self._download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._download_btn.setEnabled(False)
        self._download_btn.clicked.connect(self._download_unified_csv)
        self._preview_layout.addWidget(self._download_btn)

        return card

    def _create_pipeline_log_card(self):
        card = QFrame()
        card.setObjectName("pipelineCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(12)

        title = QLabel("PIPELINE LOG")
        title.setObjectName("pipelineCardTitle")
        layout.addWidget(title)

        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setPlaceholderText("Click 'Run Pipeline' to start...")
        self._log_text.setMaximumHeight(220)
        self._log_text.setStyleSheet("""
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
        layout.addWidget(self._log_text)

        return card

    # ------------------------------------------------------------------
    # PIPELINE ACTIONS
    # ------------------------------------------------------------------

    def _run_pipeline(self):
        """Execute the full ML pipeline."""
        store = DataStore.get()

        if store.unified_dataset is None:
            QMessageBox.warning(
                self,
                "Merge Required",
                "Run Data Merge on the Data Merge & Pipeline page first.",
            )
            return

        self._run_pipeline_btn.setEnabled(False)
        self._download_btn.setEnabled(False)
        self._log_text.clear()

        self._pipeline_worker = PipelineWorker(self)
        self._pipeline_worker.step_started.connect(self._on_pipeline_step)
        self._pipeline_worker.step_progress.connect(self._on_pipeline_progress)
        self._pipeline_worker.finished_success.connect(self._on_pipeline_success)
        self._pipeline_worker.finished_error.connect(self._on_pipeline_error)
        self._pipeline_worker.start()

    def _on_pipeline_step(self, step: str, message: str):
        """Update log and highlight active stage."""
        timestamp = QTimer.currentTime().toString() if hasattr(QTimer, 'currentTime') else "Now"
        self._log_text.append(f"[{step}] {message}")

        # Highlight active stage
        step_map = {
            "read_excel": 0, "validate": 0,
            "remove_duplicates": 1, "handle_missing": 1,
            "encode_categorical": 2, "scale_numerical": 2,
            "generate_labels": 3, "prepare_features": 3,
            "train_model": 3, "save_outputs": 4,
        }
        stage_idx = step_map.get(step, -1)
        self._highlight_stage(stage_idx)

    def _on_pipeline_progress(self, progress: int):
        """Update progress bar if you add one."""
        pass

    def _highlight_stage(self, active_idx: int):
        """Highlight the currently active pipeline stage."""
        for i, (stage, icon, name, key) in enumerate(self._stage_widgets):
            if i == active_idx:
                stage.setStyleSheet("""
                    #pipelineStage {
                        background-color: rgba(79, 140, 255, 0.15);
                        border: 1px solid rgba(79, 140, 255, 0.4);
                        border-radius: 10px;
                    }
                """)
                icon.setStyleSheet("font-size: 24px; color: #4f8cff;")
            elif i < active_idx:
                stage.setStyleSheet("""
                    #pipelineStage {
                        background-color: rgba(52, 211, 153, 0.1);
                        border: 1px solid rgba(52, 211, 153, 0.3);
                        border-radius: 10px;
                    }
                """)
                icon.setStyleSheet("font-size: 24px; color: #34d399;")
            else:
                stage.setStyleSheet("""
                    #pipelineStage {
                        background-color: rgba(255, 255, 255, 0.03);
                        border: 1px solid rgba(255, 255, 255, 0.08);
                        border-radius: 10px;
                    }
                """)
                icon.setStyleSheet("font-size: 24px; color: rgba(255,255,255,0.4);")

    def _on_pipeline_success(self, results: dict):
        """Handle successful pipeline completion."""
        self._highlight_stage(4)  # All stages complete

        store = DataStore.get()
        ml_service = results.get("model")
        if ml_service:
            store.set_trained_model(ml_service)

        metrics = results.get("training_metrics", {})
        summary = results.get("pipeline_summary", {})

        self._log_text.append("✅ Pipeline completed successfully!")
        self._log_text.append(f"Accuracy: {metrics.get('accuracy', 'N/A')}")
        self._log_text.append(f"CV Score: {metrics.get('cv_mean', 'N/A')} ± {metrics.get('cv_std', 'N/A')}")

        self._refresh_from_store()
        self._run_pipeline_btn.setEnabled(True)
        self._download_btn.setEnabled(True)

        QMessageBox.information(
            self, "Pipeline Complete",
            f"Model trained successfully!\n\n"
            f"Accuracy: {metrics.get('accuracy', 'N/A')}\n"
            f"Saved to: outputs/"
        )

    def _on_pipeline_error(self, error: str):
        """Handle pipeline error."""
        self._log_text.append(f"❌ ERROR: {error}")
        self._run_pipeline_btn.setEnabled(True)
        QMessageBox.critical(self, "Pipeline Error", error)

    def _download_unified_csv(self):
        """Download the unified dataset CSV."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Unified Dataset", "unified_dataset.csv",
            "CSV Files (*.csv)"
        )
        if not path:
            return

        import pandas as pd

        store = DataStore.get()
        unified = store.unified_dataset
        if unified is None:
            QMessageBox.warning(self, "No Data", "No unified dataset available.")
            return

        if isinstance(unified, dict):
            pd.DataFrame(unified["rows"], columns=unified["headers"]).to_csv(
                path, index=False
            )
        else:
            unified.to_csv(path, index=False)
        QMessageBox.information(self, "Saved", f"Saved to:\n{path}")

    # ------------------------------------------------------------------
    # REFRESH FROM DATA STORE
    # ------------------------------------------------------------------

    def _refresh_from_store(self):
        """Update UI with live data from DataStore."""
        store = DataStore.get()

        # Update quality metrics
        self._update_quality_metrics(store)

        # Update preview
        self._update_preview(store)

    def _update_quality_metrics(self, store: DataStore):
        """Rebuild quality metrics from actual portal data."""
        # Clear old metrics
        while self._metrics_container.count():
            item = self._metrics_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        readiness = store.get_readiness()
        total_rows = 0
        quality_score = 0

        for portal, ready in readiness.items():
            data = store.get_portal(portal)
            if data:
                rows = data["row_count"]
                total_rows += rows
                completeness = 100  # Simplified
                color = "#34d399" if ready else "#6b7280"
                self._metrics_container.addWidget(
                    self._create_metric_row(
                        f"{portal.upper()} completeness",
                        100 if ready else 0,
                        color,
                        f"{rows:,} rows" if ready else "Missing"
                    )
                )

        # Duplicate detection (simplified)
        self._metrics_container.addWidget(
            self._create_metric_row("Duplicate rows", 0, "#6b7280", "0")
        )

        self._quality_footer.setText(
            f'Data quality score: <span id="pipelineQualityScore">{store.ready_count() * 25}/100</span>'
            f" — {store.ready_count()}/4 portals ready"
        )

    def _update_preview(self, store: DataStore):
        """Update dataset preview with actual columns."""
        # Clear old tags
        while self._tags_grid.count():
            item = self._tags_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        all_columns = set()
        for portal in ["mis", "sao", "guidance", "registrar"]:
            data = store.get_portal(portal)
            if data:
                all_columns.update(data["headers"])

        # Color mapping
        portal_colors = {
            "mis": "blue", "sao": "green",
            "guidance": "orange", "registrar": "purple"
        }

        style_map = {
            "blue": "pipelineFeatureBlue",
            "green": "pipelineFeatureGreen",
            "orange": "pipelineFeatureOrange",
            "purple": "pipelineFeaturePurple",
        }

        for i, col in enumerate(sorted(all_columns)):
            # Determine color based on which portal has this column
            color = "blue"
            for portal, data in store.portals.items():
                if data and col in data["headers"]:
                    color = portal_colors.get(portal, "blue")
                    break

            pill = QLabel(col)
            pill.setObjectName(style_map.get(color, "pipelineFeatureBlue"))
            pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._tags_grid.addWidget(pill, i // 4, i % 4)

        # Update meta
        total_rows = sum(
            (d["row_count"] if d else 0)
            for d in store.portals.values()
        )
        self._preview_meta.setText(
            f"{total_rows:,} records across {store.ready_count()} portals"
            f" · {len(all_columns)} features"
        )

        if store.ready_count() > 0:
            self._download_btn.setEnabled(True)

    # ------------------------------------------------------------------
    # NAVIGATION
    # ------------------------------------------------------------------

    def _navigate_to_merge(self):
        pass

    # ------------------------------------------------------------------
    # MAIN SETUP
    # ------------------------------------------------------------------

    def setup_ui(self):
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(30, 30, 30, 30)
        self.main_layout.setSpacing(20)

        # =====================================
        # FIXED HEADER
        # =====================================

        self.fixed_header_container = QFrame()
        self.fixed_header_container.setObjectName("fixedHeaderContainer")
        fixed_header_layout = QVBoxLayout()
        fixed_header_layout.setContentsMargins(20, 20, 20, 20)
        fixed_header_layout.setSpacing(0)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(15)

        header_text_layout = QVBoxLayout()
        header_text_layout.setSpacing(5)

        header = QLabel("Data Pipeline")
        header.setObjectName("header")

        subheader = QLabel("Academic Year 2024–2025")
        subheader.setObjectName("subHeader")

        header_text_layout.addWidget(header)
        header_text_layout.addWidget(subheader)

        header_layout.addLayout(header_text_layout)
        header_layout.addStretch()

        model_card = QFrame()
        model_card.setObjectName("pipelineModelCard")

        model_layout = QHBoxLayout()
        model_layout.setContentsMargins(20, 15, 20, 15)
        model_layout.setSpacing(12)

        model_status = QLabel("● Model Active")
        model_status.setObjectName("pipelineModelStatus")

        opacity_effect = QGraphicsOpacityEffect(model_status)
        model_status.setGraphicsEffect(opacity_effect)

        status_animation = QPropertyAnimation(opacity_effect, b"opacity")
        status_animation.setDuration(1200)
        status_animation.setStartValue(1.0)
        status_animation.setKeyValueAt(0.5, 0.3)
        status_animation.setEndValue(1.0)
        status_animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        status_animation.setLoopCount(-1)
        status_animation.start()

        semester_pill = QLabel("1st Semester 2024–25  ▾")
        semester_pill.setObjectName("pipelineSemesterPill")

        run_button = QPushButton("Run Prediction")
        run_button.setObjectName("runButton")
        run_button.setCursor(Qt.CursorShape.PointingHandCursor)
        run_button.setIcon(QIcon("assets/icons/play.svg"))
        run_button.clicked.connect(self.on_run_prediction)
        run_button.setFixedWidth(130)

        model_layout.addWidget(model_status)
        model_layout.addWidget(semester_pill)
        model_layout.addWidget(run_button)

        model_card.setLayout(model_layout)
        header_layout.addWidget(model_card)

        fixed_header_layout.addLayout(header_layout)
        self.fixed_header_container.setLayout(fixed_header_layout)
        self.main_layout.addWidget(self.fixed_header_container)

        # =====================================
        # PREPROCESSING SECTION
        # =====================================

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
        self._run_pipeline_btn.clicked.connect(self._run_pipeline)

        preprocess_row.addLayout(preprocess_left, 1)
        preprocess_row.addWidget(
            self._run_pipeline_btn,
            0,
            Qt.AlignmentFlag.AlignTop,
        )
        self.main_layout.addLayout(preprocess_row)

        # Pipeline stages
        self.main_layout.addWidget(self._create_pipeline_stages())

        # Readiness panel
        self._readiness_panel = ReadinessPanelWidget(
            on_ready_callback=self._navigate_to_merge
        )
        self.main_layout.addWidget(self._readiness_panel)

        # Bottom two-column row
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(20)
        bottom_row.addWidget(self._create_quality_report_card(), 1)
        bottom_row.addWidget(self._create_dataset_preview_card(), 1)
        self.main_layout.addLayout(bottom_row)

        # Pipeline log
        self.main_layout.addWidget(self._create_pipeline_log_card())

        self.main_layout.addStretch()
        self.setLayout(self.main_layout)
        self.init_prediction()

        # Listen for DataStore changes
        DataStore.get().add_listener(self._on_store_changed)

    def _on_store_changed(self, key: str):
        """Refresh UI when any portal data changes."""
        self._refresh_from_store()

    def closeEvent(self, event):
        """Clean up listener on close."""
        DataStore.get().remove_listener(self._on_store_changed)
        super().closeEvent(event)