"""Machine Learning model training and prediction service."""

import pickle
import json
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix, accuracy_score


class MLService:
    MODEL_REGISTRY = {
        "random_forest": RandomForestClassifier,
        "gradient_boosting": GradientBoostingClassifier,
        "logistic_regression": LogisticRegression,
    }

    def __init__(self):
        self.model: Optional[object] = None
        self.training_history: dict = {}
        self.feature_names: list[str] = []
        self.target_classes: list = []

    def train(self,
              X: np.ndarray,
              y: np.ndarray,
              model_type: str = "random_forest",
              test_size: float = 0.2,
              random_state: int = 42,
              **model_kwargs) -> dict:
        if model_type not in self.MODEL_REGISTRY:
            raise ValueError(f"Unknown model type: {model_type}")

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=test_size, random_state=random_state, stratify=y
        )

        model_class = self.MODEL_REGISTRY[model_type]
        self.model = model_class(random_state=random_state, **model_kwargs)
        self.model.fit(X_train, y_train)

        y_pred = self.model.predict(X_test)
        accuracy = accuracy_score(y_test, y_pred)
        cv_scores = cross_val_score(self.model, X, y, cv=5)

        self.training_history = {
            "model_type": model_type,
            "test_size": test_size,
            "train_samples": len(X_train),
            "test_samples": len(X_test),
            "accuracy": round(accuracy, 4),
            "cv_mean": round(cv_scores.mean(), 4),
            "cv_std": round(cv_scores.std(), 4),
            "classification_report": classification_report(y_test, y_pred, output_dict=True),
            "confusion_matrix": confusion_matrix(y_test, y_pred).tolist(),
        }

        self.target_classes = list(self.model.classes_)
        return self.training_history

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("No model trained yet.")
        return self.model.predict(X)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("No model trained yet.")
        if not hasattr(self.model, "predict_proba"):
            raise RuntimeError("Model does not support probability predictions.")
        return self.model.predict_proba(X)

    def save_model(self, path: str | Path, metadata: Optional[dict] = None) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        package = {
            "model": self.model,
            "feature_names": self.feature_names,
            "target_classes": self.target_classes,
            "training_history": self.training_history,
            "metadata": metadata or {},
        }

        with open(path, "wb") as f:
            pickle.dump(package, f)

    def load_model(self, path: str | Path) -> None:
        with open(path, "rb") as f:
            package = pickle.load(f)

        self.model = package["model"]
        self.feature_names = package.get("feature_names", [])
        self.target_classes = package.get("target_classes", [])
        self.training_history = package.get("training_history", {})

    def get_feature_importance(self) -> Optional[pd.DataFrame]:
        if self.model is None:
            return None

        if hasattr(self.model, "feature_importances_"):
            importances = self.model.feature_importances_
        elif hasattr(self.model, "coef_"):
            importances = np.abs(self.model.coef_[0]) if self.model.coef_.ndim > 1 else np.abs(self.model.coef_)
        else:
            return None

        return pd.DataFrame({
            "feature": self.feature_names or [f"feature_{i}" for i in range(len(importances))],
            "importance": importances,
        }).sort_values("importance", ascending=False)