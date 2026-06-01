from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QLineEdit,
    QComboBox,
    QProgressBar,
    QGraphicsOpacityEffect,
    QGridLayout,
)
from PyQt6.QtCore import QTimer, Qt, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QIcon

from .student_profile_drawer import StudentProfileDrawer
from ui.components.loading_overlay import LoadingOverlay
from ui.mixins.prediction_mixin import PredictionMixin

COHORT_STUDENTS = [
    {
        "name": "Lea Torres",
        "id": "2024-10008",
        "college": "CTE",
        "program": "BSIE",
        "gwa": 3.45,
        "absences": 13,
        "score": 75,
        "risk_level": "High",
        "factor": "GWA drop (sem 1)",
        "category": "high_risk",
        "failed_subjects": 2,
        "referrals": 2,
        "shap_factors": [
            ("GWA drop (sem 1)", 39),
            ("Absences > 20%", 20),
            ("No org membership", 13),
            ("Working student", 6),
            ("Failed ≥ 2 subjects", 11),
            ("Low psych score", 10),
        ],
    },
    {
        "name": "Cathy Ramos",
        "id": "2024-10012",
        "college": "CBAA",
        "program": "BSBA",
        "gwa": 3.12,
        "absences": 30,
        "score": 77,
        "risk_level": "High",
        "factor": "Absences > 20%",
        "category": "high_risk",
    },
    {
        "name": "James Reyes",
        "id": "2024-10019",
        "college": "CITE",
        "program": "BSIT",
        "gwa": 2.98,
        "absences": 21,
        "score": 83,
        "risk_level": "High",
        "factor": "Absences > 20%",
        "category": "high_risk",
    },
    {
        "name": "Ana Bautista",
        "id": "2024-10024",
        "college": "COED",
        "program": "BSED",
        "gwa": 2.85,
        "absences": 8,
        "score": 97,
        "risk_level": "High",
        "factor": "No org membership",
        "category": "high_risk",
    },
    {
        "name": "Mark Dela Cruz",
        "id": "2024-10031",
        "college": "CON",
        "program": "BSN",
        "gwa": 3.28,
        "absences": 18,
        "score": 72,
        "risk_level": "High",
        "factor": "Failed ≥ 2 subjects",
        "category": "high_risk",
    },
    {
        "name": "Jane Smith",
        "id": "2024-10035",
        "college": "CAS",
        "program": "BSSW",
        "gwa": 3.05,
        "absences": 15,
        "score": 68,
        "risk_level": "High",
        "factor": "Low psych score",
        "category": "high_risk",
    },
    {
        "name": "Rico Mendoza",
        "id": "2024-10041",
        "college": "CTE",
        "program": "BSME",
        "gwa": 1.69,
        "absences": 4,
        "score": 22,
        "risk_level": "Low",
        "factor": "—",
        "category": "low_risk",
    },
    {
        "name": "Sofia Lim",
        "id": "2024-10048",
        "college": "CBAA",
        "program": "BSA",
        "gwa": 1.82,
        "absences": 6,
        "score": 31,
        "risk_level": "Low",
        "factor": "—",
        "category": "low_risk",
    },
    {
        "name": "Noah Villanueva",
        "id": "2024-10052",
        "college": "COED",
        "program": "BEED",
        "gwa": 2.15,
        "absences": 9,
        "score": 48,
        "risk_level": "Moderate",
        "factor": "Working student",
        "category": "moderate_risk",
    },
    {
        "name": "Ella Cruz",
        "id": "2024-10059",
        "college": "CITE",
        "program": "BSCpE",
        "gwa": 2.42,
        "absences": 11,
        "score": 55,
        "risk_level": "Moderate",
        "factor": "Financial aid lapse",
        "category": "moderate_risk",
    },
]

TABLE_COLUMNS = [
    ("STUDENT ID", 1),
    ("NAME", 2),
    ("COLLEGE", 1),
    ("GWA", 1),
    ("ABSENCES", 1),
    ("RISK SCORE", 2),
    ("RISK LEVEL", 1),
    ("PRIMARY FACTOR", 2),
    ("", 1),
]


