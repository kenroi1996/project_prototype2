from pathlib import Path

import pandas as pd
from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QComboBox, QProgressBar, QGraphicsOpacityEffect,
    QGridLayout, QTextEdit,
)
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QMargins, QThread, pyqtSignal
from PyQt6.QtGui import QIcon, QColor, QPainter
from PyQt6.QtCharts import (
    QChart, QChartView, QBarSet, QBarSeries,
    QBarCategoryAxis, QValueAxis,
)

from ui.components.loading_overlay import LoadingOverlay
from ui.mixins.prediction_mixin import PredictionMixin
from ui.dialogs.confirmation_dialog import show_error
from services.data_store import DataStore
from services.training_engine import TrainingEngine, TrainingResult
from services.system_config import SystemConfig


# =============================================================================
# BACKGROUND WORKER
# =============================================================================

class TrainingWorker(QThread):
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(object)   # emits TrainingResult
    error    = pyqtSignal(str)

    def __init__(self, test_size: float, n_folds: int = 5):
        super().__init__(parent=None)
        self.test_size = test_size
        self.n_folds   = n_folds

    def run(self):
        try:
            store = DataStore.get()

            # ── FIX: always use raw_merged_dataset as the training source ──────
            # _original_unified_dataset and unified_dataset may both be the
            # engineered output from a previous pipeline run (Final_Avg_GRD
            # already dropped), which causes define_target() to be skipped
            # and risk_label to be missing → "Training Error" on every retrain.
            #
            # raw_merged_dataset is set once after MergeEngine.merge() and is
            # never overwritten by the pipeline or prediction flow, so it always
            # contains the original columns including Final_Avg_GRD.
            unified = store.get_raw_merged_dataset()

            if unified is None:
                # Fallback: no merge has been run yet — try building from portals
                unified = store.build_unified_dataset()
                if unified is None:
                    self.error.emit(
                        "No merged dataset found.\n\n"
                        "Please run the Data Merge step first, then retrain."
                    )
                    return
                print(
                    "[TrainingWorker] WARNING: raw_merged_dataset not available. "
                    "Built unified dataset from portals directly. "
                    "If training fails with 'risk_label missing', re-run the Data Merge."
                )

            if isinstance(unified, dict):
                headers = unified.get("headers", [])
                rows    = unified.get("rows", [])
            else:
                # DataFrame
                headers = list(unified.columns)
                rows    = unified.astype(str).fillna("").values.tolist()

            if not rows:
                self.error.emit("Unified dataset is empty. Merge or upload data first.")
                return

            print(
                f"[TrainingWorker] Training source: {len(rows):,} rows × "
                f"{len(headers)} columns | "
                f"Final_Avg_GRD present: {'Final_Avg_GRD' in headers}"
            )

            # ── Delegate entirely to TrainingEngine ───────────────────────────
            # TrainingEngine owns: feature engineering, SMOTE (inside CV folds),
            # stratified CV, threshold optimisation, metrics, and ModelRegistry
            # persistence.  Nothing else should duplicate these steps.
            engine = TrainingEngine(
                headers     = headers,
                rows        = rows,
                model_id    = "rf",
                test_size   = self.test_size,
                n_folds     = self.n_folds,
                progress_cb = lambda step, pct: self.progress.emit(step, pct),
            )
            result = engine.run()

            if not result.success:
                self.error.emit("\n".join(result.errors))
                return

            self.finished.emit(result)

        except Exception as e:
            self.error.emit(str(e))


# =============================================================================
# MODEL TRAINING PAGE
# =============================================================================

