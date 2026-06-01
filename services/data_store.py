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

        self.unified_dataset = None   # set after merge
        self.trained_model   = None   # set after training
        self.model_ready     = False
        self.predictions     = None

        # Callbacks: list of callables notified on any portal change
        self._listeners: list = []

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
        """Store merged dataset (dict with headers/rows or DataFrame) and notify listeners."""
        self.unified_dataset = data
        self._notify("unified_dataset")

    def clear_unified_dataset(self):
        """Clear merged dataset after portal data changes or reset."""
        self.unified_dataset = None
        self._notify("unified_dataset")

    def clear_all(self):
        """Reset everything."""
        for key in self.portals:
            self.portals[key] = None
        self.unified_dataset = None
        self.model_ready     = False
        self._notify("all")

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
        """Store trained ML model from pipeline."""
        self.trained_model = model_service
        self.model_ready = True

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