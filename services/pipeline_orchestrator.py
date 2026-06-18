"""
End-to-end pipeline orchestrator.
Coordinates: Unified CSV → Feature Engineering → Train

Two-phase architecture:
  Phase 1 (train):   run_full_feature_pipeline() — defines target + engineers features
  Phase 2 (predict): run_prediction_pipeline()   — engineers features only (no target)

TRAINING RESPONSIBILITY
-----------------------
All training logic now lives exclusively in TrainingEngine:
  • Feature engineering (via run_full_feature_pipeline)
  • DataPipeline preprocessing (fill / encode / scale)
  • SMOTE oversampling inside CV folds
  • Stratified cross-validation (Recall / F1 / PR-AUC)
  • Threshold optimisation
  • ModelRegistry persistence

PipelineOrchestrator.run() is a thin coordinator that reads the unified
dataset, calls TrainingEngine, and returns a results dict for the UI.
It no longer instantiates MLService, calls ml_service.train(), or runs
DataPipeline steps independently — doing so created a duplicate training
path that ran SMOTE before splitting (leakage) and bypassed TrainingEngine
entirely.

RAW vs ENGINEERED DATASET
--------------------------
PipelineOrchestrator.run() accepts an excel_path for the on-disk CSV path,
but also accepts a pre-loaded DataFrame via the `df` parameter so that
DataMergePipelinePage can pass the raw merged data directly without
writing/reading a temp file unnecessarily.

TRAINING SOURCE PRIORITY (most → least preferred):
  1. DataStore.raw_merged_dataset  — set by merge, never overwritten by pipeline
  2. excel_path / df parameter     — fallback for standalone / test runs
"""

import json
from pathlib import Path
from typing import Optional, Callable

import pandas as pd

from .excel_service import read_excel_file
from .ml_service import MLService
from .feature_engineering import (
    TARGET_COLUMN,
    TRAINING_FEATURES,
    FINAL_FEATURES,
    _ensure_geo_cache,
)
from .training_engine import TrainingEngine, TrainingResult


