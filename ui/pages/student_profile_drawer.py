from __future__ import annotations
import json

from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QScrollArea,
    QProgressBar,
    QGraphicsDropShadowEffect,
    QStackedWidget,
    QGridLayout,
)
from PyQt6.QtCore import (
    Qt,
    QPropertyAnimation,
    QEasingCurve,
    QRect,
    QEvent,
    QThread,
    pyqtSignal,
)
from PyQt6.QtGui import QColor

from services.data_store import DataStore


# ── Default fallbacks ─────────────────────────────────────────────────────────

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

_LOW_RISK_TIPS = [
    ("📘", "Maintain consistent class attendance and study habits."),
    ("👥", "Join student organizations to build peer support networks."),
    ("📅", "Check in with your academic advisor once per semester."),
    ("💡", "Explore scholarship opportunities before the deadline."),
]


# ── Intervention DB loader ────────────────────────────────────────────────────

class _InterventionLoader(QThread):
    """
    Fetches the most recent per_student intervention record for a given
    student_id from public.interventions, then returns its recommendations
    as a list of dicts.
    """
    finished = pyqtSignal(list)   # list of rec dicts (may be empty)
    error    = pyqtSignal(str)

    def __init__(self, student_id: str):
        super().__init__()
        self._student_id = str(student_id)

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.finished.emit([])
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT recommendations, logged_at
                    FROM   public.interventions
                    WHERE  student_id = %s
                      AND  mode       = 'per_student'
                    ORDER  BY logged_at DESC
                    LIMIT  1
                """, (self._student_id,))
                row = cur.fetchone()

            if not row:
                self.finished.emit([])
                return

            recs_raw, _ = row
            # psycopg2 may return JSONB already parsed or as a string
            if isinstance(recs_raw, str):
                try:
                    recs_raw = json.loads(recs_raw)
                except Exception:
                    recs_raw = []
            self.finished.emit(recs_raw if isinstance(recs_raw, list) else [])
        except Exception as e:
            self.error.emit(str(e))
            self.finished.emit([])


# ── Student profile DB loader ─────────────────────────────────────────────────

class _StudentProfileLoader(QThread):
    """
    Fetches all background fields for a student directly from the DB.

    Priority for each field:
      1. dim_student  (canonical, already merged)
      2. Staging tables as fallback (registrar / guidance / SAO / MIS)
         via COALESCE so we always get a value even if dim_student hasn't
         been backfilled yet.

    Returns a flat dict keyed by the display field names used in
    _on_profile_loaded().  Never raises — emits {} on any error.
    """
    finished = pyqtSignal(dict)
    error    = pyqtSignal(str)

    _SQL = """
        SELECT
            -- ── Academic (fact table — most recent term) ─────────────
            fsr.year_level,
            fsr.entrance_exam_score,
            fsr.high_school_gpa,

            -- ── Academic (dim_student preferred, registrar fallback) ──
            COALESCE(NULLIF(ds.shs_strand,        ''), r.shs_strand)        AS shs_strand,
            COALESCE(NULLIF(ds.hs_type,           ''), r.hs_type)           AS hs_type,
            COALESCE(NULLIF(ds.graduation_honors, ''), r.graduation_honors) AS graduation_honors,
            COALESCE(NULLIF(ds.hs_school_name,    ''), r.hs_school)         AS hs_school_name,

            -- ── Personal (dim_student preferred, staging fallbacks) ───
            COALESCE(NULLIF(ds.sex_code,     ''), mis.sex_code)             AS sex_code,
            COALESCE(NULLIF(ds.civil_status, ''), mis.civil_status)         AS civil_status,
            COALESCE(NULLIF(ds.religion,     ''), mis.religion)             AS religion,

            COALESCE(NULLIF(ds.home_municipality, ''), r.municipality)      AS home_municipality,

            COALESCE(NULLIF(ds.family_income_bracket,    ''),
                     g.family_income_bracket)                               AS family_income_bracket,
            COALESCE(NULLIF(ds.parent_highest_education, ''),
                     g.parent_highest_education)                            AS parent_highest_education,

            COALESCE(NULLIF(ds.scholarship_type, ''), sao.scholarship_type) AS scholarship_type

        FROM  public.dim_student ds

        -- most recent prediction term for year_level / scores
        LEFT JOIN public.fact_student_academic_risk fsr
              ON  fsr.student_key = ds.student_key

        -- staging fallbacks
        LEFT JOIN public.registrar_student_profile r
              ON  r.student_id = ds.student_id
        LEFT JOIN public.guidance_student_profile  g
              ON  g.student_id = ds.student_id
        LEFT JOIN public.sao_student_profile       sao
              ON  sao.student_id = ds.student_id
        LEFT JOIN public.mis_students              mis
              ON  mis.id_no = ds.student_id

        WHERE ds.student_id = %s
        ORDER BY fsr.predicted_at DESC NULLS LAST
        LIMIT 1
    """

    def __init__(self, student_id: str):
        super().__init__()
        self._student_id = str(student_id)

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.finished.emit({})
            return
        try:
            with conn.cursor() as cur:
                cur.execute(self._SQL, (self._student_id,))
                row = cur.fetchone()
                if not row:
                    self.finished.emit({})
                    return
                cols = [d[0] for d in cur.description]
                self.finished.emit(dict(zip(cols, row)))
        except Exception as e:
            self.error.emit(str(e))
            self.finished.emit({})


# ── Profile panel ─────────────────────────────────────────────────────────────

class StudentProfilePanel(QFrame):
    """Right-side panel content for a single student profile."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("profilePanel")
        self._panel_width    = 720
        self.setFixedWidth(self._panel_width)
        self._loader:         _InterventionLoader   | None = None
        self._profile_loader: _StudentProfileLoader | None = None
        self._build_ui()
        self._apply_styles()

    # ── Styles ────────────────────────────────────────────────────────

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
            #bgKey {
                color: rgba(255, 255, 255, 0.35);
                font-size: 11px;
                background: transparent;
            }
            #bgVal {
                color: rgba(255, 255, 255, 0.75);
                font-size: 11px;
                font-weight: 600;
                background: transparent;
            }
            #bgValMissing {
                color: rgba(255, 255, 255, 0.20);
                font-size: 11px;
                background: transparent;
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

    # ── UI build ──────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }")

        content = QWidget()
        content.setObjectName("profileScrollContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 24, 32, 32)
        layout.setSpacing(22)

        # ── Top bar ───────────────────────────────────────────────────
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

        # ── Identity ──────────────────────────────────────────────────
        identity = QHBoxLayout()
        identity.setSpacing(20)

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
        self.risk_pill  = QLabel()
        self.risk_pill.setObjectName("profileRiskPill")
        identity_text.addWidget(self.name_label)
        identity_text.addWidget(self.meta_label)
        identity_text.addWidget(self.risk_pill)
        identity_text.addStretch()

        identity.addWidget(self.avatar)
        identity.addLayout(identity_text, 1)
        layout.addLayout(identity)

        layout.addWidget(self._divider())

        # ── SHAP breakdown ────────────────────────────────────────────
        layout.addWidget(self._section_title("RISK FACTOR BREAKDOWN"))
        self.shap_container = QVBoxLayout()
        self.shap_container.setSpacing(10)
        shap_host = QWidget()
        shap_host.setLayout(self.shap_container)
        layout.addWidget(shap_host)

        layout.addWidget(self._divider())

        # ── Background: two-group grid (loaded from DB) ───────────────
        layout.addWidget(self._section_title("BACKGROUND"))

        # Stacked: 0 = loading bar, 1 = grids content
        self._bg_stack = QStackedWidget()

        # index 0 — thin loading bar
        bg_loading = QWidget()
        bg_loading.setStyleSheet("background:transparent;")
        bll = QVBoxLayout(bg_loading)
        bll.setContentsMargins(0, 8, 0, 8)
        self._bg_bar = QProgressBar()
        self._bg_bar.setRange(0, 0)
        self._bg_bar.setFixedHeight(3)
        self._bg_bar.setTextVisible(False)
        self._bg_bar.setStyleSheet("""
            QProgressBar { background:rgba(255,255,255,0.08);
                border-radius:2px; border:none; }
            QProgressBar::chunk { background:#4f8cff; border-radius:2px; }
        """)
        bll.addWidget(self._bg_bar)
        bll.addStretch()
        self._bg_stack.addWidget(bg_loading)     # index 0

        # index 1 — actual content
        bg_content = QWidget()
        bg_content.setStyleSheet("background:transparent;")
        bg_cl = QVBoxLayout(bg_content)
        bg_cl.setContentsMargins(0, 4, 0, 0)
        bg_cl.setSpacing(6)

        # Academic sub-header
        acad_lbl = QLabel("ACADEMIC")
        acad_lbl.setStyleSheet(
            "color:rgba(79,140,255,0.60); font-size:10px; font-weight:700; "
            "letter-spacing:0.8px; background:transparent; padding-top:4px;")
        bg_cl.addWidget(acad_lbl)

        self._acad_grid = QGridLayout()
        self._acad_grid.setSpacing(6)
        self._acad_grid.setColumnStretch(0, 3)
        self._acad_grid.setColumnStretch(1, 4)
        self._acad_grid.setColumnStretch(2, 3)
        self._acad_grid.setColumnStretch(3, 4)
        acad_host = QWidget()
        acad_host.setStyleSheet("background:transparent;")
        acad_host.setLayout(self._acad_grid)
        bg_cl.addWidget(acad_host)

        # Personal sub-header
        pers_lbl = QLabel("PERSONAL")
        pers_lbl.setStyleSheet(
            "color:rgba(167,139,250,0.60); font-size:10px; font-weight:700; "
            "letter-spacing:0.8px; background:transparent; padding-top:8px;")
        bg_cl.addWidget(pers_lbl)

        self._pers_grid = QGridLayout()
        self._pers_grid.setSpacing(6)
        self._pers_grid.setColumnStretch(0, 3)
        self._pers_grid.setColumnStretch(1, 4)
        self._pers_grid.setColumnStretch(2, 3)
        self._pers_grid.setColumnStretch(3, 4)
        pers_host = QWidget()
        pers_host.setStyleSheet("background:transparent;")
        pers_host.setLayout(self._pers_grid)
        bg_cl.addWidget(pers_host)

        self._bg_stack.addWidget(bg_content)     # index 1
        self._bg_stack.setCurrentIndex(0)        # start in loading state
        layout.addWidget(self._bg_stack)

        layout.addWidget(self._divider())

        # ── Recommended actions ───────────────────────────────────────
        # Section header row with status label (shown while loading)
        rec_header_row = QHBoxLayout()
        rec_header_row.setSpacing(10)
        rec_header_row.addWidget(self._section_title("RECOMMENDED ACTIONS"))
        rec_header_row.addStretch()
        self._rec_status_lbl = QLabel("")
        self._rec_status_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.28); font-size:10px; background:transparent;")
        rec_header_row.addWidget(self._rec_status_lbl)
        layout.addLayout(rec_header_row)

        # Stacked: 0=loading bar, 1=content host
        self._rec_stack = QStackedWidget()
        self._rec_stack.setMinimumHeight(60)

        # index 0 — loading indicator
        loading_w = QWidget()
        loading_w.setStyleSheet("background:transparent;")
        ll = QVBoxLayout(loading_w)
        ll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._rec_bar = QProgressBar()
        self._rec_bar.setRange(0, 0)
        self._rec_bar.setFixedHeight(3)
        self._rec_bar.setTextVisible(False)
        self._rec_bar.setStyleSheet("""
            QProgressBar { background:rgba(255,255,255,0.08);
                border-radius:2px; border:none; }
            QProgressBar::chunk { background:#4f8cff; border-radius:2px; }
        """)
        ll.addWidget(self._rec_bar)
        self._rec_stack.addWidget(loading_w)           # 0

        # index 1 — actual recommendation content
        self._rec_host = QWidget()
        self._rec_host.setStyleSheet("background:transparent;")
        self.rec_container = QVBoxLayout(self._rec_host)
        self.rec_container.setContentsMargins(0, 0, 0, 0)
        self.rec_container.setSpacing(10)
        self._rec_stack.addWidget(self._rec_host)      # 1

        self._rec_stack.setCurrentIndex(1)
        layout.addWidget(self._rec_stack)

        # ── Action buttons ────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        notify_btn  = QPushButton("✉  Notify Advisor")
        notify_btn.setObjectName("profileNotifyBtn")
        notify_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        counsel_btn = QPushButton("📅  Schedule Counseling")
        counsel_btn.setObjectName("profileSecondaryBtn")
        counsel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        export_btn  = QPushButton("↓  Export Report")
        export_btn.setObjectName("profileExportBtn")
        export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_row.addWidget(notify_btn, 2)
        btn_row.addWidget(counsel_btn, 2)
        btn_row.addWidget(export_btn, 1)
        layout.addLayout(btn_row)

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(40)
        shadow.setXOffset(-8)
        shadow.setYOffset(0)
        shadow.setColor(QColor(0, 0, 0, 160))
        self.setGraphicsEffect(shadow)

    # ── Helpers ───────────────────────────────────────────────────────

    def _section_title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("profileSectionTitle")
        return label

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setObjectName("profileDivider")
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        return line

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _clear_grid(self, grid: QGridLayout):
        while grid.count():
            item = grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _create_shap_row(self, label_text: str, percentage) -> QWidget:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(14)

        label = QLabel(label_text)
        label.setObjectName("profileShapLabel")
        label.setMinimumWidth(200)
        label.setMaximumWidth(260)

        bar = QProgressBar()
        bar.setValue(int(percentage))
        bar.setTextVisible(False)
        bar.setFixedHeight(8)
        bar.setStyleSheet("""
            QProgressBar {
                background-color: rgba(255, 255, 255, 0.08);
                border-radius: 4px; border: none;
            }
            QProgressBar::chunk {
                background-color: #ff5b5b; border-radius: 4px;
            }
        """)

        pct = QLabel(f"{int(percentage)}%")
        pct.setObjectName("profileShapPercent")
        pct.setFixedWidth(40)
        pct.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        row_layout.addWidget(label)
        row_layout.addWidget(bar, 1)
        row_layout.addWidget(pct)
        return row

    def _build_ai_rec_card(self, rec: dict, idx: int) -> QWidget:
        """
        Stripe-style card matching the interventions page layout.
        Works for both per-student recommendation objects and
        cohort issue objects.
        """
        # Per-student keys: type, action, rationale, timeline
        # Cohort keys: issue, recommended_action, description
        rtype    = rec.get("type",             "—")
        action   = rec.get("action")           or rec.get("recommended_action", "—")
        rat      = rec.get("rationale")        or rec.get("description",        "")
        timeline = rec.get("timeline",         "")

        type_colors = {
            "Academic Support": "#4f8cff",
            "Financial Aid":    "#f5b335",
            "Counseling":       "#a78bfa",
            "Program Guidance": "#34d399",
            "Peer Support":     "#f59e0b",
        }
        color = type_colors.get(rtype, "#8b949e")

        outer = QWidget()
        outer.setStyleSheet("background:transparent;")
        row = QHBoxLayout(outer)
        row.setContentsMargins(0, 4, 0, 4)
        row.setSpacing(0)

        stripe = QFrame()
        stripe.setFixedWidth(3)
        stripe.setStyleSheet(f"background:{color}; border-radius:2px;")
        row.addWidget(stripe)

        cw = QWidget()
        cw.setStyleSheet("background:transparent;")
        cl = QVBoxLayout(cw)
        cl.setContentsMargins(14, 4, 8, 4)
        cl.setSpacing(4)

        # Type + timeline meta row
        meta = QHBoxLayout()
        meta.setSpacing(10)
        type_lbl = QLabel(rtype.upper() if rtype != "—" else "")
        type_lbl.setStyleSheet(
            f"color:{color}; font-size:10px; font-weight:700; "
            "letter-spacing:0.5px; background:transparent;")
        meta.addWidget(type_lbl)
        if timeline:
            tl_lbl = QLabel(f"⏱  {timeline}")
            tl_lbl.setStyleSheet(
                "color:rgba(255,255,255,0.30); font-size:10px; background:transparent;")
            meta.addWidget(tl_lbl)
        meta.addStretch()
        cl.addLayout(meta)

        action_lbl = QLabel(action)
        action_lbl.setWordWrap(True)
        action_lbl.setStyleSheet(
            "color:#e8eaf0; font-size:12px; font-weight:600; background:transparent;")
        cl.addWidget(action_lbl)

        if rat:
            rat_lbl = QLabel(rat)
            rat_lbl.setWordWrap(True)
            rat_lbl.setStyleSheet(
                "color:rgba(255,255,255,0.45); font-size:11px; background:transparent;")
            cl.addWidget(rat_lbl)

        row.addWidget(cw, 1)
        return outer

    def _build_general_tip(self, icon: str, text: str) -> QFrame:
        """Amber box used for low-risk general guidance tips."""
        box = QFrame()
        box.setObjectName("profileRecBox")
        bl = QHBoxLayout(box)
        bl.setContentsMargins(16, 12, 16, 12)
        bl.setSpacing(12)
        icon_lbl = QLabel(icon)
        icon_lbl.setFixedWidth(22)
        text_lbl = QLabel(text)
        text_lbl.setObjectName("profileRecText")
        text_lbl.setWordWrap(True)
        bl.addWidget(icon_lbl)
        bl.addWidget(text_lbl, 1)
        return box

    # ── Data loading ──────────────────────────────────────────────────

    def load_student(self, student: dict):
        """Populate panel fields from a student record dict."""

        # ── Identity ──────────────────────────────────────────────────
        self.name_label.setText(student.get("name", "—"))
        self.meta_label.setText(
            f"{student.get('id', '—')}  ·  "
            f"{student.get('college', '—')}  ·  "
            f"{student.get('program', '—')}"
        )
        score    = student.get("score",    0)
        category = student.get("category", "")
        label    = student.get("label",    "At Risk")
        self.risk_pill.setText(f"●  {label} — {score}%")

        pill_styles = {
            "high_risk": (
                "rgba(255,91,91,0.12)", "rgba(255,91,91,0.25)", "#ff6b6b"),
            "moderate_risk": (
                "rgba(245,179,53,0.12)", "rgba(245,179,53,0.25)", "#f5b335"),
        }
        bg, border, fg = pill_styles.get(
            category, ("rgba(52,211,153,0.12)", "rgba(52,211,153,0.25)", "#34d399"))
        self.risk_pill.setStyleSheet(
            f"background-color:{bg}; border:1px solid {border}; "
            "border-radius:14px; font-size:12px; font-weight:600; "
            f"padding:6px 14px; color:{fg};"
        )

        # ── SHAP factors ──────────────────────────────────────────────
        self._clear_layout(self.shap_container)
        factors = student.get("shap_factors", DEFAULT_SHAP_FACTORS)
        for entry in factors:
            if len(entry) == 4:
                _, human_label, formatted_value, pct = entry
                label_text = f"{human_label}:  {formatted_value}"
            elif len(entry) == 2:
                label_text, pct = entry
            else:
                continue
            self.shap_container.addWidget(
                self._create_shap_row(label_text, pct))

        # ── Background: trigger DB load ───────────────────────────────
        self._clear_grid(self._acad_grid)
        self._clear_grid(self._pers_grid)
        self._bg_stack.setCurrentIndex(0)   # show loading bar

        student_id = student.get("id") or student.get("student_id")
        if student_id:
            self._start_profile_load(str(student_id))
        else:
            # No ID — switch straight to content (all dashes)
            self._bg_stack.setCurrentIndex(1)

        # ── Recommended actions ───────────────────────────────────────
        self._clear_layout(self.rec_container)
        self._current_student = student

        if category == "low_risk" or (
                "low" in str(category).lower() and "high" not in str(category).lower()
                and "mod" not in str(category).lower()):
            # Low risk: static general guidance only, no DB lookup
            self._rec_status_lbl.setText("")
            self._rec_stack.setCurrentIndex(1)
            self._render_low_risk_tips()
        else:
            # High / Moderate: fetch from interventions table
            student_id = student.get("id") or student.get("student_id")
            if student_id:
                self._start_intervention_load(str(student_id))
            else:
                # No ID — fall back to generated recommendations
                self._rec_status_lbl.setText("")
                self._rec_stack.setCurrentIndex(1)
                self._render_fallback_recs(student)

    def _start_intervention_load(self, student_id: str):
        """Show loading bar and fetch interventions from DB."""
        if self._loader is not None:
            try:
                self._loader.finished.disconnect()
                self._loader.error.disconnect()
            except RuntimeError:
                pass
            if self._loader.isRunning():
                self._loader.quit()
                self._loader.wait(1000)
            try:
                self._loader.deleteLater()
            except RuntimeError:
                pass
            self._loader = None

        self._rec_status_lbl.setText("Loading…")
        self._rec_stack.setCurrentIndex(0)

        self._loader = _InterventionLoader(student_id)
        self._loader.finished.connect(self._on_interventions_loaded)
        self._loader.error.connect(
            lambda e: print(f"[ProfileDrawer] Intervention load error: {e}"))
        self._loader.finished.connect(self._loader.deleteLater)
        self._loader.error.connect(self._loader.deleteLater)
        self._loader.start()

    # ── Background profile loader ──────────────────────────────────────

    def _start_profile_load(self, student_id: str):
        """Cancel any in-flight profile load and start a fresh one."""
        if self._profile_loader is not None:
            try:
                self._profile_loader.finished.disconnect()
                self._profile_loader.error.disconnect()
            except RuntimeError:
                pass
            if self._profile_loader.isRunning():
                self._profile_loader.quit()
                self._profile_loader.wait(1000)
            try:
                self._profile_loader.deleteLater()
            except RuntimeError:
                pass
            self._profile_loader = None

        self._profile_loader = _StudentProfileLoader(student_id)
        self._profile_loader.finished.connect(self._on_profile_loaded)
        self._profile_loader.error.connect(
            lambda e: print(f"[ProfileDrawer] Profile load error: {e}"))
        self._profile_loader.finished.connect(self._profile_loader.deleteLater)
        self._profile_loader.error.connect(self._profile_loader.deleteLater)
        self._profile_loader.start()

    def _on_profile_loaded(self, data: dict):
        """Populate the Academic and Personal grids from DB data."""
        self._profile_loader = None
        self._clear_grid(self._acad_grid)
        self._clear_grid(self._pers_grid)

        def _fmt(value) -> str | None:
            v = str(value or "").strip()
            return None if v.lower() in ("", "—", "none", "nan", "unknown", "null") else v

        def _grid_row(grid: QGridLayout, grid_row: int, key: str,
                      value: str | None, col_offset: int = 0):
            k = QLabel(key)
            k.setObjectName("bgKey")
            v = QLabel(value if value else "—")
            v.setObjectName("bgVal" if value else "bgValMissing")
            v.setWordWrap(True)
            grid.addWidget(k, grid_row, col_offset * 2)
            grid.addWidget(v, grid_row, col_offset * 2 + 1)

        # ── Academic ──────────────────────────────────────────────────
        exam = data.get("entrance_exam_score")
        gpa  = data.get("high_school_gpa")

        acad_items = [
            ("Year Level",   _fmt(data.get("year_level"))),
            ("Entrance Exam", f"{float(exam):.0f}" if exam is not None else None),
            ("HS GPA",        f"{float(gpa):.2f}"  if gpa  is not None else None),
            ("SHS Strand",   _fmt(data.get("shs_strand"))),
            ("HS Type",      _fmt(data.get("hs_type"))),
            ("Honors",       _fmt(data.get("graduation_honors"))),
        ]
        for i, (key, val) in enumerate(acad_items):
            _grid_row(self._acad_grid, i // 2, key, val, col_offset=i % 2)

        # ── Personal ──────────────────────────────────────────────────
        pers_items = [
            ("Municipality",  _fmt(data.get("home_municipality"))),
            ("Sex",           _fmt(data.get("sex_code"))),
            ("Civil Status",  _fmt(data.get("civil_status"))),
            ("Religion",      _fmt(data.get("religion"))),
            ("Income Bracket",_fmt(data.get("family_income_bracket"))),
            ("Parent Edu",    _fmt(data.get("parent_highest_education"))),
            ("Scholarship",   _fmt(data.get("scholarship_type"))),
            ("HS School",     _fmt(data.get("hs_school_name"))),
        ]
        for i, (key, val) in enumerate(pers_items):
            _grid_row(self._pers_grid, i // 2, key, val, col_offset=i % 2)

        # Switch from loading bar to content
        self._bg_stack.setCurrentIndex(1)

    def _on_interventions_loaded(self, recs: list):
        self._loader = None
        self._rec_status_lbl.setText("")
        self._rec_stack.setCurrentIndex(1)
        self._clear_layout(self.rec_container)

        if recs:
            # ── Show AI-generated recommendations from interventions log ──
            self._rec_status_lbl.setText(
                f"AI · {len(recs)} intervention{'s' if len(recs) != 1 else ''}")

            for i, rec in enumerate(recs):
                self.rec_container.addWidget(
                    self._build_ai_rec_card(rec, i))
                if i < len(recs) - 1:
                    sep = QFrame()
                    sep.setFrameShape(QFrame.Shape.HLine)
                    sep.setStyleSheet("color:rgba(255,255,255,0.06); background:transparent;")
                    self.rec_container.addWidget(sep)
        else:
            # ── No intervention record yet — fall back to generated tips ──
            self._render_fallback_recs(self._current_student)

    def _render_low_risk_tips(self):
        """General guidance for low-risk students (no AI, no DB)."""
        # Header note
        note = QLabel("This student is at low academic risk. "
                      "General guidance tips are shown below.")
        note.setWordWrap(True)
        note.setStyleSheet(
            "color:rgba(52,211,153,0.70); font-size:11px; "
            "background:transparent; padding-bottom:4px;")
        self.rec_container.addWidget(note)

        for icon, text in _LOW_RISK_TIPS:
            self.rec_container.addWidget(self._build_general_tip(icon, text))

    def _render_fallback_recs(self, student: dict):
        """
        Auto-generated recommendations when no intervention record exists.
        Shown for high/moderate risk students not yet analyzed by Ollama.
        """
        category = student.get("category", "")
        factors  = student.get("shap_factors", [])

        note = QLabel(
            "No AI intervention record found for this student.\n"
            "Run \"Analyze All High-Risk Students\" on the Interventions page "
            "to generate personalized plans."
        )
        note.setWordWrap(True)
        note.setStyleSheet(
            "color:rgba(255,255,255,0.30); font-size:11px; "
            "background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.08); "
            "border-radius:8px; padding:10px 12px;")
        self.rec_container.addWidget(note)

        recs: list[tuple[str, str]] = []
        if category == "high_risk":
            recs.append(("⚡", "Immediate referral to academic advisor recommended."))
            recs.append(("💬", "Schedule guidance counseling session this week."))
        elif "mod" in category.lower():
            recs.append(("📋", "Monitor academic performance closely this semester."))
            recs.append(("💬", "Consider a check-in with the guidance office."))

        if factors:
            top_feat = factors[0][0] if len(factors[0]) >= 1 else ""
            if "Financial" in top_feat or "financial" in top_feat:
                recs.append(("💰", "Refer to scholarship or financial assistance office."))
            elif "Distance" in top_feat or "distance" in top_feat:
                recs.append(("🏠", "Explore dormitory or housing assistance options."))
            elif "Entrance" in top_feat or "HS_GPA" in top_feat:
                recs.append(("📚", "Enroll in academic bridging or tutorial program."))
            elif "Strand" in top_feat or "strand" in top_feat:
                recs.append(("🔄", "Consider academic advising on program alignment."))
            elif "First_Gen" in top_feat:
                recs.append(("👨‍👩‍👧", "Connect with first-generation student support services."))

        for icon, text in recs:
            self.rec_container.addWidget(self._build_general_tip(icon, text))

    def _cancel_loaders(self):
        """Stop any in-flight QThreads. Safe to call at any time."""
        for attr in ("_loader", "_profile_loader"):
            worker = getattr(self, attr, None)
            if worker is None:
                continue
            try:
                worker.finished.disconnect()
                worker.error.disconnect()
            except RuntimeError:
                pass
            try:
                if worker.isRunning():
                    worker.quit()
                    worker.wait(1000)
                worker.deleteLater()
            except RuntimeError:
                pass
            setattr(self, attr, None)


# ── Drawer overlay ────────────────────────────────────────────────────────────

class StudentProfileDrawer(QWidget):
    """
    Full-area overlay with a slide-in panel from the right.
    Attach to the page container (parent of QScrollArea), not the scrollable page.
    """

    PANEL_WIDTH   = 720
    ANIM_DURATION = 320

    def __init__(self, host: QWidget):
        super().__init__(host)
        self._host       = host
        self._is_open    = False
        self._slide_anim = None

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.hide()

        self._backdrop = QFrame(self)
        self._backdrop.setObjectName("profileBackdrop")
        self._backdrop.setStyleSheet(
            "#profileBackdrop { background-color: rgba(0, 0, 0, 0.45); }")
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
        x = self.width() - self.PANEL_WIDTH if open_state else self.width()
        self._panel.setGeometry(QRect(x, 0, self.PANEL_WIDTH, h))

    def open_drawer(self, student: dict):
        self._panel.load_student(student)
        self._sync_geometry()
        self.show()
        self.raise_()
        self._is_open = True

        start = QRect(self.width(), 0, self.PANEL_WIDTH, self.height())
        end   = QRect(
            self.width() - self.PANEL_WIDTH, 0, self.PANEL_WIDTH, self.height())
        self._run_slide(start, end)

    def close_drawer(self):
        if not self._is_open:
            return
        # Cancel any in-flight DB loaders so they don't write to a
        # stale widget after the panel has been hidden/replaced.
        self._panel._cancel_loaders()
        start         = self._panel.geometry()
        end           = QRect(self.width(), 0, self.PANEL_WIDTH, self.height())
        self._is_open = False
        self._run_slide(start, end, on_finished=self.hide)

    def _run_slide(self, start: QRect, end: QRect, on_finished=None):
        if (self._slide_anim
                and self._slide_anim.state()
                == QPropertyAnimation.State.Running):
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