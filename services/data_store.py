from datetime import datetime

import pandas as pd


# =============================================================================
# Thread-safe notification relay
# =============================================================================
# DataStore._notify() can be called from any thread (e.g. TrainingWorker).
# Listeners are UI callbacks that touch QWidgets and must run on the main
# thread. _NotifyRelay is a QObject whose signal is always delivered via a
# Qt QueuedConnection, which guarantees the slot executes on the main thread
# regardless of which thread emitted it.
#
# Import is deferred to avoid pulling PyQt6 into pure-logic test contexts —
# _NotifyRelay is constructed lazily on first use.
# =============================================================================

class _NotifyRelay:
    """
    Thin wrapper around a pyqtSignal so DataStore can fire listener callbacks
    on the main thread even when _notify() is called from a worker thread.
    """
    _instance = None

    @classmethod
    def get(cls) -> "_NotifyRelay":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        # Build the QObject subclass dynamically so that importing data_store
        # in a non-Qt context (unit tests, CLI scripts) doesn't crash.
        from PyQt6.QtCore import QObject, pyqtSignal

        class _Relay(QObject):
            notify = pyqtSignal(str)   # carries the changed key

        self._relay = _Relay()

    def connect(self, slot):
        """Connect a listener slot — always delivered on the main thread."""
        from PyQt6.QtCore import Qt
        self._relay.notify.connect(slot, Qt.ConnectionType.QueuedConnection)

    def disconnect(self, slot):
        try:
            self._relay.notify.disconnect(slot)
        except Exception:
            pass

    def emit(self, key: str):
        """Emit from any thread; Qt delivers it on the main thread."""
        self._relay.notify.emit(key)


