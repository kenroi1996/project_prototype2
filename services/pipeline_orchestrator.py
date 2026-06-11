"""
End-to-end pipeline orchestrator.
Coordinates: Unified CSV → Feature Engineering → Clean → Train

Two-phase architecture:
  Phase 1 (train):   run_full_feature_pipeline() — defines target + engineers features
  Phase 2 (predict): run_prediction_pipeline()   — engineers features only (no target)

GeoCache loading
----------------
Municipality coordinates are loaded ONCE from public.geo_cache in PostgreSQL.
_ensure_geo_cache() (inside feature_engineering.py) is called automatically
at the start of both pipeline entry points, so neither the orchestrator nor
TrainingWorker need to call load_geo_cache() manually anymore.

The orchestrator still calls _ensure_geo_cache() explicitly via the import
so the step notification fires correctly in the UI progress log.
"""

import json
from pathlib import Path
from typing import Optional, Callable

import pandas as pd

from .excel_service import read_excel_file, rows_to_dataframe
from .preprocessing_service import DataPipeline
from .ml_service import MLService
from .feature_engineering import (
    run_full_feature_pipeline,
    run_prediction_pipeline,
    TARGET_COLUMN,
    TRAINING_FEATURES,
    FINAL_FEATURES,          # legacy alias — kept for any callers that use it
    _ensure_geo_cache,       # called explicitly so the UI step fires
)


