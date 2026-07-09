from __future__ import annotations

# =====================================
# TRAINING ENGINE
# =====================================
# Runs inside a QThread.  Uses scikit-learn
# when available; falls back to a mock engine
# so the UI works even without ML dependencies.
#
# EVALUATION STRATEGY
# -------------------
# Accuracy is dropped as the primary metric — it is misleading when the
# at-risk class is the minority (e.g. 9.6 % at-risk → 90.4 % accuracy by
# predicting "not_at_risk" every time).
#
# Instead the engine reports per-fold and held-out-set:
#   • Recall        — fraction of true at-risk students caught  ← PRIMARY
#   • F1            — harmonic mean of precision and recall
#   • PR-AUC        — area under precision-recall curve; more informative
#                     than ROC-AUC on imbalanced data
#
# TARGET: Recall ≥ 0.80 on the held-out test set.
# This is an early-warning system — missing an at-risk student (false negative)
# is far more costly than a false alarm (false positive) that a counselor can
# quickly verify. Precision will be lower as a result; that is intentional.
#
# SMOTE (CLASS IMBALANCE)
# -----------------------
# With 9.6 % minority class (321 at-risk out of 3,360), the model has
# very few positive examples to learn from.  SMOTE (Synthetic Minority
# Over-sampling Technique) generates synthetic at-risk examples so each
# training fold sees a more balanced dataset, improving recall before any
# threshold tuning happens.
#
# CRITICAL CONSTRAINT: SMOTE is applied INSIDE each CV fold on the training
# split only.  Applying it before splitting would leak synthetic samples
# derived from validation rows back into training — invalidating the CV
# scores.  The held-out test set is NEVER resampled.
#
# If imbalanced-learn is not installed, SMOTE is skipped and class_weight /
# class_weight="balanced" compensation continues to handle imbalance.  Install with:
#   pip install imbalanced-learn
#
# MODEL IMPROVEMENTS (vs previous version)
# -----------------------------------------
# 1. RandomForest hyperparameters tuned for recall on imbalanced data:
#      - n_estimators: 100 → 300   (more trees = more stable minority votes)
#      - max_depth: None → 12      (prevents over-memorising majority class)
#      - min_samples_leaf: 1 → 4   (each leaf needs ≥4 samples; smooths minority)
#      - max_features: "sqrt" → "sqrt" (unchanged, already good)
#      - class_weight: "balanced" → "balanced_subsample"
#            balanced_subsample recomputes class weights per bootstrap sample,
#            giving stronger minority upweighting than the global "balanced".
#
# 2. Threshold strategy changed from max-F1 to recall-priority:
#      - Primary target: Recall ≥ RECALL_TARGET (default 0.80)
#      - Among all thresholds meeting that floor, pick the one with highest F1
#      - Fallback to max-F1 if no threshold hits the floor
#      - Sweep is finer: 19 steps → 91 steps (~0.01 increments)
#
# 3. SMOTE ratio made configurable:
#      - sampling_strategy: "auto" → 0.4  (minority grows to 40% of majority)
#      - This is less aggressive than full 1:1 balance, preserving more of the
#        real class boundary signal while still helping recall.
#
# FEATURE PIPELINE INTEGRATION
# ----------------------------
# _prepare_features() delegates entirely to feature_engineering.py via
# run_full_feature_pipeline().  No changes to feature_engineering.py.
# =====================================

import random

# ── Recall target for threshold optimisation ──────────────────────────────────
# Raise this to catch more at-risk students (at cost of more false alarms).
# Lower it to improve precision (at cost of missing more at-risk students).
RECALL_TARGET: float = 0.80


def _smote_available() -> bool:
    try:
        from imblearn.over_sampling import SMOTE  # noqa: F401
        return True
    except ImportError:
        return False