class ModelTrainingPage(PredictionMixin, QWidget):
    """
    Model Training page — train a Random Forest classifier on historical
    student data, view evaluation metrics, and save the model for prediction.
    """

    def __init__(self):
        super().__init__()
        self._training_worker: TrainingWorker | None = None
        self._metric_tiles: dict = {}

        self.setup_ui()
        self.overlay = LoadingOverlay(self)
        self._check_dataset_ready()

    # ------------------------------------------------------------------
    # UI builders
    # ------------------------------------------------------------------

    def _create_metric_tile(self, value: str, label: str) -> QFrame:
        tile = QFrame()
        tile.setObjectName("mlMetricCard")
        layout = QVBoxLayout(tile)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(2)
        val = QLabel(value)
        val.setObjectName("mlMetricValue")
        lbl = QLabel(label)
        lbl.setObjectName("mlMetricLabel")
        layout.addWidget(val)
        layout.addWidget(lbl)
        return tile

    def _create_config_combo(self, label_text: str, options: list):
        label = QLabel(label_text)
        label.setObjectName("trainConfigLabel")
        combo = QComboBox()
        combo.setObjectName("trainConfigCombo")
        combo.addItems(options)
        combo.setCursor(Qt.CursorShape.PointingHandCursor)
        wrap = QFrame()
        wrap_layout = QVBoxLayout(wrap)
        wrap_layout.setContentsMargins(0, 0, 0, 0)
        wrap_layout.setSpacing(6)
        wrap_layout.addWidget(label)
        wrap_layout.addWidget(combo)
        return wrap, combo

    def _create_confusion_matrix_chart(self, tn=0, fp=0, fn=0, tp=0):
        not_at_risk = QBarSet("Actual Not-At-Risk")
        not_at_risk.setColor(QColor("#34d399"))
        not_at_risk.append([tn, fp])
        at_risk = QBarSet("Actual At-Risk")
        at_risk.setColor(QColor("#ff5b5b"))
        at_risk.append([fn, tp])
        series = QBarSeries()
        series.append(not_at_risk)
        series.append(at_risk)
        chart = QChart()
        chart.addSeries(series)
        chart.setBackgroundVisible(False)
        chart.setPlotAreaBackgroundVisible(False)
        chart.setTitle("")
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignTop)
        chart.legend().setLabelColor(QColor("#8b949e"))
        chart.setMargins(QMargins(0, 0, 0, 0))
        axis_x = QBarCategoryAxis()
        axis_x.append(["Predicted Not-At-Risk", "Predicted At-Risk"])
        axis_x.setLabelsColor(QColor("#8b949e"))
        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        series.attachAxis(axis_x)
        max_val = max(tn, fp, fn, tp, 1)
        axis_y = QValueAxis()
        axis_y.setRange(0, max_val * 1.2)
        axis_y.setTickCount(5)
        axis_y.setLabelsColor(QColor("#8b949e"))
        axis_y.setGridLineColor(QColor(255, 255, 255, 20))
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(axis_y)
        view = QChartView(chart)
        view.setRenderHint(QPainter.RenderHint.Antialiasing)
        view.setMinimumHeight(280)
        view.setStyleSheet("background: transparent; border: none;")
        return view, chart, series

    def _create_chart_card(self, title_text: str, chart_widget) -> QFrame:
        card = QFrame()
        card.setObjectName("trainPanelCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(12)
        title = QLabel(title_text)
        title.setObjectName("trainPanelTitle")
        layout.addWidget(title)
        layout.addWidget(chart_widget)
        return card

    def _create_shap_importance_card(self) -> QFrame:
        chart_host = QFrame()
        self._shap_layout = QVBoxLayout(chart_host)
        self._shap_layout.setContentsMargins(0, 0, 0, 0)
        self._shap_layout.setSpacing(10)
        placeholder = QLabel("Train a model to see feature importance")
        placeholder.setObjectName("trainLogMuted")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._shap_layout.addWidget(placeholder)
        return self._create_chart_card("FEATURE IMPORTANCE", chart_host)

    def _create_shap_row(self, label_text: str, percentage: int, color: str) -> QFrame:
        row = QFrame()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        label = QLabel(label_text)
        label.setObjectName("trainShapLabel")
        label.setFixedWidth(180)
        bar = QProgressBar()
        bar.setValue(min(percentage, 100))
        bar.setTextVisible(False)
        bar.setFixedHeight(8)
        bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: rgba(255,255,255,0.08);
                border-radius: 4px; border: none;
            }}
            QProgressBar::chunk {{
                background-color: {color}; border-radius: 4px;
            }}
        """)
        pct = QLabel(f"{percentage}%")
        pct.setObjectName("trainShapPercent")
        pct.setFixedWidth(36)
        pct.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(label)
        layout.addWidget(bar, 1)
        layout.addWidget(pct)
        return row

    # ------------------------------------------------------------------
    # Dataset status
    # ------------------------------------------------------------------

    def _check_dataset_ready(self):
        store = DataStore.get()
        # Check raw_merged_dataset first (the correct training source),
        # then fall back to unified_dataset for the row count display.
        raw = store.raw_merged_dataset  # direct attr — no warning spam
        if raw is not None or store.unified_dataset is not None or store.ready_count() > 0:
            if raw is not None:
                rows = len(raw.get("rows", []))
            elif isinstance(store.unified_dataset, dict):
                rows = len(store.unified_dataset.get("rows", []))
            elif store.unified_dataset is not None:
                rows = len(store.unified_dataset)
            else:
                rows = sum(
                    p["row_count"] for p in store.portals.values() if p
                )
            self._dataset_info_lbl.setText(
                f"✓  {store.ready_count()}/4 portals ready  ·  ~{rows:,} rows available"
            )
            self._dataset_info_lbl.setStyleSheet(
                "color: #34d399; font-size: 12px; background: transparent;"
            )
            self._start_btn.setEnabled(True)
        else:
            self._dataset_info_lbl.setText(
                "⚠  No data found. Upload from portals and run merge first."
            )
            self._dataset_info_lbl.setStyleSheet(
                "color: #f5b335; font-size: 12px; background: transparent;"
            )
            self._start_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Training actions
    # ------------------------------------------------------------------

    def _on_start_training(self):
        store = DataStore.get()
        raw   = store.raw_merged_dataset  # direct attr — no warning spam
        if raw is None and store.unified_dataset is None and store.ready_count() == 0:
            show_error(self, "No Data",
                       "No merged dataset found.\n\n"
                       "Please run the Data Merge step first, then retrain.")
            return

        split_text = self._split_combo.currentText()
        folds_text = self._folds_combo.currentText()
        test_size  = float(split_text.split("/")[1].strip().replace("%", "")) / 100
        n_folds    = int(folds_text.split("-")[0].strip())

        self._progress_bar.setValue(0)
        self._progress_step_lbl.setText("Starting…")
        self._log_text.clear()
        self._log_text.append("🚀 Starting Random Forest training...")
        self._start_btn.setEnabled(False)

        self.overlay.set_message("Training Model…", "Preparing data")
        self.overlay.show()

        self._training_worker = TrainingWorker(test_size=test_size, n_folds=n_folds)
        self._training_worker.progress.connect(
            self._on_training_progress, Qt.ConnectionType.QueuedConnection)
        self._training_worker.finished.connect(
            self._on_training_finished, Qt.ConnectionType.QueuedConnection)
        self._training_worker.error.connect(
            self._on_training_error, Qt.ConnectionType.QueuedConnection)
        self._training_worker.finished.connect(self._training_worker.deleteLater)
        self._training_worker.error.connect(self._training_worker.deleteLater)
        self._training_worker.start()

    def _on_training_progress(self, step: str, pct: int):
        self._progress_bar.setValue(pct)
        self._progress_step_lbl.setText(step)
        self._log_text.append(f"[{pct}%] {step}")
        self.overlay.set_message("Training Model…", step)

    def _on_training_finished(self, result: TrainingResult):
        """Runs in the main thread via QueuedConnection."""
        self.overlay.hide()
        self._start_btn.setEnabled(True)

        for text, style in result.log_lines:
            if style == "success":
                self._log_text.append(f'<span style="color:#34d399">{text}</span>')
            elif style == "warning":
                self._log_text.append(f'<span style="color:#f5b335">{text}</span>')
            else:
                self._log_text.append(text)

        if result.imbalance_warning:
            self._log_text.append(
                f'<span style="color:#f5b335">{result.imbalance_warning}</span>'
            )

        DataStore.get().add_activity(
            f"Random Forest trained — Recall {result.recall:.1f}%  "
            f"F1 {result.f1_score:.3f}  PR-AUC {result.pr_auc:.3f}",
            icon="🧠", color="#a78bfa",
        )

        tile_values = {
            "Recall":    f"{result.recall:.1f}%",
            "F1 Score":  f"{result.f1_score:.3f}",
            "Precision": f"{result.precision:.1f}%",
            "PR-AUC":    f"{result.pr_auc:.3f}",
        }
        for label, tile in self._metric_tiles.items():
            if label in tile_values:
                layout = tile.layout()
                if layout and layout.count() > 0:
                    val_lbl = layout.itemAt(0).widget()
                    if val_lbl:
                        val_lbl.setText(tile_values[label])

        cm = result.confusion_matrix
        if len(cm) >= 2 and len(cm[0]) >= 2:
            self._update_confusion_matrix(cm[0][0], cm[0][1], cm[1][0], cm[1][1])

        if result.shap_values:
            import pandas as pd
            importance_df = pd.DataFrame(result.shap_values, columns=["feature", "importance"])
            importance_df["importance"] = importance_df["importance"] / 100
            self._update_shap_chart(importance_df)

        db_msg = self._persist_model_to_db()

        from PyQt6.QtWidgets import QMessageBox
        smote_line = (
            "\nSMOTE oversampling applied (imbalanced-learn)."
            if result.smote_applied else
            "\nclass_weight=balanced used (install imbalanced-learn for SMOTE)."
        )
        QMessageBox.information(
            self, "Training Complete",
            f"Random Forest trained successfully!\n\n"
            f"Recall    : {result.recall:.1f}%\n"
            f"F1 Score  : {result.f1_score:.3f}\n"
            f"PR-AUC    : {result.pr_auc:.3f}\n"
            f"Threshold : {result.decision_threshold:.2f}\n"
            f"{smote_line}\n\n"
            f"Model saved and ready for prediction.{db_msg}",
        )
        self._training_worker = None

    def _persist_model_to_db(self) -> str:
        try:
            from database.connection import get_connection
            conn = get_connection()
            if conn is None:
                msg = "Could not open a database connection."
                self._log_text.append(f"⚠ {msg} Model kept in memory only.")
                return f"\n\n⚠ {msg}"
            try:
                save_result = DataStore.get().save_model_to_disk("rf", db_conn=conn)
            finally:
                conn.close()

            if not save_result.get("success"):
                err = save_result.get("error", "unknown error")
                self._log_text.append(f"⚠ Model save failed: {err}")
                return f"\n\n⚠ Model save failed: {err}"

            db_id = save_result.get("db_id")
            if db_id:
                size     = save_result.get("metadata", {}).get("model_size_bytes")
                size_txt = f" ({size:,} bytes)" if size else ""
                self._log_text.append(
                    f"💾 Saved to database — model_id={db_id}{size_txt}, now active."
                )
                return f"\n\n💾 Stored in database (model_id={db_id}), set as active."

            self._log_text.append("⚠ Saved to disk, but database insert failed.")
            return "\n\n⚠ Saved to disk, but database insert failed."

        except Exception as e:
            self._log_text.append(f"⚠ Database save error: {e}")
            return f"\n\n⚠ Database save error: {e}"

    def _on_training_error(self, error_msg: str):
        self.overlay.hide()
        self._start_btn.setEnabled(True)
        self._log_text.append(f"❌ ERROR: {error_msg}")
        show_error(self, "Training Error", "Training could not complete.", error_msg)

    # ------------------------------------------------------------------
    # Chart updates
    # ------------------------------------------------------------------

    def _update_confusion_matrix(self, tn, fp, fn, tp):
        layout = self._cm_card.layout()
        while layout.count() > 1:
            item   = layout.takeAt(1)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        new_view, self._cm_chart, self._cm_series = \
            self._create_confusion_matrix_chart(tn, fp, fn, tp)
        self._cm_chart_view = new_view
        layout.addWidget(self._cm_chart_view)

    def _update_shap_chart(self, importance_df):
        while self._shap_layout.count():
            item   = self._shap_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        colors = ["#ff5b5b", "#ff5b5b", "#f5b335", "#f5b335",
                  "#4f8cff", "#4f8cff", "#4f8cff", "#4f8cff",
                  "#34d399", "#34d399"]
        for idx, row in importance_df.head(10).iterrows():
            pct   = min(int(row["importance"] * 100), 100)
            color = colors[idx % len(colors)]
            self._shap_layout.addWidget(
                self._create_shap_row(row["feature"], pct, color)
            )

    # ------------------------------------------------------------------
    # Main setup
    # ------------------------------------------------------------------

    def setup_ui(self):
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(30, 30, 30, 30)
        self.main_layout.setSpacing(20)

        # ── Header ────────────────────────────────────────────────────
        fixed_header = QFrame()
        fixed_header.setObjectName("fixedHeaderContainer")
        fh_layout = QVBoxLayout(fixed_header)
        fh_layout.setContentsMargins(20, 20, 20, 20)
        header_row = QHBoxLayout()
        header_row.setSpacing(15)
        title_col = QVBoxLayout()
        title_col.setSpacing(5)
        header = QLabel("MODEL TRAINING")
        header.setObjectName("header")
        self._ay_subheader_lbl = QLabel(f"Academic Year {SystemConfig.academic_year()}")
        self._ay_subheader_lbl.setObjectName("subHeader")
        title_col.addWidget(header)
        title_col.addWidget(self._ay_subheader_lbl)
        header_row.addLayout(title_col)
        header_row.addStretch()

        model_card   = QFrame()
        model_card.setObjectName("trainModelCard")
        model_layout = QHBoxLayout(model_card)
        model_layout.setContentsMargins(20, 15, 20, 15)
        model_layout.setSpacing(12)
        model_status = QLabel("● Model Active")
        model_status.setObjectName("trainModelStatus")
        self._model_status_opacity = QGraphicsOpacityEffect(model_status)
        model_status.setGraphicsEffect(self._model_status_opacity)
        self._status_animation = QPropertyAnimation(
            self._model_status_opacity, b"opacity")
        self._status_animation.setDuration(1200)
        self._status_animation.setStartValue(1.0)
        self._status_animation.setKeyValueAt(0.5, 0.3)
        self._status_animation.setEndValue(1.0)
        self._status_animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._status_animation.setLoopCount(-1)
        self._status_animation.start()
        self._sem_pill_lbl = QLabel(f"{SystemConfig.term_label()}  ▾")
        self._sem_pill_lbl.setObjectName("trainSemesterPill")
        go_pred_btn = QPushButton("Go to Prediction →")
        go_pred_btn.setObjectName("runButton")
        go_pred_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        go_pred_btn.setIcon(QIcon("assets/icons/play.svg"))
        go_pred_btn.clicked.connect(self._go_to_prediction_page)
        go_pred_btn.setFixedWidth(155)
        model_layout.addWidget(model_status)
        model_layout.addWidget(self._sem_pill_lbl)
        model_layout.addWidget(go_pred_btn)
        header_row.addWidget(model_card)
        fh_layout.addLayout(header_row)
        self.main_layout.addWidget(fixed_header)

        # ── Section header ────────────────────────────────────────────
        section_row = QHBoxLayout()
        section_row.setSpacing(16)
        section_left = QVBoxLayout()
        section_left.setSpacing(6)
        section_title = QLabel("Model Training & Evaluation")
        section_title.setObjectName("trainSectionTitle")
        section_desc  = QLabel(
            "Train a Random Forest classifier on historical student records"
        )
        section_desc.setObjectName("trainSectionDesc")
        section_left.addWidget(section_title)
        section_left.addWidget(section_desc)
        train_btn = QPushButton("🧠 Train Model")
        train_btn.setObjectName("trainModelBtn")
        train_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        train_btn.setFixedHeight(40)
        train_btn.clicked.connect(self._on_start_training)
        section_row.addLayout(section_left, 1)
        section_row.addWidget(train_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.main_layout.addLayout(section_row)

        # ── Model info card ───────────────────────────────────────────
        rf_card = QFrame()
        rf_card.setObjectName("trainPanelCard")
        rf_layout = QVBoxLayout(rf_card)
        rf_layout.setContentsMargins(24, 20, 24, 20)
        rf_layout.setSpacing(14)
        rf_header = QHBoxLayout()
        rf_title_col = QVBoxLayout()
        rf_title_col.setSpacing(4)
        rf_name = QLabel("Random Forest")
        rf_name.setObjectName("mlModelName")
        rf_tags = QLabel(
            "Ensemble · Handles class imbalance · Interpretable feature importances"
        )
        rf_tags.setObjectName("mlModelTags")
        rf_title_col.addWidget(rf_name)
        rf_title_col.addWidget(rf_tags)
        active_badge = QLabel("✓  Selected")
        active_badge.setObjectName("mlModelCheck")
        active_badge.setVisible(True)
        rf_header.addLayout(rf_title_col, 1)
        rf_header.addWidget(active_badge, 0, Qt.AlignmentFlag.AlignTop)
        rf_layout.addLayout(rf_header)

        metrics_grid = QGridLayout()
        metrics_grid.setSpacing(10)
        for i, (label, default) in enumerate([
            ("Recall", "--"), ("F1 Score", "--"),
            ("Precision", "--"), ("PR-AUC", "--"),
        ]):
            tile = self._create_metric_tile(default, label)
            metrics_grid.addWidget(tile, i // 2, i % 2)
            self._metric_tiles[label] = tile
        rf_layout.addLayout(metrics_grid)
        self.main_layout.addWidget(rf_card)

        # ── Training config + progress ────────────────────────────────
        train_row = QHBoxLayout()
        train_row.setSpacing(20)

        config_card = QFrame()
        config_card.setObjectName("trainPanelCard")
        config_layout = QVBoxLayout(config_card)
        config_layout.setContentsMargins(24, 20, 24, 20)
        config_layout.setSpacing(16)
        config_title = QLabel("TRAINING CONFIGURATION")
        config_title.setObjectName("trainPanelTitle")
        config_layout.addWidget(config_title)
        self._dataset_info_lbl = QLabel("Checking dataset…")
        self._dataset_info_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.4); font-size: 12px; background: transparent;"
        )
        config_layout.addWidget(self._dataset_info_lbl)
        split_widget, self._split_combo = self._create_config_combo(
            "Training / Test Split", ["80% / 20%", "70% / 30%", "90% / 10%"]
        )
        folds_widget, self._folds_combo = self._create_config_combo(
            "Cross-Validation Folds", ["5-Fold", "10-Fold", "3-Fold"]
        )
        config_layout.addWidget(split_widget)
        config_layout.addWidget(folds_widget)
        config_layout.addStretch()
        self._start_btn = QPushButton("🧠  Start Training")
        self._start_btn.setObjectName("trainStartBtn")
        self._start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._start_btn.clicked.connect(self._on_start_training)
        config_layout.addWidget(self._start_btn)

        progress_card = QFrame()
        progress_card.setObjectName("trainPanelCard")
        progress_layout = QVBoxLayout(progress_card)
        progress_layout.setContentsMargins(24, 20, 24, 20)
        progress_layout.setSpacing(14)
        progress_title = QLabel("TRAINING PROGRESS")
        progress_title.setObjectName("trainPanelTitle")
        progress_layout.addWidget(progress_title)
        self.progress_title = QLabel(
            'Training <span style="color:white;font-weight:bold;">'
            'Random Forest</span>...'
        )
        self.progress_title.setObjectName("trainProgressTitle")
        self.progress_title.setTextFormat(Qt.TextFormat.RichText)
        progress_layout.addWidget(self.progress_title)
        self._progress_step_lbl = QLabel("Ready to train.")
        self._progress_step_lbl.setObjectName("trainLogMuted")
        progress_layout.addWidget(self._progress_step_lbl)
        self._progress_bar = QProgressBar()
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(10)
        self._progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: rgba(255,255,255,0.08);
                border-radius: 5px; border: none;
            }
            QProgressBar::chunk {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4f8cff, stop:1 #34d399);
                border-radius: 5px;
            }
        """)
        progress_layout.addWidget(self._progress_bar)
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setPlaceholderText("Click 'Start Training' to begin...")
        self._log_text.setMaximumHeight(200)
        self._log_text.setStyleSheet("""
            QTextEdit {
                background-color: rgba(0,0,0,0.2);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 8px; color: #b8bcc8;
                font-family: 'Consolas', monospace;
                font-size: 12px; padding: 10px;
            }
        """)
        progress_layout.addWidget(self._log_text)
        progress_layout.addStretch()
        train_row.addWidget(config_card, 1)
        train_row.addWidget(progress_card, 1)
        self.main_layout.addLayout(train_row)

        # ── Charts row ────────────────────────────────────────────────
        charts_row = QHBoxLayout()
        charts_row.setSpacing(20)
        initial_view, self._cm_chart, self._cm_series = \
            self._create_confusion_matrix_chart()
        self._cm_chart_view = initial_view
        self._cm_card = self._create_chart_card(
            "CONFUSION MATRIX (LAST RUN)", self._cm_chart_view
        )
        charts_row.addWidget(self._cm_card, 1)
        charts_row.addWidget(self._create_shap_importance_card(), 1)
        self.main_layout.addLayout(charts_row)

        self.main_layout.addStretch()
        self.setLayout(self.main_layout)
        self.init_prediction()
        DataStore.get().add_listener(self._on_store_changed)

    def _on_store_changed(self, key: str):
        self._check_dataset_ready()

    def _go_to_prediction_page(self):
        from PyQt6.QtWidgets import QStackedWidget
        widget = self.parent()
        while widget is not None:
            if isinstance(widget, QStackedWidget):
                for i in range(widget.count()):
                    page = widget.widget(i)
                    if page and "Prediction" in type(page).__name__:
                        widget.setCurrentIndex(i)
                        return
            widget = widget.parent()
        from ui.dialogs.confirmation_dialog import show_info
        show_info(self, "Go to Prediction",
                  "Navigate to the Prediction page to run prediction.",
                  "Use the sidebar or navigation to switch pages.")

    def closeEvent(self, event):
        DataStore.get().remove_listener(self._on_store_changed)
        super().closeEvent(event)