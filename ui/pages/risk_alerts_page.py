from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QButtonGroup,
    QSizePolicy,
    QGraphicsOpacityEffect,
)
from PyQt6.QtCore import QTimer, Qt, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QIcon

from .student_profile_drawer import StudentProfileDrawer
from ui.components.loading_overlay import LoadingOverlay
from ui.mixins.prediction_mixin import PredictionMixin
from services.data_store import DataStore


# =====================================
# SAMPLE DATA (used until predictions run)
# =====================================

ALERT_STUDENTS = [
    {
        "name": "Lea Torres",
        "id": "2024-10008",
        "college": "CTE",
        "program": "BSIE",
        "factor": "GWA drop (sem 1)",
        "score": 75,
        "category": "high_risk",
        "gwa": 3.45,
        "absences": 13,
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
        "factor": "Absences > 20%",
        "score": 77,
        "category": "high_risk",
    },
    {
        "name": "James Reyes",
        "id": "2024-10019",
        "college": "CITE",
        "program": "BSIT",
        "factor": "Absences > 20%",
        "score": 83,
        "category": "newly_flagged",
    },
    {
        "name": "Ana Bautista",
        "id": "2024-10024",
        "college": "COED",
        "program": "BSED",
        "factor": "No org membership",
        "score": 97,
        "category": "newly_flagged",
    },
    {
        "name": "Mark Dela Cruz",
        "id": "2024-10031",
        "college": "CON",
        "program": "BSN",
        "factor": "Failed ≥ 2 subjects",
        "score": 72,
        "category": "intervened",
    },
    {
        "name": "Jane Smith",
        "id": "2024-10035",
        "college": "CAS",
        "program": "BSSW",
        "factor": "Low psych score",
        "score": 68,
        "category": "intervened",
    },
]

TAB_FILTERS = [
    ("all",           "All Alerts",     187),
    ("high_risk",     "High Risk",      187),
    ("newly_flagged", "Newly Flagged",   43),
    ("intervened",    "Intervened",      22),
]


