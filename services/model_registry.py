"""
Model Registry
==============
Centralized service for persisting, loading, and versioning trained ML models.

Storage layers (both used together):
  1. PostgreSQL  — public.model_registry table (primary; stores the model
                   binary itself in model_blob, plus queryable metadata)
  2. Disk        — ml/saved_models/*.pkl + *.json (fast startup load / fallback)

The serialized package (pickle) always contains the full dict:
    {
        "model":            sklearn estimator,
        "feature_names":    list[str],
        "target_classes":   list,
        "training_history": dict,
        "metadata":         dict,
        "preprocessor":     fitted DataPipeline   ← carries LabelEncoders + Scaler
    }

Storing the preprocessor alongside the model ensures that PredictionEngine
can replay the exact same encode+scale transformations used during training,
preventing the "0 or 100 risk score" bug that occurs when categorical columns
are sent as raw strings to model.predict_proba().
"""

import json
import pickle
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


class ModelRegistry:
    """Centralized registry for model persistence and versioning."""

    MODEL_DIR    = Path(__file__).resolve().parent.parent / "ml" / "saved_models"
    MAX_VERSIONS = 3

    MODEL_NAMES = {
        "rf":                 "Random Forest",
        "xgb":                "Gradient Boosting",
        "lr":                 "Logistic Regression",
        "random_forest":      "Random Forest",
        "gradient_boosting":  "Gradient Boosting",
        "logistic_regression": "Logistic Regression",
    }

    # ==========================================================================
    # PRIMARY ENTRY POINT
    # ==========================================================================

    @classmethod
    def save_model(
        cls,
        model_obj:     Any,
        model_id:      str,
        feature_names: list,
        metadata:      Optional[Dict[str, Any]] = None,
        db_conn=None,
        preprocessor:  Any = None,
    ) -> Dict[str, Any]:
        """
        Save a trained model to disk and (optionally) to PostgreSQL.

        The pickle package now always includes the fitted preprocessor
        (DataPipeline with LabelEncoders + StandardScaler) so that
        PredictionEngine can apply the same transformations used during
        training without needing a separate file.

        Parameters
        ----------
        model_obj     : trained sklearn model object
        model_id      : "rf" | "xgb" | "lr"
        feature_names : list of feature column names used in training
        metadata      : dict — accuracy, f1_score, precision, recall, etc.
        db_conn       : psycopg2 connection (optional — disk-only if None)
        preprocessor  : fitted DataPipeline instance (optional but strongly
                        recommended — without it risk scores will be 0 or 100)
        """
        try:
            cls.ensure_model_dir()

            timestamp         = datetime.now().strftime("%Y%m%d_%H%M%S")
            model_filename    = f"model_{timestamp}.pkl"
            metadata_filename = f"model_{timestamp}.json"
            model_path        = cls.MODEL_DIR / model_filename
            metadata_path     = cls.MODEL_DIR / metadata_filename

            full_metadata = {
                "timestamp":     datetime.now().isoformat(),
                "model_id":      model_id,
                "model_name":    cls.MODEL_NAMES.get(model_id, model_id),
                "feature_names": list(feature_names),
                "feature_count": len(feature_names),
                "pkl_path":      str(model_path),
                "saved_at":      model_filename,
                "has_preprocessor": preprocessor is not None,
            }
            if metadata:
                full_metadata.update(metadata)

            # ── Serialize the FULL package (model + preprocessor) ─────────────
            # This is the critical change: previously only model_obj was pickled.
            # Now the entire package is serialized so the preprocessor survives
            # disk saves, DB blob storage, and app restarts.
            package = {
                "model":            model_obj,
                "feature_names":    list(feature_names),
                "target_classes":   getattr(model_obj, "classes_", []),
                "training_history": metadata or {},
                "metadata":         full_metadata,
                "preprocessor":     preprocessor,
            }
            model_bytes = pickle.dumps(package)
            full_metadata["model_size_bytes"] = len(model_bytes)
            full_metadata["stored_in_db"]     = db_conn is not None

            with open(model_path, "wb") as f:
                f.write(model_bytes)

            with open(metadata_path, "w") as f:
                json.dump(full_metadata, f, indent=2, default=str)

            db_id = None
            if db_conn is not None:
                db_id = cls._save_to_db(db_conn, model_id, full_metadata, model_bytes)

            cls._cleanup_old_versions()

            print(
                f"[ModelRegistry] Saved '{cls.MODEL_NAMES.get(model_id, model_id)}' "
                f"-> {model_path}"
                + (f"  (DB id={db_id})" if db_id else "  (disk only)")
                + (f"  [preprocessor included]" if preprocessor is not None
                   else "  [WARNING: no preprocessor — scores may be 0 or 100]")
            )

            return {
                "success":       True,
                "path":          str(model_path),
                "metadata_path": str(metadata_path),
                "db_id":         db_id,
                "metadata":      full_metadata,
            }

        except Exception as e:
            return {"success": False, "error": f"Failed to save model: {e}"}

    # ==========================================================================
    # LOAD
    # ==========================================================================

    @classmethod
    def load_latest_model(cls, db_conn=None) -> Optional[Dict[str, Any]]:
        if db_conn is not None:
            result = cls._load_active_from_db(db_conn)
            if result:
                return result
            print("[ModelRegistry] No active model in DB — falling back to disk")
        return cls._load_latest_from_disk()

    @classmethod
    def load_model_by_timestamp(cls, timestamp: str) -> Optional[Dict[str, Any]]:
        try:
            cls.ensure_model_dir()
            model_path    = cls.MODEL_DIR / f"model_{timestamp}.pkl"
            metadata_path = cls.MODEL_DIR / f"model_{timestamp}.json"
            if not model_path.exists():
                return None
            metadata = {}
            if metadata_path.exists():
                with open(metadata_path) as f:
                    metadata = json.load(f)
            with open(model_path, "rb") as f:
                data = pickle.load(f)
            return cls._unpack(data, metadata, "unknown")
        except Exception as e:
            print(f"[ModelRegistry] Error loading model {timestamp}: {e}")
            return None

    @classmethod
    def load_model_by_db_id(cls, db_conn, db_model_id: int) -> Optional[Dict[str, Any]]:
        try:
            with db_conn.cursor() as cur:
                cur.execute(
                    "SELECT model_type, metadata, model_blob "
                    "FROM public.model_registry WHERE model_id = %s",
                    (db_model_id,),
                )
                row = cur.fetchone()
            if not row:
                return None
            model_type, meta, blob = row
            if isinstance(meta, str):
                meta = json.loads(meta)
            return (cls._load_from_blob(blob, meta, model_type)
                    or cls._load_from_meta(meta, model_type))
        except Exception as e:
            print(f"[ModelRegistry] load_model_by_db_id error: {e}")
            return None

    # ==========================================================================
    # LIST / DELETE / INFO
    # ==========================================================================

    @classmethod
    def list_models(cls) -> list[Dict[str, Any]]:
        try:
            cls.ensure_model_dir()
            models = []
            for meta_path in sorted(cls.MODEL_DIR.glob("model_*.json"), reverse=True):
                try:
                    with open(meta_path) as f:
                        meta = json.load(f)
                    models.append({"timestamp": meta_path.stem.replace("model_", ""),
                                   **meta})
                except Exception:
                    pass
            return models
        except Exception as e:
            print(f"[ModelRegistry] Error listing models: {e}")
            return []

    @classmethod
    def list_models_from_db(cls, db_conn) -> list[Dict[str, Any]]:
        try:
            with db_conn.cursor() as cur:
                cur.execute(
                    "SELECT model_id, model_name, model_type, created_at, "
                    "is_active, metadata FROM public.model_registry "
                    "ORDER BY created_at DESC"
                )
                rows = cur.fetchall()
            return [{"db_id": r[0], "model_name": r[1], "model_type": r[2],
                     "created_at": str(r[3]), "is_active": r[4],
                     "metadata": r[5] if isinstance(r[5], dict)
                                 else json.loads(r[5] or "{}")}
                    for r in rows]
        except Exception as e:
            print(f"[ModelRegistry] list_models_from_db error: {e}")
            return []

    @classmethod
    def delete_model(cls, timestamp: str) -> Dict[str, Any]:
        try:
            (cls.MODEL_DIR / f"model_{timestamp}.pkl").unlink(missing_ok=True)
            (cls.MODEL_DIR / f"model_{timestamp}.json").unlink(missing_ok=True)
            return {"success": True, "message": f"Deleted model {timestamp}"}
        except Exception as e:
            return {"success": False, "message": f"Failed to delete model: {e}"}

    @classmethod
    def set_active_model(cls, db_conn, db_model_id: int) -> bool:
        try:
            with db_conn.cursor() as cur:
                cur.execute("UPDATE public.model_registry "
                            "SET is_active = FALSE WHERE is_active = TRUE")
                cur.execute("UPDATE public.model_registry "
                            "SET is_active = TRUE WHERE model_id = %s",
                            (db_model_id,))
            db_conn.commit()
            print(f"[ModelRegistry] Active model set to DB id={db_model_id}")
            return True
        except Exception as e:
            db_conn.rollback()
            print(f"[ModelRegistry] set_active_model error: {e}")
            return False

    @classmethod
    def get_model_info(cls, db_conn=None) -> Optional[Dict[str, Any]]:
        if db_conn is not None:
            try:
                with db_conn.cursor() as cur:
                    cur.execute(
                        "SELECT model_id, model_name, model_type, created_at, metadata "
                        "FROM public.model_registry WHERE is_active = TRUE "
                        "ORDER BY created_at DESC LIMIT 1"
                    )
                    row = cur.fetchone()
                if row:
                    meta = row[4] if isinstance(row[4], dict) \
                           else json.loads(row[4] or "{}")
                    return {"db_id": row[0], "model_name": row[1],
                            "model_type": row[2], "created_at": str(row[3]),
                            "accuracy": meta.get("accuracy"),
                            "f1_score": meta.get("f1_score"),
                            "feature_count": meta.get("feature_count", 0),
                            "feature_names": meta.get("feature_names", []),
                            "train_size": meta.get("train_size"),
                            "timestamp": meta.get("timestamp", "")}
            except Exception as e:
                print(f"[ModelRegistry] get_model_info DB error: {e}")
        models = cls.list_models()
        if not models:
            return None
        latest = models[0]
        return {"model_id": latest.get("model_id", "unknown"),
                "accuracy": latest.get("accuracy"),
                "f1_score": latest.get("f1_score"),
                "feature_count": latest.get("feature_count", 0),
                "timestamp": latest.get("timestamp", ""),
                "train_size": latest.get("train_size")}

    # ==========================================================================
    # PRIVATE — PostgreSQL helpers
    # ==========================================================================

    @classmethod
    def ensure_schema(cls, db_conn) -> None:
        with db_conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS public.model_registry ("
                "model_id SERIAL PRIMARY KEY, model_name VARCHAR(100) NOT NULL, "
                "model_type VARCHAR(50) NOT NULL, "
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                "is_active BOOLEAN DEFAULT FALSE, metadata JSONB)"
            )
            cur.execute("ALTER TABLE public.model_registry "
                        "ADD COLUMN IF NOT EXISTS model_blob BYTEA")
        db_conn.commit()

    @classmethod
    def _save_to_db(cls, db_conn, model_id: str,
                    metadata: dict, model_bytes: bytes) -> Optional[int]:
        import psycopg2
        model_name = metadata.get("model_name",
                                   cls.MODEL_NAMES.get(model_id, model_id))
        safe_meta  = cls._make_json_safe(metadata)
        try:
            cls.ensure_schema(db_conn)
            with db_conn.cursor() as cur:
                cur.execute("UPDATE public.model_registry "
                            "SET is_active = FALSE WHERE is_active = TRUE")
                cur.execute(
                    "INSERT INTO public.model_registry "
                    "(model_name, model_type, is_active, metadata, model_blob) "
                    "VALUES (%s, %s, TRUE, %s, %s) RETURNING model_id",
                    (model_name, model_id, json.dumps(safe_meta),
                     psycopg2.Binary(model_bytes)),
                )
                new_id = cur.fetchone()[0]
            db_conn.commit()
            print(f"[ModelRegistry] DB row inserted: model_id={new_id}, "
                  f"name='{model_name}', blob={len(model_bytes):,} bytes, "
                  f"is_active=TRUE")
            return new_id
        except Exception as e:
            try: db_conn.rollback()
            except Exception: pass
            print(f"[ModelRegistry] DB save failed (disk save still succeeded): {e}")
            return None

    @classmethod
    def _load_active_from_db(cls, db_conn) -> Optional[Dict[str, Any]]:
        try:
            with db_conn.cursor() as cur:
                cur.execute(
                    "SELECT model_id, model_type, metadata, model_blob, created_at "
                    "FROM public.model_registry WHERE is_active = TRUE "
                    "ORDER BY created_at DESC LIMIT 1"
                )
                row = cur.fetchone()
            if not row:
                return None
            db_id, model_type, meta, blob, created_at = row
            if isinstance(meta, str):
                meta = json.loads(meta)
            result = (cls._load_from_blob(blob, meta, model_type)
                      or cls._load_from_meta(meta, model_type))
            if result:
                print(f"[ModelRegistry] Loaded active model from DB: "
                      f"id={db_id}, type={model_type}, created={created_at}, "
                      f"preprocessor={'yes' if result.get('preprocessor') else 'NO'}")
            return result
        except Exception as e:
            print(f"[ModelRegistry] _load_active_from_db error: {e}")
            return None

    @classmethod
    def _load_from_blob(cls, blob, meta: dict,
                        model_type: str) -> Optional[Dict[str, Any]]:
        if blob is None:
            return None
        try:
            data = pickle.loads(bytes(blob))
            return cls._unpack(data, meta, model_type)
        except Exception as e:
            print(f"[ModelRegistry] Failed to unpickle DB blob: {e}")
            return None

    @classmethod
    def _load_from_meta(cls, meta: dict,
                        model_type: str) -> Optional[Dict[str, Any]]:
        pkl_path = meta.get("pkl_path") or meta.get("saved_at")
        if pkl_path and not Path(pkl_path).is_absolute():
            pkl_path = str(cls.MODEL_DIR / pkl_path)
        if not pkl_path or not Path(pkl_path).exists():
            print(f"[ModelRegistry] pkl not found at: {pkl_path}")
            return None
        try:
            with open(pkl_path, "rb") as f:
                data = pickle.load(f)
            return cls._unpack(data, meta, model_type)
        except Exception as e:
            print(f"[ModelRegistry] Failed to unpickle {pkl_path}: {e}")
            return None

    @classmethod
    def _unpack(cls, data: Any, meta: dict,
                model_type: str) -> Optional[Dict[str, Any]]:
        """
        Unpack a loaded pickle into a standard model dict.

        Handles two formats:
          - New format: dict with keys model/feature_names/preprocessor/…
          - Old format: raw sklearn estimator object (no preprocessor)
        """
        if isinstance(data, dict) and "model" in data:
            # New format — full package
            preprocessor = data.get("preprocessor")
            if preprocessor is None:
                print("[ModelRegistry] WARNING: loaded model has no preprocessor. "
                      "Risk scores may be 0 or 100. Retrain to fix.")
            return {
                "model":         data["model"],
                "model_id":      data.get("metadata", {}).get("model_id", model_type),
                "feature_names": data.get("feature_names",
                                          meta.get("feature_names", [])),
                "target_classes": data.get("target_classes", []),
                "training_history": data.get("training_history", {}),
                "metadata":      meta,
                "path":          meta.get("pkl_path", ""),
                "preprocessor":  preprocessor,
            }
        else:
            # Old format — raw sklearn model, no preprocessor
            print("[ModelRegistry] WARNING: old-format pkl (no preprocessor). "
                  "Retrain to get correct risk scores.")
            return {
                "model":         data,
                "model_id":      model_type,
                "feature_names": meta.get("feature_names", []),
                "metadata":      meta,
                "path":          meta.get("pkl_path", ""),
                "preprocessor":  None,
            }

    # ==========================================================================
    # PRIVATE — disk helpers
    # ==========================================================================

    @classmethod
    def ensure_model_dir(cls) -> Path:
        cls.MODEL_DIR.mkdir(parents=True, exist_ok=True)
        return cls.MODEL_DIR

    @classmethod
    def _load_latest_from_disk(cls) -> Optional[Dict[str, Any]]:
        try:
            cls.ensure_model_dir()
            model_files = sorted(cls.MODEL_DIR.glob("model_*.pkl"), reverse=True)
            if not model_files:
                return None
            pkl_path      = model_files[0]
            metadata_path = pkl_path.with_suffix(".json")
            metadata      = {}
            if metadata_path.exists():
                with open(metadata_path) as f:
                    metadata = json.load(f)
            with open(pkl_path, "rb") as f:
                data = pickle.load(f)
            return cls._unpack(data, metadata, metadata.get("model_id", "unknown"))
        except Exception as e:
            print(f"[ModelRegistry] Error loading latest model from disk: {e}")
            return None

    @classmethod
    def _cleanup_old_versions(cls) -> None:
        try:
            model_files = sorted(cls.MODEL_DIR.glob("model_*.pkl"), reverse=True)
            for old_path in model_files[cls.MAX_VERSIONS:]:
                old_path.unlink(missing_ok=True)
                old_path.with_suffix(".json").unlink(missing_ok=True)
                print(f"[ModelRegistry] Pruned old version: {old_path.name}")
        except Exception as e:
            print(f"[ModelRegistry] Cleanup error: {e}")

    # ==========================================================================
    # UTILITIES
    # ==========================================================================

    @staticmethod
    def _make_json_safe(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: ModelRegistry._make_json_safe(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [ModelRegistry._make_json_safe(v) for v in obj]
        try:
            import numpy as np
            if isinstance(obj, np.integer):  return int(obj)
            if isinstance(obj, np.floating): return float(obj)
            if isinstance(obj, np.ndarray):  return obj.tolist()
        except ImportError:
            pass
        try:
            import pandas as pd
            if isinstance(obj, pd.Timestamp): return obj.isoformat()
        except ImportError:
            pass
        if isinstance(obj, datetime):
            return obj.isoformat()
        if not isinstance(obj, (str, int, float, bool, type(None))):
            return str(obj)
        return obj