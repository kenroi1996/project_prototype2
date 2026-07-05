from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QLineEdit, QComboBox, QProgressBar, QGraphicsOpacityEffect,
    QGridLayout,
)
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QIcon

from .student_profile_drawer import StudentProfileDrawer
from ui.components.loading_overlay import LoadingOverlay
from ui.mixins.prediction_mixin import PredictionMixin
from services.data_store import DataStore
from services.system_config import SystemConfig


TABLE_COLUMNS = [
    ("STUDENT ID",     1),
    ("NAME",           2),
    ("COLLEGE",        1),
    ("RISK SCORE",     2),   # col 3 — score bar + percentage
    ("RISK LEVEL",     1),   # col 4
    ("PRIMARY FACTOR", 2),   # col 5
    ("",               1),   # col 6 — View button
]


class StudentCohortPage(PredictionMixin, QWidget):
    """Student Cohort Explorer — searchable cohort table with risk metrics."""

    _RISK_LEVELS = {
        "high_risk":     "High",
        "moderate_risk": "Moderate",
        "low_risk":      "Low",
    }

    def __init__(self):
        super().__init__()
        self._table_rows    = []
        self._profile_drawer = None
        self.setup_ui()
        self._apply_filters()
        self.overlay = LoadingOverlay(self)

        DataStore.get().add_listener(self._on_store_updated)

        existing = DataStore.get().predictions
        if existing and getattr(existing, "success", False):
            self._apply_predictions(existing)

    # ------------------------------------------------------------------
    # Window events
    # ------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self._ensure_profile_drawer()

    def hideEvent(self, event):
        if self._profile_drawer is not None and self._profile_drawer._is_open:
            self._profile_drawer.hide()
            self._profile_drawer._is_open = False
        super().hideEvent(event)

    def closeEvent(self, event):
        DataStore.get().remove_listener(self._on_store_updated)
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Profile drawer
    # ------------------------------------------------------------------

    def _find_drawer_host(self):
        widget = self.parent()
        while widget:
            parent = widget.parent()
            if parent is not None and \
               parent.metaObject().className() == "QStackedWidget":
                return widget
            widget = parent
        return None

    def _ensure_profile_drawer(self):
        if self._profile_drawer is not None:
            return
        host = self._find_drawer_host()
        if host is not None:
            self._profile_drawer = StudentProfileDrawer(host)

    def _open_student_profile(self, student):
        self._ensure_profile_drawer()
        if self._profile_drawer is not None:
            profile = dict(student)
            profile.setdefault("factor", student.get("factor", "—"))
            self._profile_drawer.open_drawer(profile)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _go_to_prediction_page(self):
        from PyQt6.QtWidgets import QStackedWidget
        widget = self.parent()
        while widget is not None:
            if isinstance(widget, QStackedWidget):
                for i in range(widget.count()):
                    page = widget.widget(i)
                    if page and "Prediction" in type(page).__name__:
                        widget.setCurrentIndex(i)
                        return
            widget = widget.parent()
        from ui.dialogs.confirmation_dialog import show_info
        show_info(
            self, "Go to Prediction",
            "Navigate to the Prediction page to upload portal datasets "
            "and run prediction.",
            "Use the sidebar or navigation to switch pages.",
        )

    # ------------------------------------------------------------------
    # Filter combo helper
    # ------------------------------------------------------------------

    def _create_filter_combo(self, items, default_index=0):
        combo = QComboBox()
        combo.setObjectName("cohortFilterCombo")
        combo.addItems(items)
        combo.blockSignals(True)
        combo.setCurrentIndex(default_index)
        combo.blockSignals(False)
        combo.setCursor(Qt.CursorShape.PointingHandCursor)
        return combo

    # ------------------------------------------------------------------
    # Cell / badge builders
    # ------------------------------------------------------------------

    def _create_risk_score_cell(self, score: float):
        """
        score is a float in 0-100 range.
        QProgressBar.setValue() requires int, so we cast explicitly.
        The label shows '< 1%' for sub-1% scores to avoid showing '0%'
        for students who have a tiny but non-zero risk probability.
        """
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        bar = QProgressBar()
        bar.setValue(max(0, min(100, int(score))))   # int required by Qt
        bar.setTextVisible(False)
        bar.setFixedHeight(8)
        bar.setMaximumWidth(80)

        color = ("#ff5b5b" if score >= 50
                 else "#f5b335" if score >= 25
                 else "#3fb950")
        bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: rgba(255,255,255,0.08);
                border-radius: 4px; border: none;
            }}
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 4px;
            }}
        """)

        # Show '< 1%' for sub-1 scores so the cell never reads '0%'
        if 0 < score < 1:
            label_text = "< 1%"
        else:
            label_text = f"{score:.1f}%" if score % 1 else f"{int(score)}%"

        pct = QLabel(label_text)
        pct.setStyleSheet("color: rgba(255,255,255,0.55); font-size: 12px;")
        pct.setFixedWidth(42)

        layout.addWidget(bar, 1)
        layout.addWidget(pct)
        return container

    def _create_risk_badge(self, level):
        badge = QLabel(f"● {level}")
        badge.setObjectName(
            "cohortRiskBadge"         if level == "High"     else
            "cohortRiskBadgeModerate" if level == "Moderate" else
            "cohortRiskBadgeLow"
        )
        return badge

    # ------------------------------------------------------------------
    # Table row
    # ------------------------------------------------------------------

    def _create_table_row(self, student):
        row = QFrame()
        row.setObjectName("cohortTableRow")
        row.setCursor(Qt.CursorShape.PointingHandCursor)

        grid = QGridLayout(row)
        grid.setContentsMargins(16, 12, 16, 12)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(0)

        for col, (_, stretch) in enumerate(TABLE_COLUMNS):
            grid.setColumnStretch(col, stretch)

        # col 0 — Student ID
        id_lbl = QLabel(student["id"])
        id_lbl.setObjectName("cohortCellId")

        # col 1 — Name (clickable)
        name_lbl = QLabel(student["name"])
        name_lbl.setObjectName("cohortCellName")
        name_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        name_lbl.mousePressEvent = lambda e, s=student: (
            self._open_student_profile(s)
            if e.button() == Qt.MouseButton.LeftButton else None
        )

        # col 2 — College
        college_lbl = QLabel(student["college"])
        college_lbl.setObjectName("cohortCellMuted")

        # col 3 — Risk Score (bar + percentage)
        score_cell = self._create_risk_score_cell(student["score"])

        # col 4 — Risk Level badge
        risk_badge = self._create_risk_badge(student["risk_level"])

        # col 5 — Primary Factor
        factor_lbl = QLabel(student.get("factor", "—"))
        factor_lbl.setObjectName("cohortCellMuted")

        # col 6 — View button
        view_btn = QPushButton("View")
        view_btn.setObjectName("cohortViewButton")
        view_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        view_btn.clicked.connect(
            lambda _, s=student: self._open_student_profile(s)
        )

        grid.addWidget(id_lbl,      0, 0)
        grid.addWidget(name_lbl,    0, 1)
        grid.addWidget(college_lbl, 0, 2)
        grid.addWidget(score_cell,  0, 3)
        grid.addWidget(risk_badge,  0, 4)
        grid.addWidget(factor_lbl,  0, 5)
        grid.addWidget(view_btn,    0, 6, Qt.AlignmentFlag.AlignRight)

        row.mousePressEvent = lambda e, s=student: (
            self._open_student_profile(s)
            if e.button() == Qt.MouseButton.LeftButton else None
        )
        return row

    def _create_table_header(self):
        header = QFrame()
        grid   = QGridLayout(header)
        grid.setContentsMargins(16, 14, 16, 10)
        grid.setHorizontalSpacing(12)

        for col, (title, stretch) in enumerate(TABLE_COLUMNS):
            grid.setColumnStretch(col, stretch)
            if title:
                lbl = QLabel(title)
                lbl.setObjectName("cohortTableHeader")
                grid.addWidget(lbl, 0, col)
        return header

    # ------------------------------------------------------------------
    # Empty state
    # ------------------------------------------------------------------

    def _build_empty_state(self) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 60, 0, 60)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = QLabel("📊")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("font-size: 48px; background: transparent;")

        title = QLabel("No prediction results yet")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            "color: rgba(255,255,255,0.6); font-size: 15px; "
            "font-weight: 600; background: transparent;"
        )

        sub = QLabel(
            "Go to the Prediction page, upload the four portal datasets,\n"
            "merge them, and run the pipeline to score incoming students."
        )
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        sub.setStyleSheet(
            "color: rgba(255,255,255,0.35); font-size: 12px; background: transparent;"
        )

        go_btn = QPushButton("⚡  Go to Prediction")
        go_btn.setFixedWidth(180)
        go_btn.setFixedHeight(38)
        go_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        go_btn.setStyleSheet("""
            QPushButton {
                background-color: #2ecc71; border: none; border-radius: 8px;
                color: white; font-size: 13px; font-weight: 700;
            }
            QPushButton:hover { background-color: #29b765; }
        """)
        go_btn.clicked.connect(self._go_to_prediction_page)

        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addWidget(sub)
        layout.addSpacing(8)
        layout.addWidget(go_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        return host

    # ------------------------------------------------------------------
    # Filters
    # ------------------------------------------------------------------

    def _apply_filters(self):
        if not hasattr(self, "search_input") or not hasattr(self, "college_combo"):
            return
        if not self._table_rows:
            return

        query          = self.search_input.text().strip().lower()
        risk_filter    = self.risk_combo.currentText()
        college_filter = self.college_combo.currentText()

        for row, student in self._table_rows:
            visible = True
            if query:
                haystack = f"{student['name']} {student['id']}".lower()
                visible  = query in haystack
            if visible and risk_filter != "All risk levels":
                visible = student["risk_level"] == risk_filter
            if visible and college_filter != "All colleges":
                visible = student["college"] == college_filter
            row.setVisible(visible)

    # ------------------------------------------------------------------
    # Table population
    # ------------------------------------------------------------------

    def _populate_table(self, students):
        while self.rows_layout.count():
            item   = self.rows_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._table_rows = []

        if not students:
            self.rows_layout.addWidget(self._build_empty_state())
            return

        for student in students:
            row = self._create_table_row(student)
            self._table_rows.append((row, student))
            self.rows_layout.addWidget(row)

    # ------------------------------------------------------------------
    # Prediction results → table
    # ------------------------------------------------------------------

    def _prediction_to_student(self, pred: dict) -> dict:
        # score arrives as 0-100 float from both PredictionEngine and
        # DashboardTermService — keep as float, never cast to int here
        # so sub-1% scores display as '< 1%' instead of '0%'.
        score = round(float(pred.get("score", 0)), 1)
        score = max(0.0, min(100.0, score))

        if score >= 50:
            category = "high_risk"
        elif score >= 25:
            category = "moderate_risk"
        else:
            category = "low_risk"

        try:
            gwa = float(pred.get("gwa") or 0)
        except (TypeError, ValueError):
            gwa = 0.0
        try:
            absences = int(float(pred.get("absences") or 0))
        except (TypeError, ValueError):
            absences = 0

        return {
            "name":         pred.get("name", "—"),
            "id":           str(pred.get("student_id", "—")),
            "college":      pred.get("college", "—"),
            "program":      pred.get("program", "—"),
            "gwa":          gwa,
            "absences":     absences,
            "score":        score,
            "risk_level":   self._RISK_LEVELS.get(category, "Low"),
            "factor":       pred.get("factor", "—"),
            "category":     category,
            "shap_factors": pred.get("shap_factors", []),
        }

    def _on_store_updated(self, key: str):
        if key in ("system_config", "all"):
            self._refresh_term_labels()
        if key in ("predictions", "all"):
            result = DataStore.get().predictions
            if result and getattr(result, "success", False):
                from services.auth_service import AuthService
                role   = (AuthService.current_role() or "").strip().lower()
                source = getattr(result, "_source", "admin")
                if (role == "counselor" and source == "counselor") or \
                (role != "counselor" and source != "counselor"):
                    self._apply_predictions(result)
            else:
                # ── Predictions cleared — reset to empty state ────────────
                self._populate_table([])
                self.explorer_meta.setText(
                    "No prediction results yet  ·  "
                    "Run prediction to populate this table"
                )
                if self._profile_drawer is not None and self._profile_drawer._is_open:
                    self._profile_drawer.close_drawer()

    def _refresh_term_labels(self):
        if hasattr(self, "_ay_sub_lbl"):
            self._ay_sub_lbl.setText(f"Academic Year {SystemConfig.academic_year()}")
        if hasattr(self, "_sem_pill_lbl"):
            self._sem_pill_lbl.setText(f"{SystemConfig.term_label()}  ▾")

    def _apply_predictions(self, result):
        if not result or not result.success:
            return
        students = [self._prediction_to_student(p) for p in result.predictions]
        self._populate_table(students)
        total    = len(students)
        high     = sum(1 for s in students if s["risk_level"] == "High")
        moderate = sum(1 for s in students if s["risk_level"] == "Moderate")
        self.explorer_meta.setText(
            f"{total:,} students scored  ·  "
            f"{high:,} high-risk  ·  {moderate:,} moderate  ·  "
            f"Click a student to view full profile"
        )
        if self._profile_drawer is not None and self._profile_drawer._is_open:
            self._profile_drawer.close_drawer()
        self._apply_filters()

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def setup_ui(self):
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(30, 30, 30, 30)
        self.main_layout.setSpacing(20)

        # ── Header ────────────────────────────────────────────────────
        self.fixed_header_container = QFrame()
        self.fixed_header_container.setObjectName("fixedHeaderContainer")
        fixed_header_layout = QVBoxLayout()
        fixed_header_layout.setContentsMargins(20, 20, 20, 20)
        fixed_header_layout.setSpacing(0)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(15)

        header_text_layout = QVBoxLayout()
        header_text_layout.setSpacing(5)

        header = QLabel("Student Cohort")
        header.setObjectName("header")

        self._ay_sub_lbl = QLabel(f"Academic Year {SystemConfig.academic_year()}")
        self._ay_sub_lbl.setObjectName("subHeader")

        header_text_layout.addWidget(header)
        header_text_layout.addWidget(self._ay_sub_lbl)
        header_layout.addLayout(header_text_layout)
        header_layout.addStretch()

        model_card   = QFrame()
        model_card.setObjectName("cohortModelCard")
        model_layout = QHBoxLayout()
        model_layout.setContentsMargins(20, 15, 20, 15)
        model_layout.setSpacing(12)

        model_status = QLabel("● Model Active")
        model_status.setObjectName("cohortModelStatus")
        opacity_effect   = QGraphicsOpacityEffect(model_status)
        model_status.setGraphicsEffect(opacity_effect)
        status_animation = QPropertyAnimation(opacity_effect, b"opacity")
        status_animation.setDuration(1200)
        status_animation.setStartValue(1.0)
        status_animation.setKeyValueAt(0.5, 0.3)
        status_animation.setEndValue(1.0)
        status_animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        status_animation.setLoopCount(-1)
        status_animation.start()

        self._sem_pill_lbl = QLabel(f"{SystemConfig.term_label()}  ▾")
        self._sem_pill_lbl.setObjectName("cohortSemesterPill")

        run_button = QPushButton("Go to Prediction →")
        run_button.setObjectName("runButton")
        run_button.setCursor(Qt.CursorShape.PointingHandCursor)
        run_button.setIcon(QIcon("assets/icons/play.svg"))
        run_button.clicked.connect(self._go_to_prediction_page)
        run_button.setFixedWidth(155)
        from services.auth_service import AuthService
        if (AuthService.current_role() or "").strip().lower() == "counselor":
            run_button.hide()

        model_layout.addWidget(model_status)
        model_layout.addWidget(self._sem_pill_lbl)
        model_layout.addWidget(run_button)
        model_card.setLayout(model_layout)
        header_layout.addWidget(model_card)

        fixed_header_layout.addLayout(header_layout)
        self.fixed_header_container.setLayout(fixed_header_layout)
        self.main_layout.addWidget(self.fixed_header_container)

        # ── Explorer toolbar ──────────────────────────────────────────
        explorer_bar = QHBoxLayout()
        explorer_bar.setSpacing(16)

        explorer_left = QVBoxLayout()
        explorer_left.setSpacing(4)

        explorer_title = QLabel("Student Cohort Explorer")
        explorer_title.setObjectName("cohortExplorerTitle")

        self.explorer_meta = QLabel(
            "No prediction results yet  ·  "
            "Run prediction to populate this table"
        )
        self.explorer_meta.setObjectName("cohortExplorerMeta")

        explorer_left.addWidget(explorer_title)
        explorer_left.addWidget(self.explorer_meta)
        explorer_bar.addLayout(explorer_left, 1)

        filters_layout = QHBoxLayout()
        filters_layout.setSpacing(10)

        search_wrap  = QFrame()
        search_wrap.setFixedWidth(260)
        search_inner = QHBoxLayout(search_wrap)
        search_inner.setContentsMargins(12, 0, 0, 0)

        search_icon = QLabel("🔍")
        search_icon.setStyleSheet(
            "color: rgba(255,255,255,0.35); font-size: 12px;"
        )

        self.search_input = QLineEdit()
        self.search_input.setObjectName("cohortSearchInput")
        self.search_input.setPlaceholderText("Search name or ID...")
        self.search_input.textChanged.connect(self._apply_filters)

        search_inner.addWidget(search_icon)
        search_inner.addWidget(self.search_input, 1)

        self.risk_combo = self._create_filter_combo(
            ["All risk levels", "High", "Moderate", "Low"]
        )
        self.college_combo = self._create_filter_combo(
            ["All colleges", "COTE","CTHM"]
        )

        self.risk_combo.currentIndexChanged.connect(self._apply_filters)
        self.college_combo.currentIndexChanged.connect(self._apply_filters)

        filters_layout.addWidget(search_wrap)
        filters_layout.addWidget(self.risk_combo)
        filters_layout.addWidget(self.college_combo)

        explorer_bar.addLayout(filters_layout)
        self.main_layout.addLayout(explorer_bar)

        # ── Data table ────────────────────────────────────────────────
        table_container = QFrame()
        table_container.setObjectName("cohortTableContainer")
        table_layout    = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 8)
        table_layout.setSpacing(0)

        table_layout.addWidget(self._create_table_header())

        self.rows_layout = QVBoxLayout()
        self.rows_layout.setSpacing(0)

        self._populate_table([])

        rows_host = QWidget()
        rows_host.setLayout(self.rows_layout)
        table_layout.addWidget(rows_host)

        self.main_layout.addWidget(table_container, 1)
        self.setLayout(self.main_layout)
        self.init_prediction()