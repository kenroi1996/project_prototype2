"""
workers/pipeline_worker.py
───────────────────────────
QThread worker that runs the full feature-engineering pipeline and
persists engineered features to feature_store.

Signals
───────
step(name: str, message: str)        ← emitted at each pipeline stage
progress(pct: int)                   ← 0–100
finished(result: dict)               ← {"headers", "rows", "run_id", ...}
error(message: str)

Usage (from a QWidget)
─────
    from workers.pipeline_worker import FeaturePipelineWorker

    self._worker = FeaturePipelineWorker(run_id=42)
    self._worker.step.connect(self._on_step)
    self._worker.progress.connect(self._progress_bar.setValue)
    self._worker.finished.connect(self._on_pipeline_done)
    self._worker.error.connect(self._on_pipeline_error)
    self._worker.start()
"""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from services.data_store import DataStore
from services.feature_engineering import run_full_feature_pipeline, TARGET_COLUMN


class FeaturePipelineWorker(QThread):
    step     = pyqtSignal(str, str)   # (stage_name, message)
    progress = pyqtSignal(int)        # 0-100
    finished = pyqtSignal(dict)       # result payload
    error    = pyqtSignal(str)

    def __init__(self, run_id: int = 0, parent=None) -> None:
        super().__init__(parent)
        self._run_id = run_id

    def run(self) -> None:
        try:
            import pandas as pd

            store = DataStore.get()

            self.step.emit("load", "Loading unified dataset from DataStore…")
            self.progress.emit(5)

            unified = store.unified_dataset
            if unified is None:
                raise ValueError(
                    "No unified dataset found. Run Data Merge first."
                )

            if isinstance(unified, dict):
                df = pd.DataFrame(unified["rows"], columns=unified["headers"])
            else:
                df = unified.copy()

            self.step.emit("engineer", "Defining risk labels & engineering features…")
            self.progress.emit(20)
            df = run_full_feature_pipeline(df)

            self.step.emit("persist", "Persisting features to feature_store…")
            self.progress.emit(70)
            self._persist(df)

            headers = list(df.columns)
            rows    = df.astype(str).fillna("").values.tolist()

            self.step.emit("done", f"Pipeline complete: {len(rows):,} rows · {len(headers)} features")
            self.progress.emit(100)
            self.finished.emit({
                "headers": headers,
                "rows":    rows,
                "run_id":  self._run_id,
                "target":  TARGET_COLUMN,
            })

        except Exception as exc:
            self.error.emit(str(exc))

    def _persist(self, df) -> None:
        """Write engineered rows to feature_store table."""
        from db import get_session
        from models.feature_store import FeatureStore

        col = lambda name: df[name].tolist() if name in df.columns else [None] * len(df)

        records = []
        for i, sid in enumerate(col("Student_ID")):
            records.append(FeatureStore(
                student_id            = str(sid),
                run_id                = self._run_id,
                gpa_tier              = _safe_int(col("GPA_Tier")[i]),
                has_college_grade     = _safe_int(col("Has_College_Grade")[i]),
                year_level            = _safe_int(col("Year_Level")[i]),
                entrance_exam_tier    = _safe_int(col("Entrance_Exam_Tier")[i]),
                hs_performance_tier   = _safe_int(col("HS_Performance_Tier")[i]),
                strand_program_match  = _safe_float(col("Strand_Program_Match")[i]),
                financial_stress      = _safe_int(col("Financial_Stress")[i]),
                first_gen_student     = _safe_int(col("First_Gen_Student")[i]),
                has_scholarship       = _safe_int(col("Has_Scholarship")[i]),
                gap_years             = _safe_int(col("Gap_Years")[i]),
                private_hs            = _safe_int(col("Private_HS")[i]),
                has_hs_honors         = _safe_int(col("Has_HS_Honors")[i]),
                age_at_enrollment     = _safe_float(col("Age_At_Enrollment")[i]),
                risk_label            = str(col(TARGET_COLUMN)[i])
                                        if TARGET_COLUMN in df.columns else None,
            ))

        with get_session() as session:
            session.bulk_save_objects(records)


def _safe_int(v) -> int | None:
    try:
        return int(float(v)) if v is not None and str(v) not in ("nan", "") else None
    except (ValueError, TypeError):
        return None


def _safe_float(v) -> float | None:
    try:
        return float(v) if v is not None and str(v) not in ("nan", "") else None
    except (ValueError, TypeError):
        return None
