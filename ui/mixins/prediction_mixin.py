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
                unified_dataset = store.get_prediction_dataset(),
                progress_cb     = lambda s, p: self.progress.emit(s, p),
            )
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class PredictionMixin:
    """
    Mixin that provides confirmation dialog + loading overlay
    for any page that has a Run Prediction button.

    NOTE: Persistence (saving to fact_student_academic_risk) is handled
    exclusively by _FusedPredictionWorker in prediction_page.py, which has
    access to the user-selected academic term. This mixin only handles the
    Dashboard's "Run Prediction" shortcut button — it updates the UI but
    does NOT duplicate the persistence write.
    """

    def init_prediction(
        self,
        dialog_title:    str = "Run Prediction",
        dialog_message:  str = "Are you sure you want to run the prediction model?",
        dialog_detail:   str = "This will score all students in the current dataset.",
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
        from services.model_registry import ModelRegistry

        store = DataStore.get()

        # ── Load model from registry if not in memory ─────────────────
        if not store.trained_model:
            model_pkg = ModelRegistry.load_latest_model()
            if model_pkg:
                try:
                    store.trained_model = {
                        "model":         model_pkg["model"],
                        "model_id":      model_pkg["model_id"],
                        "feature_names": model_pkg["feature_names"],
                        "metadata":      model_pkg["metadata"],
                        "target_col":    "risk_label",
                    }
                    store.model_ready = True
                    print(f"[PredictionMixin] Loaded model from registry")
                except Exception as e:
                    print(f"[PredictionMixin] Failed to load model: {e}")

        # ── Build unified dataset if portals are loaded ────────────────
        if not store.unified_dataset and store.ready_count() > 0:
            try:
                unified = store.build_unified_dataset()
                if unified is not None:
                    print(f"[PredictionMixin] Built unified dataset: {len(unified)} rows")
            except Exception as e:
                print(f"[PredictionMixin] Failed to build unified dataset: {e}")

        # ── Guard: no model ───────────────────────────────────────────
        if not store.trained_model:
            self.overlay.hide()
            from ui.dialogs.confirmation_dialog import show_warning
            show_warning(
                self,
                "No Trained Model",
                "No trained model found.",
                "Go to Model Training and train a model first.",
            )
            return

        # ── Guard: no dataset ─────────────────────────────────────────
        if not store.unified_dataset:
            self.overlay.hide()
            from ui.dialogs.confirmation_dialog import show_warning
            show_warning(
                self,
                "No Dataset",
                "No student dataset is loaded.",
                "Upload portal files and run a prediction from the Prediction page.",
            )
            return

        # ── Run prediction ────────────────────────────────────────────
        self.overlay.set_message("Running Prediction…", "Scoring students")
        self._pred_worker = _PredictionWorker()
        self._pred_worker.progress.connect(
            lambda s, p: self.overlay.set_message("Running Prediction…", s)
        )
        self._pred_worker.finished.connect(self._on_prediction_complete)
        self._pred_worker.error.connect(self._on_prediction_error)
        self._pred_worker.finished.connect(
            self._pred_worker.deleteLater,
            __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.ConnectionType.QueuedConnection,
        )
        self._pred_worker.start()

    def _on_prediction_complete(self, result):
        """
        Called when _PredictionWorker finishes.

        Persistence is intentionally NOT done here — the canonical write path
        is _FusedPredictionWorker in prediction_page.py, which has the
        user-selected academic term.  This handler only updates the in-memory
        store and refreshes the UI.
        """
        from services.data_store import DataStore
        self.overlay.hide()

        if not result or not result.success:
            return

        store = DataStore.get()
        store.predictions = result
        store.set_last_prediction_run()

        try:
            s = result.summary
            store.add_activity(
                f"Prediction run — {s.total:,} students scored, "
                f"{s.high_risk:,} high-risk  ·  {s.moderate_risk:,} moderate-risk",
                icon="⚡",
                color="#34d399",
            )
        except Exception:
            store.add_activity("Prediction completed", icon="⚡", color="#34d399")

        store._notify("predictions")
        self._apply_predictions(result)

    def _on_prediction_complete_mock(self):
        self.overlay.hide()

    def _on_prediction_error(self, error_msg: str):
        self.overlay.hide()
        print(f"[PredictionMixin] Prediction error: {error_msg}")
        try:
            from ui.dialogs.confirmation_dialog import show_error
            show_error(self, "Prediction Failed",
                       "The prediction could not complete.", error_msg)
        except Exception:
            pass

    def _apply_predictions(self, result):
        """Override in each page to update UI with prediction results."""
        pass