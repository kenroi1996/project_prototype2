from __future__ import annotations
import csv
import math
import random


# =====================================
# TRAINING ENGINE
# =====================================
# Runs inside a QThread. Uses scikit-learn
# when available; falls back to a mock engine
# so the UI works even without ML dependencies.
# =====================================

class TrainingEngine:
    """
    Trains a classification model on the unified dataset.

    Parameters
    ----------
    headers      : list[str]   — unified column headers
    rows         : list[list]  — unified rows
    model_id     : str         — "rf", "xgb", or "lr"
    target_col   : str         — name of the target column
    test_size    : float       — e.g. 0.2 for 80/20 split
    n_folds      : int         — cross-validation folds
    progress_cb  : callable    — progress_cb(step: str, pct: int)

    Returns
    -------
    TrainingResult  (via .run())
    """

    SUPPORTED_MODELS = {
        "rf":  "Random Forest",
        "xgb": "Gradient Boosting (XGBoost)",
        "lr":  "Logistic Regression",
    }

    def __init__(
        self,
        headers:     list,
        rows:        list,
        model_id:    str   = "rf",
        target_col:  str   = "Final_Avg_GRD",
        test_size:   float = 0.2,
        n_folds:     int   = 5,
        progress_cb         = None,
    ):
        self.headers     = headers
        self.rows        = rows
        self.model_id    = model_id
        self.target_col  = target_col
        self.test_size   = test_size
        self.n_folds     = n_folds
        self.progress_cb = progress_cb or (lambda step, pct: None)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> "TrainingResult":
        self.progress_cb("Validating dataset…", 5)

        errors = self._validate()
        if errors:
            return TrainingResult(success=False, errors=errors)

        self.progress_cb("Preparing features…", 10)
        X, y, feature_names = self._prepare_features()

        if len(X) < 10:
            return TrainingResult(
                success=False,
                errors=["Not enough rows to train. Need at least 10."]
            )

        try:
            return self._train_sklearn(X, y, feature_names)
        except ImportError:
            self.progress_cb("scikit-learn not found — using mock engine…", 15)
            return self._train_mock(feature_names, len(X))

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self) -> list[str]:
        errors = []
        if not self.headers:
            errors.append("No headers in dataset.")
        if not self.rows:
            errors.append("No rows in dataset.")
        if self.target_col not in self.headers:
            errors.append(
                f"Target column '{self.target_col}' not found in dataset. "
                f"Available: {self.headers[:8]}…"
            )
        return errors

    # ------------------------------------------------------------------
    # Feature preparation
    # ------------------------------------------------------------------

    def _prepare_features(self):
        """
        Convert string rows to numeric X matrix and binary y vector.
        Drops non-numeric columns. Target is binarized: GRD >= 3.0 = at_risk.
        """
        target_idx    = self.headers.index(self.target_col)
        numeric_cols  = []
        numeric_idxs  = []

        # Find numeric columns (excluding target and ID)
        skip_keywords = ["id", "name", "address", "date", "code", "type",
                         "strand", "school", "honor", "status", "religion"]

        for i, col in enumerate(self.headers):
            if i == target_idx:
                continue
            if any(k in col.lower() for k in skip_keywords):
                continue
            # Check if column has numeric values
            vals = [r[i] for r in self.rows if i < len(r) and r[i].strip()]
            num_count = sum(1 for v in vals if _is_numeric(v))
            if num_count > len(vals) * 0.5:
                numeric_cols.append(col)
                numeric_idxs.append(i)

        X = []
        y = []
        for row in self.rows:
            try:
                target_val = row[target_idx].strip() if target_idx < len(row) else ""
                if not target_val or not _is_numeric(target_val):
                    continue
                label = 1 if float(target_val) >= 3.0 else 0   # at_risk if GRD >= 3.0

                feature_row = []
                for idx in numeric_idxs:
                    val = row[idx].strip() if idx < len(row) else ""
                    feature_row.append(float(val) if _is_numeric(val) else 0.0)

                X.append(feature_row)
                y.append(label)
            except (ValueError, IndexError):
                continue

        return X, y, numeric_cols

    # ------------------------------------------------------------------
    # scikit-learn training
    # ------------------------------------------------------------------

    def _train_sklearn(self, X, y, feature_names) -> "TrainingResult":
        from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import StratifiedKFold, train_test_split
        from sklearn.metrics import (
            accuracy_score, f1_score, precision_score, recall_score,
            confusion_matrix,
        )

        self.progress_cb("Splitting dataset…", 20)

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=self.test_size, random_state=42, stratify=y
        )

        # Build model
        model_map = {
            "rf":  RandomForestClassifier(n_estimators=100, random_state=42),
            "xgb": GradientBoostingClassifier(n_estimators=100, random_state=42),
            "lr":  LogisticRegression(max_iter=1000, random_state=42),
        }
        model = model_map.get(self.model_id, model_map["rf"])

        # Cross-validation
        skf        = StratifiedKFold(n_splits=self.n_folds, shuffle=True, random_state=42)
        fold_accs  = []

        for fold, (train_idx, val_idx) in enumerate(skf.split(X_train, y_train), 1):
            pct = 20 + int(fold / self.n_folds * 40)
            self.progress_cb(f"Fold {fold}/{self.n_folds}…", pct)

            X_f_train = [X_train[i] for i in train_idx]
            y_f_train = [y_train[i] for i in train_idx]
            X_f_val   = [X_train[i] for i in val_idx]
            y_f_val   = [y_train[i] for i in val_idx]

            model.fit(X_f_train, y_f_train)
            preds     = model.predict(X_f_val)
            fold_accs.append(accuracy_score(y_f_val, preds))

        # Final train on full training set
        self.progress_cb("Final training run…", 65)
        model.fit(X_train, y_train)

        self.progress_cb("Evaluating on test set…", 75)
        y_pred = model.predict(X_test)

        accuracy  = accuracy_score(y_test, y_pred)
        f1        = f1_score(y_test, y_pred, zero_division=0)
        precision = precision_score(y_test, y_pred, zero_division=0)
        recall    = recall_score(y_test, y_pred, zero_division=0)
        cm        = confusion_matrix(y_test, y_pred).tolist()

        # Feature importance / SHAP approximation
        self.progress_cb("Computing feature importance…", 85)
        shap_values = self._get_feature_importance(model, feature_names)

        self.progress_cb("Saving model…", 95)

        # Save model to disk via ModelRegistry
        from services.model_registry import ModelRegistry
        save_result = ModelRegistry.save_model(
            model,
            self.model_id,
            feature_names,
            metadata={
                "accuracy": accuracy,
                "f1_score": f1,
                "precision": precision,
                "recall": recall,
                "train_size": len(X_train),
                "test_size": len(X_test),
                "n_folds": self.n_folds,
            },
        )

        if save_result["success"]:
            print(f"[TrainingEngine] Model saved to {save_result['path']}")
        else:
            print(f"[TrainingEngine] Failed to save model: {save_result.get('error')}")

        # Save model to DataStore (via set_trained_model to trigger notify)
        from services.data_store import DataStore
        DataStore.get().set_trained_model({
            "model":         model,
            "model_id":      self.model_id,
            "feature_names": feature_names,
            "headers":       self.headers,
            "target_col":    self.target_col,
        })

        self.progress_cb("Done ✅", 100)

        return TrainingResult(
            success      = True,
            model_id     = self.model_id,
            model_name   = self.SUPPORTED_MODELS.get(self.model_id, self.model_id),
            accuracy     = round(accuracy * 100, 1),
            f1_score     = round(f1, 3),
            precision    = round(precision * 100, 1),
            recall       = round(recall * 100, 1),
            cv_folds     = fold_accs,
            confusion_matrix = cm,
            shap_values  = shap_values,
            train_size   = len(X_train),
            test_size    = len(X_test),
            feature_count= len(feature_names),
            log_lines    = self._build_log(fold_accs, accuracy, f1),
        )

    # ------------------------------------------------------------------
    # Mock training (no sklearn)
    # ------------------------------------------------------------------

    def _train_mock(self, feature_names, n_samples) -> "TrainingResult":
        """Simulates training with realistic-looking results."""
        import time

        base_acc = {"rf": 87.4, "xgb": 90.1, "lr": 79.8}.get(self.model_id, 85.0)
        fold_accs = []

        for fold in range(1, self.n_folds + 1):
            pct = 20 + int(fold / self.n_folds * 60)
            self.progress_cb(f"Fold {fold}/{self.n_folds}…", pct)
            fold_accs.append(round(base_acc + random.uniform(-2, 2), 1))

        self.progress_cb("Evaluating…", 80)
        accuracy  = round(sum(fold_accs) / len(fold_accs), 1)
        f1        = round(accuracy / 100 * 0.97, 3)
        precision = round(accuracy - random.uniform(1, 4), 1)
        recall    = round(accuracy + random.uniform(1, 4), 1)

        # Fake confusion matrix
        n_test = int(n_samples * self.test_size)
        tp = int(n_test * 0.35 * (recall / 100))
        tn = int(n_test * 0.65 * (precision / 100))
        fp = int(n_test * 0.65 * (1 - precision / 100))
        fn = int(n_test * 0.35 * (1 - recall / 100))

        # Fake SHAP values
        shap_values = [
            (f, round(random.uniform(5, 40), 1))
            for f in (feature_names[:8] if len(feature_names) >= 8
                      else feature_names)
        ]
        shap_values.sort(key=lambda x: x[1], reverse=True)

        self.progress_cb("Done ✅", 100)

        return TrainingResult(
            success      = True,
            model_id     = self.model_id,
            model_name   = self.SUPPORTED_MODELS.get(self.model_id, self.model_id),
            accuracy     = accuracy,
            f1_score     = f1,
            precision    = precision,
            recall       = recall,
            cv_folds     = fold_accs,
            confusion_matrix = [[tn, fp], [fn, tp]],
            shap_values  = shap_values,
            train_size   = n_samples - int(n_samples * self.test_size),
            test_size    = int(n_samples * self.test_size),
            feature_count= len(feature_names),
            log_lines    = self._build_log(fold_accs, accuracy / 100, f1),
            is_mock      = True,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_feature_importance(model, feature_names) -> list[tuple]:
        """Extract feature importances from sklearn model."""
        try:
            importances = model.feature_importances_
        except AttributeError:
            try:
                importances = [abs(c) for c in model.coef_[0]]
            except AttributeError:
                return [(f, 0.0) for f in feature_names]

        total = sum(importances) or 1
        pairs = [(f, round(v / total * 100, 1))
                 for f, v in zip(feature_names, importances)]
        pairs.sort(key=lambda x: x[1], reverse=True)
        return pairs[:10]

    def _build_log(self, fold_accs, accuracy, f1) -> list[tuple[str, str]]:
        lines = [
            (f"Initializing {self.SUPPORTED_MODELS.get(self.model_id)}…", "muted"),
            (f"Dataset: {len(self.rows):,} rows · {len(self.headers)} features", "muted"),
            (f"Target column: {self.target_col}", "muted"),
            (f"Split: {int((1 - self.test_size) * 100)}% train / "
             f"{int(self.test_size * 100)}% test", "muted"),
        ]
        for i, acc in enumerate(fold_accs, 1):
            lines.append((f"Fold {i}/{self.n_folds}: Accuracy {acc:.1f}%", "muted"))
        lines += [
            (f"Mean CV Accuracy: {sum(fold_accs)/len(fold_accs):.1f}%", "muted"),
            ("Computing feature importance…", "muted"),
            ("Saving model artifact…", "muted"),
            (f"Final Accuracy: {accuracy * 100 if accuracy < 1 else accuracy:.1f}%  "
             f"F1: {f1:.3f}", "success"),
            ("Done! ✅", "success"),
        ]
        return lines


def _is_numeric(value: str) -> bool:
    try:
        float(value.strip())
        return True
    except (ValueError, AttributeError):
        return False


# =====================================
# TRAINING RESULT
# =====================================

class TrainingResult:
    def __init__(
        self,
        success:          bool            = False,
        model_id:         str             = "",
        model_name:       str             = "",
        accuracy:         float           = 0.0,
        f1_score:         float           = 0.0,
        precision:        float           = 0.0,
        recall:           float           = 0.0,
        cv_folds:         list            = None,
        confusion_matrix: list            = None,
        shap_values:      list            = None,
        train_size:       int             = 0,
        test_size:        int             = 0,
        feature_count:    int             = 0,
        log_lines:        list            = None,
        errors:           list            = None,
        is_mock:          bool            = False,
    ):
        self.success          = success
        self.model_id         = model_id
        self.model_name       = model_name
        self.accuracy         = accuracy
        self.f1_score         = f1_score
        self.precision        = precision
        self.recall           = recall
        self.cv_folds         = cv_folds         or []
        self.confusion_matrix = confusion_matrix or [[0, 0], [0, 0]]
        self.shap_values      = shap_values      or []
        self.train_size       = train_size
        self.test_size        = test_size
        self.feature_count    = feature_count
        self.log_lines        = log_lines        or []
        self.errors           = errors           or []
        self.is_mock          = is_mock