class RiskAlertsPage(PredictionMixin, QWidget):
    """Risk Alerts page — high-risk student monitoring and intervention."""

    def __init__(self):
        super().__init__()
        self._tab_buttons   = {}
        self._alert_cards   = []
        self._profile_drawer = None
        self.setup_ui()

        # Listen for prediction results
        DataStore.get().add_listener(self._on_store_updated)

        # If predictions already exist when page is created, apply them
        result = DataStore.get().predictions
        if result and result.success:
            self._apply_predictions(result)

    # ------------------------------------------------------------------
    # DataStore listener
    # ------------------------------------------------------------------

    def _on_store_updated(self, key: str):
        if key == "predictions":
            result = DataStore.get().predictions
            if result and result.success:
                self._apply_predictions(result)

    # ------------------------------------------------------------------
    # Prediction results → update UI
    # ------------------------------------------------------------------

    def _apply_predictions(self, result):
        """Replace sample cards with real prediction data."""

        # ── Clear existing cards ──────────────────────────────────────
        for card, _ in self._alert_cards:
            card.setParent(None)
            card.deleteLater()
        self._alert_cards.clear()

        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # ── Build student list from predictions ───────────────────────
        students = []
        for pred in result.predictions:
            if pred["category"] in ("high_risk", "moderate_risk"):
                students.append({
                    "name":         pred["name"],
                    "id":           pred["student_id"],
                    "college":      pred["college"],
                    "program":      pred["program"],
                    "factor":       pred.get("factor", "—"),
                    "score":        pred["score"],
                    "category":     pred["category"],
                    "gwa":          pred.get("gwa", "—"),
                    "absences":     pred.get("absences", "—"),
                    "shap_factors": pred.get("shap_factors", []),
                })

        # Sort by score descending (highest risk first)
        students.sort(key=lambda s: s["score"], reverse=True)

        # ── Update tab counts ─────────────────────────────────────────
        s           = result.summary
        high_count  = s.high_risk
        total_count = s.high_risk + s.moderate_risk

        tab_labels = {
            "all":           f"All Alerts ({total_count})",
            "high_risk":     f"High Risk ({high_count})",
            "newly_flagged": f"Newly Flagged ({high_count})",
            "intervened":    f"Intervened (0)",
        }
        for tid, label in tab_labels.items():
            if tid in self._tab_buttons:
                self._tab_buttons[tid].setText(label)
                self._tab_buttons[tid].adjustSize()
                self._tab_buttons[tid].setFixedWidth(
                    self._tab_buttons[tid].sizeHint().width()
                )

        # ── Update warning banner ─────────────────────────────────────
        self._banner_text.setText(
            f"{high_count} students are flagged as high-risk this semester. "
            "Early intervention within the first 6 weeks significantly "
            "improves retention outcomes."
        )

        # ── Rebuild cards ─────────────────────────────────────────────
        for student in students:
            card = self.create_student_alert_card(student)
            self._alert_cards.append((card, student))
            self.cards_layout.addWidget(card)

        self.cards_layout.addStretch()

        # ── Re-apply current tab filter ───────────────────────────────
        current_tab = next(
            (tid for tid, btn in self._tab_buttons.items()
             if btn.isChecked()),
            "all",
        )
        self._filter_alerts(current_tab)

    # ------------------------------------------------------------------
    # Show / hide events
    # ------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self._ensure_profile_drawer()

    def hideEvent(self, event):
        if self._profile_drawer is not None and self._profile_drawer._is_open:
            self._profile_drawer.hide()
            self._profile_drawer._is_open = False
        super().hideEvent(event)

    # ------------------------------------------------------------------
    # Profile drawer helpers
    # ------------------------------------------------------------------

    def _find_drawer_host(self):
        widget = self.parent()
        while widget:
            parent = widget.parent()
            if (parent is not None and
                    parent.metaObject().className() == "QStackedWidget"):
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
            self._profile_drawer.open_drawer(student)

    # ------------------------------------------------------------------
    # Card builder
    # ------------------------------------------------------------------

    def create_student_alert_card(self, student):
        """Build a single student risk alert card."""
        card = QFrame()
        card.setObjectName("studentAlertCard")
        card.setProperty("category", student["category"])

        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(16)

        # Left: student info
        info_layout = QVBoxLayout()
        info_layout.setSpacing(6)

        name_row = QHBoxLayout()
        name_row.setSpacing(10)

        status_dot = QLabel("●")
        status_dot.setObjectName("riskStatusDot")
        status_dot.setFixedWidth(14)

        name_label = QLabel(student["name"])
        name_label.setObjectName("studentAlertName")

        risk_badge = QLabel(
            student.get("label", "High Risk")
            if "label" in student
            else "High Risk"
        )
        risk_badge.setObjectName("highRiskBadge")

        name_row.addWidget(status_dot)
        name_row.addWidget(name_label)
        name_row.addWidget(risk_badge)
        name_row.addStretch()

        meta = QLabel(
            f"{student['id']} · {student['college']} · {student['program']}"
        )
        meta.setObjectName("studentAlertMeta")

        factor = QLabel()
        factor.setObjectName("studentAlertFactor")
        factor.setText(
            f'Primary factor: <span style="color:#6eb5ff;">'
            f'{student["factor"]}</span>'
            f' · Score: <span style="color:rgba(255,255,255,0.45);">'
            f'{student["score"]}%</span>'
        )
        factor.setTextFormat(Qt.TextFormat.RichText)

        info_layout.addLayout(name_row)
        info_layout.addWidget(meta)
        info_layout.addWidget(factor)

        # Right: action buttons
        actions_layout = QVBoxLayout()
        actions_layout.setSpacing(8)
        actions_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        notify_btn = QPushButton("Notify Advisor")
        notify_btn.setObjectName("alertActionButton")
        notify_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        profile_btn = QPushButton("View Profile")
        profile_btn.setObjectName("alertActionButton")
        profile_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        profile_btn.clicked.connect(
            lambda checked=False, s=student: self._open_student_profile(s)
        )

        actions_layout.addWidget(notify_btn)
        actions_layout.addWidget(profile_btn)

        card_layout.addLayout(info_layout, 1)
        card_layout.addLayout(actions_layout, 0)

        return card

    # ------------------------------------------------------------------
    # Warning banner
    # ------------------------------------------------------------------

    def create_warning_banner(self):
        banner = QFrame()
        banner.setObjectName("riskAlertBanner")

        layout = QHBoxLayout(banner)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        icon = QLabel("⚠")
        icon.setStyleSheet("color: #f5b335; font-size: 16px;")
        icon.setFixedWidth(20)

        # Store ref so _apply_predictions can update the count
        self._banner_text = QLabel(
            "187 students are flagged as high-risk this semester. "
            "Early intervention within the first 6 weeks significantly "
            "improves retention outcomes."
        )
        self._banner_text.setObjectName("riskAlertBannerText")
        self._banner_text.setWordWrap(True)

        layout.addWidget(icon)
        layout.addWidget(self._banner_text, 1)

        return banner

    # ------------------------------------------------------------------
    # Tab bar
    # ------------------------------------------------------------------

    def create_tab_bar(self):
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(28)

        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)

        for tab_id, label, count in TAB_FILTERS:
            btn = QPushButton(f"{label} ({count})")
            btn.setObjectName("riskAlertTab")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setSizePolicy(
                QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Fixed,
            )
            btn.clicked.connect(
                lambda checked, tid=tab_id: self._filter_alerts(tid)
            )
            self._tab_group.addButton(btn)
            self._tab_buttons[tab_id] = btn
            btn.adjustSize()
            btn.setFixedWidth(btn.sizeHint().width())
            layout.addWidget(btn)

        layout.addStretch()
        self._tab_buttons["all"].setChecked(True)

        return container

    # ------------------------------------------------------------------
    # Filter
    # ------------------------------------------------------------------

    def _filter_alerts(self, tab_id):
        for card, student in self._alert_cards:
            if tab_id == "all":
                visible = True
            elif tab_id == "high_risk":
                visible = student["category"] in ("high_risk", "newly_flagged")
            else:
                visible = student["category"] == tab_id
            card.setVisible(visible)

    # ------------------------------------------------------------------
    # Setup UI
    # ------------------------------------------------------------------

    def setup_ui(self):
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(30, 30, 30, 30)
        self.main_layout.setSpacing(20)

        # ── Fixed header ──────────────────────────────────────────────
        self.fixed_header_container = QFrame()
        self.fixed_header_container.setObjectName("fixedHeaderContainer")
        fixed_header_layout = QVBoxLayout()
        fixed_header_layout.setContentsMargins(20, 20, 20, 20)
        fixed_header_layout.setSpacing(0)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(15)

        header_text_layout = QVBoxLayout()
        header_text_layout.setSpacing(5)

        header = QLabel("Risk Alerts")
        header.setObjectName("header")

        subheader = QLabel("Academic Year 2024–2025")
        subheader.setObjectName("subHeader")

        header_text_layout.addWidget(header)
        header_text_layout.addWidget(subheader)

        header_layout.addLayout(header_text_layout)
        header_layout.addStretch()

        # Model status card
        model_card = QFrame()
        model_card.setObjectName("modelCard")

        model_layout = QHBoxLayout()
        model_layout.setContentsMargins(20, 15, 20, 15)
        model_layout.setSpacing(12)

        self.model_status = QLabel("● Model Active")
        self.model_status.setObjectName("modelStatus")

        opacity_effect = QGraphicsOpacityEffect(self.model_status)
        self.model_status.setGraphicsEffect(opacity_effect)

        status_animation = QPropertyAnimation(opacity_effect, b"opacity")
        status_animation.setDuration(1200)
        status_animation.setStartValue(1.0)
        status_animation.setKeyValueAt(0.5, 0.3)
        status_animation.setEndValue(1.0)
        status_animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        status_animation.setLoopCount(-1)
        status_animation.start()
        self._status_anim = status_animation   # keep reference

        semester_pill = QLabel("1st Semester 2024–25")
        semester_pill.setObjectName("semesterPill")

        run_button = QPushButton("Run Prediction")
        run_button.setObjectName("runButton")
        run_button.setCursor(Qt.CursorShape.PointingHandCursor)
        run_button.setIcon(QIcon("assets/icons/play.svg"))
        run_button.clicked.connect(self.on_run_prediction)
        run_button.setFixedWidth(130)

        model_layout.addWidget(self.model_status)
        model_layout.addWidget(semester_pill)
        model_layout.addWidget(run_button)

        model_card.setLayout(model_layout)
        header_layout.addWidget(model_card)

        fixed_header_layout.addLayout(header_layout)
        self.fixed_header_container.setLayout(fixed_header_layout)
        self.main_layout.addWidget(self.fixed_header_container)

        # ── Warning banner ────────────────────────────────────────────
        self.main_layout.addWidget(self.create_warning_banner())

        # ── Tab bar ───────────────────────────────────────────────────
        self.main_layout.addWidget(self.create_tab_bar())

        # ── Student alert cards ───────────────────────────────────────
        self.cards_layout = QVBoxLayout()
        self.cards_layout.setSpacing(12)

        for student in ALERT_STUDENTS:
            card = self.create_student_alert_card(student)
            self._alert_cards.append((card, student))
            self.cards_layout.addWidget(card)

        self.cards_layout.addStretch()
        self.main_layout.addLayout(self.cards_layout)
        self.main_layout.addStretch()

        self.setLayout(self.main_layout)
        self.init_prediction()