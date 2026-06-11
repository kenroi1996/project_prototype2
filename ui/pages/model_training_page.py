from pathlib import Path

import pandas as pd
from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QComboBox,
    QProgressBar,
    QGraphicsOpacityEffect,
    QGridLayout,
    QMessageBox,
    QTextEdit,
)
from PyQt6.QtCore import QTimer, Qt, QPropertyAnimation, QEasingCurve, QMargins, QThread, pyqtSignal
from PyQt6.QtGui import QIcon, QColor, QPainter
from PyQt6.QtCharts import (
    QChart,
    QChartView,
    QBarSet,
    QBarSeries,
    QBarCategoryAxis,
    QValueAxis,
)

from ui.components.loading_overlay import LoadingOverlay
from ui.mixins.prediction_mixin import PredictionMixin
from services.data_store import DataStore
from services.ml_service import MLService


# =============================================================================
# BACKGROUND WORKER
# =============================================================================

class TrainingWorker(QThread):
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(object)
    error    = pyqtSignal(str)

    def __init__(self, model_type: str, test_size: float, n_folds: int = 5):
        # Never pass a QWidget as parent to a QThread
        super().__init__(parent=None)
        self.model_type = model_type
        self.test_size  = test_size
        self.n_folds    = n_folds

    def run(self):
        try:
            store = DataStore.get()

            # Always use the original raw unified dataset (before any feature
            # engineering or scaling). store.unified_dataset gets overwritten with
            # the processed version after the first training run, which causes
            # subsequent runs to receive already-engineered data and trigger the
            # "received already-engineered data" warning.
            # _original_unified_dataset is set once on first merge and never
            # overwritten by training — it always holds the raw portal data.
            unified = store._original_unified_dataset or store.unified_dataset

            if unified is None:
                unified = store.build_unified_dataset()
                if unified is None:
                    self.error.emit(
                        "No unified dataset found. "
                        "Upload data from at least one portal first."
                    )
                    return

            if isinstance(unified, dict):
                headers    = unified.get("headers", [])
                rows       = unified.get("rows", [])
                unified_df = pd.DataFrame(rows, columns=headers)
            else:
                unified_df = unified.copy()

            if unified_df.empty:
                self.error.emit("Unified dataset is empty. Merge or upload data first.")
                return

            # ── Step 1: Feature engineering ──────────────────────────────────
            self.progress.emit("Defining risk labels from grades & scores...", 10)
            from services.feature_engineering import run_full_feature_pipeline, TARGET_COLUMN
            unified_df = run_full_feature_pipeline(unified_df)
            target_col = TARGET_COLUMN

            # ── Convert string labels → integers ─────────────────────────────
            # run_full_feature_pipeline() leaves risk_label as "at_risk" /
            # "not_at_risk" strings. ml_service.train() and DataPipeline both
            # need integer labels (0 / 1). Without this mapping, compute_sample_weight
            # and SMOTE receive string y values and raise
            # "invalid literal for int() with base 10: 'at_risk'".
            if target_col in unified_df.columns:
                unified_df[target_col] = unified_df[target_col].map(
                    {"not_at_risk": 0, "at_risk": 1}
                ).fillna(0).astype(int)

            # ── Step 2: DataPipeline preprocessing ───────────────────────────
            # NOTE: deduplication is intentionally omitted here.
            # run_full_feature_pipeline() already deduplicates on Student_ID
            # BEFORE dropping it (see feature_engineering.py). Calling
            # remove_duplicates() again at this stage — after Student_ID is
            # gone — would compare only low-cardinality bucketed feature columns
            # and incorrectly collapse distinct students with identical feature
            # vectors, causing ~43% data loss.
            from services.preprocessing_service import DataPipeline

            pipeline = DataPipeline(unified_df)
            pipeline._target_column = target_col

            self.progress.emit("Handling missing values...", 25)
            pipeline.fill_missing(strategy="auto")

            self.progress.emit("Encoding categorical features...", 50)
            pipeline.encode_categorical(drop_first=False)

            self.progress.emit("Scaling numerical features...", 65)
            pipeline.scale_numerical(method="standard")

            # ── Step 3: Build feature matrix ─────────────────────────────────
            self.progress.emit("Preparing feature matrix...", 80)
            X, y, feature_names = pipeline.prepare_features(target_col=target_col)

            self.progress.emit(f"Training {self.model_type}...", 90)

            # ── Step 4: Train ─────────────────────────────────────────────────
            ml_service = MLService()
            ml_service.feature_names = feature_names
            ml_service.train(
                X, y,
                model_type=self.model_type,
                test_size=self.test_size,
            )

            # ── Attach fitted preprocessor so PredictionEngine can replay ─────
            # The DataPipeline holds fitted LabelEncoders and a StandardScaler.
            # Without these, PredictionEngine would send raw string/unscaled values
            # to model.predict_proba(), causing all probabilities to collapse to
            # 0.0 or 1.0 (the "0 or 100 risk score" bug).
            ml_service.preprocessor = pipeline

            # ── Step 5: Persist plain data to DataStore ───────────────────────
            store.set_trained_model(ml_service)
            processed_headers = list(pipeline.df.columns)
            processed_rows    = pipeline.df.astype(str).fillna("").values.tolist()
            store.set_unified_dataset(
                {"headers": processed_headers, "rows": processed_rows}
            )

            self.progress.emit("Training complete!", 100)
            # Emit only the plain ml_service object — no QWidgets
            self.finished.emit(ml_service)

        except Exception as e:
            self.error.emit(str(e))


