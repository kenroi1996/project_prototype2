from PyQt6.QtCore import QTimer, QThread, pyqtSignal

from ui.dialogs.confirmation_dialog import ConfirmationDialog
from ui.components.loading_overlay import LoadingOverlay


class _PredictionWorker(QThread):
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(object)   # PredictionResult
    error    = pyqtSignal(str)
 
    def run(self):
        try:
            from services.prediction_engine import PredictionEngine
            from services.data_store import DataStore
            store  = DataStore.get()
            result = PredictionEngine.run(
                model_data      = store.trained_model,
                unified_dataset = store.unified_dataset,
                progress_cb     = lambda s, p: self.progress.emit(s, p),
            )
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class PredictionMixin:
    """
    Mixin that provides confirmation dialog + loading overlay
    for any page that has a Run Prediction button.
    """

    def init_prediction(
        self,
        dialog_title:    str = "Run Prediction",
        dialog_message:  str = "Are you sure you want to run the prediction model?",
        dialog_detail:   str = "This will overwrite all existing prediction results.",
        overlay_message: str = "Running Prediction...",
        overlay_sub:     str = "Analyzing student records",
    ):
        self._dialog_title    = dialog_title
        self._dialog_message  = dialog_message
        self._dialog_detail   = dialog_detail
        self._overlay_message = overlay_message
        self._overlay_sub     = overlay_sub

        self.overlay = LoadingOverlay(self)

    def on_run_prediction(self):
        """Show confirmation dialog; proceed only if user confirms."""
        dialog = ConfirmationDialog(
            self._dialog_title,
            self._dialog_message,
            detail=self._dialog_detail,
            parent=self,
        )

        if not dialog.exec():
            return

        self.overlay.set_message(self._overlay_message, self._overlay_sub)
        self.overlay.show()
        self._prediction_confirmed()

    def _prediction_confirmed(self):
        """Runs the real prediction engine."""
        from services.data_store import DataStore
        from services.ml_service import MLService
        from pathlib import Path

        store = DataStore.get()

        # ── Try to load model from disk if not in memory ──
        if not store.trained_model:
            model_path = Path("outputs/trained_model.pkl")
            if model_path.exists():
                try:
                    ml_service = MLService()
                    ml_service.load_model(str(model_path))
                    store.set_trained_model(ml_service)
                    print("[PredictionMixin] Loaded model from outputs/trained_model.pkl")
                except Exception as e:
                    print(f"[PredictionMixin] Failed to load model: {e}")

        # ── Try to build unified dataset if not in memory ──
        if not store.unified_dataset and store.ready_count() > 0:
            try:
                unified = store.build_unified_dataset()
                if unified is not None:
                    print(f"[PredictionMixin] Built unified dataset: {len(unified)} rows")
            except Exception as e:
                print(f"[PredictionMixin] Failed to build unified dataset: {e}")

        # ── Check if we can run real prediction ──
        if not store.trained_model:
            self.overlay.set_message("Running Prediction…", "No model trained yet — using demo mode")
            QTimer.singleShot(3000, self._on_prediction_complete_mock)
            return

        if not store.unified_dataset:
            self.overlay.set_message("Running Prediction…", "No unified dataset — using demo mode")
            QTimer.singleShot(3000, self._on_prediction_complete_mock)
            return

        # ── Run real prediction ──
        self.overlay.set_message("Running Prediction…", "Scoring students")

        self._pred_worker = _PredictionWorker()
        self._pred_worker.progress.connect(
            lambda s, p: self.overlay.set_message("Running Prediction…", s)
        )
        self._pred_worker.finished.connect(self._on_prediction_complete)
        self._pred_worker.error.connect(self._on_prediction_error)
        self._pred_worker.start()

    def _on_prediction_complete(self, result):
        from services.data_store import DataStore
        self.overlay.hide()

        if result.success:
            DataStore.get().predictions = result
            DataStore.get()._notify("predictions")
            self._apply_predictions(result)

    def _on_prediction_complete_mock(self):
        self.overlay.hide()

    def _on_prediction_error(self, error_msg: str):
        self.overlay.hide()
        print(f"[PredictionMixin] Prediction error: {error_msg}")

    def _apply_predictions(self, result):
        """Override in each page to update UI with prediction results."""
        pass