from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QLineEdit, QFileDialog, QGraphicsOpacityEffect,
    QProgressBar, QScrollArea, QGridLayout, QComboBox,
)
from PyQt6.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve, QThread, pyqtSignal,
    QRect, QParallelAnimationGroup,
)
from PyQt6.QtGui import QColor, QIcon

from ui.mixins.prediction_mixin import PredictionMixin
from ui.components.loading_overlay import LoadingOverlay
from ui.dialogs.portal_dataset_dialog import PortalDatasetDialog
from ui.dialogs.clean_data_window import CleanDataWindow
from ui.dialogs.confirmation_dialog import ConfirmationDialog, show_error, show_warning
from services.data_store import DataStore
from services.excel_service import (
    read_excel_file,
    dataframe_to_rows,
    rows_to_dataframe,
)
from services.system_config import SystemConfig

ACCENT = "#4f8cff"

_PORTAL_CONFIG = {
    "mis":       {"label": "MIS",       "full": "Management Information System",  "icon": "", "color": "#4f8cff"},
    "sao":       {"label": "SAO",       "full": "Student Affairs Office",         "icon": "", "color": "#a78bfa"},
    "guidance":  {"label": "Guidance",  "full": "Guidance Office",                "icon": "", "color": "#34d399"},
    "registrar": {"label": "Registrar", "full": "Registrar's Office",             "icon": "", "color": "#f5b335"},
}

_DATASET_CONFIG = {
    "title":  "Prediction Dataset",
    "office": "Incoming First-Year Students",
    "accent": ACCENT,
}


# =============================================================================
# SLIDE STACK — carousel-style step container
# =============================================================================
# Lightweight horizontal-slide equivalent of QStackedWidget. Uses the same
# QPropertyAnimation-on-geometry technique as StudentProfileDrawer's slide-in
# panel (ui/components/student_profile_drawer.py), just horizontal and scoped
# to a fixed-size host instead of a full-window overlay. Qt clips child
# widgets to their parent's rect by default, so pages positioned outside the
# host's bounds during the slide are simply not painted — no manual clipping
# needed.
# =============================================================================

class _SlideStack(QWidget):
    """Holds N pages side-by-side; slide_to(index) animates between them."""

    ANIM_DURATION = 320

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pages: list[QWidget] = []
        self._current_index = -1
        self._anim_group: QParallelAnimationGroup | None = None

    def add_page(self, widget: QWidget) -> int:
        widget.setParent(self)
        widget.hide()
        self._pages.append(widget)
        if self._current_index == -1:
            self._current_index = 0
            widget.setGeometry(self.rect())
            widget.show()
        return len(self._pages) - 1

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if 0 <= self._current_index < len(self._pages):
            self._pages[self._current_index].setGeometry(self.rect())

    def current_index(self) -> int:
        return self._current_index

    def slide_to(self, index: int):
        if index == self._current_index or not (0 <= index < len(self._pages)):
            return
        if self._anim_group and self._anim_group.state() == QParallelAnimationGroup.State.Running:
            self._anim_group.stop()

        direction = 1 if index > self._current_index else -1
        outgoing  = self._pages[self._current_index]
        incoming  = self._pages[index]
        w, h      = self.width(), self.height()

        incoming.setGeometry(direction * w, 0, w, h)
        incoming.show()
        incoming.raise_()

        anim_out = QPropertyAnimation(outgoing, b"geometry")
        anim_out.setDuration(self.ANIM_DURATION)
        anim_out.setStartValue(outgoing.geometry())
        anim_out.setEndValue(QRect(-direction * w, 0, w, h))
        anim_out.setEasingCurve(QEasingCurve.Type.InOutCubic)

        anim_in = QPropertyAnimation(incoming, b"geometry")
        anim_in.setDuration(self.ANIM_DURATION)
        anim_in.setStartValue(incoming.geometry())
        anim_in.setEndValue(QRect(0, 0, w, h))
        anim_in.setEasingCurve(QEasingCurve.Type.InOutCubic)

        self._anim_group = QParallelAnimationGroup()
        self._anim_group.addAnimation(anim_out)
        self._anim_group.addAnimation(anim_in)

        prev_index = self._current_index

        def _on_finished():
            self._pages[prev_index].hide()
            self._current_index = index

        self._anim_group.finished.connect(_on_finished)
        self._anim_group.start()


# =============================================================================
# FUSED WORKER
# =============================================================================

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


# =============================================================================
# PREDICTION PAGE
# =============================================================================

