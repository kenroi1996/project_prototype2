from datetime import datetime

import pandas as pd


# =====================================
# DATA STORE SINGLETON
# =====================================

class DataStore:
    """
    Central singleton that holds cleaned datasets from all four portals.
    Access anywhere in the app via DataStore.get().
    """

    _instance = None

    @classmethod
    def get(cls) -> "DataStore":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        # Each value: None or {"headers": list, "rows": list, "timestamp": str, "row_count": int}
        self.portals: dict = {
            "mis":       None,
            "sao":       None,
            "guidance":  None,
            "registrar": None,
        }

        self._original_unified_dataset = None  # preserves Student_ID and original columns
        self.unified_dataset = None   # set after merge or training
        self.trained_model   = None   # set after training
        self.model_ready     = False
        self.predictions     = None
        self.last_prediction_run = None

        # Callbacks: list of callables notified on any portal change
        self._listeners: list = []

        # Auto-load latest model from disk on startup
        self._load_persisted_model()

    # ------------------------------------------------------------------
    # Portal data management
    # ------------------------------------------------------------------

    def set_portal(self, key: str, headers: list, rows: list):
        """Store cleaned data for a portal and notify listeners."""
        if key not in self.portals:
            raise KeyError(f"Unknown portal key: '{key}'. "
                           f"Valid keys: {list(self.portals.keys())}")

        self.portals[key] = {
            "headers":   list(headers),
            "rows":      [list(r) for r in rows],
            "row_count": len(rows),
            "timestamp": datetime.now().strftime("%b %d, %Y · %H:%M"),
        }
        self._notify(key)

    def get_portal(self, key: str) -> dict | None:
        """Return portal data dict or None if not yet uploaded."""
        return self.portals.get(key)

    def clear_portal(self, key: str):
        """Remove a portal's data (e.g. re-upload)."""
        if key in self.portals:
            self.portals[key] = None
            self._notify(key)

    def set_unified_dataset(self, data) -> None:
        """Store merged dataset (dict with headers/rows or DataFrame) and notify listeners.
        Preserves original with Student_ID if this is the initial merge."""
        self.unified_dataset = data
        # Preserve original unified dataset if not already set (first time from merge)
        if self._original_unified_dataset is None and data is not None:
            self._original_unified_dataset = data
        self._notify("unified_dataset")

    def clear_unified_dataset(self):
        """Clear merged dataset after portal data changes or reset."""
        self.unified_dataset = None
        self._original_unified_dataset = None
        self._notify("unified_dataset")

    def clear_all(self):
        """Reset everything."""
        for key in self.portals:
            self.portals[key] = None
        self.unified_dataset = None
        self._original_unified_dataset = None
        self.model_ready     = False
        self.last_prediction_run = None
        self._notify("all")
    
    def get_prediction_dataset(self) -> dict:
        """Get unified dataset for prediction (with Student_ID column preserved).
        
        Returns the original merged dataset (with Student_ID) if available,
        otherwise falls back to the current unified_dataset.
        """
        if self._original_unified_dataset is not None:
            return self._original_unified_dataset
        return self.unified_dataset

    def set_last_prediction_run(self, timestamp: str | None = None):
        """Store latest successful prediction timestamp and notify listeners."""
        self.last_prediction_run = (
            timestamp
            if timestamp is not None
            else datetime.now().strftime("%b %d, %Y · %H:%M")
        )
        self._notify("last_prediction_run")

    # ------------------------------------------------------------------
    # Readiness checks
    # ------------------------------------------------------------------

    def all_portals_ready(self) -> bool:
        """True only when all four portals have uploaded data."""
        return all(v is not None for v in self.portals.values())

    def get_readiness(self) -> dict:
        return {k: v is not None for k, v in self.portals.items()}

    def ready_count(self) -> int:
        """How many portals have data uploaded."""
        return sum(1 for v in self.portals.values() if v is not None)

    # ------------------------------------------------------------------
    # Listener / observer pattern
    # ------------------------------------------------------------------

    def add_listener(self, callback):
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback):
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _notify(self, key: str):
        for cb in self._listeners:
            try:
                cb(key)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Model & Unified Dataset
    # ------------------------------------------------------------------

    def set_trained_model(self, model_service):
        """Store trained ML model in PredictionEngine-compatible format."""
        if isinstance(model_service, dict):
            self.trained_model = model_service
        else:
            # Accept MLService objects and convert to serializable model package
            self.trained_model = {
                "model": getattr(model_service, "model", None),
                "feature_names": list(getattr(model_service, "feature_names", []) or []),
                "target_classes": list(getattr(model_service, "target_classes", []) or []),
                "training_history": dict(
                    getattr(model_service, "training_history", {}) or {}
                ),
                "target_col": "risk_label",
                "is_mock": False,
            }
        self.model_ready = True
        self._notify("trained_model")

    def save_model_to_disk(self, model_id: str, metadata: dict = None) -> dict:
        """
        Save the current trained model to disk using ModelRegistry.

        Parameters
        ----------
        model_id : str
            Model type ('rf', 'xgb', 'lr')
        metadata : dict, optional
            Additional metadata (accuracy, precision, recall, etc.)

        Returns
        -------
        dict
            Save result: {"success": bool, "path": str, ...}
        """
        if not self.trained_model or "model" not in self.trained_model:
            return {"success": False, "error": "No trained model in memory"}

        from services.model_registry import ModelRegistry

        feature_names = self.trained_model.get("feature_names", [])
        model_obj = self.trained_model["model"]

        # Merge with training history metadata
        full_metadata = {
            **(self.trained_model.get("training_history", {})),
            **(metadata or {}),
        }

        return ModelRegistry.save_model(model_obj, model_id, feature_names, full_metadata)

    def _load_persisted_model(self) -> None:
        """
        Load the latest trained model from disk on app startup.
        Called automatically from __init__.
        """
        from services.model_registry import ModelRegistry

        model_pkg = ModelRegistry.load_latest_model()
        if model_pkg:
            try:
                self.trained_model = {
                    "model": model_pkg["model"],
                    "model_id": model_pkg["model_id"],
                    "feature_names": model_pkg["feature_names"],
                    "metadata": model_pkg["metadata"],
                    "target_col": "risk_label",
                }
                self.model_ready = True
                timestamp = model_pkg["metadata"].get("timestamp", "unknown")
                print(f"[DataStore] Auto-loaded model from {timestamp}")
            except Exception as e:
                print(f"[DataStore] Failed to load persisted model: {e}")


    def build_unified_dataset(self) -> pd.DataFrame | None:
        """
        Merge all 4 portal datasets into one unified dataset.
        Call this after all portals have uploaded.
        """
        portal_data = {}
        for key in ["mis", "sao", "guidance", "registrar"]:
            data = self.get_portal(key)
            if data:
                portal_data[key] = data

        if not portal_data:
            return None

        # Convert each portal's headers+rows to DataFrame
        dfs = []
        for key, data in portal_data.items():
            df = pd.DataFrame(data["rows"], columns=data["headers"])
            df["portal_source"] = key
            dfs.append(df)

        unified = self._merge_portal_dfs(dfs)
        self.unified_dataset = unified
        return unified

    def _merge_portal_dfs(self, dfs: list[pd.DataFrame]) -> pd.DataFrame | None:
        """
        Merge multiple portal DataFrames on student ID.
        """
        if not dfs:
            return None

        # ID column names vary by portal → standardize to "student_id"
        id_mappings = {
            "mis": ["ID_NO", "id_no", "ID", "id", "STUDENT_ID", "student_id"],
            "sao": ["STUDENT_ID", "student_id", "ID_NO", "id_no"],
            "guidance": ["student_id", "STUDENT_ID", "id", "ID"],
            "registrar": ["student_id", "STUDENT_ID", "id", "ID"],
        }

        standardized = []
        for df in dfs:
            portal = df["portal_source"].iloc[0]
            possible_ids = id_mappings.get(portal, ["student_id"])

            id_col = None
            for col in possible_ids:
                if col in df.columns:
                    id_col = col
                    break

            if id_col:
                df = df.rename(columns={id_col: "student_id"})

            standardized.append(df)

        # Merge iteratively
        merged = standardized[0].copy()
        for df in standardized[1:]:
            # Drop portal_source from right side to avoid duplicates
            right = df.drop(columns=["portal_source"], errors="ignore")
            merged = merged.merge(
                right,
                on="student_id",
                how="outer",
                suffixes=('', f'_{df["portal_source"].iloc[0]}')
            )

        return merged

    # ------------------------------------------------------------------
    # Debug / summary
    # ------------------------------------------------------------------

    def summary(self) -> str:
        lines = ["DataStore Summary:"]
        for key, val in self.portals.items():
            if val:
                lines.append(
                    f"  ✓ {key:12s} {val['row_count']:,} rows  "
                    f"· uploaded {val['timestamp']}"
                )
            else:
                lines.append(f"  ✗ {key:12s} not uploaded")
        lines.append(
            f"\n  All ready: {self.all_portals_ready()}  "
            f"({self.ready_count()}/4)"
        )
        return "\n".join(lines)