"""
services/xgb_service.py
────────────────────────
XGBoost regression model: train, save, load, predict.

Target variable : Final_Avg_GRD  (continuous, 1.0–5.0 Philippine GPA scale)
Task            : Regression (predict grade → threshold → at-risk flag)

Rules
─────
• No Qt imports — pure ML logic only.
• Caller (TrainingWorker) is responsible for passing progress callbacks.
• Model is saved as XGBoost's native JSON format for portability.
• All paths come from config.py — never hardcoded.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

from config import AT_RISK_THRESHOLD, MODEL_PATH

try:
    import xgboost as xgb
    _XGB_AVAILABLE = True
except ImportError:
    _XGB_AVAILABLE = False
    print("[XGBService] xgboost not installed — mock mode active.")


# ── Type alias ────────────────────────────────────────────────────────────────
ProgressCB = Callable[[str, int], None]   # (message, percent 0-100)


class XGBService:
    """
    Wraps XGBoost regressor lifecycle: train → save → load → predict.

    The model predicts Final_Avg_GRD (continuous).
    Use ``is_at_risk()`` to convert a prediction to a boolean flag.
    """

    def __init__(self) -> None:
        self.model: Optional[Any] = None        # xgb.XGBRegressor or mock
        self.feature_names: list[str] = []
        self.training_history: dict = {}
        self._is_mock: bool = False

    # ── Training ──────────────────────────────────────────────────────────────

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        feature_names: list[str],
        test_size: float = 0.2,
        n_estimators: int = 300,
        max_depth: int = 6,
        learning_rate: float = 0.05,
        progress_cb: Optional[ProgressCB] = None,
    ) -> dict:
        """
        Train XGBoost on (X, y).

        Parameters
        ----------
        X              : feature matrix (n_samples, n_features)
        y              : target vector  (Final_Avg_GRD values)
        feature_names  : column names matching X
        test_size      : fraction held out for evaluation
        n_estimators   : boosting rounds
        max_depth      : tree depth
        learning_rate  : eta
        progress_cb    : optional callable(msg: str, pct: int)

        Returns
        -------
        dict  training history (rmse, mae, r2, feature_importance)
        """
        cb = progress_cb or (lambda m, p: None)

        self.feature_names = list(feature_names)

        cb("Splitting dataset…", 10)
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score

        X_tr, X_te, y_tr, y_te = train_test_split(
            X, y, test_size=test_size, random_state=42
        )

        if not _XGB_AVAILABLE:
            cb("xgboost not found — using mock regressor…", 20)
            self._train_mock(X_tr, y_tr, feature_names)
        else:
            cb("Fitting XGBoost regressor…", 20)
            self.model = xgb.XGBRegressor(
                n_estimators=n_estimators,
                max_depth=max_depth,
                learning_rate=learning_rate,
                subsample=0.8,
                colsample_bytree=0.8,
                random_state=42,
                n_jobs=-1,
                eval_metric="rmse",
            )
            self.model.fit(
                X_tr, y_tr,
                eval_set=[(X_te, y_te)],
                verbose=False,
            )
            self._is_mock = False

        cb("Evaluating on test set…", 75)
        y_pred = self.predict(X_te)

        rmse = float(np.sqrt(mean_squared_error(y_te, y_pred)))
        mae  = float(mean_absolute_error(y_te, y_pred))
        r2   = float(r2_score(y_te, y_pred))

        cb("Computing feature importance…", 90)
        importance = self.get_feature_importance()

        self.training_history = {
            "rmse":               round(rmse, 4),
            "mae":                round(mae, 4),
            "r2":                 round(r2, 4),
            "train_size":         len(X_tr),
            "test_size":          len(X_te),
            "n_estimators":       n_estimators,
            "feature_importance": importance,
            "is_mock":            self._is_mock,
        }

        cb("Done ✅", 100)
        return self.training_history

    # ── Prediction ────────────────────────────────────────────────────────────

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Return predicted Final_Avg_GRD values."""
        if self.model is None:
            raise RuntimeError("No model loaded. Call train() or load() first.")
        return np.array(self.model.predict(X), dtype=float)

    def predict_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Predict from a DataFrame.
        Adds columns: predicted_grd, at_risk.
        """
        X = df[self.feature_names].values
        preds = self.predict(X)
        result = df.copy()
        result["predicted_grd"] = np.round(preds, 4)
        result["at_risk"] = preds >= AT_RISK_THRESHOLD
        return result

    @staticmethod
    def is_at_risk(predicted_grd: float) -> bool:
        """True if the predicted grade meets or exceeds the risk threshold."""
        return predicted_grd >= AT_RISK_THRESHOLD

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self, path: Optional[Path] = None) -> Path:
        """
        Save model to disk as XGBoost JSON.
        Returns the path it was saved to.
        """
        if self.model is None:
            raise RuntimeError("No model to save.")
        save_path = Path(path or MODEL_PATH)
        save_path.parent.mkdir(parents=True, exist_ok=True)

        if _XGB_AVAILABLE and not self._is_mock:
            self.model.save_model(str(save_path))
        else:
            # Pickle fallback for mock
            import pickle
            pkl_path = save_path.with_suffix(".pkl")
            with open(pkl_path, "wb") as f:
                pickle.dump(self.model, f)
            save_path = pkl_path

        print(f"[XGBService] Model saved to {save_path}")
        return save_path

    def load(self, path: Optional[Path] = None) -> None:
        """Load a saved model from disk."""
        load_path = Path(path or MODEL_PATH)
        if not load_path.exists():
            # Try pickle fallback
            pkl = load_path.with_suffix(".pkl")
            if pkl.exists():
                import pickle
                with open(pkl, "rb") as f:
                    self.model = pickle.load(f)
                self._is_mock = True
                print(f"[XGBService] Loaded mock model from {pkl}")
                return
            raise FileNotFoundError(f"No model file found at {load_path}")

        if _XGB_AVAILABLE:
            self.model = xgb.XGBRegressor()
            self.model.load_model(str(load_path))
            self._is_mock = False
        else:
            import pickle
            with open(load_path, "rb") as f:
                self.model = pickle.load(f)
            self._is_mock = True
        print(f"[XGBService] Model loaded from {load_path}")

    # ── Feature importance ────────────────────────────────────────────────────

    def get_feature_importance(self) -> list[tuple[str, float]]:
        """
        Returns [(feature_name, importance_score), ...] sorted descending.
        """
        if self.model is None:
            return []
        try:
            if _XGB_AVAILABLE and not self._is_mock:
                scores = self.model.feature_importances_
            else:
                import random
                scores = [random.uniform(0.01, 0.40)
                          for _ in self.feature_names]
            total = sum(scores) or 1.0
            pairs = [
                (name, round(score / total, 4))
                for name, score in zip(self.feature_names, scores)
            ]
            return sorted(pairs, key=lambda x: x[1], reverse=True)
        except Exception:
            return []

    # ── Mock ──────────────────────────────────────────────────────────────────

    def _train_mock(self, X, y, feature_names) -> None:
        """Lightweight mock so the UI works without xgboost installed."""
        import random

        class _MockRegressor:
            def __init__(self, mean_y: float, feature_importances_):
                self._mean = mean_y
                self.feature_importances_ = feature_importances_

            def predict(self, X):
                return np.array(
                    [self._mean + random.gauss(0, 0.3) for _ in range(len(X))]
                )

            def save_model(self, path):
                pass

        mean_y = float(np.mean(y)) if len(y) else 2.5
        fi = [random.uniform(0.01, 0.4) for _ in feature_names]
        total = sum(fi) or 1.0
        fi = [v / total for v in fi]

        self.model    = _MockRegressor(mean_y, fi)
        self._is_mock = True
