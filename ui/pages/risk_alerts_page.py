from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QButtonGroup, QSizePolicy, QGraphicsOpacityEffect,
)
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QIcon

from .student_profile_drawer import StudentProfileDrawer
from ui.components.loading_overlay import LoadingOverlay
from ui.mixins.prediction_mixin import PredictionMixin
from services.data_store import DataStore
from services.system_config import SystemConfig


TAB_FILTERS = [
    ("all",           "All Alerts",   0),
    ("high_risk",     "High Risk",    0),
    ("moderate_risk", "Moderate Risk",0),
]


class RiskAlertsPage(PredictionMixin, QWidget):
    def __init__(self):
        super().__init__()
        self._tab_buttons:   dict  = {}
        self._alert_cards:   list  = []
        self._profile_drawer       = None
        self._has_predictions      = False

        self.setup_ui()
        DataStore.get().add_listener(self._on_store_updated)

        result = DataStore.get().predictions
        if result and result.success:
            self._apply_predictions(result)

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
                self._has_predictions = False
                while self.cards_layout.count():
                    item = self.cards_layout.takeAt(0)
                    if item.widget():
                        item.widget().deleteLater()
                self._alert_cards.clear()
                self._empty_state.setVisible(True)
                self._live_content.setVisible(False)
                self._banner_text.setText(
                    "Run a prediction to populate risk alerts.")
                for tid, btn in self._tab_buttons.items():
                    labels = {
                        "all": "All Alerts",
                        "high_risk": "High Risk",
                        "moderate_risk": "Moderate Risk",
                    }
                    btn.setText(f"{labels[tid]} (0)")


    def _apply_predictions(self, result):
        self._has_predictions = True
        self._empty_state.setVisible(False)
        self._live_content.setVisible(True)

        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._alert_cards.clear()

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
                    "label":        pred.get("label", "At Risk"),
                    "shap_factors": pred.get("shap_factors", []),
                    # pass through all remaining meta for profile drawer
                    **{k: v for k, v in pred.items()
                       if k not in ("name", "student_id", "college", "program",
                                    "factor", "score", "category", "label",
                                    "shap_factors")},
                })

        students.sort(key=lambda s: s["score"], reverse=True)

        s            = result.summary
        high_count   = s.high_risk
        mod_count    = s.moderate_risk
        total_count  = high_count + mod_count

        counts = {
            "all":           total_count,
            "high_risk":     high_count,
            "moderate_risk": mod_count,
        }
        labels = {
            "all":           "All Alerts",
            "high_risk":     "High Risk",
            "moderate_risk": "Moderate Risk",
        }
        for tid, btn in self._tab_buttons.items():
            text = f"{labels[tid]} ({counts[tid]})"
            btn.setText(text)
            btn.adjustSize()
            btn.setFixedWidth(btn.sizeHint().width() + 16)

        self._banner_text.setText(
            f"{high_count} students flagged as high-risk  ·  "
            f"{mod_count} moderate-risk  ·  "
            f"{total_count} total requiring attention this semester."
        )

        for student in students:
            card = self._build_alert_card(student)
            self._alert_cards.append((card, student))
            self.cards_layout.addWidget(card)

        self.cards_layout.addStretch()

        active = next(
            (tid for tid, btn in self._tab_buttons.items() if btn.isChecked()),
            "all",
        )
        self._filter_alerts(active)

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

        try:
            from services.activity_logger import ActivityLogger
            from services.data_store import DataStore
            _conn = DataStore.get().db_conn
            if _conn:
                ActivityLogger.log_view_student(
                    _conn,
                    student_id   = str(student.get("id", "")),
                    student_name = student.get("name", ""),
                )
                _conn.commit()
        except Exception as _e:
            print(f"[RiskAlertsPage] View log error: {_e}")

    def _build_alert_card(self, student: dict) -> QFrame:
        card = QFrame()
        card.setObjectName("studentAlertCard")
        card.setProperty("category", student["category"])

        layout = QHBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(16)

        info = QVBoxLayout()
        info.setSpacing(6)

        name_row = QHBoxLayout()
        name_row.setSpacing(10)

        dot = QLabel("●")
        dot.setObjectName("riskStatusDot")
        dot.setFixedWidth(14)

        name_lbl = QLabel(student.get("id", student.get("name", "—")))
        name_lbl.setObjectName("studentAlertName")

        badge = QLabel(student.get("label", "At Risk"))
        badge.setObjectName(
            "highRiskBadge" if student["category"] == "high_risk"
            else "moderateRiskBadge"
        )

        name_row.addWidget(dot)
        name_row.addWidget(name_lbl)
        name_row.addWidget(badge)
        name_row.addStretch()

        meta = QLabel(
            f"{student['college']}  ·  {student['program']}"
        )
        meta.setObjectName("studentAlertMeta")

        shap = student.get("shap_factors", [])
        if shap and len(shap[0]) == 4:
            _, human_label, formatted_value, _ = shap[0]
            factor_text = (
                f'Primary factor: '
                f'<span style="color:#6eb5ff;">{human_label}</span>'
                f'  <span style="color:rgba(255,255,255,0.38);">'
                f'{formatted_value}</span>'
                f'  ·  Score: '
                f'<span style="color:rgba(255,255,255,0.45);">'
                f'{student["score"]}%</span>'
            )
        else:
            factor_text = (
                f'Primary factor: '
                f'<span style="color:#6eb5ff;">{student["factor"]}</span>'
                f'  ·  Score: '
                f'<span style="color:rgba(255,255,255,0.45);">'
                f'{student["score"]}%</span>'
            )

        factor_lbl = QLabel(factor_text)
        factor_lbl.setObjectName("studentAlertFactor")
        factor_lbl.setTextFormat(Qt.TextFormat.RichText)

        info.addLayout(name_row)
        info.addWidget(meta)
        info.addWidget(factor_lbl)

        actions = QVBoxLayout()
        actions.setSpacing(8)
        actions.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        profile_btn = QPushButton("View Profile")
        profile_btn.setObjectName("alertActionButton")
        profile_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        profile_btn.clicked.connect(
            lambda _, s=student: self._open_student_profile(s)
        )

        actions.addWidget(profile_btn)

        layout.addLayout(info, 1)
        layout.addLayout(actions, 0)
        return card

    def _build_warning_banner(self) -> QFrame:
        banner = QFrame()
        banner.setObjectName("riskAlertBanner")

        row = QHBoxLayout(banner)
        row.setContentsMargins(16, 14, 16, 14)
        row.setSpacing(12)

        icon = QLabel("⚠")
        icon.setStyleSheet("color: #f5b335; font-size: 16px;")
        icon.setFixedWidth(20)

        self._banner_text = QLabel("Run a prediction to populate risk alerts.")
        self._banner_text.setObjectName("riskAlertBannerText")
        self._banner_text.setWordWrap(True)

        row.addWidget(icon)
        row.addWidget(self._banner_text, 1)
        return banner

    def _build_tab_bar(self) -> QWidget:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(28)

        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)

        for tab_id, label, count in TAB_FILTERS:
            btn = QPushButton(f"{label} ({count})")
            btn.setObjectName("riskAlertTab")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(
                lambda _, tid=tab_id: self._filter_alerts(tid)
            )
            self._tab_group.addButton(btn)
            self._tab_buttons[tab_id] = btn
            btn.adjustSize()
            btn.setFixedWidth(btn.sizeHint().width() + 16)
            row.addWidget(btn)

        row.addStretch()
        self._tab_buttons["all"].setChecked(True)
        return container

    def _build_empty_state(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("riskAlertsEmptyState")

        col = QVBoxLayout(frame)
        col.setContentsMargins(0, 60, 0, 60)
        col.setSpacing(14)
        col.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = QLabel("🔔")
        icon.setStyleSheet("font-size: 48px; background: transparent;")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("No risk alerts yet")
        title.setObjectName("emptyStateTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sub = QLabel(
            "Run a prediction from the Prediction page or the Dashboard\n"
            "to identify at-risk students and populate this list."
        )
        sub.setObjectName("emptyStateSub")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)

        run_btn = QPushButton("⚡  Go to Prediction")
        run_btn.setObjectName("emptyStateBtn")
        run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        run_btn.setFixedWidth(180)
        run_btn.setFixedHeight(38)
        run_btn.clicked.connect(self.on_run_prediction)
        from services.auth_service import AuthService
        if (AuthService.current_role() or "").strip().lower() == "counselor":
            run_btn.hide()

        col.addWidget(icon)
        col.addWidget(title)
        col.addWidget(sub)
        col.addSpacing(8)
        col.addWidget(run_btn, 0, Qt.AlignmentFlag.AlignCenter)

        return frame

    def _filter_alerts(self, tab_id: str):
        for card, student in self._alert_cards:
            card.setVisible(tab_id == "all" or student["category"] == tab_id)

    def setup_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(30, 30, 30, 30)
        self.main_layout.setSpacing(20)

        header_frame = QFrame()
        header_frame.setObjectName("fixedHeaderContainer")
        fh_layout = QVBoxLayout(header_frame)
        fh_layout.setContentsMargins(20, 20, 20, 20)

        header_row = QHBoxLayout()
        header_row.setSpacing(15)

        text_col = QVBoxLayout()
        text_col.setSpacing(5)
        title = QLabel("Risk Alerts")
        title.setObjectName("header")
        self._ay_sub_lbl = QLabel(f"Academic Year {SystemConfig.academic_year()}")
        self._ay_sub_lbl.setObjectName("subHeader")
        text_col.addWidget(title)
        text_col.addWidget(self._ay_sub_lbl)
        header_row.addLayout(text_col)
        header_row.addStretch()

        model_card = QFrame()
        model_card.setObjectName("modelCard")
        mc_layout = QHBoxLayout(model_card)
        mc_layout.setContentsMargins(20, 15, 20, 15)
        mc_layout.setSpacing(12)

        self.model_status = QLabel("● Model Active")
        self.model_status.setObjectName("modelStatus")
        opacity = QGraphicsOpacityEffect(self.model_status)
        self.model_status.setGraphicsEffect(opacity)
        anim = QPropertyAnimation(opacity, b"opacity")
        anim.setDuration(1200)
        anim.setStartValue(1.0)
        anim.setKeyValueAt(0.5, 0.3)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        anim.setLoopCount(-1)
        anim.start()
        self._status_anim = anim

        self._sem_pill_lbl = QLabel(SystemConfig.term_label())
        self._sem_pill_lbl.setObjectName("semesterPill")
        self._sem_pill_lbl.setObjectName("semesterPill")

        #run_btn = QPushButton("Run Prediction")
        #run_btn.setObjectName("runButton")
        #run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        #run_btn.setIcon(QIcon("assets/icons/play.svg"))
        #run_btn.clicked.connect(self.on_run_prediction)
        #run_btn.setFixedWidth(130)
        # Hide for counselors
        #from services.auth_service import AuthService
        #if (AuthService.current_role() or "").strip().lower() == "counselor":
        #    run_btn.hide()

        mc_layout.addWidget(self.model_status)
        mc_layout.addWidget(self._sem_pill_lbl)
        #mc_layout.addWidget(run_btn)
        header_row.addWidget(model_card)

        fh_layout.addLayout(header_row)
        self.main_layout.addWidget(header_frame)

        self.main_layout.addWidget(self._build_warning_banner())
        self.main_layout.addWidget(self._build_tab_bar())

        self._empty_state = self._build_empty_state()
        self.main_layout.addWidget(self._empty_state)

        self._live_content = QWidget()
        self._live_content.setVisible(False)
        live_layout = QVBoxLayout(self._live_content)
        live_layout.setContentsMargins(0, 0, 0, 0)
        live_layout.setSpacing(0)

        self.cards_layout = QVBoxLayout()
        self.cards_layout.setSpacing(12)
        live_layout.addLayout(self.cards_layout)

        self.main_layout.addWidget(self._live_content)
        self.main_layout.addStretch()

        self.init_prediction()
        self._apply_styles()

    def _apply_styles(self):
        self.setStyleSheet("""
            #riskAlertsEmptyState {
                background-color: #13172a;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 16px;
            }
            #emptyStateTitle {
                color: rgba(255,255,255,0.75);
                font-size: 16px; font-weight: 700;
                background: transparent;
            }
            #emptyStateSub {
                color: rgba(255,255,255,0.40);
                font-size: 13px; background: transparent;
            }
            #emptyStateBtn {
                background-color: #4f8cff;
                border: none; border-radius: 8px;
                color: white; font-size: 12px; font-weight: 700;
            }
            #emptyStateBtn:hover { background-color: rgba(79,140,255,0.85); }
            #moderateRiskBadge {
                background-color: rgba(245,179,53,0.12);
                border: 1px solid rgba(245,179,53,0.30);
                border-radius: 10px;
                color: #f5b335;
                font-size: 11px; font-weight: 600;
                padding: 3px 10px;
            }
        """)

    def _refresh_term_labels(self):
        if hasattr(self, "_ay_sub_lbl"):
            self._ay_sub_lbl.setText(f"Academic Year {SystemConfig.academic_year()}")
        if hasattr(self, "_sem_pill_lbl"):
            self._sem_pill_lbl.setText(SystemConfig.term_label())

    def closeEvent(self, event):
        DataStore.get().remove_listener(self._on_store_updated)
        super().closeEvent(event)