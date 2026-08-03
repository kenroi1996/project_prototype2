"""
inspect_model_pkl.py
=====================
Loads a saved EarlyAlert model artifact and reports what's ACTUALLY inside
it — encoders, preprocessor, feature names, dtypes — rather than trusting
the metadata JSON, which we've already found to be out of sync with the
real training feature set.

Usage:
    python inspect_model_pkl.py "D:\\project_prototype_2\\ml\\saved_models\\model_20260731_212954.pkl"

Run this on the machine where the .pkl actually lives (this environment
doesn't have access to your local filesystem).
"""

import sys
import pickle
import joblib


def _try_load(path: str):
    """Try joblib first (sklearn's usual choice), fall back to raw pickle."""
    try:
        return joblib.load(path)
    except Exception as e_joblib:
        try:
            with open(path, "rb") as f:
                return pickle.load(f)
        except Exception as e_pickle:
            print(f"[FATAL] Could not load pkl with joblib ({e_joblib}) "
                  f"or pickle ({e_pickle}).")
            sys.exit(1)


def _describe_encoder(name: str, obj):
    """Print what kind of encoder this is and its learned mapping, if any."""
    cls = type(obj).__name__
    print(f"    {name}: {cls}")

    if hasattr(obj, "classes_"):
        # LabelEncoder — this is the ordinality-risk case we're checking for.
        classes = list(obj.classes_)
        mapping = {cls_val: i for i, cls_val in enumerate(classes)}
        print(f"      -> LabelEncoder classes (in learned order): {classes}")
        print(f"      -> integer mapping: {mapping}")
        print(f"      ** WARNING: this column was LABEL-encoded, not one-hot. **")
        print(f"      ** RF will see these as ordered integers 0..{len(classes)-1}. **")

    elif hasattr(obj, "categories_"):
        # OneHotEncoder (sklearn) — the "safe" case for a non-ordinal category.
        print(f"      -> OneHotEncoder categories: {list(obj.categories_)}")

    elif hasattr(obj, "mean_") or hasattr(obj, "scale_"):
        # StandardScaler / MinMaxScaler
        print(f"      -> Scaler fitted params: mean_={getattr(obj, 'mean_', None)}, "
              f"scale_={getattr(obj, 'scale_', None)}")


def inspect(path: str):
    print(f"Loading: {path}\n")
    obj = _try_load(path)

    print(f"Top-level object type: {type(obj).__name__}")
    print()

    # ── Case 1: dict-style bundle (model + preprocessor + feature_names) ──
    if isinstance(obj, dict):
        print("Structure: dict bundle. Keys found:")
        for k in obj.keys():
            print(f"  - {k}: {type(obj[k]).__name__}")
        print()

        model = obj.get("model")
        preprocessor = obj.get("preprocessor")
        feature_names = obj.get("feature_names") or obj.get("feature_columns")
        encoders = obj.get("encoders") or obj.get("_encoders")

    # ── Case 2: custom object with attributes ──────────────────────────────
    elif hasattr(obj, "__dict__"):
        print("Structure: custom object. Attributes found:")
        for k in vars(obj).keys():
            print(f"  - {k}")
        print()

        model = getattr(obj, "model", obj)  # fall back to obj itself
        preprocessor = getattr(obj, "preprocessor", None)
        feature_names = getattr(obj, "feature_names", None)
        encoders = getattr(obj, "_encoders", None) or getattr(obj, "encoders", None)

    # ── Case 3: bare sklearn estimator, nothing else saved ──────────────────
    else:
        print("Structure: bare estimator — no preprocessor/encoders were pickled "
              "alongside it at all. This confirms has_preprocessor: false at the "
              "object level, not just in the metadata.")
        model = obj
        preprocessor = None
        feature_names = None
        encoders = None

    print()
    print("=" * 70)
    print("MODEL")
    print("=" * 70)
    if model is not None:
        print(f"Type: {type(model).__name__}")
        if hasattr(model, "n_features_in_"):
            print(f"n_features_in_: {model.n_features_in_}")
        if hasattr(model, "feature_names_in_"):
            fni = list(model.feature_names_in_)
            print(f"feature_names_in_ ({len(fni)}): {fni}")

            # Direct check: is Strand_Program_Match a single column here,
            # or did it get split into one-hot columns at fit time?
            spm_cols = [c for c in fni if c.startswith("Strand_Program_Match")]
            print()
            print(f"Strand_Program_Match related columns in the fitted model: {spm_cols}")
            if len(spm_cols) == 1 and spm_cols[0] == "Strand_Program_Match":
                print("  -> Single column. Almost certainly LABEL-encoded, not one-hot.")
                print("     Check the encoders section below to confirm the mapping.")
            elif len(spm_cols) > 1:
                print("  -> Multiple columns. One-hot encoded as intended.")
            else:
                print("  -> Not found under this name — check for renaming.")
        if hasattr(model, "classes_"):
            print(f"model.classes_ (target labels): {list(model.classes_)}")
    else:
        print("No model object found.")

    print()
    print("=" * 70)
    print("PREPROCESSOR")
    print("=" * 70)
    if preprocessor is not None:
        print(f"Found: {type(preprocessor).__name__}")
        for attr in ("mean_", "scale_", "categories_"):
            if hasattr(preprocessor, attr):
                print(f"  {attr}: {getattr(preprocessor, attr)}")
    else:
        print("None saved. (Matches has_preprocessor: false in the metadata JSON.)")
        print("Any categorical/scaling transforms used at prediction time will")
        print("have to be re-derived — there's no guarantee they'll match training.")

    print()
    print("=" * 70)
    print("ENCODERS")
    print("=" * 70)
    if encoders:
        if isinstance(encoders, dict):
            for name, enc in encoders.items():
                _describe_encoder(name, enc)
        else:
            print(f"encoders object found but not a dict: {type(encoders).__name__}")
    else:
        print("No separate encoders dict found in the pickle.")
        print("If Strand_Program_Match / Program / Age_Group / Distance_Bucket")
        print("were LabelEncoded, their mappings are NOT recoverable from this")
        print("artifact — a strong argument for re-saving with has_preprocessor=true.")

    print()
    print("=" * 70)
    print("SAVED feature_names (if present in the bundle)")
    print("=" * 70)
    if feature_names:
        print(f"Count: {len(feature_names)}")
        print(feature_names)
    else:
        print("Not found at the top level of this pickle.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python inspect_model_pkl.py <path_to_pkl>")
        sys.exit(1)
    inspect(sys.argv[1])