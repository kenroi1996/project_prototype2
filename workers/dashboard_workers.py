"""
workers/dashboard_workers.py
===============================
Background QThread workers for the Dashboard page.

  - _InterventionRateLoader : counts total intervention log records
                              across all terms

Extracted verbatim from ui/pages/dashboard_page.py — no logic changes.
"""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal as _Signal

from services.data_store import DataStore


class _InterventionRateLoader(QThread):
    """Counts total intervention log records across all terms."""
    finished = _Signal(int, int)   # (total_logs, per_student_logs)
    error    = _Signal(str)

    def run(self) -> None:
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No DB connection")
            return
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM public.interventions")
                total = int(cur.fetchone()[0])
                cur.execute(
                    "SELECT COUNT(*) FROM public.interventions "
                    "WHERE mode = 'per_student'"
                )
                per_student = int(cur.fetchone()[0])
            self.finished.emit(total, per_student)
        except Exception as exc:
            self.error.emit(str(exc))