class StudentCohortPage(PredictionMixin, QWidget):
    """Student Cohort Explorer — searchable cohort table with risk metrics."""

    def __init__(self):
        super().__init__()
        self._table_rows = []
        self._profile_drawer = None
        self.setup_ui()
        #self._apply_page_styles()
        self._apply_filters()
        self.overlay = LoadingOverlay(self)

    def showEvent(self, event):
        super().showEvent(event)
        self._ensure_profile_drawer()

    def hideEvent(self, event):
        if self._profile_drawer is not None and self._profile_drawer._is_open:
            self._profile_drawer.hide()
            self._profile_drawer._is_open = False
        super().hideEvent(event)

    def _find_drawer_host(self):
        widget = self.parent()
        while widget:
            parent = widget.parent()
            if parent is not None and parent.metaObject().className() == "QStackedWidget":
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


    def _create_filter_combo(self, items, default_index=0):
        combo = QComboBox()
        combo.setObjectName("cohortFilterCombo")
        combo.addItems(items)
        combo.blockSignals(True)
        combo.setCurrentIndex(default_index)
        combo.blockSignals(False)
        combo.setCursor(Qt.CursorShape.PointingHandCursor)
        return combo

    def _create_risk_score_cell(self, score):
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        bar = QProgressBar()
        bar.setValue(score)
        bar.setTextVisible(False)
        bar.setFixedHeight(8)
        bar.setMaximumWidth(80)

        color = "#ff5b5b" if score >= 60 else "#f5b335" if score >= 40 else "#3fb950"
        bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: rgba(255, 255, 255, 0.08);
                border-radius: 4px;
                border: none;
            }}
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 4px;
            }}
        """)

        pct = QLabel(f"{score}%")
        pct.setStyleSheet(
            "color: rgba(255,255,255,0.55); font-size: 12px;"
        )
        pct.setFixedWidth(36)

        layout.addWidget(bar, 1)
        layout.addWidget(pct)
        return container

    def _create_risk_badge(self, level):
        badge = QLabel(f"● {level}")
        if level == "High":
            badge.setObjectName("cohortRiskBadge")
        elif level == "Moderate":
            badge.setObjectName("cohortRiskBadgeModerate")
        else:
            badge.setObjectName("cohortRiskBadgeLow")
        return badge

    def _create_table_row(self, student):
        row = QFrame()
        row.setObjectName("cohortTableRow")
        row.setCursor(Qt.CursorShape.PointingHandCursor)

        grid = QGridLayout(row)
        grid.setContentsMargins(16, 12, 16, 12)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(0)

        col = 0
        for _, stretch in TABLE_COLUMNS:
            grid.setColumnStretch(col, stretch)
            col += 1

        id_lbl = QLabel(student["id"])
        id_lbl.setObjectName("cohortCellId")

        name_lbl = QLabel(student["name"])
        name_lbl.setObjectName("cohortCellName")
        name_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        name_lbl.mousePressEvent = lambda event, s=student: (
            self._open_student_profile(s)
            if event.button() == Qt.MouseButton.LeftButton
            else None
        )

        college_lbl = QLabel(student["college"])
        college_lbl.setObjectName("cohortCellMuted")

        gwa_lbl = QLabel(f"{student['gwa']:.2f}")
        gwa_lbl.setObjectName(
            "cohortGwaRisk" if student["gwa"] >= 2.5 else "cohortGwaGood"
        )

        abs_lbl = QLabel(str(student["absences"]))
        abs_lbl.setObjectName(
            "cohortAbsencesRisk" if student["absences"] >= 10 else "cohortCellMuted"
        )

        factor_lbl = QLabel(student["factor"])
        factor_lbl.setObjectName("cohortCellMuted")

        view_btn = QPushButton("View")
        view_btn.setObjectName("cohortViewButton")
        view_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        view_btn.clicked.connect(
            lambda checked=False, s=student: self._open_student_profile(s)
        )

        grid.addWidget(id_lbl, 0, 0)
        grid.addWidget(name_lbl, 0, 1)
        grid.addWidget(college_lbl, 0, 2)
        grid.addWidget(gwa_lbl, 0, 3)
        grid.addWidget(abs_lbl, 0, 4)
        grid.addWidget(self._create_risk_score_cell(student["score"]), 0, 5)
        grid.addWidget(self._create_risk_badge(student["risk_level"]), 0, 6)
        grid.addWidget(factor_lbl, 0, 7)
        grid.addWidget(view_btn, 0, 8, Qt.AlignmentFlag.AlignRight)

        row.mousePressEvent = lambda event, s=student: (
            self._open_student_profile(s)
            if event.button() == Qt.MouseButton.LeftButton
            else None
        )

        return row

    def _create_table_header(self):
        header = QFrame()
        grid = QGridLayout(header)
        grid.setContentsMargins(16, 14, 16, 10)
        grid.setHorizontalSpacing(12)

        for col, (title, stretch) in enumerate(TABLE_COLUMNS):
            grid.setColumnStretch(col, stretch)
            if title:
                lbl = QLabel(title)
                lbl.setObjectName("cohortTableHeader")
                grid.addWidget(lbl, 0, col)

        return header

    def _apply_filters(self):
        if not hasattr(self, "search_input") or not hasattr(self, "college_combo"):
            return
        if not self._table_rows:
            return

        query = self.search_input.text().strip().lower()
        risk_filter = self.risk_combo.currentText()
        college_filter = self.college_combo.currentText()

        for row, student in self._table_rows:
            visible = True

            if query:
                haystack = f"{student['name']} {student['id']}".lower()
                visible = query in haystack

            if visible and risk_filter != "All risk levels":
                visible = student["risk_level"] == risk_filter

            if visible and college_filter != "All colleges":
                visible = student["college"] == college_filter

            row.setVisible(visible)

    def setup_ui(self):
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(30, 30, 30, 30)
        self.main_layout.setSpacing(20)

        # =====================================
        # FIXED HEADER
        # =====================================

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

        subheader = QLabel("Academic Year 2024–2025")
        subheader.setObjectName("subHeader")

        header_text_layout.addWidget(header)
        header_text_layout.addWidget(subheader)

        header_layout.addLayout(header_text_layout)
        header_layout.addStretch()

        model_card = QFrame()
        model_card.setObjectName("cohortModelCard")

        model_layout = QHBoxLayout()
        model_layout.setContentsMargins(20, 15, 20, 15)
        model_layout.setSpacing(12)

        model_status = QLabel("● Model Active")
        model_status.setObjectName("cohortModelStatus")

        opacity_effect = QGraphicsOpacityEffect(model_status)
        model_status.setGraphicsEffect(opacity_effect)

        status_animation = QPropertyAnimation(opacity_effect, b"opacity")
        status_animation.setDuration(1200)
        status_animation.setStartValue(1.0)
        status_animation.setKeyValueAt(0.5, 0.3)
        status_animation.setEndValue(1.0)
        status_animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        status_animation.setLoopCount(-1)
        status_animation.start()

        semester_pill = QLabel("1st Semester 2024–25  ▾")
        semester_pill.setObjectName("cohortSemesterPill")

        run_button = QPushButton("Run Prediction")
        run_button.setObjectName("runButton")
        run_button.setCursor(Qt.CursorShape.PointingHandCursor)
        run_button.setIcon(QIcon("assets/icons/play.svg"))
        run_button.clicked.connect(self.on_run_prediction)     
        run_button.setFixedWidth(130)

        model_layout.addWidget(model_status)
        model_layout.addWidget(semester_pill)
        model_layout.addWidget(run_button)

        model_card.setLayout(model_layout)
        header_layout.addWidget(model_card)

        fixed_header_layout.addLayout(header_layout)
        self.fixed_header_container.setLayout(fixed_header_layout)
        self.main_layout.addWidget(self.fixed_header_container)

        # =====================================
        # EXPLORER TOOLBAR
        # =====================================

        explorer_bar = QHBoxLayout()
        explorer_bar.setSpacing(16)

        explorer_left = QVBoxLayout()
        explorer_left.setSpacing(4)

        explorer_title = QLabel("Student Cohort Explorer")
        explorer_title.setObjectName("cohortExplorerTitle")

        explorer_meta = QLabel(
            "First-year students · 1,248 enrolled · "
            "Click a student to view full profile"
        )
        explorer_meta.setObjectName("cohortExplorerMeta")

        explorer_left.addWidget(explorer_title)
        explorer_left.addWidget(explorer_meta)

        explorer_bar.addLayout(explorer_left, 1)

        filters_layout = QHBoxLayout()
        filters_layout.setSpacing(10)

        search_wrap = QFrame()
        search_wrap.setFixedWidth(260)
        search_inner = QHBoxLayout(search_wrap)
        search_inner.setContentsMargins(12, 0, 0, 0)

        search_icon = QLabel("🔍")
        search_icon.setStyleSheet("color: rgba(255,255,255,0.35); font-size: 12px;")

        self.search_input = QLineEdit()
        self.search_input.setObjectName("cohortSearchInput")
        self.search_input.setPlaceholderText("Search name or ID...")
        self.search_input.textChanged.connect(self._apply_filters)

        search_inner.addWidget(search_icon)
        search_inner.addWidget(self.search_input, 1)

        self.risk_combo = self._create_filter_combo(
            ["All risk levels", "High", "Moderate", "Low"],
            default_index=0,
        )

        self.college_combo = self._create_filter_combo(
            ["All colleges", "CTE", "CBAA", "CITE", "COED", "CON", "CAS"],
            default_index=0,
        )

        self.risk_combo.blockSignals(True)
        high_index = self.risk_combo.findText("High")
        if high_index >= 0:
            self.risk_combo.setCurrentIndex(high_index)
        self.risk_combo.blockSignals(False)

        self.risk_combo.currentIndexChanged.connect(self._apply_filters)
        self.college_combo.currentIndexChanged.connect(self._apply_filters)

        filters_layout.addWidget(search_wrap)
        filters_layout.addWidget(self.risk_combo)
        filters_layout.addWidget(self.college_combo)

        explorer_bar.addLayout(filters_layout)
        self.main_layout.addLayout(explorer_bar)

        # =====================================
        # DATA TABLE
        # =====================================

        table_container = QFrame()
        table_container.setObjectName("cohortTableContainer")
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 8)
        table_layout.setSpacing(0)

        table_layout.addWidget(self._create_table_header())

        self.rows_layout = QVBoxLayout()
        self.rows_layout.setSpacing(0)

        for student in COHORT_STUDENTS:
            row = self._create_table_row(student)
            self._table_rows.append((row, student))
            self.rows_layout.addWidget(row)

        rows_host = QWidget()
        rows_host.setLayout(self.rows_layout)
        table_layout.addWidget(rows_host)

        self.main_layout.addWidget(table_container, 1)

        self.setLayout(self.main_layout)
        self.init_prediction()