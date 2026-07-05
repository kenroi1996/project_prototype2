"""
EarlyAlert — Counselor Window
==============================
Same sidebar layout as DashboardWindow but restricted to:
  Dashboard, Risk Alerts, Student Cohort, Prediction History,
  Interventions, Settings

Shared term selector header bar (always visible):
  AY [combo]  Semester [combo]  [Load Term Data]  ← status label
  On Load → _CounselorTermLoader queries DB → builds PredictionResult
  → pushes to DataStore.predictions → all pages update automatically.
"""
import sys

from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QFrame, QSizePolicy,
    QGraphicsDropShadowEffect, QScrollArea, QStackedWidget,
    QComboBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPixmap, QIcon

from services.auth_service  import AuthService
from services.data_store    import DataStore
from services.system_config import SystemConfig


# ── Page index ────────────────────────────────────────────────────────────────
_PAGE_IDX = {
    "Dashboard":          0,
    "Data Analytics":     1,   # ← ADD
    "Risk Alerts":        2,   # ← was 1
    "Student Cohort":     3,   # ← was 2
    "Prediction History": 4,   # ← was 3
    "Interventions":      5,   # ← was 4
    "Settings":           6,   # ← was 5
}


# ── DB term loader ────────────────────────────────────────────────────────────

class _TermListLoader(QThread):
    """Fetch distinct (academic_year, semester) pairs that have saved data."""
    finished = pyqtSignal(list)   # list of (ay_str, sem_int)
    error    = pyqtSignal(str)

    def run(self):
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
        except Exception as e:
            self.error.emit(str(e))