class PredictionPage(PredictionMixin, QWidget):
    """
    4-step wizard: Dataset Details → Upload Portals → Merge & Clean →
    Run Pipeline & Predict. Each step is a full-width page inside a
    _SlideStack; Back/Next slide between them. Business logic (portal
    upload, merge, clean, fused predict, DB checks) is unchanged from the
    previous flat-scroll layout — only the navigation/presentation changed.
    """

    _STEP_META = [
        ("01", "Dataset Details"),
        ("02", "Upload Portals"),
        ("03", "Merge & Clean"),
        ("04", "Run & Predict"),
    ]
    _WIZARD_HEIGHT = 460   # fixed height for the slide area; tallest step
                           # (portal grid) sets the floor, shorter steps use
                           # addStretch() to pin content to the top instead
                           # of stretching to fill the space.

    def __init__(self):
        super().__init__()
        self._portal_data: dict = {k: None for k in _PORTAL_CONFIG}
        self._merged_headers: list | None = None
        self._merged_rows:    list | None = None
        self._merged  = False
        self._cleaned = False
        self._fused_worker: _FusedPredictionWorker | None = None
        self._available_terms: list = []

        # Wizard navigation state
        self._current_step  = 0
        self._furthest_step = 0
        self._step_badges: dict = {}
        self._step_titles: dict = {}
        self._step_connectors: list = []

        self.setup_ui()
        self._apply_styles()
        self.init_prediction(
            overlay_message="Running Pipeline & Prediction…",
            overlay_sub="Preparing features",
        )
        self._refresh_button_states()
        DataStore.get().add_listener(self._on_store_updated)

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_model_status()

    # ------------------------------------------------------------------
    # UI BUILD
    # ------------------------------------------------------------------

    def setup_ui(self):
        self.setObjectName("page")
        self.overlay = LoadingOverlay(self)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("predScroll")

        container = QWidget()
        container.setObjectName("predPageContainer")
        self.main_layout = QVBoxLayout(container)
        self.main_layout.setContentsMargins(30, 30, 30, 30)
        self.main_layout.setSpacing(20)

        scroll.setWidget(container)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self.main_layout.addWidget(self._build_header())
        self.main_layout.addWidget(self._build_model_status_panel())
        self.main_layout.addWidget(self._build_wizard())
        self.main_layout.addStretch()

        self._init_wizard_state()

    def _on_store_updated(self, key: str):
        if key in ("system_config", "all"):
            if hasattr(self, "_sem_pill_lbl"):
                self._sem_pill_lbl.setText(f"{SystemConfig.term_label()}  ▾")
            if hasattr(self, "_academic_year_combo"):
                self._academic_year_combo.setCurrentText(SystemConfig.academic_year())
            if hasattr(self, "_semester_combo"):
                self._semester_combo.setCurrentIndex(SystemConfig.semester() - 1)
        if key in ("trained_model", "all"):
            self._refresh_model_status()

    # ------------------------------------------------------------------
    # Model Status Panel
    # ------------------------------------------------------------------

    def _build_model_status_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("predModelStatusPanel")

        layout = QHBoxLayout(panel)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(0)

        left = QHBoxLayout()
        left.setSpacing(12)

        self._model_status_dot = QLabel("●")
        self._model_status_dot.setObjectName("predStatusDot")

        status_col = QVBoxLayout()
        status_col.setSpacing(3)
        self._model_status_title = QLabel("No Model Trained")
        self._model_status_title.setObjectName("predStatusTitle")
        self._model_status_sub = QLabel(
            "Train a model from the Model Training page before running prediction."
        )
        self._model_status_sub.setObjectName("predStatusSub")
        self._model_status_sub.setWordWrap(True)
        status_col.addWidget(self._model_status_title)
        status_col.addWidget(self._model_status_sub)

        left.addWidget(self._model_status_dot)
        left.addLayout(status_col, 1)
        layout.addLayout(left, 2)

        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setFixedWidth(1)
        div.setStyleSheet("background: rgba(255,255,255,0.08); border: none;")
        layout.addSpacing(20)
        layout.addWidget(div)
        layout.addSpacing(20)

        self._metric_chips_layout = QHBoxLayout()
        self._metric_chips_layout.setSpacing(12)
        layout.addLayout(self._metric_chips_layout, 3)

        self._refresh_model_status()
        return panel

    def _refresh_model_status(self):
        store = DataStore.get()
        model = store.trained_model

        while self._metric_chips_layout.count():
            item = self._metric_chips_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not model or not store.model_ready:
            self._model_status_dot.setStyleSheet(
                "color: #ff5b5b; font-size: 11px; background: transparent;"
            )
            self._model_status_title.setText("No Model Trained")
            self._model_status_sub.setText(
                "Train a model from the Model Training page before running prediction."
            )
            self._model_status_title.setStyleSheet(
                "color: rgba(255,255,255,0.75); font-size: 14px; "
                "font-weight: 700; background: transparent;"
            )
            self._metric_chips_layout.addWidget(
                self._make_chip("Prediction Unavailable", "—", "#ff5b5b")
            )
            self._metric_chips_layout.addStretch()
            return

        self._model_status_dot.setStyleSheet(
            "color: #34d399; font-size: 11px; background: transparent;"
        )
        self._model_status_title.setText("✓  Model Active - Ready for Prediction")
        self._model_status_title.setStyleSheet(
            "color: #34d399; font-size: 14px; font-weight: 700; background: transparent;"
        )

        meta      = model.get("metadata", {}) or {}
        model_id  = model.get("model_id", "rf")
        recall    = meta.get("recall")
        f1        = meta.get("f1_score")
        precision = meta.get("precision")
        pr_auc    = meta.get("pr_auc")
        threshold = (model.get("decision_threshold")
                     or meta.get("decision_threshold")
                     or meta.get("threshold"))
        train_sz  = meta.get("train_size")
        timestamp = meta.get("timestamp")

        ts_str = ""
        if timestamp:
            try:
                from datetime import datetime as _dt
                ts = _dt.fromisoformat(str(timestamp))
                ts_str = f"  ·  Trained {ts.strftime('%b %d, %Y  %H:%M')}"
            except Exception:
                ts_str = f"  ·  {timestamp}"
        self._model_status_sub.setText(
            f"Random Forest  ·  ID: {model_id}{ts_str}"
        )
        self._model_status_sub.setStyleSheet(
            "color: rgba(255,255,255,0.45); font-size: 12px; background: transparent;"
        )

        def _fmt_pct(v):
            return f"{v*100:.1f}%" if v is not None else "—"
        def _fmt_f(v, decimals=3):
            return f"{v:.{decimals}f}" if v is not None else "—"

        chips = [
            ("Recall",    _fmt_pct(recall),    "#34d399"),
            ("Precision", _fmt_pct(precision), "#4f8cff"),
            ("F1 Score",  _fmt_f(f1),          "#a78bfa"),
            ("PR-AUC",    _fmt_f(pr_auc),      "#f5b335"),
            ("Threshold", _fmt_f(threshold, 2),"#8b949e"),
        ]
        if train_sz:
            chips.append(("Train Size", f"{int(train_sz):,}", "#8b949e"))

        for label, value, color in chips:
            self._metric_chips_layout.addWidget(self._make_chip(label, value, color))
        self._metric_chips_layout.addStretch()

    def _make_chip(self, label: str, value: str, color: str) -> QFrame:
        chip = QFrame()
        chip.setObjectName("predMetricChip")
        col = QVBoxLayout(chip)
        col.setContentsMargins(14, 10, 14, 10)
        col.setSpacing(3)

        val_lbl = QLabel(value)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        val_lbl.setStyleSheet(
            f"color: {color}; font-size: 15px; font-weight: 800; background: transparent;"
        )
        key_lbl = QLabel(label)
        key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        key_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.40); font-size: 10px; "
            "font-weight: 600; letter-spacing: 0.5px; background: transparent;"
        )
        col.addWidget(val_lbl)
        col.addWidget(key_lbl)
        return chip

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def _build_header(self) -> QFrame:
        container = QFrame()
        container.setObjectName("predHeaderCard")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 20, 24, 20)

        row = QHBoxLayout()
        row.setSpacing(20)

        text_col = QVBoxLayout()
        text_col.setSpacing(5)
        title = QLabel("PREDICTION CENTER")
        title.setObjectName("header")
        sub = QLabel(
            "Upload incoming student datasets, merge portals, "
            "and score each student with the trained risk model"
        )
        sub.setObjectName("subHeader")
        text_col.addWidget(title)
        text_col.addWidget(sub)
        row.addLayout(text_col, 1)

        pill = QFrame()
        pill.setObjectName("predModelPill")
        pill_row = QHBoxLayout(pill)
        pill_row.setContentsMargins(20, 12, 20, 12)
        pill_row.setSpacing(12)

        dot = QLabel("●")
        dot.setObjectName("predModelDot")
        opacity = QGraphicsOpacityEffect(dot)
        dot.setGraphicsEffect(opacity)
        anim = QPropertyAnimation(opacity, b"opacity")
        anim.setDuration(1200)
        anim.setStartValue(1.0)
        anim.setKeyValueAt(0.5, 0.3)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        anim.setLoopCount(-1)
        anim.start()
        self._status_anim = anim

        status_lbl = QLabel("Model Active")
        status_lbl.setObjectName("predModelStatus")
        self._sem_pill_lbl = QLabel(f"{SystemConfig.term_label()}  ▾")
        self._sem_pill_lbl.setObjectName("predSemesterPill")
        self._sem_pill_lbl.setObjectName("predSemesterPill")

        pill_row.addWidget(dot)
        pill_row.addWidget(status_lbl)
        pill_row.addSpacing(8)
        pill_row.addWidget(self._sem_pill_lbl)
        row.addWidget(pill)

        layout.addLayout(row)
        return container

    # ------------------------------------------------------------------
    # Wizard shell: step indicator + slide stack + nav
    # ------------------------------------------------------------------

    def _build_wizard(self) -> QFrame:
        card = QFrame()
        card.setObjectName("predWizardCard")
        card.setStyleSheet("""
            QFrame#predWizardCard {
                background-color: rgba(255,255,255,0.02);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 14px;
            }
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_step_indicator())

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(255,255,255,0.07); border: none;")
        layout.addWidget(sep)

        self._slide_stack = _SlideStack()
        self._slide_stack.setObjectName("predSlideStack")
        self._slide_stack.setFixedHeight(self._WIZARD_HEIGHT)

        self._step_pages = [
            self._build_details_card(),
            self._build_portals_card(),
            self._build_merge_card(),
            self._build_fused_card(),
        ]
        for page in self._step_pages:
            self._slide_stack.add_page(page)

        layout.addWidget(self._slide_stack)
        layout.addWidget(self._build_wizard_nav())
        return card

    def _build_step_indicator(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("predStepIndicatorBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(24, 18, 24, 18)
        row.setSpacing(6)

        for i, (num, title) in enumerate(self._STEP_META):
            row.addWidget(self._build_step_chip(i, num, title), 1)
            if i < len(self._STEP_META) - 1:
                connector = QFrame()
                connector.setFixedHeight(2)
                connector.setFixedWidth(36)
                row.addWidget(connector, 0)
                self._step_connectors.append(connector)

        return bar

    def _build_step_chip(self, idx: int, num: str, title: str) -> QFrame:
        chip = QFrame()
        chip.setObjectName(f"predStepChip_{idx}")
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(chip)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(10)

        badge = QLabel(num)
        badge.setFixedSize(26, 26)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_lbl = QLabel(title)

        row.addWidget(badge)
        row.addWidget(title_lbl)
        row.addStretch()

        # Click-to-jump — same inline mousePressEvent pattern already used
        # elsewhere in this app (e.g. student_cohort_page.py's clickable
        # student ID cell) rather than wrapping in a QPushButton, which
        # doesn't lay out a badge + label combo cleanly.
        chip.mousePressEvent = lambda e, i=idx: (
            self._on_step_chip_clicked(i)
            if e.button() == Qt.MouseButton.LeftButton else None
        )

        self._step_badges[idx] = badge
        self._step_titles[idx] = title_lbl
        return chip

    def _build_wizard_nav(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("predWizardNav")
        row = QHBoxLayout(bar)
        row.setContentsMargins(24, 16, 24, 20)
        row.setSpacing(10)

        self._back_btn = QPushButton("←  Back")
        self._back_btn.setObjectName("predSecondaryBtn")
        self._back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back_btn.setFixedHeight(38)
        self._back_btn.setFixedWidth(110)
        self._back_btn.clicked.connect(self._go_back)

        self._step_progress_lbl = QLabel("")
        self._step_progress_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.30); font-size: 11px; background: transparent;"
        )

        self._next_btn = QPushButton("Next  →")
        self._next_btn.setObjectName("predPrimaryBtn")
        self._next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_btn.setFixedHeight(38)
        self._next_btn.setFixedWidth(130)
        self._next_btn.clicked.connect(self._go_next)

        row.addWidget(self._back_btn)
        row.addStretch()
        row.addWidget(self._step_progress_lbl)
        row.addStretch()
        row.addWidget(self._next_btn)
        return bar

    # ------------------------------------------------------------------
    # Wizard navigation state machine
    # ------------------------------------------------------------------

    def _init_wizard_state(self):
        self._current_step  = 0
        self._furthest_step = 0
        self._update_wizard_nav()
        self._update_step_indicator()

    def _step_is_complete(self, idx: int) -> bool:
        if idx == 0:
            return bool(self._name_input.text().strip())
        if idx == 1:
            return self._all_portals_uploaded()
        if idx == 2:
            return self._merged and self._merged_rows is not None
        return False   # step 3 (index) has no "next" — terminal step

    def _go_next(self):
        if self._current_step >= len(self._step_pages) - 1:
            return
        if not self._step_is_complete(self._current_step):
            if self._current_step == 0:
                self._name_input.setFocus()
            return
        self._go_to_step(self._current_step + 1)

    def _go_back(self):
        if self._current_step <= 0:
            return
        self._go_to_step(self._current_step - 1)

    def _go_to_step(self, idx: int):
        idx = max(0, min(idx, len(self._step_pages) - 1))
        if idx > self._current_step and not self._step_is_complete(self._current_step):
            return
        self._current_step  = idx
        self._furthest_step = max(self._furthest_step, idx)
        self._slide_stack.slide_to(idx)
        self._update_wizard_nav()
        self._update_step_indicator()

    def _on_step_chip_clicked(self, idx: int):
        if idx <= self._furthest_step:
            self._go_to_step(idx)

    def _update_wizard_nav(self):
        self._back_btn.setVisible(self._current_step > 0)
        is_last = self._current_step == len(self._step_pages) - 1
        self._next_btn.setVisible(not is_last)
        self._next_btn.setEnabled(self._step_is_complete(self._current_step))
        self._step_progress_lbl.setText(
            f"Step {self._current_step + 1} of {len(self._step_pages)}"
        )

    def _update_step_indicator(self):
        for i in range(len(self._STEP_META)):
            badge     = self._step_badges[i]
            title_lbl = self._step_titles[i]
            num       = self._STEP_META[i][0]

            if i == self._current_step:
                badge.setText(num)
                badge.setStyleSheet(
                    f"background:{ACCENT}; color:white; border-radius:13px; "
                    "font-size:11px; font-weight:800;"
                )
                title_lbl.setStyleSheet(
                    "color:#e8eaf0; font-size:12px; font-weight:700; background:transparent;"
                )
            elif i < self._furthest_step or (i <= self._furthest_step and self._step_is_complete(i)):
                badge.setText("✓")
                badge.setStyleSheet(
                    "background:rgba(52,211,153,0.18); color:#34d399; "
                    "border:1px solid rgba(52,211,153,0.4); border-radius:13px; "
                    "font-size:12px; font-weight:800;"
                )
                title_lbl.setStyleSheet(
                    "color:rgba(255,255,255,0.55); font-size:12px; "
                    "font-weight:600; background:transparent;"
                )
            else:
                badge.setText(num)
                badge.setStyleSheet(
                    "background:rgba(255,255,255,0.06); color:rgba(255,255,255,0.35); "
                    "border:1px solid rgba(255,255,255,0.12); border-radius:13px; "
                    "font-size:11px; font-weight:700;"
                )
                title_lbl.setStyleSheet(
                    "color:rgba(255,255,255,0.30); font-size:12px; "
                    "font-weight:600; background:transparent;"
                )

        for i, connector in enumerate(self._step_connectors):
            passed = i < self._furthest_step or i < self._current_step
            connector.setStyleSheet(
                f"background:{'#34d399' if passed else 'rgba(255,255,255,0.10)'}; "
                "border-radius:1px;"
            )

    # ------------------------------------------------------------------
    # Step 01 — Dataset Details
    # ------------------------------------------------------------------

    def _build_details_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("predCardDetails")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        layout.addWidget(self._step_label("01", "Dataset Details"))

        hint = QLabel(
            "Name this cohort, select the academic term, then continue to "
            "upload the portal files."
        )
        hint.setObjectName("predHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addWidget(self._input_label("Dataset Name"))
        self._name_input = QLineEdit()
        self._name_input.setObjectName("predInput")
        self._name_input.setPlaceholderText("e.g. Incoming First-Year Cohort 2025")
        self._name_input.textChanged.connect(self._refresh_button_states)
        layout.addWidget(self._name_input)

        term_row = QHBoxLayout()
        term_row.setSpacing(10)

        ay_col = QVBoxLayout()
        ay_col.setSpacing(6)
        ay_col.addWidget(self._input_label("Academic Year"))
        self._academic_year_combo = QComboBox()
        self._academic_year_combo.setObjectName("predTermCombo")
        for ay in ["2022-2023", "2023-2024", "2024-2025", "2025-2026", "2026-2027"]:
            self._academic_year_combo.addItem(ay)
        self._academic_year_combo.setCurrentText(SystemConfig.academic_year())
        ay_col.addWidget(self._academic_year_combo)

        sem_col = QVBoxLayout()
        sem_col.setSpacing(6)
        sem_col.addWidget(self._input_label("Semester"))
        self._semester_combo = QComboBox()
        self._semester_combo.setObjectName("predTermCombo")
        self._semester_combo.addItem("1st Semester", userData=1)
        self._semester_combo.addItem("2nd Semester", userData=2)
        self._semester_combo.setCurrentIndex(SystemConfig.semester() - 1)
        sem_col.addWidget(self._semester_combo)

        term_row.addLayout(ay_col, 2)
        term_row.addLayout(sem_col, 1)
        layout.addLayout(term_row)

        self._term_preview = QLabel()
        self._term_preview.setObjectName("predTermPreview")
        self._update_term_preview()
        self._academic_year_combo.currentTextChanged.connect(
            lambda _: self._update_term_preview())
        self._semester_combo.currentIndexChanged.connect(
            lambda _: self._update_term_preview())
        layout.addWidget(self._term_preview)

        layout.addStretch()
        return card

    def _update_term_preview(self):
        ay  = self._academic_year_combo.currentText()
        sem = self._semester_combo.currentText()
        self._term_preview.setText(f"Term: {ay} — {sem}")

    def _get_selected_term(self) -> tuple[str, int]:
        ay  = self._academic_year_combo.currentText()
        sem = self._semester_combo.currentData() or 1
        return ay, int(sem)

    # ------------------------------------------------------------------
    # Step 02 — Upload Portal Datasets
    # ------------------------------------------------------------------

    def _build_portals_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("predCardPortals")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        layout.addWidget(self._step_label("02", "Upload Portal Datasets"))

        hint = QLabel(
            "Upload the incoming student export from each office. "
            "All four are required before merging."
        )
        hint.setObjectName("predHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._portal_cards: dict = {}
        grid = QGridLayout()
        grid.setSpacing(12)

        for i, key in enumerate(list(_PORTAL_CONFIG.keys())):
            pcard = self._build_portal_tile(key)
            self._portal_cards[key] = pcard
            grid.addWidget(pcard, i // 2, i % 2)

        layout.addLayout(grid)
        layout.addStretch()
        return card

    def _build_portal_tile(self, key: str) -> QFrame:
        cfg   = _PORTAL_CONFIG[key]
        color = cfg["color"]

        tile = QFrame()
        tile.setObjectName("predPortalTile")
        layout = QVBoxLayout(tile)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)

        icon_lbl = QLabel(cfg["icon"])
        icon_lbl.setStyleSheet("font-size: 18px; background: transparent;")
        top.addWidget(icon_lbl)

        name_col = QVBoxLayout()
        name_col.setSpacing(1)
        name_lbl = QLabel(cfg["label"])
        name_lbl.setStyleSheet(
            f"color: {color}; font-size: 13px; font-weight: 700; background: transparent;"
        )
        full_lbl = QLabel(cfg["full"])
        full_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.35); font-size: 10px; background: transparent;"
        )
        name_col.addWidget(name_lbl)
        name_col.addWidget(full_lbl)
        top.addLayout(name_col, 1)

        dot = QLabel("●")
        dot.setObjectName(f"portalDot_{key}")
        dot.setStyleSheet(
            "color: rgba(255,255,255,0.18); font-size: 9px; background: transparent;"
        )
        top.addWidget(dot)
        layout.addLayout(top)

        status_lbl = QLabel("No file uploaded")
        status_lbl.setObjectName(f"portalStatus_{key}")
        status_lbl.setWordWrap(True)
        status_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.3); font-size: 11px; background: transparent;"
        )
        layout.addWidget(status_lbl)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(0)

        btn = QPushButton("  Browse File")
        btn.setObjectName("predPortalBtn")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(30)
        btn.setFixedWidth(120)
        btn.clicked.connect(lambda _, k=key: self._browse_portal(k))
        btn_row.addWidget(btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        return tile

    # ------------------------------------------------------------------
    # Step 03 — Merge & Clean
    # ------------------------------------------------------------------

    def _build_merge_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("predCardMerge")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        layout.addWidget(self._step_label("03", "Merge & Clean"))

        self._merge_hint = QLabel("Upload all four portal datasets to enable merging.")
        self._merge_hint.setObjectName("predHint")
        self._merge_hint.setWordWrap(True)
        layout.addWidget(self._merge_hint)

        self._merge_report_lbl = QLabel("")
        self._merge_report_lbl.setObjectName("predSuccess")
        self._merge_report_lbl.setWordWrap(True)
        self._merge_report_lbl.hide()
        layout.addWidget(self._merge_report_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._merge_btn = QPushButton("⚙  Merge Portals")
        self._merge_btn.setObjectName("predPrimaryBtn")
        self._merge_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._merge_btn.setFixedHeight(36)
        self._merge_btn.clicked.connect(self._run_merge)

        self._view_merged_btn = QPushButton("View Merged")
        self._view_merged_btn.setObjectName("predSecondaryBtn")
        self._view_merged_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._view_merged_btn.setFixedHeight(36)
        self._view_merged_btn.clicked.connect(self._view_merged)

        self._clean_btn = QPushButton("Clean Data")
        self._clean_btn.setObjectName("predSecondaryBtn")
        self._clean_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clean_btn.setFixedHeight(36)
        self._clean_btn.clicked.connect(self._clean_data)

        btn_row.addWidget(self._merge_btn)
        btn_row.addWidget(self._view_merged_btn)
        btn_row.addWidget(self._clean_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        layout.addStretch()
        return card

    # ------------------------------------------------------------------
    # Step 04 — Run Pipeline & Predict
    # ------------------------------------------------------------------

    def _build_fused_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("predCardPredict")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        top_row = QHBoxLayout()
        top_row.setSpacing(20)

        label_col = QVBoxLayout()
        label_col.setSpacing(6)
        label_col.addWidget(self._step_label("04", "Run Pipeline & Predict"))
        self._fused_hint = QLabel("Merge the portal datasets to enable prediction.")
        self._fused_hint.setObjectName("predHint")
        self._fused_hint.setWordWrap(True)
        label_col.addWidget(self._fused_hint)

        self._fused_btn = QPushButton("Run Pipeline & Predict")
        self._fused_btn.setObjectName("predRunBtn")
        self._fused_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fused_btn.setFixedWidth(220)
        self._fused_btn.setFixedHeight(42)
        self._fused_btn.clicked.connect(self._on_fused_clicked)

        top_row.addLayout(label_col, 1)
        top_row.addWidget(self._fused_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(top_row)

        self._progress_bar = QProgressBar()
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(6)
        self._progress_bar.setObjectName("predProgressBar")
        layout.addWidget(self._progress_bar)

        self._progress_label = QLabel("")
        self._progress_label.setObjectName("predHint")
        layout.addWidget(self._progress_label)

        layout.addStretch()
        return card

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------

    def _step_label(self, number: str, title: str) -> QWidget:
        row = QHBoxLayout()
        row.setSpacing(10)
        row.setContentsMargins(0, 0, 0, 0)

        num = QLabel(number)
        num.setObjectName("predStepNumber")
        num.setFixedWidth(28)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFixedWidth(1)
        sep.setFixedHeight(16)
        sep.setStyleSheet("background: rgba(255,255,255,0.18); border: none;")

        title_lbl = QLabel(title)
        title_lbl.setObjectName("predStepTitle")

        row.addWidget(num)
        row.addWidget(sep)
        row.addWidget(title_lbl)
        row.addStretch()

        host = QWidget()
        host.setLayout(row)
        return host

    def _input_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("predInputLabel")
        return lbl

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def _all_portals_uploaded(self) -> bool:
        return all(v is not None for v in self._portal_data.values())

    def _refresh_button_states(self):
        all_up = self._all_portals_uploaded()
        merged = self._merged and self._merged_rows is not None

        self._merge_btn.setEnabled(all_up)
        self._view_merged_btn.setEnabled(merged)
        self._clean_btn.setEnabled(merged)
        self._fused_btn.setEnabled(merged)

        if not all_up:
            missing = [_PORTAL_CONFIG[k]["label"]
                       for k, v in self._portal_data.items() if v is None]
            self._merge_hint.setText(f"Still needed: {', '.join(missing)}")
        elif not merged:
            self._merge_hint.setText(
                "All portals ready — click Merge Portals to unify the datasets."
            )
        elif not self._cleaned:
            self._merge_hint.setText(
                f"Merged  ·  {len(self._merged_rows):,} students  ·  "
                "Optionally clean the data before predicting."
            )
        else:
            self._merge_hint.setText(
                f"✓  Merged & cleaned  ·  {len(self._merged_rows):,} students ready."
            )

        if merged:
            self._fused_hint.setText(
                f"Ready — {len(self._merged_rows):,} students will be scored "
                "and results saved to the database."
            )
        else:
            self._fused_hint.setText("Merge the portal datasets to enable prediction.")

        # Keep wizard nav / step indicator in sync with whatever changed
        # (portal upload, merge, clean, or dataset-name edits all funnel
        # through this one method already).
        if hasattr(self, "_back_btn"):
            self._update_wizard_nav()
            self._update_step_indicator()

    def _update_portal_card_ui(self, key: str):
        cfg   = _PORTAL_CONFIG[key]
        color = cfg["color"]
        data  = self._portal_data[key]
        tile  = self._portal_cards[key]

        dot = tile.findChild(QLabel, f"portalDot_{key}")
        lbl = tile.findChild(QLabel, f"portalStatus_{key}")

        if data is None:
            if dot:
                dot.setStyleSheet(
                    "color: rgba(255,255,255,0.18); font-size: 9px; background: transparent;"
                )
            if lbl:
                lbl.setText("No file uploaded")
                lbl.setStyleSheet(
                    "color: rgba(255,255,255,0.3); font-size: 11px; background: transparent;"
                )
        else:
            if dot:
                dot.setStyleSheet(
                    f"color: {color}; font-size: 9px; background: transparent;"
                )
            if lbl:
                lbl.setText(f"✓  {data['row_count']:,} rows · {len(data['headers'])} columns")
                lbl.setStyleSheet(
                    f"color: {color}; font-size: 11px; background: transparent;"
                )

    # ------------------------------------------------------------------
    # Portal file upload
    # ------------------------------------------------------------------

    def _browse_portal(self, key: str):
        cfg  = _PORTAL_CONFIG[key]
        path, _ = QFileDialog.getOpenFileName(
            self, f"Select {cfg['full']} dataset", "",
            "Data Files (*.xlsx *.xls *.csv);;All Files (*)",
        )
        if not path:
            return
        try:
            df = read_excel_file(path)
        except Exception as e:
            show_error(self, "Upload Failed",
                       f"Could not read the {cfg['label']} file.", str(e))
            return

        headers, rows = dataframe_to_rows(df)
        if not headers or not rows:
            show_warning(self, "Empty File",
                         f"The {cfg['label']} file contains no usable rows.")
            return

        wrong = self._detect_engineered_columns(headers)
        if wrong:
            show_error(self, "Wrong File Type",
                       f"The {cfg['label']} file contains processed feature columns.",
                       f"Unexpected columns: {', '.join(wrong[:5])}"
                       + (" and more…" if len(wrong) > 5 else ""))
            return

        self._portal_data[key] = {"headers": headers, "rows": rows, "row_count": len(rows)}
        self._merged  = False
        self._cleaned = False
        self._merged_headers = None
        self._merged_rows    = None
        self._merge_report_lbl.hide()
        self._update_portal_card_ui(key)
        self._refresh_button_states()

        DataStore.get().add_activity(
            f"{cfg['label']} dataset uploaded — {len(rows):,} rows",
            icon=cfg["icon"], color=cfg["color"],
        )

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def _run_merge(self):
        if not self._all_portals_uploaded():
            return
        from services.merge_engine import MergeEngine

        portals = {k: {"headers": v["headers"], "rows": v["rows"]}
                   for k, v in self._portal_data.items()}
        result = MergeEngine.merge(portals)

        if not result.success:
            show_error(self, "Merge Failed",
                       "The portal datasets could not be merged.",
                       "\n".join(result.report.errors))
            return

        self._merged_headers = result.headers
        self._merged_rows    = result.rows
        self._merged  = True
        self._cleaned = False

        r = result.report
        report_text = (
            f"✓  {r.total_merged:,} students  ·  {len(result.headers)} columns  ·  "
            f"Coverage {r.coverage_pct:.0f}%"
        )
        if any(r.unmatched.values()):
            parts = [f"{_PORTAL_CONFIG[k]['label']}: {v:,} unmatched"
                     for k, v in r.unmatched.items() if v]
            report_text += f"  ·  {',  '.join(parts)}"

        self._merge_report_lbl.setText(report_text)
        self._merge_report_lbl.show()

        DataStore.get().add_activity(
            f"Portals merged — {r.total_merged:,} students  ·  "
            f"\"{self._name_input.text().strip()}\" "
            f"({self._academic_year_combo.currentText()})",
            icon="⚙", color=ACCENT,
        )

        try:
            from services.activity_logger import ActivityLogger
            _conn = DataStore.get().db_conn
            if _conn:
                ActivityLogger.log_merge(
                    _conn,
                    total_merged = r.total_merged,
                    coverage_pct = float(r.coverage_pct),
                    dataset_name = self._name_input.text().strip(),
                )
                _conn.commit()
        except Exception as _e:
            print(f"[PredictionPage] Merge log error: {_e}")

        self._refresh_button_states()

    # ------------------------------------------------------------------
    # View / Clean
    # ------------------------------------------------------------------

    def _view_merged(self):
        if not self._merged or not self._merged_rows:
            return
        PortalDatasetDialog(
            portal_title="Merged Prediction Dataset",
            headers=self._merged_headers,
            rows=self._merged_rows,
            accent=ACCENT,
            readonly=True,
            parent=self,
        ).exec()

    def _clean_data(self):
        if not self._merged or not self._merged_rows:
            return
        clean = CleanDataWindow(
            self._merged_headers, self._merged_rows, _DATASET_CONFIG, parent=self,
        )
        if not clean.exec():
            return
        self._merged_headers = list(clean.cleaned_headers)
        self._merged_rows    = [list(r) for r in clean.cleaned_rows]
        self._cleaned        = True
        self._merge_report_lbl.setText(
            f"✓  Merged & cleaned  ·  {len(self._merged_rows):,} rows  ·  "
            f"{len(self._merged_headers)} columns — ready to predict"
        )
        DataStore.get().add_activity(
            f"Merged dataset cleaned — {len(self._merged_rows):,} rows",
            icon="🧹", color="#34d399",
        )
        self._refresh_button_states()

    # ------------------------------------------------------------------
    # Duplicate term check
    # ------------------------------------------------------------------

    def _check_existing_prediction(self, academic_year: str, semester: int) -> int:
        """
        Return the count of rows already saved in fact_student_academic_risk
        for the given (academic_year, semester).
        Returns 0 if the DB is unavailable or the term has no records.
        """
        conn = DataStore.get().db_conn
        if not conn:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM   public.fact_student_academic_risk fsr
                    JOIN   public.dim_academic_term t
                           ON t.term_key = fsr.term_key
                    WHERE  t.academic_year = %s
                      AND  t.semester      = %s
                    """,
                    (academic_year, semester),
                )
                row = cur.fetchone()
                return int(row[0]) if row else 0
        except Exception as exc:
            print(f"[PredictionPage] Term existence check failed: {exc}")
            return 0   # fail open — don't block the user on a DB error

    # ------------------------------------------------------------------
    # Fused Run Pipeline & Predict
    # ------------------------------------------------------------------

    def _on_fused_clicked(self):
        if not self._merged or not self._merged_rows:
            return

        name        = self._name_input.text().strip()
        school_year = self._academic_year_combo.currentText()
        if not name or not school_year:
            show_warning(self, "Missing Details",
                         "Fill in the Dataset Name and School Year (Step 01) "
                         "before running prediction.")
            self._go_to_step(0)
            self._name_input.setFocus()
            return

        store = DataStore.get()
        if not store.trained_model:
            from services.model_registry import ModelRegistry
            pkg = ModelRegistry.load_latest_model()
            if pkg:
                store.trained_model = {
                    "model":         pkg["model"],
                    "model_id":      pkg["model_id"],
                    "feature_names": pkg["feature_names"],
                    "metadata":      pkg["metadata"],
                    "target_col":    "risk_label",
                }
                store.model_ready = True
            else:
                show_warning(self, "No Trained Model",
                             "No trained model was found.",
                             "Complete Model Training first, then return here.")
                return

        # ── Duplicate term check ──────────────────────────────────────
        # Query the DB before showing the confirmation dialog.
        # If this (academic_year, semester) already has saved predictions,
        # warn the user and require explicit confirmation to overwrite.
        academic_year, semester = self._get_selected_term()
        existing_count = self._check_existing_prediction(academic_year, semester)
        if existing_count > 0:
            sem_label = "1st" if semester == 1 else "2nd"
            overwrite_dlg = ConfirmationDialog(
                "Prediction Already Exists",
                f"{academic_year} — {sem_label} Semester already has "
                f"{existing_count:,} saved prediction records.",
                detail="Running again will overwrite the existing results for "
                       "this term. Do you want to continue?",
                confirm_label="Overwrite",
                parent=self,
            )
            if not overwrite_dlg.exec():
                return

        # ── Normal confirmation ───────────────────────────────────────
        dialog = ConfirmationDialog(
            "Run Pipeline & Predict",
            f"Score {len(self._merged_rows):,} students in \"{name}\" ({school_year})?",
            detail="Feature engineering runs first, then the trained model scores "
                   "each student. Results are saved to the database automatically.",
            parent=self,
        )
        if not dialog.exec():
            return

        self._fused_btn.setEnabled(False)
        self._progress_bar.setValue(0)
        self._progress_label.setText("Starting…")
        self.overlay.set_message("Running Pipeline & Prediction…", "Engineering features")
        self.overlay.show()

        self._fused_worker = _FusedPredictionWorker(
            headers       = self._merged_headers,
            rows          = self._merged_rows,
            name          = name,
            school_year   = school_year,
            academic_year = academic_year,
            semester      = semester,
        )
        self._fused_worker.progress.connect(self._on_fused_progress)
        self._fused_worker.finished.connect(self._on_fused_finished)
        self._fused_worker.error.connect(self._on_fused_error)
        self._fused_worker.finished.connect(
            self._fused_worker.deleteLater,
            Qt.ConnectionType.QueuedConnection,
        )
        self._fused_worker.error.connect(
            self._fused_worker.deleteLater,
            Qt.ConnectionType.QueuedConnection,
        )
        self._fused_worker.start()

    def _on_fused_progress(self, step: str, pct: int):
        self._progress_bar.setValue(pct)
        self._progress_label.setText(step)
        self.overlay.set_message("Running Pipeline & Prediction…", step)

    def _on_fused_finished(self, result):
        self._progress_bar.setValue(100)
        self._progress_label.setText("Done ✅")
        self._fused_btn.setEnabled(True)
        self._on_prediction_complete(result)
        self._fused_worker = None

    def _on_fused_error(self, error_msg: str):
        self.overlay.hide()
        self._progress_bar.setValue(0)
        self._progress_label.setText("")
        self._fused_btn.setEnabled(True)
        self._fused_worker = None
        show_error(self, "Pipeline & Prediction Failed",
                   "The operation could not complete.", error_msg)

    # ------------------------------------------------------------------
    # PredictionMixin hook
    # ------------------------------------------------------------------

    def _apply_predictions(self, result):
        if not result or not result.success:
            return
        s    = result.summary
        name = self._name_input.text().strip()
        yr   = self._academic_year_combo.currentText()
        self._fused_hint.setText(
            f"✓  Scored {s.total:,} students  ·  {s.high_risk:,} high-risk  ·  "
            f"\"{name}\" ({yr})  ·  Saved to database."
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_engineered_columns(headers: list) -> list[str]:
        _MARKERS = {
            "risk_label", "Entrance_Exam_Tier", "HS_Performance_Tier",
            "Strand_Program_Match", "Financial_Stress", "First_Gen_Student",
            "Has_Scholarship", "Gap_Years", "Private_HS", "Has_HS_Honors",
            "Age_At_Enrollment", "Age_Group", "Distance_Bucket", "Distance_KM",
            "Program_Risk_Index", "Municipality_Risk_Index",
            "GPA_Tier", "Has_College_Grade", "Year_Level",
        }
        return sorted(_MARKERS & {h.strip() for h in headers})

    def _apply_styles(self):
        pass  # Styles defined in theme.qss

    def closeEvent(self, event):
        DataStore.get().remove_listener(self._on_store_updated)
        super().closeEvent(event)