class PipelineOrchestrator:
    STEP_NAMES = [
        "read_excel", "geo_cache", "train_model", "save_outputs",
    ]

    def __init__(self):
        self.ml_service:  Optional[MLService]    = None
        self.last_result: Optional[TrainingResult] = None
        self.results:     dict                   = {}
        self._cancelled:  bool                   = False

    def cancel(self):
        self._cancelled = True

    def _check_cancelled(self):
        if self._cancelled:
            raise InterruptedError("Pipeline was cancelled by user.")

    def run(
        self,
        excel_path:       str | Path,
        model_type:       str                              = "random_forest",
        test_size:        float                            = 0.2,
        n_folds:          int                              = 5,
        save_path:        Optional[str]                    = None,
        on_step:          Optional[Callable[[str, str], None]] = None,
    ) -> dict:

        def notify(step: str, msg: str):
            if on_step:
                on_step(step, msg)

        # ── Step 1: Load the raw merged dataset ───────────────────────────────
        # Priority: DataStore.raw_merged_dataset → excel_path fallback.
        #
        # We always prefer raw_merged_dataset because:
        #   • It is set once by MergeEngine and never overwritten.
        #   • unified_dataset may already be engineered (Final_Avg_GRD dropped),
        #     which causes define_target() to be skipped and risk_label to be
        #     missing — the "Training Error" the user sees on retrain.
        #   • Reading from DataStore avoids writing/reading a temp CSV.
        notify("read_excel", "Loading dataset for training...")
        self._check_cancelled()

        df: pd.DataFrame | None = None

        try:
            from .data_store import DataStore
        except ModuleNotFoundError:
            from data_store import DataStore

        store = DataStore.get()
        raw   = store.get_raw_merged_dataset()

        if raw is not None:
            # Use the in-memory raw merged dataset — fastest and safest path.
            df = pd.DataFrame(raw["rows"], columns=raw["headers"])
            notify(
                "read_excel",
                f"Loaded {len(df):,} rows · {len(df.columns)} columns "
                f"(from raw merged dataset)"
            )
            print(
                f"[PipelineOrchestrator] Using raw_merged_dataset: "
                f"{len(df):,} rows × {len(df.columns)} columns | "
                f"Final_Avg_GRD present: {'Final_Avg_GRD' in df.columns}"
            )
        else:
            # Fallback: read from disk (standalone run or first run after restart).
            notify("read_excel", f"Reading {Path(excel_path).name}...")
            df = read_excel_file(excel_path)
            notify(
                "read_excel",
                f"Loaded {len(df):,} rows · {len(df.columns)} columns "
                f"(from file)"
            )
            print(
                f"[PipelineOrchestrator] raw_merged_dataset not available — "
                f"reading from {excel_path}. "
                f"Final_Avg_GRD present: {'Final_Avg_GRD' in df.columns}"
            )

            # Warn if the file looks engineered (no grade column).
            if "Final_Avg_GRD" not in df.columns and TARGET_COLUMN not in df.columns:
                raise RuntimeError(
                    "The dataset loaded from disk appears to be already-engineered "
                    "(Final_Avg_GRD is missing and risk_label is absent).\n\n"
                    "Please re-run the Data Merge step so the raw dataset is "
                    "available for training."
                )

        # ── Step 2: Warm GeoCache ─────────────────────────────────────────────
        notify("geo_cache", "Loading municipality coordinates from database...")
        self._check_cancelled()
        _ensure_geo_cache()
        from .feature_engineering import _GEO_CACHE
        notify(
            "geo_cache",
            f"GeoCache ready: {len(_GEO_CACHE)} municipalities loaded"
            if _GEO_CACHE else
            "⚠ GeoCache empty — run geo_cache_setup.sql to seed coordinates"
        )

        # ── Step 3: Train via TrainingEngine ──────────────────────────────────
        # TrainingEngine owns the full pipeline:
        #   normalize → define_target → engineer_features → deduplicate
        #   → drop_raw → drop_leakage → fill_missing → encode → scale
        #   → SMOTE (inside CV folds only) → StratifiedKFold
        #   → threshold optimisation → ModelRegistry.save_model()
        notify("train_model", "Starting TrainingEngine...")
        self._check_cancelled()

        headers = list(df.columns)
        rows    = df.astype(str).fillna("").values.tolist()

        def _progress(step: str, pct: int):
            notify("train_model", f"[{pct}%] {step}")

        engine = TrainingEngine(
            headers     = headers,
            rows        = rows,
            model_id    = "rf",
            test_size   = test_size,
            n_folds     = n_folds,
            progress_cb = _progress,
        )
        result = engine.run()
        self.last_result = result

        if not result.success:
            raise RuntimeError(
                "TrainingEngine failed:\n" + "\n".join(result.errors)
            )

        notify(
            "train_model",
            f"Recall: {result.recall:.1f}%  "
            f"F1: {result.f1_score:.3f}  "
            f"PR-AUC: {result.pr_auc:.3f}  "
            f"Threshold: {result.decision_threshold:.2f}"
        )

        # ── Step 4: Optional artifact export ─────────────────────────────────
        if save_path:
            notify("save_outputs", "Saving pipeline report...")
            self._check_cancelled()
            save_dir = Path(save_path)
            save_dir.mkdir(parents=True, exist_ok=True)

            report = {
                "recall":             result.recall,
                "f1_score":           result.f1_score,
                "precision":          result.precision,
                "pr_auc":             result.pr_auc,
                "decision_threshold": result.decision_threshold,
                "cv_recalls":         result.cv_recalls,
                "cv_f1s":             result.cv_f1s,
                "cv_pr_aucs":         result.cv_pr_aucs,
                "train_size":         result.train_size,
                "test_size":          result.test_size,
                "feature_count":      result.feature_count,
                "smote_applied":      result.smote_applied,
                "feature_importance": result.shap_values,
            }
            with open(save_dir / "pipeline_report.json", "w") as f:
                json.dump(report, f, indent=2, default=str)

            notify("save_outputs", f"Report saved to {save_dir}")

        self.results = {
            "training_result":    result,
            "recall":             result.recall,
            "f1_score":           result.f1_score,
            "pr_auc":             result.pr_auc,
            "feature_importance": result.shap_values,
            # Forwarded so DataMergePipelinePage._on_pipeline_success()
            # can populate the "View Engineered Dataset" button.
            "engineered_headers": result.engineered_headers,
            "engineered_rows":    result.engineered_rows,
        }

        return self.results

    def get_cleaned_data_for_ui(self) -> tuple[list, list]:
        """Legacy shim — returns empty if called outside old flow."""
        return [], []