# =============================================================================
# MODEL TRAINING PAGE
# =============================================================================

class ModelTrainingPage(PredictionMixin, QWidget):
    """Model Training page — model selection, training config, and evaluation."""

    MODEL_CONFIGS = {
        "rf": {
            "name":  "Random Forest",
            "tags":  "Ensemble · Interpretable · Recommended",
            "class": "random_forest",
        },
        "xgb": {
            "name":  "Gradient Boosting",
            "tags":  "High accuracy · Slower to train",
            "class": "gradient_boosting",
        },
        "lr": {
            "name":  "Logistic Regression",
            "tags":  "Baseline · Fast · Most interpretable",
            "class": "logistic_regression",
        },
    }

    def __init__(self):
        super().__init__()
        self._model_cards            = {}
        self._selected_model_id      = "rf"
        self._training_worker: TrainingWorker | None = None

        self.setup_ui()
        self._select_model("rf")
        self.overlay = LoadingOverlay(self)
        self._check_dataset_ready()

    # ------------------------------------------------------------------
    # UI BUILDERS
    # ------------------------------------------------------------------

    def _create_metric_tile(self, value, label):
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

    def _create_model_card(self, model_id: str, config: dict):
        card = QFrame()
        card.setObjectName("trainPanelCard")
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.setProperty("model_id", model_id)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        header       = QHBoxLayout()
        header_left  = QVBoxLayout()
        header_left.setSpacing(4)

        name = QLabel(config["name"])
        name.setObjectName("mlModelName")

        tags = QLabel(config["tags"])
        tags.setObjectName("mlModelTags")

        header_left.addWidget(name)
        header_left.addWidget(tags)

        check = QLabel("✓")
        check.setObjectName("mlModelCheck")
        check.setVisible(False)

        header.addLayout(header_left, 1)
        header.addWidget(check, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        self._metric_tiles[model_id] = {}
        metrics_grid = QGridLayout()
        metrics_grid.setSpacing(10)

        for i, (label, default_val) in enumerate([
            ("Accuracy",  "--"),
            ("F1 Score",  "--"),
            ("Precision", "--"),
            ("Recall",    "--"),
        ]):
            tile = self._create_metric_tile(default_val, label)
            metrics_grid.addWidget(tile, i // 2, i % 2)
            self._metric_tiles[model_id][label] = tile

        layout.addLayout(metrics_grid)

        card.mousePressEvent = lambda event, mid=model_id: (
            self._select_model(mid)
            if event.button() == Qt.MouseButton.LeftButton
            else None
        )

        self._model_cards[model_id] = (card, check)
        return card

    def _select_model(self, model_id):
        self._selected_model_id = model_id
        for mid, (card, check) in self._model_cards.items():
            selected = mid == model_id
            card.setObjectName(
                "trainPanelCardSelected" if selected else "trainPanelCard"
            )
            check.setVisible(selected)

        if hasattr(self, "progress_title"):
            config = self.MODEL_CONFIGS.get(model_id, {})
            name   = config.get("name", "Unknown")
            self.progress_title.setText(
                f'Training <span style="color:white;font-weight:bold;">'
                f"{name}</span>..."
            )

    def _create_shap_row(self, label_text, percentage, color):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        label = QLabel(label_text)
        label.setObjectName("trainShapLabel")
        label.setFixedWidth(150)

        bar = QProgressBar()
        bar.setValue(min(percentage, 100))
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

        pct = QLabel(f"{percentage}%")
        pct.setObjectName("trainShapPercent")
        pct.setFixedWidth(36)
        pct.setAlignment(Qt.AlignmentFlag.AlignRight)

        layout.addWidget(label)
        layout.addWidget(bar, 1)
        layout.addWidget(pct)
        return row

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
        axis_y  = QValueAxis()
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

    def _create_training_config_card(self):
        card = QFrame()
        card.setObjectName("trainPanelCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        title = QLabel("TRAINING CONFIGURATION")
        title.setObjectName("trainPanelTitle")
        layout.addWidget(title)

        self._dataset_info_lbl = QLabel("Checking dataset…")
        self._dataset_info_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.4); font-size: 12px; background: transparent;"
        )
        layout.addWidget(self._dataset_info_lbl)

        split_widget,  self._split_combo  = self._create_config_combo(
            "Training / Test Split", ["80% / 20%", "70% / 30%", "90% / 10%"]
        )
        folds_widget,  self._folds_combo  = self._create_config_combo(
            "Cross-Validation Folds", ["5-Fold", "10-Fold", "3-Fold"]
        )
        target_widget, self._target_combo = self._create_config_combo(
            "Target Variable",
            ["risk_label (auto-generated)", "At_Risk (binary)"],
        )

        layout.addWidget(split_widget)
        layout.addWidget(folds_widget)
        layout.addWidget(target_widget)
        layout.addStretch()

        self._start_btn = QPushButton("🧠  Start Training")
        self._start_btn.setObjectName("trainStartBtn")
        self._start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._start_btn.clicked.connect(self._on_start_training)
        layout.addWidget(self._start_btn)

        return card

    def _create_config_combo(self, label_text, options):
        label = QLabel(label_text)
        label.setObjectName("trainConfigLabel")

        combo = QComboBox()
        combo.setObjectName("trainConfigCombo")
        combo.addItems(options)
        combo.setCursor(Qt.CursorShape.PointingHandCursor)

        wrap = QWidget()
        wrap_layout = QVBoxLayout(wrap)
        wrap_layout.setContentsMargins(0, 0, 0, 0)
        wrap_layout.setSpacing(6)
        wrap_layout.addWidget(label)
        wrap_layout.addWidget(combo)

        return wrap, combo

    def _create_training_progress_card(self):
        card = QFrame()
        card.setObjectName("trainPanelCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title = QLabel("TRAINING PROGRESS")
        title.setObjectName("trainPanelTitle")
        layout.addWidget(title)

        self.progress_title = QLabel()
        self.progress_title.setObjectName("trainProgressTitle")
        self.progress_title.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.progress_title)

        self._progress_step_lbl = QLabel("Ready to train.")
        self._progress_step_lbl.setObjectName("trainLogMuted")
        layout.addWidget(self._progress_step_lbl)

        self._progress_bar = QProgressBar()
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(10)
        self._progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: rgba(255, 255, 255, 0.08);
                border-radius: 5px;
                border: none;
            }
            QProgressBar::chunk {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4f8cff, stop:1 #34d399
                );
                border-radius: 5px;
            }
        """)
        layout.addWidget(self._progress_bar)

        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setPlaceholderText("Click 'Start Training' to begin...")
        self._log_text.setMaximumHeight(200)
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

        layout.addStretch()
        return card

    def _create_chart_card(self, title_text, chart_widget):
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

    def _create_shap_importance_card(self):
        chart_host = QWidget()
        self._shap_layout = QVBoxLayout(chart_host)
        self._shap_layout.setContentsMargins(0, 0, 0, 0)
        self._shap_layout.setSpacing(10)

        placeholder = QLabel("Train a model to see feature importance")
        placeholder.setObjectName("trainLogMuted")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._shap_layout.addWidget(placeholder)

        return self._create_chart_card("FEATURE IMPORTANCE (SHAP)", chart_host)

    # ------------------------------------------------------------------
    # DATASET STATUS
    # ------------------------------------------------------------------

    def _check_dataset_ready(self):
        store = DataStore.get()

        if store.unified_dataset is not None or store.ready_count() > 0:
            rows = 0
            if store.unified_dataset is not None:
                if isinstance(store.unified_dataset, dict):
                    rows = len(store.unified_dataset.get("rows", []))
                else:
                    rows = len(store.unified_dataset)
            else:
                for portal in store.portals.values():
                    if portal:
                        rows += portal["row_count"]

            self._dataset_info_lbl.setText(
                f"✓  {store.ready_count()}/4 portals ready  ·  ~{rows:,} rows available"
            )
            self._dataset_info_lbl.setStyleSheet(
                "color: #34d399; font-size: 12px; background: transparent;"
            )
            self._start_btn.setEnabled(True)
        else:
            self._dataset_info_lbl.setText(
                "⚠  No data found. Upload from portals first."
            )
            self._dataset_info_lbl.setStyleSheet(
                "color: #f5b335; font-size: 12px; background: transparent;"
            )
            self._start_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # TRAINING ACTIONS
    # ------------------------------------------------------------------

    def _on_start_training(self):
        store = DataStore.get()

        if store.unified_dataset is None and store.ready_count() == 0:
            QMessageBox.warning(self, "No Data", "Upload data from at least one portal first.")
            return

        split_text = self._split_combo.currentText()
        folds_text = self._folds_combo.currentText()

        test_size = float(split_text.split("/")[1].strip().replace("%", "")) / 100
        n_folds   = int(folds_text.split("-")[0].strip())

        model_config = self.MODEL_CONFIGS.get(self._selected_model_id, {})
        model_type   = model_config.get("class", "random_forest")

        # Reset UI
        self._progress_bar.setValue(0)
        self._progress_step_lbl.setText("Starting…")
        self._log_text.clear()
        self._log_text.append(f"🚀 Starting training with {model_config.get('name', 'Unknown')}...")
        self._start_btn.setEnabled(False)

        self.overlay.set_message("Training Model…", "Preparing data")
        self.overlay.show()

        # Create worker with no parent — a QWidget parent on a QThread causes
        # the "Cannot set parent, new parent is in a different thread" warnings
        self._training_worker = TrainingWorker(
            model_type=model_type,
            test_size=test_size,
            n_folds=n_folds,
        )

        # QueuedConnection ensures all slots execute in the main thread
        self._training_worker.progress.connect(
            self._on_training_progress,
            Qt.ConnectionType.QueuedConnection,
        )
        self._training_worker.finished.connect(
            self._on_training_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._training_worker.error.connect(
            self._on_training_error,
            Qt.ConnectionType.QueuedConnection,
        )

        # Schedule self-cleanup after the thread finishes
        self._training_worker.finished.connect(self._training_worker.deleteLater)
        self._training_worker.error.connect(self._training_worker.deleteLater)

        self._training_worker.start()

    def _on_training_progress(self, step: str, pct: int):
        self._progress_bar.setValue(pct)
        self._progress_step_lbl.setText(step)
        self._log_text.append(f"[{pct}%] {step}")
        self.overlay.set_message("Training Model…", step)

    def _on_training_finished(self, ml_service: MLService):
        """Runs in the main thread via QueuedConnection — all UI ops are safe here."""
        self.overlay.hide()
        self._start_btn.setEnabled(True)

        history = ml_service.training_history

        self._log_text.append("✅ Training complete!")
        self._log_text.append(f"Accuracy: {history.get('accuracy', 'N/A')}")
        self._log_text.append(
            f"CV Mean: {history.get('cv_mean', 'N/A')} ± {history.get('cv_std', 'N/A')}"
        )

        acc     = history.get("accuracy")
        acc_txt = f" — {acc * 100:.1f}% accuracy" if isinstance(acc, (int, float)) else ""
        DataStore.get().add_activity(
            f"Model trained ({self._selected_model_id.upper()}){acc_txt}",
            icon="🧠",
            color="#a78bfa",
        )

        # Update metric tiles
        model_id = self._selected_model_id
        if model_id in self._metric_tiles:
            metrics = {
                "Accuracy":  f"{history.get('accuracy', 0) * 100:.1f}%",
                "F1 Score":  "--",
                "Precision": "--",
                "Recall":    "--",
            }
            report = history.get("classification_report", {})
            if "weighted avg" in report:
                avg = report["weighted avg"]
                metrics["F1 Score"]  = f"{avg.get('f1-score', 0):.3f}"
                metrics["Precision"] = f"{avg.get('precision', 0) * 100:.1f}%"
                metrics["Recall"]    = f"{avg.get('recall', 0) * 100:.1f}%"

            for label, tile in self._metric_tiles[model_id].items():
                if label in metrics:
                    layout = tile.layout()
                    if layout and layout.count() > 0:
                        val_label = layout.itemAt(0).widget()
                        if val_label:
                            val_label.setText(metrics[label])

        # Update confusion matrix
        cm = history.get("confusion_matrix", [[0, 0], [0, 0]])
        if len(cm) >= 2 and len(cm[0]) >= 2 and len(cm[1]) >= 2:
            self._update_confusion_matrix(cm[0][0], cm[0][1], cm[1][0], cm[1][1])

        # Update SHAP / feature importance
        importance_df = ml_service.get_feature_importance()
        if importance_df is not None and not importance_df.empty:
            self._update_shap_chart(importance_df)

        db_msg = self._persist_model_to_db()

        QMessageBox.information(
            self, "Training Complete",
            f"Model trained successfully!\n\n"
            f"Accuracy: {history.get('accuracy', 'N/A')}\n"
            f"CV Score: {history.get('cv_mean', 'N/A')} ± {history.get('cv_std', 'N/A')}\n\n"
            f"Model saved to DataStore. Ready for prediction."
            f"{db_msg}",
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
                save_result = DataStore.get().save_model_to_disk(
                    self._selected_model_id, db_conn=conn
                )
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
        QMessageBox.critical(self, "Training Error", error_msg)

    # ------------------------------------------------------------------
    # CHART UPDATES  (called from main thread only, via _on_training_finished)
    # ------------------------------------------------------------------

    def _update_confusion_matrix(self, tn, fp, fn, tp):
        """
        Replace the confusion matrix chart view in-place.

        The correct Qt pattern is:
          1. takeAt() to detach the old item from the layout
          2. deleteLater() to schedule safe deletion on the next event loop tick
          3. addWidget() the new widget

        Do NOT call processEvents() here — it pumps the event queue mid-slot,
        which can trigger pending cross-thread setParent calls and produce the
        'Cannot set parent, new parent is in a different thread' flood.
        """
        layout = self._cm_card.layout()

        # Remove every widget after the title label (index 0)
        while layout.count() > 1:
            item = layout.takeAt(1)        # detaches from layout, no setParent warning
            widget = item.widget()
            if widget:
                widget.deleteLater()       # deferred deletion — safe, no processEvents needed

        # Create and add the new chart view (we are on the main thread)
        new_view, self._cm_chart, self._cm_series = self._create_confusion_matrix_chart(
            tn, fp, fn, tp
        )
        self._cm_chart_view = new_view
        layout.addWidget(self._cm_chart_view)

    def _update_shap_chart(self, importance_df):
        """
        Replace SHAP rows with fresh data.

        Same rule: takeAt() + deleteLater(), no processEvents().
        """
        while self._shap_layout.count():
            item   = self._shap_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        colors = [
            "#ff5b5b", "#ff5b5b",
            "#f5b335", "#f5b335",
            "#4f8cff", "#4f8cff", "#4f8cff", "#4f8cff",
        ]

        for idx, row in importance_df.head(8).iterrows():
            feat  = row["feature"]
            pct   = min(int(row["importance"] * 100), 100)
            color = colors[idx % len(colors)]
            self._shap_layout.addWidget(self._create_shap_row(feat, pct, color))

    # ------------------------------------------------------------------
    # MAIN SETUP
    # ------------------------------------------------------------------

    def setup_ui(self):
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(30, 30, 30, 30)
        self.main_layout.setSpacing(20)

        # ── Fixed header ──────────────────────────────────────────────
        self.fixed_header_container = QFrame()
        self.fixed_header_container.setObjectName("fixedHeaderContainer")
        fixed_header_layout = QVBoxLayout()
        fixed_header_layout.setContentsMargins(20, 20, 20, 20)
        fixed_header_layout.setSpacing(0)

        header_layout      = QHBoxLayout()
        header_layout.setSpacing(15)
        header_text_layout = QVBoxLayout()
        header_text_layout.setSpacing(5)

        header = QLabel("Model Training")
        header.setObjectName("header")

        subheader = QLabel("Academic Year 2024–2025")
        subheader.setObjectName("subHeader")

        header_text_layout.addWidget(header)
        header_text_layout.addWidget(subheader)
        header_layout.addLayout(header_text_layout)
        header_layout.addStretch()

        model_card   = QFrame()
        model_card.setObjectName("trainModelCard")
        model_layout = QHBoxLayout()
        model_layout.setContentsMargins(20, 15, 20, 15)
        model_layout.setSpacing(12)

        model_status = QLabel("● Model Active")
        model_status.setObjectName("trainModelStatus")

        self._model_status_opacity = QGraphicsOpacityEffect(model_status)
        model_status.setGraphicsEffect(self._model_status_opacity)

        self._status_animation = QPropertyAnimation(self._model_status_opacity, b"opacity")
        self._status_animation.setDuration(1200)
        self._status_animation.setStartValue(1.0)
        self._status_animation.setKeyValueAt(0.5, 0.3)
        self._status_animation.setEndValue(1.0)
        self._status_animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._status_animation.setLoopCount(-1)
        self._status_animation.start()

        semester_pill = QLabel("1st Semester 2024–25  ▾")
        semester_pill.setObjectName("trainSemesterPill")

        run_button = QPushButton("Go to Prediction →")
        run_button.setObjectName("runButton")
        run_button.setCursor(Qt.CursorShape.PointingHandCursor)
        run_button.setIcon(QIcon("assets/icons/play.svg"))
        run_button.clicked.connect(self._go_to_prediction_page)
        run_button.setFixedWidth(155)

        model_layout.addWidget(model_status)
        model_layout.addWidget(semester_pill)
        model_layout.addWidget(run_button)
        model_card.setLayout(model_layout)
        header_layout.addWidget(model_card)

        fixed_header_layout.addLayout(header_layout)
        self.fixed_header_container.setLayout(fixed_header_layout)
        self.main_layout.addWidget(self.fixed_header_container)

        # ── Section header ────────────────────────────────────────────
        section_row  = QHBoxLayout()
        section_row.setSpacing(16)
        section_left = QVBoxLayout()
        section_left.setSpacing(6)

        section_title = QLabel("Model Training & Evaluation")
        section_title.setObjectName("trainSectionTitle")

        section_desc = QLabel("Train the risk classifier on historical student records")
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

        # ── Model selection cards ─────────────────────────────────────
        self._metric_tiles = {}
        models_row = QHBoxLayout()
        models_row.setSpacing(16)
        for model_id, config in self.MODEL_CONFIGS.items():
            models_row.addWidget(self._create_model_card(model_id, config), 1)
        self.main_layout.addLayout(models_row)

        # ── Training config + progress ────────────────────────────────
        train_row = QHBoxLayout()
        train_row.setSpacing(20)
        train_row.addWidget(self._create_training_config_card(), 1)
        train_row.addWidget(self._create_training_progress_card(), 1)
        self.main_layout.addLayout(train_row)

        # ── Charts row ────────────────────────────────────────────────
        charts_row = QHBoxLayout()
        charts_row.setSpacing(20)

        # Store initial chart view so _update_confusion_matrix can find it via the card
        initial_cm_view, self._cm_chart, self._cm_series = self._create_confusion_matrix_chart()
        self._cm_chart_view = initial_cm_view
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
        """
        Navigate to the Prediction page.
        Walks up the widget tree to find the QStackedWidget (main navigation)
        and switches to the prediction page by object name.
        Prediction now requires portal uploads + merge — it cannot be triggered
        directly from this page using the training dataset.
        """
        from PyQt6.QtWidgets import QStackedWidget
        widget = self.parent()
        while widget is not None:
            if isinstance(widget, QStackedWidget):
                # Find the prediction page index by object name
                for i in range(widget.count()):
                    page = widget.widget(i)
                    if page and page.objectName() in ("page", "PredictionPage") and                        type(page).__name__ == "PredictionPage":
                        widget.setCurrentIndex(i)
                        return
                # Fallback: try by class name match on last page
                for i in range(widget.count()):
                    page = widget.widget(i)
                    if page and "Prediction" in type(page).__name__:
                        widget.setCurrentIndex(i)
                        return
            widget = widget.parent()
        # If navigation not found, show a hint
        from ui.dialogs.confirmation_dialog import show_info
        show_info(
            self,
            "Go to Prediction",
            "Navigate to the Prediction page to upload portal datasets and run prediction.",
            "Use the sidebar or top navigation to switch pages.",
        )

    def closeEvent(self, event):
        DataStore.get().remove_listener(self._on_store_changed)
        super().closeEvent(event)