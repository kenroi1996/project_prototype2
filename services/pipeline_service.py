"""
End-to-end pipeline orchestrator.
Coordinates: Excel → Clean → Features → Model
"""

import json
from pathlib import Path
from typing import Optional, Callable

import pandas as pd

from .excel_service import read_excel_file, rows_to_dataframe
from .preprocessing_service import DataPipeline
from .ml_service import MLService


class PipelineOrchestrator:
    STEP_NAMES = [
        "read_excel", "validate", "remove_duplicates", "handle_missing",
        "encode_categorical", "scale_numerical", "generate_labels",
        "prepare_features", "train_model", "save_outputs",
    ]

    def __init__(self):
        self.pipeline: Optional[DataPipeline] = None
        self.ml_service: Optional[MLService] = None
        self.results: dict = {}
        self._cancelled: bool = False

    def cancel(self):
        self._cancelled = True

    def _check_cancelled(self):
        if self._cancelled:
            raise InterruptedError("Pipeline was cancelled by user.")

    def run(self,
            excel_path: str | Path,
            required_columns: Optional[list[str]] = None,
            target_column: str = "risk_label",
            risk_based_on: Optional[str] = None,
            risk_rules: Optional[dict] = None,
            model_type: str = "random_forest",
            save_path: Optional[str] = None,
            on_step: Optional[Callable[[str, str], None]] = None) -> dict:

        def notify(step: str, msg: str):
            if on_step:
                on_step(step, msg)

        # Step 1: Read Excel
        notify("read_excel", f"Reading {Path(excel_path).name}...")
        df = read_excel_file(excel_path)
        self.pipeline = DataPipeline(df)
        notify("read_excel", f"Loaded {len(df)} rows, {len(df.columns)} columns")

        # Step 2: Validate
        notify("validate", "Validating columns...")
        self._check_cancelled()
        validation = self.pipeline.validate_columns(required_columns)
        if not validation["valid"]:
            raise ValueError(f"Validation failed: {validation['info']}")
        notify("validate", validation["info"])

        # Step 3: Remove Duplicates
        notify("remove_duplicates", "Removing duplicate rows...")
        self._check_cancelled()
        before = len(self.pipeline.df)
        self.pipeline.remove_duplicates()
        removed = before - len(self.pipeline.df)
        notify("remove_duplicates", f"Removed {removed} duplicate rows")

        # Step 4: Handle Missing
        notify("handle_missing", "Filling missing values...")
        self._check_cancelled()
        self.pipeline.fill_missing(strategy="auto")
        notify("handle_missing", "Missing values handled")

        # Step 5: Encode Categorical
        notify("encode_categorical", "Encoding categorical features...")
        self._check_cancelled()
        self.pipeline.encode_categorical(drop_first=False)
        notify("encode_categorical", "Categorical encoding complete")

        # Step 6: Scale Numerical
        notify("scale_numerical", "Scaling numerical features...")
        self._check_cancelled()
        self.pipeline.scale_numerical(method="standard")
        notify("scale_numerical", "Feature scaling complete")

        # Step 7: Generate Risk Labels
        notify("generate_labels", "Generating risk labels...")
        self._check_cancelled()
        self.pipeline.generate_risk_labels(
            rules=risk_rules,
            target_col=target_column,
            based_on=risk_based_on
        )
        notify("generate_labels", f"Risk labels generated in column '{target_column}'")

        # Step 8: Prepare Features
        notify("prepare_features", "Preparing feature matrix...")
        self._check_cancelled()
        X, y, feature_names = self.pipeline.prepare_features(target_col=target_column)
        notify("prepare_features", f"{len(feature_names)} features prepared")

        # Step 9: Train Model
        notify("train_model", f"Training {model_type}...")
        self._check_cancelled()
        self.ml_service = MLService()
        self.ml_service.feature_names = feature_names
        metrics = self.ml_service.train(X, y, model_type=model_type)
        notify("train_model", f"Accuracy: {metrics['accuracy']:.2%}, CV: {metrics['cv_mean']:.2%}")

        # Step 10: Save Outputs
        if save_path:
            notify("save_outputs", "Saving artifacts...")
            self._check_cancelled()
            save_dir = Path(save_path)
            save_dir.mkdir(parents=True, exist_ok=True)

            csv_path = save_dir / "cleaned_dataset.csv"
            self.pipeline.to_csv(str(csv_path))

            model_path = save_dir / "trained_model.pkl"
            self.ml_service.save_model(
                str(model_path),
                metadata={"feature_names": feature_names, "target": target_column}
            )

            report_path = save_dir / "pipeline_report.json"
            report = {
                "summary": self.pipeline.get_summary(),
                "training": self.ml_service.training_history,
                "feature_importance": self.ml_service.get_feature_importance().to_dict()
                    if self.ml_service.get_feature_importance() is not None else None,
            }
            with open(report_path, "w") as f:
                json.dump(report, f, indent=2, default=str)

            notify("save_outputs", f"Saved to {save_dir}")

        self.results = {
            "pipeline_summary": self.pipeline.get_summary(),
            "training_metrics": self.ml_service.training_history,
            "feature_importance": self.ml_service.get_feature_importance(),
            "model": self.ml_service,
        }

        return self.results

    def get_cleaned_data_for_ui(self) -> tuple[list, list]:
        if self.pipeline is None:
            raise RuntimeError("Pipeline hasn't run yet.")
        return self.pipeline.to_records()