# =============================================================================
# DATA STORE SINGLETON
# =============================================================================

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

        self._original_unified_dataset = None
        self.unified_dataset     = None
        self.trained_model       = None
        self.model_ready         = False
        self.predictions         = None
        self.last_prediction_run = None

        self.prediction_dataset_name = None
        self.prediction_school_year  = None

        # Raw meta snapshot: dict keyed on student_id → meta dict.
        # Populated by _FusedPredictionWorker BEFORE feature engineering strips
        # the raw columns (College, Home_Address, Birthdate, etc.).
        # PredictionEngine merges this back into student_meta after _prepare()
        # so the persistence layer has full demographic data to write.
        self.raw_meta_snapshot: dict = {}

        self.activities: list  = []
        self._max_activities   = 50

        # Listener list is kept for add/remove API compatibility, but
        # actual delivery now goes through _NotifyRelay (see _notify).
        self._listeners: list = []

        self._load_persisted_model()

    # ------------------------------------------------------------------
    # Portal data management
    # ------------------------------------------------------------------

    def set_portal(self, key: str, headers: list, rows: list):
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
        return self.portals.get(key)

    def clear_portal(self, key: str):
        if key in self.portals:
            self.portals[key] = None
            self._notify(key)

    def set_unified_dataset(self, data) -> None:
        self.unified_dataset = data
        if self._original_unified_dataset is None and data is not None:
            self._original_unified_dataset = data
        self._notify("unified_dataset")

    def set_prediction_dataset(self, data, name: str | None = None,
                               school_year: str | None = None) -> None:
        self.unified_dataset = data
        self._original_unified_dataset = data
        self.prediction_dataset_name = name
        self.prediction_school_year  = school_year
        self._notify("unified_dataset")

    def set_raw_meta_snapshot(self, snapshot: dict) -> None:
        """
        Store a {student_id: meta_dict} snapshot captured from the raw dataset
        before feature engineering strips demographic columns.
        Called by _FusedPredictionWorker; read by PredictionEngine._prepare().
        """
        self.raw_meta_snapshot = snapshot

    def clear_unified_dataset(self):
        self.unified_dataset = None
        self._original_unified_dataset = None
        self._notify("unified_dataset")

    def clear_all(self):
        for key in self.portals:
            self.portals[key] = None
        self.unified_dataset = None
        self._original_unified_dataset = None
        self.raw_meta_snapshot = {}
        self.model_ready     = False
        self.last_prediction_run = None
        self._notify("all")

    def get_prediction_dataset(self) -> dict:
        if self._original_unified_dataset is not None:
            return self._original_unified_dataset
        return self.unified_dataset

    def set_last_prediction_run(self, timestamp: str | None = None):
        self.last_prediction_run = (
            timestamp
            if timestamp is not None
            else datetime.now().strftime("%b %d, %Y · %H:%M")
        )
        self._notify("last_prediction_run")

    # ------------------------------------------------------------------
    # Activity feed
    # ------------------------------------------------------------------

    def add_activity(self, message: str, icon: str = "•",
                     color: str = "#4f8cff") -> None:
        self.activities.append({
            "message": message,
            "icon":    icon,
            "color":   color,
            "time":    datetime.now().strftime("%b %d · %H:%M"),
        })
        if len(self.activities) > self._max_activities:
            self.activities = self.activities[-self._max_activities:]
        self._notify("activity")

    def clear_activities(self) -> None:
        self.activities = []
        self._notify("activity")

    # ------------------------------------------------------------------
    # Readiness checks
    # ------------------------------------------------------------------

    def all_portals_ready(self) -> bool:
        return all(v is not None for v in self.portals.values())

    def get_readiness(self) -> dict:
        return {k: v is not None for k, v in self.portals.items()}

    def ready_count(self) -> int:
        return sum(1 for v in self.portals.values() if v is not None)

    # ------------------------------------------------------------------
    # Listener / observer pattern
    # ------------------------------------------------------------------

    def add_listener(self, callback):
        """
        Register a callback to be invoked when any DataStore key changes.

        The callback receives a single str argument (the changed key) and is
        ALWAYS invoked on the Qt main thread, even if the change originated
        in a worker thread — so it is safe to call QWidget methods inside it.
        """
        if callback not in self._listeners:
            self._listeners.append(callback)
            # Wire through the thread-safe relay
            _NotifyRelay.get().connect(callback)

    def remove_listener(self, callback):
        if callback in self._listeners:
            self._listeners.remove(callback)
            _NotifyRelay.get().disconnect(callback)

    def _notify(self, key: str):
        """
        Post a change notification.

        Safe to call from any thread. Delivery to all listeners is
        marshalled to the main thread via _NotifyRelay's QueuedConnection,
        so listeners can safely touch QWidgets without triggering
        'QObject::setParent: Cannot set parent, new parent is in a
        different thread' warnings.
        """
        try:
            _NotifyRelay.get().emit(key)
        except Exception:
            # Fallback for non-Qt contexts (unit tests, CLI) — call directly.
            for cb in list(self._listeners):
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
            self.trained_model = {
                "model":            getattr(model_service, "model", None),
                "feature_names":    list(getattr(model_service, "feature_names", []) or []),
                "target_classes":   list(getattr(model_service, "target_classes", []) or []),
                "training_history": dict(getattr(model_service, "training_history", {}) or {}),
                "target_col":       "risk_label",
                "is_mock":          False,
                # Fitted DataPipeline — carries LabelEncoders + StandardScaler
                # so PredictionEngine can apply the same transformations used
                # during training before calling model.predict_proba()
                "preprocessor":     getattr(model_service, "preprocessor", None),
            }
        self.model_ready = True
        self._notify("trained_model")

    def save_model_to_disk(self, model_id: str, metadata: dict = None,
                           db_conn=None) -> dict:
        if not self.trained_model or "model" not in self.trained_model:
            return {"success": False, "error": "No trained model in memory"}

        from services.model_registry import ModelRegistry

        feature_names = self.trained_model.get("feature_names", [])
        model_obj     = self.trained_model["model"]
        full_metadata = {
            **(self.trained_model.get("training_history", {})),
            **(metadata or {}),
        }

        # Pass the preprocessor (fitted DataPipeline with LabelEncoders + Scaler)
        # so it is included in the pickle package and survives disk/DB saves.
        # Without this, loaded models have no preprocessor and produce 0/100 scores.
        preprocessor = self.trained_model.get("preprocessor")

        return ModelRegistry.save_model(
            model_obj, model_id, feature_names, full_metadata,
            db_conn=db_conn, preprocessor=preprocessor,
        )

    def _load_persisted_model(self) -> None:
        from services.model_registry import ModelRegistry

        model_pkg = ModelRegistry.load_latest_model()
        if model_pkg:
            try:
                self.trained_model = {
                    "model":         model_pkg["model"],
                    "model_id":      model_pkg["model_id"],
                    "feature_names": model_pkg["feature_names"],
                    "metadata":      model_pkg["metadata"],
                    "target_col":    "risk_label",
                }
                self.model_ready = True
                timestamp = model_pkg["metadata"].get("timestamp", "unknown")
                print(f"[DataStore] Auto-loaded model from {timestamp}")
            except Exception as e:
                print(f"[DataStore] Failed to load persisted model: {e}")

    def build_unified_dataset(self) -> pd.DataFrame | None:
        portal_data = {}
        for key in ["mis", "sao", "guidance", "registrar"]:
            data = self.get_portal(key)
            if data:
                portal_data[key] = data

        if not portal_data:
            return None

        dfs = []
        for key, data in portal_data.items():
            df = pd.DataFrame(data["rows"], columns=data["headers"])
            df["portal_source"] = key
            dfs.append(df)

        unified = self._merge_portal_dfs(dfs)
        self.unified_dataset = unified
        return unified

    def _merge_portal_dfs(self, dfs: list[pd.DataFrame]) -> pd.DataFrame | None:
        if not dfs:
            return None

        id_mappings = {
            "mis":       ["ID_NO", "id_no", "ID", "id", "STUDENT_ID", "student_id"],
            "sao":       ["STUDENT_ID", "student_id", "ID_NO", "id_no"],
            "guidance":  ["student_id", "STUDENT_ID", "id", "ID"],
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

        merged = standardized[0].copy()
        for df in standardized[1:]:
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