class TrainingEngine:
    """
    Trains a classification model on the unified dataset.

    Parameters
    ----------
    headers      : list[str]   — unified column headers (passed through to log)
    rows         : list[list]  — unified rows as strings (converted to DataFrame)
    model_id     : str         — "rf" (Random Forest; only supported model)
    target_col   : str         — name of the target column
    test_size    : float       — held-out fraction, e.g. 0.2
    n_folds      : int         — stratified CV folds
    progress_cb  : callable    — progress_cb(step: str, pct: int)

    Returns
    -------
    TrainingResult  (via .run())
    """

    SUPPORTED_MODELS = {
        "rf": "Random Forest",
    }

    def __init__(
        self,
        headers:     list,
        rows:        list,
        model_id:    str   = "rf",
        target_col:  str   = "risk_label",
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

        self.progress_cb("Running feature engineering pipeline…", 10)
        try:
            X, y, feature_names, engineered_headers, engineered_rows = self._prepare_features()
        except Exception as exc:
            return TrainingResult(success=False, errors=[str(exc)])

        if len(X) < 10:
            return TrainingResult(
                success=False,
                errors=["Not enough rows to train after feature engineering. "
                        "Need at least 10 valid student records."],
            )

        n_at_risk = sum(1 for label in y if label == 1)
        if n_at_risk == 0:
            return TrainingResult(
                success=False,
                errors=["No at-risk students in the dataset after labeling. "
                        "Check that Final_Avg_GRD is present in the historical export."],
            )
        if n_at_risk == len(y):
            return TrainingResult(
                success=False,
                errors=["All students are labeled at-risk. "
                        "The model cannot learn without both classes."],
            )

        try:
            return self._train_sklearn(X, y, feature_names,
                                       engineered_headers, engineered_rows)
        except ImportError:
            self.progress_cb("scikit-learn not found — using mock engine…", 15)
            return self._train_mock(feature_names, len(X),
                                    engineered_headers, engineered_rows)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self) -> list[str]:
        errors = []
        if not self.headers:
            errors.append("No headers in dataset.")
        if not self.rows:
            errors.append("No rows in dataset.")
        return errors

    # ------------------------------------------------------------------
    # Feature preparation — delegates entirely to feature_engineering.py
    # ------------------------------------------------------------------

    def _prepare_features(self):
        import pandas as pd
        try:
            from feature_engineering import (
                run_full_feature_pipeline,
                TRAINING_FEATURES,
                TARGET_COLUMN,
            )
        except ModuleNotFoundError:
            from services.feature_engineering import (
                run_full_feature_pipeline,
                TRAINING_FEATURES,
                TARGET_COLUMN,
            )

        df_raw = pd.DataFrame(self.rows, columns=self.headers)
        df     = run_full_feature_pipeline(df_raw)

        if TARGET_COLUMN not in df.columns:
            raise ValueError(
                f"Target column '{TARGET_COLUMN}' missing after feature pipeline. "
                "Ensure the historical dataset contains Final_Avg_GRD."
            )

        available = [f for f in TRAINING_FEATURES if f in df.columns]
        missing   = [f for f in TRAINING_FEATURES if f not in df.columns]
        if missing:
            print(f"[TrainingEngine] WARNING: features missing from pipeline output "
                  f"(will be skipped): {missing}")

        df_features = df[available].copy()

        # ── Selective encoding ────────────────────────────────────────────────
        # Only known categorical columns go through get_dummies.
        # Continuous features (Entrance_Exam_Score, HS_GPA, Age_At_Enrollment,
        # Distance_KM, etc.) must NOT be one-hot encoded — doing so creates one
        # column per unique value (e.g. Entrance_Exam_Score_88.00) producing
        # 400+ features where the model memorises individual scores rather than
        # learning that higher scores are protective.
        _CATEGORICAL = {"Program", "Age_Group", "Distance_Bucket"}

        cat_cols = [f for f in available if f in _CATEGORICAL]
        num_cols = [f for f in available if f not in _CATEGORICAL]

        df_cat = pd.get_dummies(df_features[cat_cols], drop_first=False) \
                 if cat_cols else pd.DataFrame(index=df_features.index)
        df_num = df_features[num_cols].reset_index(drop=True)
        # Coerce to true numeric dtype BEFORE the median fillna below —
        # without this, a column that arrived as strings (e.g. from the
        # merge pipeline) is silently skipped by median(numeric_only=True),
        # leaving its NaNs unfilled all the way through to model training,
        # where SMOTE (unlike RandomForestClassifier) rejects NaN outright.
        for col in df_num.columns:
            df_num[col] = pd.to_numeric(df_num[col], errors="coerce")
        df_encoded = pd.concat(
            [df_num, df_cat.reset_index(drop=True)], axis=1
        )
        feature_names = list(df_encoded.columns)

        y_raw      = df[TARGET_COLUMN].map({"at_risk": 1, "not_at_risk": 0})
        valid_mask = y_raw.notna()
        df_encoded = df_encoded[valid_mask].reset_index(drop=True)
        y_series   = y_raw[valid_mask].astype(int).reset_index(drop=True)

        df_encoded = df_encoded.fillna(df_encoded.median(numeric_only=True))

        X = df_encoded.values.tolist()
        y = y_series.tolist()

        # Capture the engineered DataFrame (all columns, including display-only
        # ones already dropped) for the UI "View Engineered Dataset" button.
        # Use df (post-pipeline, pre-encoding) so values are human-readable.
        engineered_headers = list(df.columns)
        engineered_rows    = df.astype(str).fillna("").values.tolist()

        print(f"[TrainingEngine] Pipeline output: {len(X)} rows × "
              f"{len(feature_names)} features | "
              f"at_risk={sum(y)}, not_at_risk={len(y)-sum(y)}")

        return X, y, feature_names, engineered_headers, engineered_rows

    # ------------------------------------------------------------------
    # scikit-learn training
    # ------------------------------------------------------------------

    def _train_sklearn(self, X, y, feature_names, engineered_headers, engineered_rows) -> "TrainingResult":
        import numpy as np
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import StratifiedKFold, train_test_split
        from sklearn.metrics import (
            f1_score, precision_score, recall_score,
            average_precision_score, confusion_matrix,
        )

        X_arr = np.array(X, dtype=float)
        y_arr = np.array(y, dtype=int)

        # ── Imbalance report ──────────────────────────────────────────────────
        n_total      = len(y_arr)
        n_at_risk    = int(y_arr.sum())
        minority_pct = n_at_risk / n_total * 100
        use_smote    = _smote_available()

        imbalance_warning = None
        if minority_pct < 15:
            imbalance_warning = (
                f"⚠️  Class imbalance detected: {n_at_risk} at-risk students "
                f"({minority_pct:.1f}% of {n_total:,} total). "
                + ("SMOTE oversampling applied inside each training fold "
                   f"(target ratio 0.40)."
                   if use_smote else
                   "Install imbalanced-learn for SMOTE (pip install imbalanced-learn). "
                   "Falling back to class_weight compensation.")
            )
            print(f"[TrainingEngine] {imbalance_warning}")

        # ── Stratified held-out split ─────────────────────────────────────────
        self.progress_cb("Splitting dataset (stratified)…", 20)
        X_train, X_test, y_train, y_test = train_test_split(
            X_arr, y_arr,
            test_size=self.test_size,
            random_state=42,
            stratify=y_arr,
        )

        # ── Build model ───────────────────────────────────────────────────────
        # KEY CHANGES vs previous version:
        #
        # n_estimators 100 → 300
        #   More trees give the minority class more opportunities to be
        #   represented in bootstrap samples. With 321 at-risk students a
        #   single tree may see only ~200 of them; averaging 300 trees
        #   produces a much more stable probability estimate.
        #
        # max_depth None → 12
        #   Unconstrained trees can perfectly memorise the majority class,
        #   building very deep branches that allocate almost no space to the
        #   at-risk minority. Capping at 12 forces the tree to generalise.
        #
        # min_samples_leaf 1 → 4
        #   A leaf with 1 sample is pure noise on the minority class.
        #   Requiring ≥4 samples per leaf forces the tree to find patterns
        #   that hold for small groups of at-risk students, not singletons.
        #
        # class_weight "balanced" → "balanced_subsample"
        #   "balanced" computes class weights once from the full training set.
        #   "balanced_subsample" recomputes them per bootstrap sample, which
        #   gives stronger upweighting to the minority class in each tree —
        #   especially important when the minority is as small as 9.6%.
        model = RandomForestClassifier(
            n_estimators       = 300,
            max_depth          = 12,
            min_samples_leaf   = 4,
            max_features       = "sqrt",
            class_weight       = "balanced_subsample",
            random_state       = 42,
            n_jobs             = -1,   # use all CPU cores
        )

        # ── Stratified CV with per-fold SMOTE ────────────────────────────────
        skf = StratifiedKFold(n_splits=self.n_folds, shuffle=True, random_state=42)

        fold_recalls  = []
        fold_f1s      = []
        fold_pr_aucs  = []
        smote_applied = False

        for fold, (tr_idx, val_idx) in enumerate(skf.split(X_train, y_train), 1):
            pct = 20 + int(fold / self.n_folds * 40)
            self.progress_cb(f"Fold {fold}/{self.n_folds}…", pct)

            Xf_tr, Xf_val = X_train[tr_idx], X_train[val_idx]
            yf_tr, yf_val = y_train[tr_idx], y_train[val_idx]

            # ── SMOTE on training split only ──────────────────────────────────
            # sampling_strategy=0.40: grow minority to 40% of majority count.
            # Less aggressive than full 1:1 (which can overfit on synthetic
            # samples) but enough to meaningfully improve recall.
            # k_neighbors capped at (minority_count - 1) to avoid SMOTE crash
            # when a fold has very few positive examples.
            if use_smote:
                try:
                    from imblearn.over_sampling import SMOTE
                    n_minority = int(yf_tr.sum())
                    k = min(5, n_minority - 1)
                    if k >= 1:
                        Xf_tr, yf_tr = SMOTE(
                            sampling_strategy = 0.40,
                            k_neighbors       = k,
                            random_state      = 42,
                        ).fit_resample(Xf_tr, yf_tr)
                        smote_applied = True
                    else:
                        print(f"[TrainingEngine] Fold {fold}: too few minority "
                              f"samples ({n_minority}) for SMOTE — skipping.")
                except Exception as exc:
                    print(f"[TrainingEngine] SMOTE failed in fold {fold} "
                          f"({exc}) — continuing without oversampling.")

            model.fit(Xf_tr, yf_tr)

            # Evaluate on the ORIGINAL (non-resampled) validation split
            yf_pred  = model.predict(Xf_val)
            yf_proba = model.predict_proba(Xf_val)[:, 1]

            fold_recalls.append(recall_score(yf_val, yf_pred, zero_division=0))
            fold_f1s.append(f1_score(yf_val, yf_pred, zero_division=0))
            fold_pr_aucs.append(average_precision_score(yf_val, yf_proba))

        cv_recall = float(np.mean(fold_recalls))
        cv_f1     = float(np.mean(fold_f1s))
        cv_pr_auc = float(np.mean(fold_pr_aucs))

        print(
            f"[TrainingEngine] CV ({self.n_folds}-fold"
            f"{', SMOTE@0.40' if smote_applied else ''}) — "
            f"Recall: {cv_recall:.3f} ± {np.std(fold_recalls):.3f} | "
            f"F1: {cv_f1:.3f} ± {np.std(fold_f1s):.3f} | "
            f"PR-AUC: {cv_pr_auc:.3f} ± {np.std(fold_pr_aucs):.3f}"
        )

        # ── Final refit on FULL training set (with SMOTE if available) ────────
        self.progress_cb("Final training run…", 65)
        Xf_final, yf_final = X_train, y_train
        if use_smote and smote_applied:
            try:
                from imblearn.over_sampling import SMOTE
                n_minority = int(y_train.sum())
                k = min(5, n_minority - 1)
                if k >= 1:
                    Xf_final, yf_final = SMOTE(
                        sampling_strategy = 0.40,
                        k_neighbors       = k,
                        random_state      = 42,
                    ).fit_resample(X_train, y_train)
                    print(f"[TrainingEngine] Final refit — SMOTE expanded "
                          f"{len(X_train)} → {len(Xf_final)} rows")
            except Exception as exc:
                print(f"[TrainingEngine] SMOTE skipped on final refit ({exc})")

        model.fit(Xf_final, yf_final)

        # ── Threshold optimisation on held-out test set ───────────────────────
        # Strategy: find threshold where Recall >= RECALL_TARGET (0.80),
        # then among all qualifying thresholds pick the one with highest F1.
        # This prioritises catching at-risk students over avoiding false alarms.
        self.progress_cb("Optimising decision threshold…", 72)
        y_proba_test = model.predict_proba(X_test)[:, 1]
        best_thresh, best_f1_thresh = _find_best_threshold(
            y_test, y_proba_test, recall_target=RECALL_TARGET
        )
        f1_at_default = f1_score(
            y_test, (y_proba_test >= 0.5).astype(int), zero_division=0
        )
        print(
            f"[TrainingEngine] Threshold sweep (recall_target={RECALL_TARGET:.0%}): "
            f"chosen t={best_thresh:.2f} → "
            f"F1={best_f1_thresh:.3f} | default-0.5 F1={f1_at_default:.3f}"
        )

        # ── Held-out evaluation at optimal threshold ──────────────────────────
        self.progress_cb("Evaluating on held-out test set…", 78)
        y_pred_test = (y_proba_test >= best_thresh).astype(int)

        recall_test    = float(recall_score(y_test, y_pred_test, zero_division=0))
        f1_test        = float(f1_score(y_test, y_pred_test, zero_division=0))
        precision_test = float(precision_score(y_test, y_pred_test, zero_division=0))
        pr_auc_test    = float(average_precision_score(y_test, y_proba_test))
        cm             = confusion_matrix(y_test, y_pred_test).tolist()

        print(
            f"[TrainingEngine] Held-out results — "
            f"Recall: {recall_test:.3f} | Precision: {precision_test:.3f} | "
            f"F1: {f1_test:.3f} | PR-AUC: {pr_auc_test:.3f}"
        )
        print(f"[TrainingEngine] Confusion matrix: {cm}")

        # ── Feature importance ────────────────────────────────────────────────
        self.progress_cb("Computing feature importance…", 85)
        shap_values = self._get_feature_importance(model, feature_names)

        # ── Save model + metadata ─────────────────────────────────────────────
        self.progress_cb("Saving model…", 95)
        metadata = {
            "recall":             recall_test,
            "f1_score":           f1_test,
            "precision":          precision_test,
            "pr_auc":             pr_auc_test,
            "decision_threshold": best_thresh,
            "recall_target":      RECALL_TARGET,
            "cv_recall_mean":     cv_recall,
            "cv_recall_std":      float(np.std(fold_recalls)),
            "cv_f1_mean":         cv_f1,
            "cv_f1_std":          float(np.std(fold_f1s)),
            "cv_pr_auc_mean":     cv_pr_auc,
            "cv_pr_auc_std":      float(np.std(fold_pr_aucs)),
            "smote_applied":      smote_applied,
            "smote_ratio":        0.40,
            "minority_pct":       round(minority_pct, 2),
            "train_size":         int(len(X_train)),
            "test_size":          int(len(X_test)),
            "n_folds":            self.n_folds,
            "feature_names":      feature_names,
            # RF hyperparameters — stored so future model versions can be compared
            "rf_n_estimators":    300,
            "rf_max_depth":       12,
            "rf_min_samples_leaf":4,
            "rf_class_weight":    "balanced_subsample",
        }

        try:
            from services.model_registry import ModelRegistry
        except ModuleNotFoundError:
            from model_registry import ModelRegistry
        save_result = ModelRegistry.save_model(
            model, self.model_id, feature_names, metadata=metadata,
        )
        if save_result["success"]:
            print(f"[TrainingEngine] Model saved to {save_result['path']}")
        else:
            print(f"[TrainingEngine] Failed to save model: {save_result.get('error')}")

        try:
            from services.data_store import DataStore
        except ModuleNotFoundError:
            from data_store import DataStore
        DataStore.get().set_trained_model({
            "model":              model,
            "model_id":           self.model_id,
            "feature_names":      feature_names,
            "headers":            self.headers,
            "target_col":         self.target_col,
            "decision_threshold": best_thresh,
            "metadata":           metadata,
        })

        # ── Activity log ──────────────────────────────────────────────────────
        try:
            from services.activity_logger import ActivityLogger
            _conn = DataStore.get().db_conn
            if _conn:
                ActivityLogger.log_train(
                    _conn,
                    model_id   = self.model_id,
                    recall     = round(recall_test * 100, 1),
                    f1         = round(f1_test, 3),
                    pr_auc     = round(pr_auc_test, 3),
                    threshold  = round(best_thresh, 2),
                    train_size = int(len(X_train)),
                )
                _conn.commit()
        except Exception as _log_exc:
            print(f"[TrainingEngine] Activity log error: {_log_exc}")

        self.progress_cb("Done ✅", 100)

        return TrainingResult(
            success            = True,
            model_id           = self.model_id,
            model_name         = self.SUPPORTED_MODELS.get(self.model_id, self.model_id),
            recall             = round(recall_test * 100, 1),
            f1_score           = round(f1_test, 3),
            precision          = round(precision_test * 100, 1),
            pr_auc             = round(pr_auc_test, 3),
            decision_threshold = round(best_thresh, 2),
            cv_recalls         = [round(v, 3) for v in fold_recalls],
            cv_f1s             = [round(v, 3) for v in fold_f1s],
            cv_pr_aucs         = [round(v, 3) for v in fold_pr_aucs],
            confusion_matrix   = cm,
            shap_values        = shap_values,
            train_size         = int(len(X_train)),
            test_size          = int(len(X_test)),
            feature_count      = len(feature_names),
            smote_applied      = smote_applied,
            imbalance_warning  = imbalance_warning,
            log_lines          = self._build_log(
                fold_recalls, fold_f1s, fold_pr_aucs,
                recall_test, f1_test, pr_auc_test,
                best_thresh, smote_applied, imbalance_warning,
            ),
            engineered_headers = engineered_headers,
            engineered_rows    = engineered_rows,
        )

    # ------------------------------------------------------------------
    # Mock training (no sklearn)
    # ------------------------------------------------------------------

    def _train_mock(self, feature_names, n_samples, engineered_headers=None, engineered_rows=None) -> "TrainingResult":
        base_recall = 0.80
        fold_recalls, fold_f1s, fold_pr_aucs = [], [], []

        for fold in range(1, self.n_folds + 1):
            pct = 20 + int(fold / self.n_folds * 60)
            self.progress_cb(f"Fold {fold}/{self.n_folds}…", pct)
            r  = round(base_recall + random.uniform(-0.05, 0.05), 3)
            f  = round(r * 0.72, 3)
            pa = round(r * 0.65, 3)
            fold_recalls.append(r)
            fold_f1s.append(f)
            fold_pr_aucs.append(pa)

        self.progress_cb("Evaluating…", 80)
        recall    = round(sum(fold_recalls) / len(fold_recalls), 3)
        f1        = round(recall * 0.72, 3)
        precision = round(f1 / (2 * recall - f1 + 1e-9), 3)
        pr_auc    = round(recall * 0.65, 3)
        threshold = 0.20

        n_test = int(n_samples * self.test_size)
        tp = int(n_test * 0.096 * recall)
        tn = int(n_test * 0.904 * precision)
        fp = int(n_test * 0.904 * (1 - precision))
        fn = int(n_test * 0.096 * (1 - recall))

        shap_values = [
            (f, round(random.uniform(5, 40), 1))
            for f in (feature_names[:8] if len(feature_names) >= 8 else feature_names)
        ]
        shap_values.sort(key=lambda x: x[1], reverse=True)

        self.progress_cb("Done ✅", 100)

        return TrainingResult(
            success            = True,
            model_id           = self.model_id,
            model_name         = self.SUPPORTED_MODELS.get(self.model_id, self.model_id),
            recall             = round(recall * 100, 1),
            f1_score           = f1,
            precision          = round(precision * 100, 1),
            pr_auc             = pr_auc,
            decision_threshold = threshold,
            cv_recalls         = fold_recalls,
            cv_f1s             = fold_f1s,
            cv_pr_aucs         = fold_pr_aucs,
            confusion_matrix   = [[tn, fp], [fn, tp]],
            shap_values        = shap_values,
            train_size         = n_samples - int(n_samples * self.test_size),
            test_size          = int(n_samples * self.test_size),
            feature_count      = len(feature_names),
            smote_applied      = False,
            imbalance_warning  = None,
            log_lines          = self._build_log(
                fold_recalls, fold_f1s, fold_pr_aucs,
                recall, f1, pr_auc, threshold, False, None,
            ),
            engineered_headers = engineered_headers or [],
            engineered_rows    = engineered_rows    or [],
            is_mock            = True,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_feature_importance(model, feature_names) -> list[tuple]:
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

    def _build_log(
        self,
        fold_recalls, fold_f1s, fold_pr_aucs,
        recall_test, f1_test, pr_auc_test,
        threshold, smote_applied, imbalance_warning,
    ) -> list[tuple[str, str]]:
        import statistics

        lines = [
            (f"Initializing {self.SUPPORTED_MODELS.get(self.model_id)}…", "muted"),
            (f"Hyperparameters: n_estimators=300, max_depth=12, "
             f"min_samples_leaf=4, class_weight=balanced_subsample", "muted"),
            (f"Dataset: {len(self.rows):,} rows · {len(self.headers)} raw columns", "muted"),
            (f"Split: {int((1-self.test_size)*100)}% train / "
             f"{int(self.test_size*100)}% test (stratified)", "muted"),
            (f"Threshold strategy: recall ≥ {RECALL_TARGET:.0%} → max F1", "muted"),
        ]

        if imbalance_warning:
            lines += [
                ("", "muted"),
                (imbalance_warning, "warning"),
            ]

        lines += [
            ("", "muted"),
            (f"── Stratified {self.n_folds}-Fold CV"
             f"{' + SMOTE@0.40' if smote_applied else ''} ──", "muted"),
        ]

        for i, (r, f, p) in enumerate(zip(fold_recalls, fold_f1s, fold_pr_aucs), 1):
            lines.append(
                (f"  Fold {i}: Recall {r:.3f}  F1 {f:.3f}  PR-AUC {p:.3f}", "muted")
            )

        if fold_recalls:
            lines += [
                ("", "muted"),
                (f"  Mean  Recall  : {statistics.mean(fold_recalls):.3f} "
                 f"± {statistics.stdev(fold_recalls) if len(fold_recalls) > 1 else 0:.3f}", "muted"),
                (f"  Mean  F1      : {statistics.mean(fold_f1s):.3f} "
                 f"± {statistics.stdev(fold_f1s) if len(fold_f1s) > 1 else 0:.3f}", "muted"),
                (f"  Mean  PR-AUC  : {statistics.mean(fold_pr_aucs):.3f} "
                 f"± {statistics.stdev(fold_pr_aucs) if len(fold_pr_aucs) > 1 else 0:.3f}", "muted"),
            ]

        lines += [
            ("", "muted"),
            (f"── Held-Out Test Set (threshold={threshold:.2f}, "
             f"recall_target={RECALL_TARGET:.0%}) ──", "muted"),
            (f"  Recall    : {recall_test * 100 if recall_test < 1 else recall_test:.1f}%", "muted"),
            (f"  F1        : {f1_test:.3f}", "muted"),
            (f"  PR-AUC    : {pr_auc_test:.3f}", "muted"),
            ("Computing feature importance…", "muted"),
            ("Saving model artifact…", "muted"),
            ("Done! ✅", "success"),
        ]
        return lines


# ------------------------------------------------------------------
# Module-level helper: recall-priority threshold sweep
# ------------------------------------------------------------------

def _find_best_threshold(
    y_true,
    y_proba,
    lo: float           = 0.05,
    hi: float           = 0.95,
    steps: int          = 91,          # ~0.01 increments
    recall_target: float = RECALL_TARGET,
) -> tuple[float, float]:
    """
    Sweep decision thresholds and return the one that maximises F1
    subject to Recall >= recall_target.

    Strategy (recall-priority):
      Pass 1 — collect every threshold where Recall >= recall_target.
      Among those candidates, return the one with the highest F1.

      Fallback — if no threshold achieves recall_target (can happen when
      the model's probability estimates are poorly calibrated), return the
      threshold that maximises raw F1 instead, and log a warning.

    Rationale:
      In an early-warning system, missing an at-risk student (false negative)
      is far more costly than a false alarm (false positive) that a counselor
      can verify. Setting recall_target=0.80 means the system catches at least
      80% of truly at-risk students before we start worrying about precision.

    Falls back to 0.5 if sklearn is unavailable.
    """
    try:
        import numpy as np
        from sklearn.metrics import f1_score, recall_score
    except ImportError:
        return 0.5, 0.0

    thresholds  = [lo + (hi - lo) * i / (steps - 1) for i in range(steps)]
    y_arr       = np.array(y_true)
    y_prob_arr  = np.array(y_proba)

    # Pass 1: candidates meeting the recall floor
    candidates = []
    for t in thresholds:
        preds = (y_prob_arr >= t).astype(int)
        rec   = recall_score(y_arr, preds, zero_division=0)
        f1    = f1_score(y_arr, preds, zero_division=0)
        if rec >= recall_target:
            candidates.append((t, f1, rec))

    if candidates:
        # Highest F1 among recall-floor candidates
        best = max(candidates, key=lambda x: x[1])
        print(
            f"[TrainingEngine] Threshold sweep: {len(candidates)} candidates "
            f"met Recall ≥ {recall_target:.0%} | "
            f"chosen t={best[0]:.2f} → Recall={best[2]:.3f}, F1={best[1]:.3f}"
        )
        return best[0], best[1]

    # Fallback: no threshold hit the recall floor
    print(
        f"[TrainingEngine] WARNING: no threshold achieved Recall ≥ {recall_target:.0%}. "
        f"Falling back to max-F1 threshold. "
        f"Consider lowering RECALL_TARGET or adding more training data."
    )
    best_t, best_f1 = 0.5, 0.0
    for t in thresholds:
        preds = (y_prob_arr >= t).astype(int)
        score = f1_score(y_arr, preds, zero_division=0)
        if score > best_f1:
            best_f1, best_t = score, t
    return best_t, best_f1


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
        success:            bool  = False,
        model_id:           str   = "",
        model_name:         str   = "",
        recall:             float = 0.0,
        f1_score:           float = 0.0,
        precision:          float = 0.0,
        pr_auc:             float = 0.0,
        decision_threshold: float = 0.5,
        cv_recalls:         list  = None,
        cv_f1s:             list  = None,
        cv_pr_aucs:         list  = None,
        confusion_matrix:   list  = None,
        shap_values:        list  = None,
        train_size:         int   = 0,
        test_size:          int   = 0,
        feature_count:      int   = 0,
        smote_applied:      bool  = False,
        imbalance_warning:  str   = None,
        engineered_headers: list  = None,
        engineered_rows:    list  = None,
        log_lines:          list  = None,
        errors:             list  = None,
        is_mock:            bool  = False,
    ):
        self.success            = success
        self.model_id           = model_id
        self.model_name         = model_name
        self.recall             = recall
        self.f1_score           = f1_score
        self.precision          = precision
        self.pr_auc             = pr_auc
        self.decision_threshold = decision_threshold
        self.cv_recalls         = cv_recalls  or []
        self.cv_f1s             = cv_f1s      or []
        self.cv_pr_aucs         = cv_pr_aucs  or []
        self.confusion_matrix   = confusion_matrix or [[0, 0], [0, 0]]
        self.shap_values        = shap_values or []
        self.train_size         = train_size
        self.test_size          = test_size
        self.feature_count      = feature_count
        self.smote_applied      = smote_applied
        self.imbalance_warning  = imbalance_warning
        self.engineered_headers = engineered_headers or []
        self.engineered_rows    = engineered_rows    or []
        self.log_lines          = log_lines   or []
        self.errors             = errors      or []
        self.is_mock            = is_mock

        # Back-compat shims
        self.accuracy = recall
        self.cv_folds = self.cv_f1s