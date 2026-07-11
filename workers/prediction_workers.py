"""
Background worker for the Prediction Center's fused "Run Pipeline &
Predict" action — feature engineering + model scoring + DB persistence,
all off the UI thread.

Extracted from ui/pages/prediction_page.py — no logic changes, only
relocation and import wiring.
"""

from PyQt6.QtCore import QThread, pyqtSignal

from services.excel_service import dataframe_to_rows, rows_to_dataframe


class _FusedPredictionWorker(QThread):
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(object)
    error    = pyqtSignal(str)

    def __init__(self, headers: list, rows: list, name: str, school_year: str,
                 academic_year: str = "2024-2025", semester: int = 1):
        super().__init__(parent=None)
        self._headers       = headers
        self._rows          = rows
        self._name          = name
        self._school_year   = school_year
        self._academic_year = academic_year
        self._semester      = semester

    def run(self):
        try:
            from services.feature_engineering import run_prediction_pipeline
            from services.prediction_engine import PredictionEngine
            from services.data_store import DataStore

            self.progress.emit("Snapshotting student demographics…", 5)
            df = rows_to_dataframe(self._headers, self._rows)

            from services.feature_engineering import normalize_columns
            df_norm = normalize_columns(df.copy())

            _SNAPSHOT_COLS = [
                "first_name", "First_Name", "firstname", "FIRSTNAME",
                "FIRST_NAME", "FIRST NAME", "fname", "FNAME", "F_NAME",
                "given_name", "Given_Name", "GIVEN_NAME",
                "last_name",  "Last_Name",  "lastname",  "LASTNAME",
                "LAST_NAME",  "LAST NAME",  "lname", "LNAME", "L_NAME",
                "surname", "Surname", "SURNAME", "family_name",
                "middle_name", "Middle_Name", "MIDDLE_NAME", "mname",
                "full_name", "Full_Name", "FULL_NAME", "name", "NAME",
                "student_name", "Student_Name", "STUDENT_NAME",
                "College", "Final_Avg_GRD", "SecCode", "Year",
                "Home_Address", "Municipality", "Civil_Status",
                "Birthdate", "Year_Enrolled", "Family_Income",
                "Parent_Highest_Education", "HS_GPA", "Year_Graduated",
                "SHS_Strand", "HS_Type", "Graduation_Honors", "HS_School",
                "Scholarship_Applicant", "Scholarship_Type", "Religion",
                "Program", "Sex_code",
            ]

            meta_snapshot: dict = {}
            if "Student_ID" in df_norm.columns:
                available = [c for c in _SNAPSHOT_COLS if c in df_norm.columns]
                for _, row in df_norm.iterrows():
                    sid = str(row["Student_ID"]).strip()
                    if not sid or sid in ("", "nan", "None"):
                        continue
                    meta_snapshot[sid] = {
                        col: (str(row[col]).strip()
                              if row[col] is not None
                              and str(row[col]) not in ("nan", "None", "")
                              else "")
                        for col in available
                    }

            DataStore.get().set_raw_meta_snapshot(meta_snapshot)
            print(f"[FusedWorker] Meta snapshot: {len(meta_snapshot)} students, "
                  f"{len(next(iter(meta_snapshot.values()), {}))} fields each")

            self.progress.emit("Engineering features…", 10)
            engineered = run_prediction_pipeline(df)

            headers, rows = dataframe_to_rows(engineered)
            if not headers or not rows:
                self.error.emit("Feature pipeline produced an empty dataset.")
                return

            self.progress.emit("Pipeline complete — committing to DataStore…", 45)
            store = DataStore.get()
            store.set_prediction_dataset(
                {"headers": headers, "rows": rows},
                name=self._name,
                school_year=self._school_year,
            )

            def _cb(step: str, pct: int):
                self.progress.emit(step, 45 + int(pct * 0.55))

            result = PredictionEngine.run(
                model_data      = store.trained_model,
                unified_dataset = store.get_prediction_dataset(),
                progress_cb     = _cb,
            )

            if result and result.success:
                try:
                    from services.activity_logger import ActivityLogger
                    _conn = store.db_conn
                    if _conn:
                        s = result.summary
                        ActivityLogger.log_predict(
                            _conn,
                            dataset_name  = self._name,
                            school_year   = self._school_year,
                            total         = s.total,
                            high_risk     = s.high_risk,
                            moderate_risk = s.moderate_risk,
                        )
                        _conn.commit()
                except Exception as _e:
                    print(f"[FusedWorker] Predict log error: {_e}")

                try:
                    from services.risk_persistence_service import RiskPersistenceService
                    RiskPersistenceService.save_predictions(
                        predictions   = result.predictions,
                        model_id      = "rf",
                        academic_year = self._academic_year,
                        semester      = str(self._semester),
                    )
                except Exception as _e:
                    print(f"[FusedWorker] Risk persistence error: {_e}")

            self.finished.emit(result)

        except Exception as e:
            self.error.emit(str(e))