"""
workers/geocoding_worker.py
────────────────────────────
QThread worker that geocodes a list of addresses using GeocodingService.

All six fixes are applied:
  #1  Rate limit enforced inside GeocodingService (1.1 s after every call).
  #2  Exponential backoff inside GeocodingService (2 → 5 → 10 s retries).
  #3  Unique user_agent set at geolocator creation in geocoding_service.py.
  #4  Single geolocator instance in geocoding_service.py (module level).
  #5  Guard in run() — if self.isRunning(): return.
  #6  geocode_batch() skips already-successful cache entries automatically.

Signals
───────
progress(current: int, total: int)   emitted after each address attempt
result(results: list[dict])          emitted when all addresses are done
error(message: str)                  emitted on unrecoverable failure

Usage
─────
    from workers.geocoding_worker import GeocodingWorker

    self._worker = GeocodingWorker(addresses)
    self._worker.progress.connect(self._on_progress)
    self._worker.result.connect(self._on_result)
    self._worker.error.connect(self._on_error)
    self._worker.start()
"""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from services.geocoding_service import GeocodingService


class GeocodingWorker(QThread):
    progress = pyqtSignal(int, int)   # (current, total)
    result   = pyqtSignal(list)       # list[dict]
    error    = pyqtSignal(str)

    def __init__(self, addresses: list[str], parent=None) -> None:
        super().__init__(parent)
        self._addresses = list(addresses)   # defensive copy

    def run(self) -> None:
        # Fix #5 — prevent double-start
        if self.isRunning() and self.currentThread() != self.thread():
            return

        if not self._addresses:
            self.result.emit([])
            return

        try:
            # Fix #6 — geocode_batch skips successful cache hits automatically
            results = GeocodingService.geocode_batch(
                self._addresses,
                progress_cb=lambda cur, tot: self.progress.emit(cur, tot),
            )
            self.result.emit(results)

        except Exception as exc:
            self.error.emit(str(exc))
