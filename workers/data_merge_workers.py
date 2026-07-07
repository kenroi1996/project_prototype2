"""
workers/data_merge_workers.py
===============================
Background QThread workers for the Data Merge & Pipeline page.

  - _MergeWorker    : runs MergeEngine.merge() off the UI thread
  - PipelineWorker  : runs the full preprocessing + training pipeline

Extracted verbatim from ui/pages/data_merge_pipeline_page.py — no logic changes.
"""
from __future__ import annotations
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from services.data_store import DataStore
from services.merge_engine import MergeEngine
from services.pipeline_orchestrator import PipelineOrchestrator


# =====================================
# MERGE WORKER THREAD
# =====================================

class _MergeWorker(QThread):
    finished = pyqtSignal(object)
    error    = pyqtSignal(str)

    def run(self):
        try:
            store  = DataStore.get()
            result = MergeEngine.merge(store.portals)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


# =====================================
# PIPELINE WORKER THREAD
# =====================================

class PipelineWorker(QThread):
    """Background worker for the full ML pipeline."""

    step_started     = pyqtSignal(str, str)
    step_progress    = pyqtSignal(int)
    finished_success = pyqtSignal(dict)
    finished_error   = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.orchestrator = PipelineOrchestrator()

    def run(self):
        try:
            store = DataStore.get()

            # ── FIX: always use raw_merged_dataset as training source ──────────
            # unified_dataset may have been overwritten by a previous pipeline
            # run with engineered data (Final_Avg_GRD dropped), which causes
            # define_target() to be skipped → risk_label missing → training fails.
            # raw_merged_dataset is set once after merge and never overwritten.
            raw = store.get_raw_merged_dataset()
            if raw is None:
                raise ValueError(
                    "No merged dataset found. "
                    "Please run the Data Merge step first."
                )

            temp_path = Path("outputs/_unified_temp.csv")
            temp_path.parent.mkdir(parents=True, exist_ok=True)

            import pandas as pd
            if isinstance(raw, dict):
                df = pd.DataFrame(raw["rows"], columns=raw["headers"])
            else:
                df = raw
            df.to_csv(temp_path, index=False)

            print(
                f"[PipelineWorker] Writing temp CSV from raw_merged_dataset: "
                f"{len(df):,} rows | Final_Avg_GRD present: {'Final_Avg_GRD' in df.columns}"
            )

            def on_step(step, msg):
                self.step_started.emit(step, msg)

            results = self.orchestrator.run(
                excel_path=str(temp_path),
                model_type="random_forest",
                save_path="outputs",
                on_step=on_step,
            )

            self.finished_success.emit(results)
        except Exception as e:
            self.finished_error.emit(str(e))