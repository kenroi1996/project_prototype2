"""Machine Learning model training and prediction service."""

import pickle
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix, accuracy_score
from sklearn.utils.class_weight import compute_sample_weight


class MLService:
    MODEL_REGISTRY = {
        "random_forest":      RandomForestClassifier,
        "gradient_boosting":  GradientBoostingClassifier,
        "logistic_regression": LogisticRegression,
    }

    # Models whose constructor accepts class_weight="balanced".
    # GradientBoosting does not — balanced via sample_weight at fit() time.
    _CLASS_WEIGHT_MODELS = {"random_forest", "logistic_regression"}

    # Minority-class threshold below which SMOTE is applied (e.g. 0.20 = 20 %)
    _SMOTE_THRESHOLD = 0.20

    def __init__(self):
        self.model: Optional[object]  = None
        self.training_history: dict   = {}
        self.feature_names: list[str] = []
        self.target_classes: list     = []
        # Fitted DataPipeline (encoders + scaler) set by the caller after training.
        # Saved in the pickle package so PredictionEngine can replay the exact
        # same transformations on prediction data before calling model.predict_proba().
        self.preprocessor = None

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def train(self,
              X: np.ndarray,
              y: np.ndarray,
              model_type: str  = "random_forest",
              test_size: float = 0.2,
              random_state: int = 42,
              **model_kwargs) -> dict:

        if model_type not in self.MODEL_REGISTRY:
            raise ValueError(f"Unknown model type: {model_type}")

        # ── Validate class distribution before splitting ─────────────────────
        unique_classes, class_counts = np.unique(y, return_counts=True)
        if len(unique_classes) < 2:
            raise ValueError(
                f"Training data contains only 1 class ({unique_classes[0]}) "
                f"— cannot train a classifier.\n\n"
                f"This usually means:\n"
                f"  • The MIS portal file has no Final_Avg_GRD column (incoming "
                f"students with no grades yet), so everyone is labeled 'not_at_risk'.\n"
                f"  • All Entrance_Exam_Score values are above the at-risk threshold "
                f"(< 60), leaving zero at-risk students.\n\n"
                f"Solution: use the HISTORICAL MIS export that includes "
                f"Final_Avg_GRD for past students. Run this SQL on your database:\n"
                f"  SELECT id_no, program, college, seccode, year, sex_code, "
                f"home_address, civil_status, religion, final_avg_grd "
                f"FROM public.mis_students WHERE final_avg_grd IS NOT NULL;"
            )
        minority_count = int(class_counts.min())
        minority_pct   = minority_count / len(y) * 100
        if minority_pct < 1.0:
            raise ValueError(
                f"Minority class has only {minority_count} samples ({minority_pct:.1f}%) "
                f"— too few to train reliably.\n\n"
                f"The historical dataset needs at least 1% at-risk students "
                f"(at least {max(20, int(len(y) * 0.01))} students with "
                f"Final_Avg_GRD >= 3.0 for the model to learn the at-risk pattern)."
            )

        # ── Train / test split ────────────────────────────────────────────────
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=test_size, random_state=random_state, stratify=y
        )

        # ── SMOTE oversampling (training split only) ──────────────────────────
        # Applied when the minority class is below _SMOTE_THRESHOLD.
        # SMOTE generates synthetic minority samples so the model sees a more
        # balanced dataset during fitting — complementary to class_weight, which
        # adjusts the loss function but does not add new signal.
        #
        # CRITICAL: SMOTE is applied ONLY to X_train / y_train. X_test / y_test
        # are never touched so evaluation metrics reflect real-world distribution.
        smote_applied = False
        smote_error   = None

        unique, counts = np.unique(y_train, return_counts=True)
        minority_pct   = counts.min() / counts.sum()

        if minority_pct < self._SMOTE_THRESHOLD:
            try:
                from imblearn.over_sampling import SMOTE

                # k_neighbors must be < minority class count
                minority_count = int(counts.min())
                k = min(5, minority_count - 1)

                if k >= 1:
                    sm = SMOTE(random_state=random_state, k_neighbors=k)
                    X_train, y_train = sm.fit_resample(X_train, y_train)
                    smote_applied = True
                    print(
                        f"[MLService] SMOTE applied — "
                        f"training set rebalanced to {len(y_train):,} samples "
                        f"(was {len(X_test) + len(y_train) - len(y_train):,})"
                    )
                else:
                    smote_error = (
                        f"Minority class too small for SMOTE "
                        f"(n={minority_count}); skipped."
                    )
                    print(f"[MLService] {smote_error}")

            except ImportError:
                smote_error = (
                    "imbalanced-learn not installed; SMOTE skipped. "
                    "Run: pip install imbalanced-learn"
                )
                print(f"[MLService] WARNING: {smote_error}")

        # ── Build model ───────────────────────────────────────────────────────
        model_class = self.MODEL_REGISTRY[model_type]
        if model_type in self._CLASS_WEIGHT_MODELS:
            model_kwargs.setdefault("class_weight", "balanced")

        self.model = model_class(random_state=random_state, **model_kwargs)

        # ── Fit ───────────────────────────────────────────────────────────────
        fit_kwargs = {}
        if model_type not in self._CLASS_WEIGHT_MODELS:
            # GradientBoosting: balance via per-sample weights.
            # After SMOTE the classes are roughly equal so weights are ~uniform;
            # before SMOTE this compensates for the skew.
            fit_kwargs["sample_weight"] = compute_sample_weight("balanced", y_train)

        self.model.fit(X_train, y_train, **fit_kwargs)

        # ── Evaluate on untouched test set ────────────────────────────────────
        y_pred   = self.model.predict(X_test)
        accuracy = accuracy_score(y_test, y_pred)

        # Balanced accuracy for CV — prevents a misleading high score caused by
        # predicting the majority class almost exclusively.
        cv_scores = cross_val_score(
            self.model, X, y, cv=5, scoring="balanced_accuracy"
        )

        # Post-SMOTE class distribution (for transparency)
        unique_post, counts_post = np.unique(y_train, return_counts=True)
        class_distribution = {int(k): int(v)
                               for k, v in zip(unique_post, counts_post)}

        self.training_history = {
            "model_type":       model_type,
            "test_size":        test_size,
            "train_samples":    len(X_train),
            "test_samples":     len(X_test),
            "accuracy":         round(accuracy, 4),
            "cv_mean":          round(cv_scores.mean(), 4),
            "cv_std":           round(cv_scores.std(), 4),
            "cv_metric":        "balanced_accuracy",
            "class_distribution": class_distribution,
            "class_balancing":  (
                "SMOTE + class_weight=balanced"
                if smote_applied and model_type in self._CLASS_WEIGHT_MODELS
                else "SMOTE + sample_weight=balanced"
                if smote_applied
                else "class_weight=balanced"
                if model_type in self._CLASS_WEIGHT_MODELS
                else "sample_weight=balanced"
            ),
            "smote_applied":    smote_applied,
            "smote_error":      smote_error,
            "classification_report": classification_report(
                y_test, y_pred, output_dict=True
            ),
            "confusion_matrix": confusion_matrix(y_test, y_pred).tolist(),
        }

        self.target_classes = list(self.model.classes_)
        return self.training_history

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_model(self, path: str | Path,
                   metadata: Optional[dict] = None) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        package = {
            "model":            self.model,
            "feature_names":    self.feature_names,
            "target_classes":   self.target_classes,
            "training_history": self.training_history,
            "metadata":         metadata or {},
            # Fitted pipeline — enables PredictionEngine to apply the same
            # LabelEncoder + StandardScaler used during training
            "preprocessor":     self.preprocessor,
        }

        with open(path, "wb") as f:
            pickle.dump(package, f)

    def load_model(self, path: str | Path) -> None:
        with open(path, "rb") as f:
            package = pickle.load(f)

        self.model            = package["model"]
        self.feature_names    = package.get("feature_names", [])
        self.target_classes   = package.get("target_classes", [])
        self.training_history = package.get("training_history", {})
        self.preprocessor     = package.get("preprocessor", None)

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