"""
Model Registry: Centralized service for persisting, loading, and versioning trained ML models.

Models are stored in ml/saved_models/ with metadata tracking:
- model_YYYYMMDD_HHMMSS.pkl (pickle file)
- model_YYYYMMDD_HHMMSS.json (metadata)

Features:
- Auto-save on training completion
- Auto-load latest model on app startup
- Version control (keep latest 3 versions)
- Metadata tracking (accuracy, features, training date, dataset info)
"""

import json
import pickle
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any


class ModelRegistry:
    """Centralized registry for model persistence and versioning."""

    # Storage configuration
    MODEL_DIR = Path(__file__).resolve().parent.parent / "ml" / "saved_models"
    MAX_VERSIONS = 3

    @classmethod
    def ensure_model_dir(cls) -> Path:
        """Create model directory if it doesn't exist."""
        cls.MODEL_DIR.mkdir(parents=True, exist_ok=True)
        return cls.MODEL_DIR

    @classmethod
    def save_model(
        cls,
        model_obj: Any,
        model_id: str,
        feature_names: list,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Save a trained model to disk with metadata.

        Parameters
        ----------
        model_obj : object
            Trained sklearn model
        model_id : str
            Model type identifier ('rf', 'xgb', 'lr')
        feature_names : list
            Feature names used in training
        metadata : dict, optional
            Additional metadata (accuracy, precision, recall, etc.)

        Returns
        -------
        dict
            Saved model info: {"success": bool, "path": str, "metadata": dict, "error": str}
        """
        try:
            cls.ensure_model_dir()

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            model_filename = f"model_{timestamp}.pkl"
            metadata_filename = f"model_{timestamp}.json"

            model_path = cls.MODEL_DIR / model_filename
            metadata_path = cls.MODEL_DIR / metadata_filename

            # Build complete metadata
            full_metadata = {
                "timestamp": datetime.now().isoformat(),
                "model_id": model_id,
                "feature_names": feature_names,
                "feature_count": len(feature_names),
                "saved_at": model_path.name,
            }

            if metadata:
                full_metadata.update(metadata)

            # Save model pickle
            with open(model_path, "wb") as f:
                pickle.dump(model_obj, f)

            # Save metadata JSON
            with open(metadata_path, "w") as f:
                json.dump(full_metadata, f, indent=2)

            # Cleanup old versions
            cls._cleanup_old_versions()

            return {
                "success": True,
                "path": str(model_path),
                "metadata_path": str(metadata_path),
                "metadata": full_metadata,
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to save model: {str(e)}",
            }

    @classmethod
    def load_latest_model(cls) -> Optional[Dict[str, Any]]:
        """
        Load the latest trained model from disk.

        Returns
        -------
        dict or None
            Model package: {"model": obj, "model_id": str, "feature_names": list,
                           "metadata": dict} or None if no model found
        """
        try:
            cls.ensure_model_dir()

            # Find all model files
            model_files = sorted(
                cls.MODEL_DIR.glob("model_*.pkl"),
                reverse=True,
            )

            if not model_files:
                return None

            latest_model_path = model_files[0]
            model_name = latest_model_path.stem

            # Load metadata
            metadata_path = cls.MODEL_DIR / f"{model_name}.json"
            metadata = {}

            if metadata_path.exists():
                with open(metadata_path, "r") as f:
                    metadata = json.load(f)

            # Load model
            with open(latest_model_path, "rb") as f:
                model_obj = pickle.load(f)

            return {
                "model": model_obj,
                "model_id": metadata.get("model_id", "unknown"),
                "feature_names": metadata.get("feature_names", []),
                "metadata": metadata,
                "path": str(latest_model_path),
            }

        except Exception as e:
            print(f"[ModelRegistry] Error loading latest model: {e}")
            return None

    @classmethod
    def load_model_by_timestamp(cls, timestamp: str) -> Optional[Dict[str, Any]]:
        """
        Load a specific model version by timestamp (e.g., '20260603_112000').

        Returns
        -------
        dict or None
            Model package or None if not found
        """
        try:
            cls.ensure_model_dir()

            model_path = cls.MODEL_DIR / f"model_{timestamp}.pkl"
            metadata_path = cls.MODEL_DIR / f"model_{timestamp}.json"

            if not model_path.exists():
                return None

            metadata = {}
            if metadata_path.exists():
                with open(metadata_path, "r") as f:
                    metadata = json.load(f)

            with open(model_path, "rb") as f:
                model_obj = pickle.load(f)

            return {
                "model": model_obj,
                "model_id": metadata.get("model_id", "unknown"),
                "feature_names": metadata.get("feature_names", []),
                "metadata": metadata,
                "path": str(model_path),
            }

        except Exception as e:
            print(f"[ModelRegistry] Error loading model {timestamp}: {e}")
            return None

    @classmethod
    def list_models(cls) -> list[Dict[str, Any]]:
        """
        List all saved models with their metadata.

        Returns
        -------
        list
            Sorted by timestamp (newest first): [{"timestamp": str, "model_id": str, ...}]
        """
        try:
            cls.ensure_model_dir()

            models = []
            for metadata_path in sorted(
                cls.MODEL_DIR.glob("model_*.json"),
                reverse=True,
            ):
                try:
                    with open(metadata_path, "r") as f:
                        metadata = json.load(f)
                    timestamp = metadata_path.stem.replace("model_", "")
                    models.append({
                        "timestamp": timestamp,
                        **metadata,
                    })
                except Exception:
                    pass

            return models

        except Exception as e:
            print(f"[ModelRegistry] Error listing models: {e}")
            return []

    @classmethod
    def delete_model(cls, timestamp: str) -> Dict[str, Any]:
        """
        Delete a specific model version.

        Parameters
        ----------
        timestamp : str
            Model timestamp (e.g., '20260603_112000')

        Returns
        -------
        dict
            {"success": bool, "message": str}
        """
        try:
            model_path = cls.MODEL_DIR / f"model_{timestamp}.pkl"
            metadata_path = cls.MODEL_DIR / f"model_{timestamp}.json"

            model_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)

            return {"success": True, "message": f"Deleted model {timestamp}"}

        except Exception as e:
            return {"success": False, "message": f"Failed to delete model: {str(e)}"}

    @classmethod
    def _cleanup_old_versions(cls) -> None:
        """Keep only the latest MAX_VERSIONS versions; delete older ones."""
        try:
            model_files = sorted(
                cls.MODEL_DIR.glob("model_*.pkl"),
                reverse=True,
            )

            # Delete old versions beyond MAX_VERSIONS
            for old_model_path in model_files[cls.MAX_VERSIONS :]:
                model_name = old_model_path.stem
                metadata_path = cls.MODEL_DIR / f"{model_name}.json"

                old_model_path.unlink(missing_ok=True)
                metadata_path.unlink(missing_ok=True)

                print(f"[ModelRegistry] Cleaned up old version: {model_name}")

        except Exception as e:
            print(f"[ModelRegistry] Cleanup error: {e}")

    @classmethod
    def get_model_info(cls) -> Optional[Dict[str, Any]]:
        """
        Get info about the latest model (for UI status displays).

        Returns
        -------
        dict or None
            {"model_id": str, "accuracy": float, "feature_count": int,
             "timestamp": str} or None
        """
        models = cls.list_models()
        if not models:
            return None

        latest = models[0]
        return {
            "model_id": latest.get("model_id", "unknown"),
            "accuracy": latest.get("accuracy", None),
            "feature_count": latest.get("feature_count", 0),
            "timestamp": latest.get("timestamp", ""),
            "train_size": latest.get("train_size", None),
        }
