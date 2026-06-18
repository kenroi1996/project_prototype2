"""
Machine Learning inference service.
=====================================
MLService is intentionally inference-only.

ALL training logic (feature engineering, SMOTE, stratified CV, threshold
optimisation, metrics, model persistence) lives exclusively in TrainingEngine.
MLService is called AFTER training, by PredictionEngine, to serve
predict / predict_proba on already-encoded feature arrays.

Previous responsibilities removed from this class
--------------------------------------------------
  ✗  train()                — now in TrainingEngine._train_sklearn()
  ✗  SMOTE oversampling     — now inside TrainingEngine CV fold loop
  ✗  cross_val_score        — now StratifiedKFold in TrainingEngine
  ✗  accuracy / CV metrics  — now Recall / F1 / PR-AUC in TrainingEngine
  ✗  save_model()           — now ModelRegistry.save_model()
  ✗  load_model()           — now ModelRegistry.load_latest_model()
  ✗  MODEL_REGISTRY dict    — only RF is used; no dispatch table needed

The preprocessor (fitted DataPipeline with LabelEncoders + StandardScaler)
is stored here after being loaded from ModelRegistry so PredictionEngine can
call ml_service.preprocessor without knowing about the registry.
"""

from typing import Optional

import numpy as np
import pandas as pd


class MLService:

    def __init__(self):
        self.model          = None
        self.feature_names: list[str] = []
        self.target_classes: list     = []
        self.preprocessor             = None   # fitted DataPipeline
        self.training_history: dict   = {}

    # ------------------------------------------------------------------
    # Load from a ModelRegistry result dict
    # ------------------------------------------------------------------

    def load_from_registry(self, registry_result: dict) -> None:
        """
        Populate MLService from the dict returned by
        ModelRegistry.load_latest_model() / load_active_from_db().

        Parameters
        ----------
        registry_result : dict with keys
            model, feature_names, target_classes,
            training_history, preprocessor
        """
        if registry_result is None:
            raise ValueError(
                "No compatible model artifact found. "
                "Retrain the model on the Model Training page."
            )
        self.model            = registry_result["model"]
        self.feature_names    = registry_result.get("feature_names", [])
        self.target_classes   = registry_result.get("target_classes", [])
        self.training_history = registry_result.get("training_history", {})
        self.preprocessor     = registry_result.get("preprocessor")

        if self.preprocessor is None:
            print(
                "[MLService] WARNING: loaded model has no preprocessor. "
                "Risk scores may be 0 or 100. Retrain to fix."
            )

        print(
            f"[MLService] Loaded — model={type(self.model).__name__}, "
            f"features={len(self.feature_names)}, "
            f"preprocessor={'yes' if self.preprocessor else 'NO'}"
        )

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def predict(self, X: np.ndarray) -> np.ndarray:
        self._require_model()
        return self.model.predict(X)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        self._require_model()
        if not hasattr(self.model, "predict_proba"):
            raise RuntimeError("Model does not support probability predictions.")
        return self.model.predict_proba(X)

    # ------------------------------------------------------------------
    # Feature importance
    # ------------------------------------------------------------------

    def get_feature_importance(self) -> Optional[pd.DataFrame]:
        if self.model is None:
            return None

        if hasattr(self.model, "feature_importances_"):
            importances = self.model.feature_importances_
        elif hasattr(self.model, "coef_"):
            importances = (
                np.abs(self.model.coef_[0])
                if self.model.coef_.ndim > 1
                else np.abs(self.model.coef_)
            )
        else:
            return None

        names = self.feature_names or [
            f"feature_{i}" for i in range(len(importances))
        ]
        return (
            pd.DataFrame({"feature": names, "importance": importances})
            .sort_values("importance", ascending=False)
            .reset_index(drop=True)
        )

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _require_model(self) -> None:
        if self.model is None:
            raise RuntimeError(
                "No model loaded. Call load_from_registry() first, "
                "or retrain on the Model Training page."
            )