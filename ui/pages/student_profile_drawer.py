from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QScrollArea,
    QProgressBar,
    QGridLayout,
    QGraphicsDropShadowEffect,
)
from PyQt6.QtCore import (
    Qt,
    QPropertyAnimation,
    QEasingCurve,
    QRect,
    QEvent,
)
from PyQt6.QtGui import QColor


DEFAULT_SHAP_FACTORS = [
    ("GWA drop (sem 1)", 39),
    ("Absences > 20%", 20),
    ("No org membership", 13),
    ("Working student", 6),
    ("Failed ≥ 2 subjects", 11),
    ("Low psych score", 10),
]

DEFAULT_BACKGROUND = [
    "Working student: No",
    "Org member: No",
    "Income bracket: C",
]

DEFAULT_RECOMMENDATIONS = [
    ("⚡", "Immediate referral to academic advisor recommended"),
    ("💬", "Schedule guidance counseling session this week"),
]


class StudentProfilePanel(QFrame):
    """Right-side panel content for a single student profile."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("profilePanel")
        self._panel_width = 500
        self.setFixedWidth(self._panel_width)
        self._build_ui()
        self._apply_styles()

    def _apply_styles(self):
        self.setStyleSheet("""
            #profilePanel {
                background-color: #12151c;
                border-left: 1px solid rgba(255, 255, 255, 0.08);
            }
            #profileBackButton {
                background-color: transparent;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 8px;
                color: rgba(255, 255, 255, 0.75);
                font-size: 12px;
                padding: 8px 14px;
            }
            #profileBackButton:hover {
                background-color: rgba(255, 255, 255, 0.06);
            }
            #profileHeaderTitle {
                color: rgba(255, 255, 255, 0.45);
                font-size: 13px;
            }
            #profileLogButton {
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
                color: rgba(255, 255, 255, 0.8);
                font-size: 12px;
                padding: 8px 12px;
            }
            #profileLogButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
            }
            #profileAvatar {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #c0392b, stop:1 #2c1810
                );
                border-radius: 40px;
                font-size: 28px;
            }
            #profileStudentName {
                font-size: 26px;
                font-weight: bold;
                color: white;
            }
            #profileStudentMeta {
                color: rgba(255, 255, 255, 0.45);
                font-size: 13px;
            }
            #profileRiskPill {
                background-color: rgba(255, 91, 91, 0.12);
                border: 1px solid rgba(255, 91, 91, 0.25);
                border-radius: 14px;
                color: #ff6b6b;
                font-size: 12px;
                font-weight: 600;
                padding: 6px 14px;
            }
            #profileSectionTitle {
                color: rgba(255, 255, 255, 0.4);
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 1px;
            }
            #profileMetricCard {
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
            }
            #profileMetricValue {
                font-size: 22px;
                font-weight: bold;
                color: white;
            }
            #profileMetricValueRisk {
                font-size: 22px;
                font-weight: bold;
                color: #ff5b5b;
            }
            #profileMetricLabel {
                color: rgba(255, 255, 255, 0.4);
                font-size: 12px;
            }
            #profileDivider {
                background-color: rgba(255, 255, 255, 0.08);
                max-height: 1px;
            }
            #profileShapLabel {
                color: rgba(255, 255, 255, 0.65);
                font-size: 12px;
            }
            #profileShapPercent {
                color: rgba(255, 255, 255, 0.45);
                font-size: 12px;
            }
            #profileTagPill {
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 14px;
                color: rgba(255, 255, 255, 0.65);
                font-size: 12px;
                padding: 8px 14px;
            }
            #profileRecBox {
                background-color: rgba(245, 158, 11, 0.08);
                border: 1px solid rgba(245, 158, 11, 0.3);
                border-radius: 10px;
            }
            #profileRecText {
                color: #e8c97a;
                font-size: 12px;
            }
            #profileNotifyBtn {
                background-color: #1a73e8;
                border: none;
                border-radius: 8px;
                color: white;
                font-size: 12px;
                font-weight: 600;
                padding: 12px;
            }
            #profileNotifyBtn:hover {
                background-color: #2980d9;
            }
            #profileSecondaryBtn {
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
                color: rgba(255, 255, 255, 0.8);
                font-size: 12px;
                padding: 12px;
            }
            #profileSecondaryBtn:hover {
                background-color: rgba(255, 255, 255, 0.08);
            }
            #profileExportBtn {
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
                color: rgba(255, 255, 255, 0.7);
                font-size: 12px;
                padding: 10px;
            }
            #profileExportBtn:hover {
                background-color: rgba(255, 255, 255, 0.08);
            }
        """)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )

        content = QWidget()
        content.setObjectName("profileScrollContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(20)

        # Top bar
        top_bar = QHBoxLayout()
        self.back_btn = QPushButton("←  Back")
        self.back_btn.setObjectName("profileBackButton")
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        header_title = QLabel("Student Profile")
        header_title.setObjectName("profileHeaderTitle")

        self.log_btn = QPushButton("📋  Log Intervention")
        self.log_btn.setObjectName("profileLogButton")
        self.log_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        top_bar.addWidget(self.back_btn)
        top_bar.addWidget(header_title)
        top_bar.addStretch()
        top_bar.addWidget(self.log_btn)
        layout.addLayout(top_bar)

        # Identity
        identity = QHBoxLayout()
        identity.setSpacing(16)

        self.avatar = QLabel("🎓")
        self.avatar.setObjectName("profileAvatar")
        self.avatar.setFixedSize(80, 80)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)

        identity_text = QVBoxLayout()
        identity_text.setSpacing(6)

        self.name_label = QLabel()
        self.name_label.setObjectName("profileStudentName")

        self.meta_label = QLabel()
        self.meta_label.setObjectName("profileStudentMeta")

        self.risk_pill = QLabel()
        self.risk_pill.setObjectName("profileRiskPill")

        identity_text.addWidget(self.name_label)
        identity_text.addWidget(self.meta_label)
        identity_text.addWidget(self.risk_pill)
        identity_text.addStretch()

        identity.addWidget(self.avatar)
        identity.addLayout(identity_text, 1)
        layout.addLayout(identity)

        layout.addWidget(self._divider())

        # Academic profile
        layout.addWidget(self._section_title("ACADEMIC PROFILE"))
        self.metrics_grid = QGridLayout()
        self.metrics_grid.setSpacing(12)
        self._metric_labels = {}
        metrics_host = QWidget()
        metrics_host.setLayout(self.metrics_grid)
        layout.addWidget(metrics_host)

        layout.addWidget(self._divider())

        # SHAP
        layout.addWidget(self._section_title("RISK FACTOR BREAKDOWN (SHAP)"))
        self.shap_container = QVBoxLayout()
        self.shap_container.setSpacing(10)
        shap_host = QWidget()
        shap_host.setLayout(self.shap_container)
        layout.addWidget(shap_host)

        layout.addWidget(self._divider())

        # Background
        layout.addWidget(self._section_title("BACKGROUND"))
        self.tags_layout = QHBoxLayout()
        self.tags_layout.setSpacing(8)
        tags_host = QWidget()
        tags_host.setLayout(self.tags_layout)
        layout.addWidget(tags_host)

        layout.addWidget(self._divider())

        # Recommended actions
        layout.addWidget(self._section_title("RECOMMENDED ACTIONS"))
        self.rec_container = QVBoxLayout()
        self.rec_container.setSpacing(10)
        rec_host = QWidget()
        rec_host.setLayout(self.rec_container)
        layout.addWidget(rec_host)

        notify_btn = QPushButton("✉  Notify Advisor")
        notify_btn.setObjectName("profileNotifyBtn")
        notify_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        counsel_btn = QPushButton("📅  Schedule Counseling")
        counsel_btn.setObjectName("profileSecondaryBtn")
        counsel_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        export_btn = QPushButton("↓  Export Report")
        export_btn.setObjectName("profileExportBtn")
        export_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        layout.addWidget(notify_btn)
        layout.addWidget(counsel_btn)
        layout.addWidget(export_btn)
        layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(40)
        shadow.setXOffset(-8)
        shadow.setYOffset(0)
        shadow.setColor(QColor(0, 0, 0, 160))
        self.setGraphicsEffect(shadow)

    def _section_title(self, text):
        label = QLabel(text)
        label.setObjectName("profileSectionTitle")
        return label

    def _divider(self):
        line = QFrame()
        line.setObjectName("profileDivider")
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        return line

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _create_shap_row(self, label_text, percentage):
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(12)

        label = QLabel(label_text)
        label.setObjectName("profileShapLabel")
        label.setFixedWidth(150)

        bar = QProgressBar()
        bar.setValue(percentage)
        bar.setTextVisible(False)
        bar.setFixedHeight(8)
        bar.setStyleSheet("""
            QProgressBar {
                background-color: rgba(255, 255, 255, 0.08);
                border-radius: 4px;
                border: none;
            }
            QProgressBar::chunk {
                background-color: #ff5b5b;
                border-radius: 4px;
            }
        """)

        pct = QLabel(f"{percentage}%")
        pct.setObjectName("profileShapPercent")
        pct.setFixedWidth(36)
        pct.setAlignment(Qt.AlignmentFlag.AlignRight)

        row_layout.addWidget(label)
        row_layout.addWidget(bar, 1)
        row_layout.addWidget(pct)
        return row

    def _create_metric_card(self, value, label, is_risk=False):
        card = QFrame()
        card.setObjectName("profileMetricCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(4)

        value_lbl = QLabel(str(value))
        value_lbl.setObjectName(
            "profileMetricValueRisk" if is_risk else "profileMetricValue"
        )

        label_lbl = QLabel(label)
        label_lbl.setObjectName("profileMetricLabel")

        card_layout.addWidget(value_lbl)
        card_layout.addWidget(label_lbl)
        return card

    def load_student(self, student):
        """Populate panel fields from a student record dict."""
        self.name_label.setText(student["name"])
        self.meta_label.setText(
            f"{student['id']} · {student['college']} · {student['program']}"
        )
        self.risk_pill.setText(
            f"●  {student.get('risk_level', 'High')} Risk — {student['score']}%"
        )

        self._clear_layout(self.metrics_grid)
        metrics = [
            (student.get("gwa", 3.45), "GWA", True),
            (student.get("absences", 13), "Absences", False),
            (student.get("failed_subjects", 2), "Failed Subjects", False),
            (student.get("referrals", 2), "Referrals", False),
        ]
        for i, (value, label, is_risk) in enumerate(metrics):
            card = self._create_metric_card(value, label, is_risk)
            self.metrics_grid.addWidget(card, i // 2, i % 2)

        self._clear_layout(self.shap_container)
        factors = student.get("shap_factors", DEFAULT_SHAP_FACTORS)
        for label_text, pct in factors:
            self.shap_container.addWidget(
                self._create_shap_row(label_text, pct)
            )

        self._clear_layout(self.tags_layout)
        tags = student.get("background", DEFAULT_BACKGROUND)
        for tag in tags:
            pill = QLabel(tag)
            pill.setObjectName("profileTagPill")
            self.tags_layout.addWidget(pill)
        self.tags_layout.addStretch()

        self._clear_layout(self.rec_container)
        recs = student.get("recommendations", DEFAULT_RECOMMENDATIONS)
        for icon, text in recs:
            box = QFrame()
            box.setObjectName("profileRecBox")
            box_layout = QHBoxLayout(box)
            box_layout.setContentsMargins(14, 12, 14, 12)
            box_layout.setSpacing(10)

            icon_lbl = QLabel(icon)
            icon_lbl.setFixedWidth(20)

            text_lbl = QLabel(text)
            text_lbl.setObjectName("profileRecText")
            text_lbl.setWordWrap(True)

            box_layout.addWidget(icon_lbl)
            box_layout.addWidget(text_lbl, 1)
            self.rec_container.addWidget(box)


class StudentProfileDrawer(QWidget):
    """
    Full-area overlay with a slide-in panel from the right.
    Attach to the page container (parent of QScrollArea), not the scrollable page.
    """

    PANEL_WIDTH = 500
    ANIM_DURATION = 320

    def __init__(self, host: QWidget):
        super().__init__(host)
        self._host = host
        self._is_open = False
        self._slide_anim = None

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.hide()

        # Dimmed backdrop
        self._backdrop = QFrame(self)
        self._backdrop.setObjectName("profileBackdrop")
        self._backdrop.setStyleSheet(
            "#profileBackdrop { background-color: rgba(0, 0, 0, 0.45); }"
        )
        self._backdrop.installEventFilter(self)

        self._panel = StudentProfilePanel(self)
        self._panel.back_btn.clicked.connect(self.close_drawer)

        self._host.installEventFilter(self)
        self._sync_geometry()

    def eventFilter(self, obj, event):
        if obj is self._host and event.type() == QEvent.Type.Resize:
            self._sync_geometry()
            if self._is_open:
                self._place_panel(open_state=True)
        if obj is self._backdrop and event.type() == QEvent.Type.MouseButtonPress:
            self.close_drawer()
            return True
        return super().eventFilter(obj, event)

    def _sync_geometry(self):
        self.setGeometry(self._host.rect())
        self._backdrop.setGeometry(self.rect())

    def _place_panel(self, open_state: bool):
        h = self.height()
        if open_state:
            x = self.width() - self.PANEL_WIDTH
        else:
            x = self.width()
        self._panel.setGeometry(QRect(x, 0, self.PANEL_WIDTH, h))

    def open_drawer(self, student: dict):
        self._panel.load_student(student)
        self._sync_geometry()
        self.show()
        self.raise_()
        self._is_open = True

        start = QRect(self.width(), 0, self.PANEL_WIDTH, self.height())
        end = QRect(
            self.width() - self.PANEL_WIDTH,
            0,
            self.PANEL_WIDTH,
            self.height(),
        )
        self._run_slide(start, end)

    def close_drawer(self):
        if not self._is_open:
            return

        start = self._panel.geometry()
        end = QRect(self.width(), 0, self.PANEL_WIDTH, self.height())
        self._is_open = False
        self._run_slide(start, end, on_finished=self.hide)

    def _run_slide(self, start: QRect, end: QRect, on_finished=None):
        if self._slide_anim and self._slide_anim.state() == QPropertyAnimation.State.Running:
            self._slide_anim.stop()

        self._panel.setGeometry(start)
        self._slide_anim = QPropertyAnimation(self._panel, b"geometry")
        self._slide_anim.setDuration(self.ANIM_DURATION)
        self._slide_anim.setStartValue(start)
        self._slide_anim.setEndValue(end)
        self._slide_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        if on_finished:
            self._slide_anim.finished.connect(
                on_finished,
                Qt.ConnectionType.SingleShotConnection,
            )
        self._slide_anim.start()
