"""
services/dashboard_term_service.py

Provides the admin dashboard with term-based prediction loading from the DB.
Mirrors the counselor window's _TermListLoader / _CounselorTermLoader pattern
but lives as a reusable service so DashboardPage stays UI-only.

Usage (from DashboardPage):
    from services.dashboard_term_service import DashboardTermService

    self._term_svc = DashboardTermService(self)
    self._term_svc.terms_loaded.connect(self._on_terms_loaded)
    self._term_svc.terms_error.connect(self._on_terms_error)
    self._term_svc.result_ready.connect(self._on_term_result_ready)
    self._term_svc.result_error.connect(self._on_term_result_error)
    self._term_svc.busy_changed.connect(self._on_term_busy_changed)
    self._term_svc.load_term_list()
"""

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from services.data_store import DataStore


# ---------------------------------------------------------------------------
# Worker: fetch distinct terms that have saved prediction data
# ---------------------------------------------------------------------------

class _TermListWorker(QThread):
    """
    Queries distinct (academic_year, semester) pairs that have rows in
    fact_student_academic_risk, ordered most-recent first.
    """
    finished = pyqtSignal(list)   # list of (ay_str, sem_int)
    error    = pyqtSignal(str)

    def run(self) -> None:
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No database connection.")
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT t.academic_year, t.semester
                    FROM   public.fact_student_academic_risk fsr
                    JOIN   public.dim_academic_term t
                           ON t.term_key = fsr.term_key
                    ORDER  BY t.academic_year DESC, t.semester DESC
                """)
                self.finished.emit(cur.fetchall())
        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Worker: load all students for a selected term and build PredictionResult
# ---------------------------------------------------------------------------

class _TermDataWorker(QThread):
    """
    Loads every student for (academic_year, semester) from the DB and
    assembles a PredictionResult-compatible object identical to what
    the ML pipeline produces, so all existing page listeners just work.
    """
    finished = pyqtSignal(object)   # PredictionResult-like
    error    = pyqtSignal(str)

    _SQL = """
        SELECT
            ds.student_id,
            TRIM(COALESCE(ds.first_name,'') || ' ' ||
                 COALESCE(ds.last_name,''))              AS full_name,
            COALESCE(dp.program_name, 'Unknown')         AS program,
            COALESCE(dp.college,      '—')               AS college,
            COALESCE(rl.risk_label,   'Low Risk')        AS risk_label,
            fsr.predicted_risk_score,
            fsr.predicted_at,
            fsr.primary_factor,
            -- fact columns (pre-enrollment academic)
            fsr.year_level,
            fsr.entrance_exam_score,
            fsr.high_school_gpa,
            -- dim_student demographic / background
            ds.sex_code,
            ds.civil_status,
            ds.home_municipality,
            ds.family_income_bracket,
            ds.parent_highest_education,
            ds.hs_school_name,
            ds.hs_type,
            ds.shs_strand,
            ds.graduation_honors,
            ds.scholarship_type,
            ds.religion
        FROM  public.fact_student_academic_risk fsr
        JOIN  public.dim_academic_term  t
              ON t.term_key       = fsr.term_key
        JOIN  public.dim_student        ds
              ON ds.student_key   = fsr.student_key
        LEFT JOIN public.dim_program    dp
              ON dp.program_key   = fsr.program_key
        LEFT JOIN public.dim_risk_level rl
              ON rl.risk_level_id = fsr.risk_level_id
        WHERE t.academic_year = %s
          AND t.semester      = %s
        ORDER BY fsr.predicted_risk_score DESC NULLS LAST
    """

    _FEATURE_HUMAN_LABELS = {
        "Entrance_Exam_Score":      "Entrance Exam Score",
        "entrance_exam_score":      "Entrance Exam Score",
        "HS_GPA":                   "High School GPA",
        "high_school_gpa":          "High School GPA",
        "Financial_Stress_Index":   "Financial Stress Index",
        "First_Gen_Student":        "First-Generation Student",
        "Gap_Years":                "Gap Years Before College",
        "Distance_Bucket":          "Distance from Campus",
        "Strand_Program_Alignment": "SHS Strand–Program Alignment",
        "Has_Scholarship":          "Has Scholarship",
        "Graduation_Honors":        "Graduated with HS Honors",
        "HS_Type_Private":          "Attended Private High School",
        "Age_at_Enrollment":        "Age at Enrollment",
    }

    def __init__(self, academic_year: str, semester: int) -> None:
        super().__init__()
        self._ay  = academic_year
        self._sem = semester

    def run(self) -> None:
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No database connection.")
            return
        try:
            with conn.cursor() as cur:
                cur.execute(self._SQL, (self._ay, self._sem))
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            result = self._build_result(rows)
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _category(label: str) -> str:
        """Fallback label-based classification (not used for DB loads)."""
        lc = label.lower()
        if "high"                       in lc: return "high_risk"
        if "moderate" in lc or "medium" in lc: return "moderate_risk"
        return "low_risk"

    @staticmethod
    def _category_from_score(score_pct: float) -> str:
        if score_pct >= 50: return "high_risk"
        if score_pct >= 25: return "moderate_risk"
        return "low_risk"


    @staticmethod
    def _get_model_importances() -> dict:
        """Load saved model feature_importances_ as {feat: pct}."""
        try:
            from services.model_registry import ModelRegistry
            pkg = ModelRegistry.load_latest_model()
            if not pkg:
                return {}
            model    = pkg.get("model")
            features = pkg.get("feature_names", [])
            if model is None or not hasattr(model, "feature_importances_"):
                return {}
            total = sum(model.feature_importances_) or 1.0
            return {
                feat: round(float(imp / total) * 100, 1)
                for feat, imp in zip(features, model.feature_importances_)
            }
        except Exception as exc:
            print(f"[DashboardTermService] importances unavailable: {exc}")
            return {}

    def _build_shap_factors(self, importances: dict) -> list:
        """Return top-8 factors as (feat, human_label, value_str, pct)."""
        if not importances:
            return []
        out = []
        for feat, pct in sorted(importances.items(),
                                 key=lambda x: x[1], reverse=True)[:8]:
            human = self._FEATURE_HUMAN_LABELS.get(
                feat, feat.replace("_", " ").title())
            out.append((feat, human, f"{pct:.1f}%", pct))
        return out

    def _build_result(self, rows: list):
        importances  = self._get_model_importances()
        shap_factors = self._build_shap_factors(importances)
        top_factor   = shap_factors[0][1] if shap_factors else "—"

        predictions = []
        for r in rows:
            score_raw  = r.get("predicted_risk_score")
            score_dec  = float(score_raw) if score_raw is not None else 0.0
            score      = round(score_dec * 100, 1)
            # Derive category from the raw decimal score — authoritative,
            # avoids stale/mismatched risk_label values in dim_risk_level.
            cat        = self._category_from_score(score)

            db_factor      = r.get("primary_factor")
            student_factor = db_factor if db_factor else top_factor

            predictions.append({
                "name":         r.get("full_name", "—"),
                "id":           str(r.get("student_id", "—")),
                "student_id":   str(r.get("student_id", "—")),
                "program":      r.get("program", "—"),
                "college":      r.get("college", "—"),
                "score":        score,
                "category":     cat,
                "label":        r.get("risk_label", "—"),
                "factor":       student_factor,
                "shap_factors": shap_factors,
                "gwa":          None,
                "absences":     None,
                # ── academic background ─────────────────────────────
                "year_level":           r.get("year_level"),
                "entrance_exam_score":  r.get("entrance_exam_score"),
                "high_school_gpa":      r.get("high_school_gpa"),
                "shs_strand":           r.get("shs_strand"),
                "hs_type":              r.get("hs_type"),
                "graduation_honors":    r.get("graduation_honors"),
                # ── personal background ─────────────────────────────
                "sex_code":                  r.get("sex_code"),
                "civil_status":              r.get("civil_status"),
                "home_municipality":         r.get("home_municipality"),
                "family_income_bracket":     r.get("family_income_bracket"),
                "parent_highest_education":  r.get("parent_highest_education"),
                "hs_school_name":            r.get("hs_school_name"),
                "scholarship_type":          r.get("scholarship_type"),
                "religion":                  r.get("religion"),
            })

        total    = len(predictions)
        high     = sum(1 for p in predictions if p["category"] == "high_risk")
        moderate = sum(1 for p in predictions if p["category"] == "moderate_risk")
        low      = total - high - moderate
        scores   = [p["score"] for p in predictions]
        avg      = round(sum(scores) / len(scores), 1) if scores else 0.0
        high_pct = round(high / total * 100, 1) if total else 0.0

        by_college: dict = {}
        for p in predictions:
            col = p["college"]
            if col not in by_college:
                by_college[col] = {"total": 0, "high": 0}
            by_college[col]["total"] += 1
            if p["category"] == "high_risk":
                by_college[col]["high"] += 1

        class _Summary:
            pass
        s               = _Summary()
        s.total         = total
        s.high_risk     = high
        s.moderate_risk = moderate
        s.low_risk      = low
        s.avg_score     = avg
        s.high_risk_pct = high_pct
        s.by_college    = by_college

        class _Result:
            pass
        result            = _Result()
        result.success    = True
        result.predictions = predictions
        result.summary    = s
        result._source    = "admin"   # admin dashboard — not counselor
        return result


# ---------------------------------------------------------------------------
# Public service
# ---------------------------------------------------------------------------

class DashboardTermService(QObject):
    """
    Manages term-list fetching and per-term prediction loading for the
    admin DashboardPage.

    Signals
    -------
    terms_loaded(terms: list)
        List of (academic_year: str, semester: int) tuples, most-recent first.
        UI should populate the AY / semester combos from this.

    terms_error(message: str)
        Term-list query failed.

    result_ready(result: object)
        PredictionResult-like object for the selected term.
        Connect directly to DashboardPage._apply_predictions.

    result_error(message: str)
        Term data query failed.

    busy_changed(is_busy: bool)
        True while either worker is running. Use to disable the Load button.
    """

    terms_loaded  = pyqtSignal(list)
    terms_error   = pyqtSignal(str)
    result_ready  = pyqtSignal(object)
    result_error  = pyqtSignal(str)
    busy_changed  = pyqtSignal(bool)

    def __init__(self, parent: QObject = None) -> None:
        super().__init__(parent)
        self._list_worker: _TermListWorker | None = None
        self._data_worker: _TermDataWorker | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_term_list(self) -> None:
        """
        Fetch available terms from the DB.
        Does nothing if already running.
        """
        if self._list_worker is not None:
            return

        self.busy_changed.emit(True)
        self._list_worker = _TermListWorker()
        self._list_worker.finished.connect(self._on_terms_received)
        self._list_worker.error.connect(self._on_terms_error)
        self._list_worker.finished.connect(self._clear_list_worker)
        self._list_worker.error.connect(self._clear_list_worker)
        self._list_worker.start()

    def load_term_data(self, academic_year: str, semester: int) -> None:
        """
        Load all prediction rows for the given (academic_year, semester).
        Cancels any in-progress data load before starting a new one.
        Does nothing if academic_year is blank or a placeholder value.
        """
        if not academic_year or academic_year in ("No data", "Loading…", "Error"):
            return
        if self._data_worker is not None:
            return   # already loading — let it finish

        self.busy_changed.emit(True)
        self._data_worker = _TermDataWorker(academic_year, semester)
        self._data_worker.finished.connect(self._on_data_received)
        self._data_worker.error.connect(self._on_data_error)
        self._data_worker.finished.connect(self._clear_data_worker)
        self._data_worker.error.connect(self._clear_data_worker)
        self._data_worker.start()

    def cleanup(self) -> None:
        """Stop all running workers. Call from owning widget's closeEvent."""
        self._stop_worker("_list_worker")
        self._stop_worker("_data_worker")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _stop_worker(self, attr: str) -> None:
        worker = getattr(self, attr, None)
        if worker is None:
            return
        try:
            worker.finished.disconnect()
            worker.error.disconnect()
        except Exception:
            pass
        try:
            if worker.isRunning():
                worker.quit()
                worker.wait(2000)
        except RuntimeError:
            pass
        setattr(self, attr, None)

    def _clear_list_worker(self) -> None:
        worker = self._list_worker
        self._list_worker = None
        if worker is not None:
            worker.deleteLater()

    def _clear_data_worker(self) -> None:
        worker = self._data_worker
        self._data_worker = None
        if worker is not None:
            worker.deleteLater()
        self.busy_changed.emit(False)

    # ------------------------------------------------------------------
    # Private slots
    # ------------------------------------------------------------------

    def _on_terms_received(self, terms: list) -> None:
        # List worker done — data worker may still start; don't emit False yet
        self.terms_loaded.emit(terms)

    def _on_terms_error(self, message: str) -> None:
        self.busy_changed.emit(False)
        self.terms_error.emit(message)

    def _on_data_received(self, result) -> None:
        # Push into DataStore so other pages (Risk Alerts, Student Cohort) update
        store = DataStore.get()
        store.predictions = result
        store._notify("predictions")
        self.result_ready.emit(result)

    def _on_data_error(self, message: str) -> None:
        self.result_error.emit(message)