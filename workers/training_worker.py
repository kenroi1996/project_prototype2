"""
workers/training_worker.py
───────────────────────────
QThread worker that trains the XGBoost regressor using features from
feature_store (or directly from the in-memory engineered DataFrame).

Signals
───────
progress(message: str, pct: int)
finished(history: dict)              ← XGBService.training_history
error(message: str)

Usage (from a QWidget)
─────
    from workers.training_worker import XGBTrainingWorker

    self._worker = XGBTrainingWorker(model_type="xgb", test_size=0.2)
    self._worker.progress.connect(
        lambda msg, pct: self._log.append(f"[{pct}%] {msg}")
    )
    self._worker.finished.connect(self._on_training_done)
    self._worker.error.connect(self._on_training_error)
    self._worker.start()
"""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from services.xgb_service import XGBService
from services.data_store import DataStore
from services.feature_engineering import TARGET_COLUMN, FINAL_FEATURES
from services.preprocessing_service import DataPipeline


class XGBTrainingWorker(QThread):
    progress = pyqtSignal(str, int)   # (message, percent)
    finished = pyqtSignal(dict)       # training history
    error    = pyqtSignal(str)

    def __init__(
        self,
        test_size:    float = 0.2,
        n_estimators: int   = 300,
        max_depth:    int   = 6,
        learning_rate: float = 0.05,
        parent = None,
    ) -> None:
        super().__init__(parent)
        self.test_size     = test_size
        self.n_estimators  = n_estimators
        self.max_depth     = max_depth
        self.learning_rate = learning_rate

    def run(self) -> None:
        try:
            import pandas as pd

            store = DataStore.get()

            self.progress.emit("Loading unified dataset…", 5)
            unified = store.unified_dataset
            if unified is None:
                raise ValueError("No unified dataset. Run Data Merge + Pipeline first.")

            if isinstance(unified, dict):
                df = pd.DataFrame(unified["rows"], columns=unified["headers"])
            else:
                df = unified.copy()

            # ── Require target column ─────────────────────────────────────────
            # XGBoost regresses on Final_Avg_GRD directly (continuous target)
            if "Final_Avg_GRD" not in df.columns:
                raise ValueError(
                    "Column 'Final_Avg_GRD' not found. "
                    "Use the raw merged dataset, not the post-pipeline encoded one."
                )

            self.progress.emit("Running feature engineering…", 15)
            from services.feature_engineering import run_full_feature_pipeline
            df = run_full_feature_pipeline(df)

            # ── Build X / y ───────────────────────────────────────────────────
            self.progress.emit("Preparing feature matrix…", 30)

            # Keep only FINAL_FEATURES that are present
            available_features = [f for f in FINAL_FEATURES if f in df.columns]

            # Encode remaining categoricals with DataPipeline
            pipeline = DataPipeline(df[available_features + ["Final_Avg_GRD"]].copy())
            pipeline._target_column = "Final_Avg_GRD"
            pipeline.fill_missing(strategy="auto")
            pipeline.encode_categorical(drop_first=False)
            # Do NOT scale — XGBoost is tree-based and doesn't need scaling

            # Extract matrices
            feature_df = pipeline.df[available_features].select_dtypes(
                include=[np.number]
            )
            final_features = list(feature_df.columns)

            X = feature_df.values
            y = pd.to_numeric(
                pipeline.df["Final_Avg_GRD"], errors="coerce"
            ).fillna(pipeline.df["Final_Avg_GRD"].mean()).values

            if len(X) == 0:
                raise ValueError("No valid rows after preprocessing.")

            self.progress.emit(
                f"Training XGBoost on {len(X):,} samples · {len(final_features)} features…",
                40,
            )

            # ── Train ─────────────────────────────────────────────────────────
            service = XGBService()
            history = service.train(
                X=X,
                y=y,
                feature_names=final_features,
                test_size=self.test_size,
                n_estimators=self.n_estimators,
                max_depth=self.max_depth,
                learning_rate=self.learning_rate,
                progress_cb=lambda msg, pct: self.progress.emit(
                    msg, 40 + int(pct * 0.5)
                ),
            )

            # ── Save model to disk ────────────────────────────────────────────
            self.progress.emit("Saving model to disk…", 95)
            saved_path = service.save()
            history["saved_path"] = str(saved_path)

            # ── Store in DataStore ────────────────────────────────────────────
            store.set_trained_model({
                "model":         service.model,
                "model_id":      "xgb",
                "feature_names": final_features,
                "target_col":    "Final_Avg_GRD",
                "service":       service,
            })

            self.progress.emit("Training complete ✅", 100)
            self.finished.emit(history)

        except Exception as exc:
            self.error.emit(str(exc))
