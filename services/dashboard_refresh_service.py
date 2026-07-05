"""
services/dashboard_refresh_service.py

Handles dashboard data-refresh logic:
  - Checks whether prediction data still exists in the database.
  - Re-applies in-memory predictions if data is still present.
  - Signals the caller to clear the UI when data has been deleted.

Usage (from DashboardPage):
    from services.dashboard_refresh_service import DashboardRefreshService

    self._refresh_svc = DashboardRefreshService()
    self._refresh_svc.data_available.connect(self._on_refresh_data_available)
    self._refresh_svc.data_cleared.connect(self._on_refresh_data_cleared)
    self._refresh_svc.error.connect(self._on_refresh_error)
    self._refresh_svc.refresh()
"""

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from services.data_store import DataStore


# ---------------------------------------------------------------------------
# Private worker — runs on a background thread
# ---------------------------------------------------------------------------

class _CheckRowCountWorker(QThread):
    """
    Runs a lightweight COUNT(*) against fact_student_academic_risk.
    Emits the row count so the service can decide what to do next.
    """
    finished = pyqtSignal(int)   # total rows still in the fact table
    error    = pyqtSignal(str)

    def run(self) -> None:
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No database connection available.")
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM public.fact_student_academic_risk"
                )
                total = int(cur.fetchone()[0])
            self.finished.emit(total)
        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Public service
# ---------------------------------------------------------------------------

class DashboardRefreshService(QObject):
    """
    Encapsulates all refresh logic for the admin dashboard.

    Signals
    -------
    data_available(predictions)
        Emitted when DB data exists and the in-memory prediction result
        object is ready for the UI to re-apply.

    data_cleared()
        Emitted when the fact table is empty — the UI should reset to
        its empty state.

    error(message: str)
        Emitted when the DB check fails (e.g. connection dropped).

    busy_changed(is_busy: bool)
        Emitted when a refresh starts (True) or completes (False).
        Use this to toggle button enabled state / spinner.
    """

    data_available = pyqtSignal(object)   # prediction result object
    data_cleared   = pyqtSignal()
    error          = pyqtSignal(str)
    busy_changed   = pyqtSignal(bool)

    def __init__(self, parent: QObject = None) -> None:
        super().__init__(parent)
        self._worker: _CheckRowCountWorker | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """
        Kick off a background DB check.
        Does nothing if a refresh is already running.
        """
        if self._worker is not None:
            return

        self.busy_changed.emit(True)

        self._worker = _CheckRowCountWorker()
        self._worker.finished.connect(self._on_count_received)
        self._worker.error.connect(self._on_worker_error)
        # Null out our reference BEFORE deleteLater destroys the C++ object,
        # so subsequent calls to refresh() never touch a deleted pointer.
        self._worker.finished.connect(self._clear_worker)
        self._worker.error.connect(self._clear_worker)
        self._worker.start()

    def cleanup(self) -> None:
        """
        Gracefully stop any running worker.
        Call this from the owning widget's closeEvent.
        """
        if self._worker is None:
            return
        try:
            self._worker.finished.disconnect()
        except Exception:
            pass
        try:
            self._worker.error.disconnect()
        except Exception:
            pass
        try:
            if self._worker.isRunning():
                self._worker.quit()
                self._worker.wait(2000)
        except RuntimeError:
            pass
        except Exception:
            pass
        self._worker = None

    # ------------------------------------------------------------------
    # Private slots
    # ------------------------------------------------------------------

    def _clear_worker(self) -> None:
        """Null our reference first, then schedule C++ cleanup."""
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()

    def _on_count_received(self, row_count: int) -> None:
        self.busy_changed.emit(False)

        if row_count == 0:
            # Fact table is empty — wipe in-memory state and broadcast to ALL
            # pages via DataStore._notify so Student Cohort, Risk Alerts, and
            # any other listener clears itself automatically.
            store = DataStore.get()
            store.predictions         = None
            store.last_prediction_run = None
            store._notify("predictions")   # ← broadcasts to all registered pages
            self.data_cleared.emit()
            return

        # Data still present — surface the cached prediction result.
        existing = DataStore.get().predictions
        if existing and getattr(existing, "success", False):
            self.data_available.emit(existing)
        else:
            # Rows exist but nothing is cached (e.g. app restarted mid-session).
            # Wipe stale in-memory data and tell UI to re-run prediction.
            store = DataStore.get()
            store.predictions = None
            store._notify("predictions")   # ← clear stale data on all pages too
            self.data_cleared.emit()

    def _on_worker_error(self, message: str) -> None:
        self.busy_changed.emit(False)
        self.error.emit(message)