class PipelineOrchestrator:
    STEP_NAMES = [
        "read_excel", "validate", "geo_cache", "define_target", "engineer_features",
        "remove_duplicates", "handle_missing", "encode_categorical",
        "scale_numerical", "prepare_features", "train_model", "save_outputs",
    ]

    def __init__(self):
        self.pipeline:    Optional[DataPipeline] = None
        self.ml_service:  Optional[MLService]    = None
        self.results:     dict                   = {}
        self._cancelled:  bool                   = False

    def cancel(self):
        self._cancelled = True

    def _check_cancelled(self):
        if self._cancelled:
            raise InterruptedError("Pipeline was cancelled by user.")

    def run(
        self,
        excel_path:        str | Path,
        required_columns:  Optional[list[str]]              = None,
        target_column:     str                              = TARGET_COLUMN,
        risk_based_on:     Optional[str]                    = None,
        risk_rules:        Optional[dict]                   = None,
        model_type:        str                              = "random_forest",
        save_path:         Optional[str]                    = None,
        on_step:           Optional[Callable[[str, str], None]] = None,
    ) -> dict:

        def notify(step: str, msg: str):
            if on_step:
                on_step(step, msg)

        # ── Step 1: Read unified CSV ──────────────────────────────────────────
        notify("read_excel", f"Reading {Path(excel_path).name}...")
        self._check_cancelled()
        df = read_excel_file(excel_path)
        notify("read_excel", f"Loaded {len(df):,} rows · {len(df.columns)} columns")

        # ── Step 2: Load GeoCache from PostgreSQL ─────────────────────────────
        # _ensure_geo_cache() queries public.geo_cache and populates _GEO_CACHE.
        # It is also called automatically inside run_full_feature_pipeline(), so
        # calling it here just ensures the UI step notification fires and the
        # cache is warm before engineer_features() runs.
        notify("geo_cache", "Loading municipality coordinates from database...")
        self._check_cancelled()
        _ensure_geo_cache()
        from .feature_engineering import _GEO_CACHE
        notify(
            "geo_cache",
            f"GeoCache ready: {len(_GEO_CACHE)} municipalities loaded"
            if _GEO_CACHE
            else "⚠ GeoCache empty — run geo_cache_setup.sql to seed coordinates"
        )

        # ── Step 3: Phase 1 — Full feature pipeline (training) ───────────────
        # run_full_feature_pipeline() does:
        #   normalize → define_target → engineer_features → drop_raw
        #   → deduplicate → drop_leakage → class-imbalance report
        notify("define_target",    "Defining risk labels from grades & exam scores...")
        notify("engineer_features", "Engineering pre-enrollment features...")
        self._check_cancelled()

        df = run_full_feature_pipeline(df)

        # Convert string labels → integers before model training
        # "at_risk" → 1,  "not_at_risk" → 0
        df[TARGET_COLUMN] = df[TARGET_COLUMN].map({"not_at_risk": 0, "at_risk": 1})

        print("Target dtype:", df[TARGET_COLUMN].dtype)
        print("Target distribution:\n", df[TARGET_COLUMN].value_counts())

        notify(
            "engineer_features",
            f"Features ready: {len(df.columns) - 1} inputs + '{TARGET_COLUMN}' target "
            f"· {len(df):,} rows",
        )

        # Snapshot the engineered DataFrame BEFORE scaling so the viewer
        # shows human-readable values (0/1 flags, integer tiers, etc.)
        engineered_headers = list(df.columns)
        engineered_rows    = df.astype(str).fillna("").values.tolist()

        # ── Step 4–7: DataPipeline (fill / encode / scale) ───────────────────
        # NOTE: do NOT call pipeline.remove_duplicates() here —
        # run_full_feature_pipeline() already deduplicated the data.
        # Running it again on engineered data collapses rows to ~28.
        self.pipeline = DataPipeline(df)
        self.pipeline._target_column = target_column

        notify("remove_duplicates", "Skipped — deduplication done in feature pipeline")

        notify("handle_missing", "Filling remaining missing values...")
        self._check_cancelled()
        self.pipeline.fill_missing(strategy="auto")
        notify("handle_missing", "Missing values handled")

        notify("encode_categorical", "Encoding categorical features...")
        self._check_cancelled()
        self.pipeline.encode_categorical(drop_first=False)
        notify("encode_categorical", "Categorical encoding complete")

        notify("scale_numerical", "Scaling numerical features...")
        self._check_cancelled()
        self.pipeline.scale_numerical(method="standard")
        notify("scale_numerical", "Feature scaling complete")

        # ── Step 8: Prepare feature matrix ───────────────────────────────────
        notify("prepare_features", "Preparing feature matrix...")
        self._check_cancelled()
        X, y, feature_names = self.pipeline.prepare_features(target_col=target_column)
        notify("prepare_features", f"{len(feature_names)} features · {len(X):,} samples")

        # ── Step 9: Train ─────────────────────────────────────────────────────
        notify("train_model", f"Training {model_type}...")
        self._check_cancelled()
        self.ml_service = MLService()
        self.ml_service.feature_names = feature_names
        metrics = self.ml_service.train(
            X, y,
            model_type   = model_type,
            class_weight = "balanced",   # corrects 93 % / 7 % class imbalance
        )
        notify(
            "train_model",
            f"Accuracy: {metrics['accuracy']:.2%}  "
            f"CV: {metrics['cv_mean']:.2%} ± {metrics['cv_std']:.2%}",
        )

        # ── Step 10: Save outputs ─────────────────────────────────────────────
        if save_path:
            notify("save_outputs", "Saving artifacts...")
            self._check_cancelled()
            save_dir = Path(save_path)
            save_dir.mkdir(parents=True, exist_ok=True)

            self.pipeline.to_csv(str(save_dir / "cleaned_dataset.csv"))

            self.ml_service.save_model(
                str(save_dir / "trained_model.pkl"),
                metadata={"feature_names": feature_names, "target": target_column},
            )

            fi     = self.ml_service.get_feature_importance()
            report = {
                "summary":            self.pipeline.get_summary(),
                "training":           self.ml_service.training_history,
                "feature_importance": fi.to_dict() if fi is not None else None,
            }
            with open(save_dir / "pipeline_report.json", "w") as f:
                json.dump(report, f, indent=2, default=str)

            notify("save_outputs", f"Saved to {save_dir}")

        self.results = {
            "pipeline_summary":   self.pipeline.get_summary(),
            "training_metrics":   self.ml_service.training_history,
            "feature_importance": self.ml_service.get_feature_importance(),
            "model":              self.ml_service,
            "engineered_headers": engineered_headers,
            "engineered_rows":    engineered_rows,
        }

        return self.results

    def get_cleaned_data_for_ui(self) -> tuple[list, list]:
        if self.pipeline is None:
            raise RuntimeError("Pipeline hasn't run yet.")
        return self.pipeline.to_records()