class _CounselorTermLoader(QThread):
    """
    Loads ALL students for the selected term from the DB and
    assembles a PredictionResult-compatible object so the existing
    DashboardPage / RiskAlertsPage / StudentCohortPage listeners
    update automatically without any page-level changes.
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
            fsr.entrance_exam_score,
            fsr.high_school_gpa,
            fsr.predicted_at,
            fsr.primary_factor
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

    def __init__(self, academic_year: str, semester: int):
        super().__init__()
        self._ay  = academic_year
        self._sem = semester

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No database connection.")
            return
        try:
            with conn.cursor() as cur:
                cur.execute(self._SQL, (self._ay, self._sem))
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]

            result = self._build_prediction_result(rows)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

    # ── Build PredictionResult from DB rows ───────────────────────────

    @staticmethod
    def _category(score: float) -> str:
        """Derive category from probability score — ignores DB risk_label."""
        pct = float(score or 0) * 100
        if pct >= 50:   return "high_risk"
        if pct >= 25:   return "moderate_risk"
        return "low_risk"

    # ── Feature label map ─────────────────────────────────────────────
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
        except Exception as e:
            print(f"[CounselorTermLoader] importances unavailable: {e}")
            return {}

    def _build_shap_factors(self, importances: dict) -> list:
        """Return top-8 factors as (feat, human_label, value_str, pct)."""
        if not importances:
            return []
        rows = []
        for feat, pct in sorted(importances.items(),
                                 key=lambda x: x[1], reverse=True)[:8]:
            human = self._FEATURE_HUMAN_LABELS.get(
                feat, feat.replace("_", " ").title())
            rows.append((feat, human, f"{pct:.1f}%", pct))
        return rows

    def _build_prediction_result(self, rows: list):
        """
        Construct an object that matches what DashboardPage._apply_predictions
        and RiskAlertsPage._apply_predictions expect:
          result.success          bool
          result.predictions      list[dict]  (keys below)
          result.summary          namespace
            .total, .high_risk, .moderate_risk, .low_risk
            .avg_score, .high_risk_pct, .by_college
        """
        importances  = self._get_model_importances()
        shap_factors = self._build_shap_factors(importances)
        top_factor   = shap_factors[0][1] if shap_factors else "—"

        predictions = []
        for r in rows:
            score_raw = r.get("predicted_risk_score")
            score = round(float(score_raw) * 100, 1) if score_raw else 0.0
            cat   = self._category(score_raw) 

            # Use per-student primary_factor stored in DB when available.
            # This is the real top SHAP factor saved at prediction time.
            # Falls back to model-level importances if column is NULL.
            # Use per-student DB primary_factor as the label (shown on
            # alert cards). Always use model importances for the SHAP bars —
            # never a single-entry 100% list which breaks the chart.
            db_factor      = r.get("primary_factor")
            student_factor = db_factor if db_factor else top_factor
            student_shap   = shap_factors   # model-level importances for all

            predictions.append({
                "name":         r.get("full_name", "—"),
                "student_id":   str(r.get("student_id", "—")),
                "program":      r.get("program", "—"),
                "college":      r.get("college", "—"),
                "score":        score,
                "category":     cat,
                "label":        r.get("risk_label", "—"),
                "factor":       student_factor,
                "shap_factors": student_shap,
                # extra fields StudentCohortPage uses
                "gwa":      None,
                "absences": None,
            })

        total    = len(predictions)
        high     = sum(1 for p in predictions if p["category"] == "high_risk")
        moderate = sum(1 for p in predictions if p["category"] == "moderate_risk")
        low      = total - high - moderate
        scores   = [p["score"] for p in predictions]
        avg      = round(sum(scores) / len(scores), 1) if scores else 0.0
        high_pct = round(high / total * 100, 1) if total else 0.0

        # by_college: {"CollegeName": {"total": N, "high": N}}
        by_college: dict = {}
        for p in predictions:
            col = p["college"]
            if col not in by_college:
                by_college[col] = {"total": 0, "high": 0}
            by_college[col]["total"] += 1
            if p["category"] == "high_risk":
                by_college[col]["high"] += 1

        # Simple namespace for summary
        class _Summary:
            pass

        s = _Summary()
        s.total         = total
        s.high_risk     = high
        s.moderate_risk = moderate
        s.low_risk      = low
        s.avg_score     = avg
        s.high_risk_pct = high_pct
        s.by_college    = by_college

        class _Result:
            pass

        result = _Result()
        result.success     = True
        result.predictions = predictions
        result.summary     = s
        return result


# ── Window background ─────────────────────────────────────────────────────────

class _Bg(QWidget):
    def paintEvent(self, e):
        QPainter(self).fillRect(self.rect(), QColor("#13172a"))


# ── Counselor Window ──────────────────────────────────────────────────────────

class CounselorWindow(_Bg):

    def __init__(self, db_conn=None):
        super().__init__()
        self._db_conn         = db_conn
        self.nav_buttons      = {}
        self._term_list_loader: _TermListLoader      | None = None
        self._term_loader:      _CounselorTermLoader | None = None
        self.setWindowTitle("EarlyAlert — Counselor Portal")
        self.resize(1500, 900)
        self.setup_ui()

    # ── Helpers shared with DashboardWindow pattern ───────────────────

    def create_nav_button(self, text, icon_path=None):
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setFixedHeight(35)
        if icon_path:
            btn.setIcon(QIcon(icon_path))
        return btn

    def on_nav_button_clicked(self, key, page_index):
        for btn in self.nav_buttons.values():
            btn.setChecked(False)
        self.nav_buttons[key].setChecked(True)
        self.stacked_widget.setCurrentIndex(page_index)

    def create_scrollable_page(self, page_widget):
        container = QWidget()
        container.setObjectName("pageShell")
        container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lo = QVBoxLayout(container)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(0)

        if hasattr(page_widget, "fixed_header_container") and \
           hasattr(page_widget, "main_layout"):
            for i in range(page_widget.main_layout.count()):
                item = page_widget.main_layout.itemAt(i)
                if item and item.widget() is page_widget.fixed_header_container:
                    page_widget.main_layout.removeWidget(
                        page_widget.fixed_header_container)
                    break
            lo.addWidget(page_widget.fixed_header_container, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page_widget)
        lo.addWidget(scroll, 1)
        return container

    @staticmethod
    def _section_label(text):
        lbl = QLabel(text)
        lbl.setObjectName("sectionLabel")
        return lbl

    # ── Setup UI ──────────────────────────────────────────────────────

    def setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ══ Shared term selector bar (always visible) ══════════════════
        root.addWidget(self._build_term_bar())

        # ══ Main body: sidebar + content ══════════════════════════════
        body = QHBoxLayout()
        body.setContentsMargins(20, 0, 20, 20)
        body.setSpacing(20)

        body.addWidget(self._build_sidebar())

        # Content area
        self.content_area = QFrame()
        self.content_area.setObjectName("contentArea")
        self.content_area.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.content_area.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True)
        content_lo = QVBoxLayout(self.content_area)
        content_lo.setContentsMargins(0, 0, 0, 0)

        self.stacked_widget = QStackedWidget()
        self.stacked_widget.setObjectName("stackedWidget")
        self.stacked_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        content_lo.addWidget(self.stacked_widget)
        body.addWidget(self.content_area, 1)

        root.addLayout(body, 1)

        # ══ Instantiate pages ══════════════════════════════════════════
        from ui.pages.dashboard_page          import DashboardPage
        from ui.pages.risk_alerts_page        import RiskAlertsPage
        from ui.pages.student_cohort_page     import StudentCohortPage
        from ui.pages.prediction_history_page import PredictionHistoryPage
        from ui.pages.settings_page           import SettingsPage
        from ui.pages.analytics_page          import AnalyticsPage

        self.dashboard_page          = DashboardPage()
        self._analytics_page         = AnalyticsPage()
        self.risk_alerts_page        = RiskAlertsPage()
        self.student_cohort_page     = StudentCohortPage()
        self.prediction_history_page = PredictionHistoryPage()
        from ui.pages.interventions_page import InterventionsPage
        self.interventions_page      = InterventionsPage()
        self.settings_page           = SettingsPage()

        pages = [
            (self.dashboard_page,          _PAGE_IDX["Dashboard"]),
            (self._analytics_page,         _PAGE_IDX["Data Analytics"]),  # ← ADD
            (self.risk_alerts_page,        _PAGE_IDX["Risk Alerts"]),
            (self.student_cohort_page,     _PAGE_IDX["Student Cohort"]),
            (self.prediction_history_page, _PAGE_IDX["Prediction History"]),
            (self.interventions_page,      _PAGE_IDX["Interventions"]),
            (self.settings_page,           _PAGE_IDX["Settings"]),
        ]
        pages.sort(key=lambda x: x[1])
        for page_widget, _ in pages:
            self.stacked_widget.addWidget(
                self.create_scrollable_page(page_widget))

        if self._db_conn:
            DataStore.get().set_db_conn(self._db_conn)

        # Clear predictions on open so counselor pages start blank
        # and only load data when counselor explicitly clicks Load Term.
        # This also prevents admin prediction data bleeding into counselor view.
        DataStore.get().predictions = None

        # Default nav
        self.stacked_widget.setCurrentIndex(_PAGE_IDX["Dashboard"])
        self.nav_buttons["Dashboard"].setChecked(True)

        # Defer term list loading until after window is shown
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(200, self._load_term_list)

    # ── Term bar ──────────────────────────────────────────────────────

    def _build_term_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("counselorTermBar")
        bar.setFixedHeight(52)
        lo = QHBoxLayout(bar)
        lo.setContentsMargins(24, 0, 24, 0)
        lo.setSpacing(14)

        # System identity
        px = QPixmap("assets/main_logo.png")
        logo = QLabel()
        if not px.isNull():
            logo.setPixmap(px.scaled(
                26, 26,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        logo.setFixedWidth(30)

        sys_lbl = QLabel("EarlyAlert")
        sys_lbl.setObjectName("counselorSysName")

        role_pill = QLabel("Counselor")
        role_pill.setObjectName("counselorRolePill")

        lo.addWidget(logo)
        lo.addWidget(sys_lbl)
        lo.addWidget(role_pill)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: rgba(255,255,255,0.12);")
        sep.setFixedHeight(24)
        lo.addWidget(sep)

        # Term label
        term_lbl = QLabel("Academic Term:")
        term_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.40); font-size:12px; background:transparent;")

        # AY combo
        self._ay_combo = QComboBox()
        self._ay_combo.setObjectName("counselorTermCombo")
        self._ay_combo.setMinimumWidth(120)
        self._ay_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ay_combo.addItem("Loading…")
        self._ay_combo.setEnabled(False)

        # Semester combo
        self._sem_combo = QComboBox()
        self._sem_combo.setObjectName("counselorTermCombo")
        self._sem_combo.addItems(["1st Semester", "2nd Semester"])
        self._sem_combo.setCursor(Qt.CursorShape.PointingHandCursor)

        # Load button
        self._load_btn = QPushButton("⟳  Load Term Data")
        self._load_btn.setObjectName("counselorLoadBtn")
        self._load_btn.setFixedHeight(32)
        self._load_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._load_btn.setEnabled(False)
        self._load_btn.clicked.connect(self._on_load_term)

        # Status label
        self._term_status = QLabel("Connecting to database…")
        self._term_status.setObjectName("counselorTermStatus")

        lo.addWidget(term_lbl)
        lo.addWidget(self._ay_combo)
        lo.addWidget(self._sem_combo)
        lo.addWidget(self._load_btn)
        lo.addWidget(self._term_status)
        lo.addStretch()

        # Signed-in user + logout
        _user = AuthService.current_user() or {}
        user_lbl = QLabel(_user.get("full_name", "—"))
        user_lbl.setObjectName("counselorUserLbl")

        logout_btn = QPushButton("Sign Out")
        logout_btn.setObjectName("logoutBtn")
        logout_btn.setFixedHeight(28)
        logout_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        logout_btn.clicked.connect(self._on_logout)

        lo.addWidget(user_lbl)
        lo.addSpacing(8)
        lo.addWidget(logout_btn)
        return bar

    # ── Sidebar ───────────────────────────────────────────────────────

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setFixedWidth(280)
        sidebar.setObjectName("sidebar")
        sidebar.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(40)
        shadow.setXOffset(0)
        shadow.setYOffset(10)
        shadow.setColor(QColor(0, 0, 0, 120))
        sidebar.setGraphicsEffect(shadow)

        outer = QVBoxLayout(sidebar)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(10)

        # Title
        title = QLabel("Counselor Portal")
        title.setObjectName("systemTitle")
        title.setStyleSheet(
            "#systemTitle { font-size:16px; font-weight:bold; }")
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        outer.addWidget(title)
        outer.addWidget(line)

        # Scrollable nav
        scroll = QScrollArea()
        scroll.setObjectName("sidebarScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        nav_host = QWidget()
        nav_host.setObjectName("sidebarNavContent")
        nav_lo = QVBoxLayout(nav_host)
        nav_lo.setContentsMargins(0, 8, 4, 8)
        nav_lo.setSpacing(10)

        # OVERVIEW
        nav_lo.addWidget(self._section_label("OVERVIEW"))
        for text, icon, key in [
            ("Dashboard",      "assets/icons/dashboard.svg",       "Dashboard"),
            ("Data Analytics", "assets/icons/analytics.png",       "Data Analytics"),  # ← ADD
            ("Risk Alerts",    "assets/icons/risk-alerts.svg",     "Risk Alerts"),
            ("Student Cohort", "assets/icons/student-cohorts.svg", "Student Cohort"),
        ]:
            btn = self.create_nav_button(text, icon)
            self.nav_buttons[key] = btn
            btn.clicked.connect(
                lambda _, i=_PAGE_IDX[key], k=key:
                self.on_nav_button_clicked(k, i))
            nav_lo.addWidget(btn)

        nav_lo.addSpacing(8)

        # PREDICTION
        nav_lo.addWidget(self._section_label("PREDICTION"))
        btn = self.create_nav_button(
            "Prediction History", "assets/icons/play.svg")
        self.nav_buttons["Prediction History"] = btn
        btn.clicked.connect(
            lambda: self.on_nav_button_clicked(
                "Prediction History", _PAGE_IDX["Prediction History"]))
        nav_lo.addWidget(btn)

        nav_lo.addSpacing(8)

        # COUNSELING
        nav_lo.addWidget(self._section_label("COUNSELING"))
        btn = self.create_nav_button(
            "Interventions", "assets/icons/check.svg")
        self.nav_buttons["Interventions"] = btn
        btn.clicked.connect(
            lambda: self.on_nav_button_clicked(
                "Interventions", _PAGE_IDX["Interventions"]))
        nav_lo.addWidget(btn)

        nav_lo.addSpacing(8)

        # ACCOUNT
        nav_lo.addWidget(self._section_label("ACCOUNT"))
        btn = self.create_nav_button("Settings", "assets/icons/check.svg")
        self.nav_buttons["Settings"] = btn
        btn.clicked.connect(
            lambda: self.on_nav_button_clicked(
                "Settings", _PAGE_IDX["Settings"]))
        nav_lo.addWidget(btn)

        nav_lo.addStretch()
        scroll.setWidget(nav_host)
        outer.addWidget(scroll, 1)

        # Admin card at bottom
        _user = AuthService.current_user() or {}
        line2 = QFrame()
        line2.setFrameShape(QFrame.Shape.HLine)
        name_lbl = QLabel(_user.get("full_name", "—"))
        name_lbl.setObjectName("adminName")
        role_lbl = QLabel("Counselor")
        role_lbl.setObjectName("adminRole")
        outer.addWidget(line2)
        outer.addWidget(name_lbl)
        outer.addWidget(role_lbl)

        return sidebar

    # ── Placeholder ───────────────────────────────────────────────────

    def _placeholder(self, label: str) -> QWidget:
        w = QWidget()
        lo = QVBoxLayout(w)
        lo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QLabel("📝")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("font-size:52px;")
        lbl = QLabel(f"{label} — coming next")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(
            "color:rgba(255,255,255,0.35); font-size:14px; background:transparent;")
        lo.addWidget(icon)
        lo.addSpacing(12)
        lo.addWidget(lbl)
        return w

    # ── Term list loading ─────────────────────────────────────────────

    def _load_term_list(self):
        self._term_list_loader = _TermListLoader()
        self._term_list_loader.finished.connect(self._on_term_list_loaded)
        self._term_list_loader.error.connect(self._on_term_list_error)
        self._term_list_loader.finished.connect(
            self._term_list_loader.deleteLater)
        self._term_list_loader.error.connect(
            self._term_list_loader.deleteLater)
        self._term_list_loader.start()

    def _on_term_list_loaded(self, terms: list):
        self._ay_combo.clear()
        if not terms:
            self._ay_combo.addItem("No data")
            self._term_status.setText(
                "No saved predictions found. Ask admin to run a prediction first.")
            return

        seen = []
        for ay, _ in terms:
            if ay not in seen:
                seen.append(ay)
        self._ay_combo.addItems(seen)

        # Pre-select most recent term
        ay, sem = terms[0]
        self._ay_combo.setCurrentText(ay)
        self._sem_combo.setCurrentIndex(sem - 1)

        self._ay_combo.setEnabled(True)
        self._load_btn.setEnabled(True)
        self._term_status.setText(
            f"{len(terms)} term(s) available — select one and click Load.")

    def _on_term_list_error(self, msg: str):
        self._term_status.setText(f"⚠ {msg}")
        self._ay_combo.clear()
        self._ay_combo.addItem("Error")

    # ── Term data loading ─────────────────────────────────────────────

    def _on_load_term(self):
        ay  = self._ay_combo.currentText().strip()
        sem = self._sem_combo.currentIndex() + 1
        if not ay or ay in ("No data", "Loading…", "Error"):
            return

        self._load_btn.setEnabled(False)
        self._load_btn.setText("Loading…")
        self._term_status.setText(
            f"Loading {ay} — {'1st' if sem == 1 else '2nd'} Semester…")

        self._term_loader = _CounselorTermLoader(ay, sem)
        self._term_loader.finished.connect(self._on_term_loaded)
        self._term_loader.error.connect(self._on_term_error)
        self._term_loader.finished.connect(self._term_loader.deleteLater)
        self._term_loader.error.connect(self._term_loader.deleteLater)
        self._term_loader.start()

    def _on_term_loaded(self, result):
        self._load_btn.setEnabled(True)
        self._load_btn.setText("⟳  Load Term Data")

        total = result.summary.total
        high  = result.summary.high_risk
        ay    = self._ay_combo.currentText()
        sem   = "1st" if self._sem_combo.currentIndex() == 0 else "2nd"

        self._term_status.setText(
            f"✓  {ay} {sem} Sem  ·  {total:,} students  ·  "
            f"{high:,} high-risk"
        )

        # Tag and store — source tag prevents admin pages from consuming this
        result._source = "counselor"
        store = DataStore.get()
        store.predictions = result
        store._notify("predictions")

    def _on_term_error(self, msg: str):
        self._load_btn.setEnabled(True)
        self._load_btn.setText("⟳  Load Term Data")
        self._term_status.setText(f"⚠ Load failed: {msg}")

    # ── Styles ────────────────────────────────────────────────────────

    def _apply_term_bar_styles(self):
        self.setStyleSheet(self.styleSheet() + """
            #counselorTermBar {
                background-color: #13172a;
                border-bottom: 1px solid rgba(255,255,255,0.08);
            }
            #counselorSysName {
                color: #e8eaf0; font-size:14px; font-weight:bold;
                background:transparent;
            }
            #counselorRolePill {
                color: #34d399;
                background: rgba(52,211,153,0.10);
                border: 1px solid rgba(52,211,153,0.25);
                border-radius: 7px;
                font-size: 10px; font-weight:700;
                padding: 2px 9px;
            }
            QComboBox#counselorTermCombo {
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 7px; color:#e8eaf0;
                font-size:12px; padding:4px 10px;
                min-height:30px;
            }
            QComboBox#counselorTermCombo:hover {
                border-color: rgba(52,211,153,0.35);
            }
            QComboBox#counselorTermCombo::drop-down {
                border:none; width:16px;
            }
            QComboBox#counselorTermCombo QAbstractItemView {
                background:#1a1f35;
                border:1px solid rgba(255,255,255,0.12);
                color:#e8eaf0;
                selection-background-color: rgba(52,211,153,0.18);
            }
            QPushButton#counselorLoadBtn {
                background: #34d399; border:none;
                border-radius:7px; color:#0e1120;
                font-size:12px; font-weight:700;
                padding:0 18px;
            }
            QPushButton#counselorLoadBtn:hover {
                background: rgba(52,211,153,0.85);
            }
            QPushButton#counselorLoadBtn:disabled {
                background: rgba(255,255,255,0.06);
                color: rgba(255,255,255,0.25);
            }
            #counselorTermStatus {
                color: rgba(255,255,255,0.40);
                font-size:11px; background:transparent;
            }
            #counselorUserLbl {
                color:rgba(255,255,255,0.45);
                font-size:12px; background:transparent;
            }
        """)

    # ── Logout ────────────────────────────────────────────────────────

    def _on_logout(self):
        from PyQt6.QtWidgets import QMessageBox

        # ── Confirmation ──────────────────────────────────────────────
        msg = QMessageBox(self)
        msg.setWindowTitle("Sign Out")
        msg.setText("Are you sure you want to sign out?")
        msg.setInformativeText(
            "You will be returned to the login screen.")
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes |
            QMessageBox.StandardButton.Cancel
        )
        msg.setDefaultButton(QMessageBox.StandardButton.Cancel)
        msg.button(QMessageBox.StandardButton.Yes).setText("Sign Out")
        msg.setStyleSheet("""
            QMessageBox { background-color: #13172a; }
            QMessageBox QLabel {
                color: #e8eaf0; font-size: 13px; background: transparent;
            }
            QMessageBox QPushButton {
                background-color: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.14);
                border-radius: 8px; color: rgba(255,255,255,0.80);
                font-size: 12px; font-weight: 600;
                padding: 8px 24px; min-width: 80px;
            }
            QMessageBox QPushButton:hover {
                background-color: rgba(255,255,255,0.12);
            }
            QMessageBox QPushButton[text="Sign Out"] {
                background-color: rgba(255,91,91,0.15);
                border-color: rgba(255,91,91,0.40);
                color: #ff5b5b;
            }
            QMessageBox QPushButton[text="Sign Out"]:hover {
                background-color: rgba(255,91,91,0.28);
            }
        """)

        if msg.exec() != QMessageBox.StandardButton.Yes:
            return

        AuthService.logout(self._db_conn)
        self.close()
        from ui.pages.login_dialog import LoginDialog
        dlg = LoginDialog()
        if dlg.exec() != LoginDialog.DialogCode.Accepted:
            sys.exit(0)
        _launch_for_role(dlg.db_conn)

    def closeEvent(self, event):
        """Stop all running threads before the window is destroyed."""
        for worker in (self._term_list_loader, self._term_loader):
            if worker is None:
                continue
            try:
                # Guard: the C++ object may already be deleted by deleteLater
                worker.finished.disconnect()
                worker.error.disconnect()
                if worker.isRunning():
                    worker.quit()
                    worker.wait(2000)
            except RuntimeError:
                pass   # already deleted — safe to ignore
            except Exception:
                pass
        # Remove DataStore listeners from all pages to prevent
        # signals firing into destroyed widgets
        try:
            from services.data_store import DataStore
            store = DataStore.get()
            for attr in ("dashboard_page", "risk_alerts_page",
             "student_cohort_page", "prediction_history_page",
             "interventions_page", "settings_page",
             "_analytics_page"):   # ← ADD
                page = getattr(self, attr, None)
                if page is None:
                    continue
                if hasattr(page, "_on_store_updated"):
                    store.remove_listener(page._on_store_updated)
                if hasattr(page, "closeEvent"):
                    try:
                        page.closeEvent(event)
                    except Exception:
                        pass
        except Exception:
            pass
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_term_bar_styles()


# ── Launch helper ─────────────────────────────────────────────────────────────

def _launch_for_role(db_conn):
    import sys
    from services.auth_service import AuthService as _AS

    # ── Post-login checks (run on every login path) ────────────────────
    # 1. Forced password change (admin reset the account)
    if _AS.needs_password_change(db_conn):
        from ui.dialogs.password_dialogs import ForcePasswordChangeDialog
        uid = int(_AS.current_user_id())
        dlg = ForcePasswordChangeDialog(db_conn, uid)
        if dlg.exec() != ForcePasswordChangeDialog.DialogCode.Accepted:
            sys.exit(0)

    # 2. Security question not yet set — mandatory on first login
    if _AS.needs_security_setup(db_conn):
        from ui.dialogs.password_dialogs import SecuritySetupDialog
        setup = SecuritySetupDialog(db_conn)
        setup.exec()

    # ── Route to the correct window ────────────────────────────────────
    role = (_AS.current_role() or "").strip().lower()
    print(f"[Routing] current_role='{role}' → "
          f"{'CounselorWindow' if role == 'counselor' else 'DashboardWindow'}")

    app = QApplication.instance()
    app._main_window = None

    if role == "counselor":
        w = CounselorWindow(db_conn=db_conn)
    else:
        from ui.dashboard_window_new import DashboardWindow
        w = DashboardWindow(db_conn=db_conn)

    w.show()
    w.raise_()
    w.activateWindow()
    app._main_window = w
    return w