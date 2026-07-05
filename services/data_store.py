import gzip
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

# Path for persisting the raw merged dataset between app restarts.
# Stored as gzip-compressed JSON so it loads fast and takes minimal disk space.
_RAW_CACHE_PATH = Path("outputs/_raw_merged_cache.json.gz")


# =============================================================================
# Thread-safe notification relay
# =============================================================================

class _NotifyRelay:
    _instance = None

    @classmethod
    def get(cls) -> "_NotifyRelay":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        from PyQt6.QtCore import QObject, pyqtSignal

        class _Relay(QObject):
            notify = pyqtSignal(str)

        self._relay = _Relay()

    def connect(self, slot):
        from PyQt6.QtCore import Qt
        self._relay.notify.connect(slot, Qt.ConnectionType.QueuedConnection)

    def disconnect(self, slot):
        try:
            self._relay.notify.disconnect(slot)
        except Exception:
            pass

    def emit(self, key: str):
        self._relay.notify.emit(key)


# =============================================================================
# DATA STORE SINGLETON
# =============================================================================

class DataStore:
    """
    Central singleton that holds cleaned datasets from all four portals.
    Access anywhere in the app via DataStore.get().

    DATASET SLOTS
    -------------
    raw_merged_dataset   : raw output from MergeEngine — headers + rows that
                           still contain Final_Avg_GRD and all original columns.
                           Used as the sole input to TrainingEngine so retraining
                           always starts from unmodified data regardless of how
                           many pipeline or prediction runs have happened since
                           the last merge.

                           Persisted to disk (_RAW_CACHE_PATH) immediately after
                           each merge so it survives app restarts — retraining
                           works correctly without forcing a re-merge every session.

                           NEVER overwritten by the pipeline or prediction flow.
                           Only replaced by a new merge run or clear_all().

    unified_dataset      : current "active" dataset used by the pipeline and
                           prediction engine.  After a merge this equals the raw
                           merged data.  After a prediction run it may hold the
                           incoming student dataset.
                           Training NEVER reads from this slot.
    """

    _instance = None

    @classmethod
    def get(cls) -> "DataStore":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.portals: dict = {
            "mis":       None,
            "sao":       None,
            "guidance":  None,
            "registrar": None,
        }

        # ── Two separate dataset slots ─────────────────────────────────────────
        self.raw_merged_dataset          = None   # raw merge output — training source
        self._original_unified_dataset   = None   # kept for prediction engine back-compat
        self.unified_dataset             = None   # engineered / active dataset

        self.trained_model       = None
        self.model_ready         = False
        self.predictions         = None
        self.last_prediction_run = None

        self.prediction_dataset_name = None
        self.prediction_school_year  = None

        self.raw_meta_snapshot: dict = {}

        self.activities: list  = []
        self._max_activities   = 20

        self._listeners: list = []

        # ── DB connection (set once after login) ──────────────────────────────
        self.db_conn = None

        self._load_persisted_model()
        self._load_raw_merged_cache()   # restore raw dataset from disk if available

    # ------------------------------------------------------------------
    # DB connection management
    # ------------------------------------------------------------------

    def set_db_conn(self, conn) -> None:
        self.db_conn = conn
        print("[DataStore] DB connection registered.")
        self._load_activities_from_db()

    def _load_activities_from_db(self) -> None:
        if not self.db_conn:
            return
        try:
            with self.db_conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT log_timestamp, user_name, action,
                           entity_type, description, status
                    FROM   public.activity_log
                    ORDER  BY log_timestamp DESC
                    LIMIT  %s
                    """,
                    (self._max_activities,),
                )
                rows = cur.fetchall()

            rows = list(reversed(rows))

            _ACTION_ICON = {
                "LOGIN":        "",
                "LOGOUT":       "",
                "LOGIN_FAILED": "",
                "UPLOAD":       "",
                "MERGE":        "",
                "TRAIN":        "",
                "PREDICT":      "⚡",
                "VIEW":         "👁",
                "EXPORT":       "💾",
            }
            _ACTION_COLOR = {
                "LOGIN":        "#34d399",
                "LOGOUT":       "#8b949e",
                "LOGIN_FAILED": "#ff5b5b",
                "UPLOAD":       "#4f8cff",
                "MERGE":        "#4f8cff",
                "TRAIN":        "#a78bfa",
                "PREDICT":      "#34d399",
                "VIEW":         "#f5b335",
                "EXPORT":       "#34d399",
            }

            self.activities = []
            for ts, user_name, action, entity_type, description, status in rows:
                action_upper = (action or "").upper()
                time_str = (
                    ts.strftime("%b %d · %H:%M") if hasattr(ts, "strftime")
                    else str(ts)[:16]
                )
                msg = description or f"{action_upper} — {entity_type}"
                if user_name:
                    msg = f"{msg}  ·  {user_name}"

                self.activities.append({
                    "message": msg,
                    "icon":    _ACTION_ICON.get(action_upper, "•"),
                    "color":   _ACTION_COLOR.get(action_upper, "#4f8cff"),
                    "time":    time_str,
                })

            print(f"[DataStore] Loaded {len(self.activities)} activity entries from DB.")
            self._notify("activity")

        except Exception as exc:
            print(f"[DataStore] Could not load activities from DB: {exc}")

    def get_db_conn(self):
        return self.db_conn

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

    # ------------------------------------------------------------------
    # Raw merged dataset — training source, persisted across restarts
    # ------------------------------------------------------------------

    def set_raw_merged_dataset(self, data: dict) -> None:
        """
        Store the raw MergeEngine output (headers + rows, still containing
        Final_Avg_GRD and all original columns).

        Called once from DataMergePipelinePage._on_merge_finished().
        Immediately persisted to disk so retraining works correctly after an
        app restart without requiring a re-merge.

        This slot is ONLY replaced by a new merge run or clear_all() —
        never by the pipeline or prediction flow.
        """
        self.raw_merged_dataset = data
        print(
            f"[DataStore] raw_merged_dataset stored: "
            f"{len(data.get('rows', [])):,} rows × "
            f"{len(data.get('headers', []))} columns"
        )

        # Persist to disk so it survives app restarts
        try:
            _RAW_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(_RAW_CACHE_PATH, "wt", encoding="utf-8") as f:
                json.dump(data, f)
            print(f"[DataStore] raw_merged_dataset cached to {_RAW_CACHE_PATH}")
        except Exception as e:
            print(f"[DataStore] WARNING: could not cache raw_merged_dataset to disk: {e}")

        self._notify("raw_merged_dataset")

    def _load_raw_merged_cache(self) -> None:
        """
        Restore the raw merged dataset from disk on startup.
        Called once from __init__() after _load_persisted_model().
        Silent if the cache file does not exist (first run or after clear_all).
        """
        if not _RAW_CACHE_PATH.exists():
            return
        try:
            with gzip.open(_RAW_CACHE_PATH, "rt", encoding="utf-8") as f:
                data = json.load(f)
            self.raw_merged_dataset = data
            print(
                f"[DataStore] raw_merged_dataset restored from disk cache: "
                f"{len(data.get('rows', [])):,} rows × "
                f"{len(data.get('headers', []))} columns | "
                f"Final_Avg_GRD present: {'Final_Avg_GRD' in data.get('headers', [])}"
            )
        except Exception as e:
            print(f"[DataStore] WARNING: could not restore raw_merged_dataset from cache: {e}")

    def get_raw_merged_dataset(self, warn: bool = True) -> dict | None:
        """
        Return the raw merged dataset for training.

        Parameters
        ----------
        warn : bool
            If True (default), print a warning when falling back to
            unified_dataset.  Pass warn=False from UI readiness checks
            that call this on every DataStore event — those don't need
            the warning and would spam the console.
        """
        if self.raw_merged_dataset is not None:
            return self.raw_merged_dataset

        # Fallback: unified_dataset may still be raw (e.g. merge was run
        # but pipeline was never run in this session AND cache is missing).
        if self.unified_dataset is not None:
            if warn:
                print(
                    "[DataStore] WARNING: raw_merged_dataset is None — "
                    "falling back to unified_dataset for training. "
                    "If training fails with 'risk_label missing', re-run "
                    "the Data Merge to restore the raw dataset."
                )
            return self.unified_dataset

        return None

    # ------------------------------------------------------------------
    # Unified (active) dataset — pipeline and prediction engine only
    # ------------------------------------------------------------------

    def set_unified_dataset(self, data) -> None:
        """
        Set the active dataset used by the pipeline and prediction engine.
        Training NEVER reads from this slot — use get_raw_merged_dataset().
        """
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
        self.raw_meta_snapshot = snapshot

    def clear_unified_dataset(self):
        self.unified_dataset = None
        self._original_unified_dataset = None
        self._notify("unified_dataset")

    def clear_all(self):
        for key in self.portals:
            self.portals[key] = None
        self.raw_merged_dataset        = None
        self.unified_dataset           = None
        self._original_unified_dataset = None
        self.raw_meta_snapshot         = {}
        self.model_ready               = False
        self.last_prediction_run       = None
        # Delete the disk cache so the next merge starts fresh
        try:
            if _RAW_CACHE_PATH.exists():
                _RAW_CACHE_PATH.unlink()
                print("[DataStore] raw_merged_dataset disk cache cleared.")
        except Exception as e:
            print(f"[DataStore] WARNING: could not delete raw_merged_dataset cache: {e}")
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
        entry = {
            "message": message,
            "icon":    icon,
            "color":   color,
            "time":    datetime.now().strftime("%b %d · %H:%M"),
        }
        self.activities.append(entry)
        if len(self.activities) > self._max_activities:
            self.activities = self.activities[-self._max_activities:]

        if self.db_conn:
            try:
                from services.activity_logger import ActivityLogger
                _ICON_ACTION = {
                    "🔐": ("LOGIN",   "SESSION"),
                    "🚪": ("LOGOUT",  "SESSION"),
                    "📂": ("UPLOAD",  "DATASET"),
                    "🔀": ("MERGE",   "DATASET"),
                    "🧠": ("TRAIN",   "MODEL"),
                    "⚡": ("PREDICT", "DATASET"),
                    "👁": ("VIEW",    "STUDENT"),
                    "💾": ("EXPORT",  "DATASET"),
                }
                action, entity_type = _ICON_ACTION.get(icon, ("ACTIVITY", "SYSTEM"))
                ActivityLogger.log(
                    self.db_conn,
                    action      = action,
                    entity_type = entity_type,
                    description = message,
                )
                self.db_conn.commit()
            except Exception as _exc:
                print(f"[DataStore] add_activity DB write error: {_exc}")

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
        if callback not in self._listeners:
            self._listeners.append(callback)
            _NotifyRelay.get().connect(callback)

    def remove_listener(self, callback):
        if callback in self._listeners:
            self._listeners.remove(callback)
            _NotifyRelay.get().disconnect(callback)

    def _notify(self, key: str):
        try:
            _NotifyRelay.get().emit(key)
        except Exception:
            for cb in list(self._listeners):
                try:
                    cb(key)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Model management
    # ------------------------------------------------------------------

    def set_trained_model(self, model_service):
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
        raw = self.raw_merged_dataset
        if raw:
            lines.append(
                f"  raw_merged_dataset: "
                f"{len(raw.get('rows', [])):,} rows × "
                f"{len(raw.get('headers', []))} columns"
                f" (cache: {'exists' if _RAW_CACHE_PATH.exists() else 'missing'})"
            )
        return "\n".join(lines)