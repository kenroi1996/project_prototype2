"""
Counselor Portal — Interventions Page
=======================================
AI-powered intervention advisor using local Ollama (offline).

Layout
------
  ┌─ Term + student selector ──────────────────────────────────────┐
  │  AY [combo] Sem [combo] [Load]  ── Student [search/combo]     │
  └────────────────────────────────────────────────────────────────┘
  ┌─ Left: Student profile ──────┐  ┌─ Right: AI Recommendations ─┐
  │  Name, score, factors        │  │  Per-student cards  OR       │
  │  Background tags             │  │  Cohort summary cards        │
  └──────────────────────────────┘  └────────────────────────────┘
  [Analyze Selected Student]   [Cohort Summary — All / Filtered]
"""
from __future__ import annotations
import json
import re

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QComboBox, QLineEdit, QScrollArea, QStackedWidget,
    QSizePolicy, QTextEdit, QProgressBar, QDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QMessageBox, QSpacerItem,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QDate
from PyQt6.QtGui import QColor, QFont

from services.data_store    import DataStore
from services.system_config import SystemConfig


# ── Prompts ───────────────────────────────────────────────────────────────────

_SYSTEM_PER_STUDENT = """You are a JSON API. Output only raw JSON. No explanation, no prose, no markdown.
Given a student risk profile, return a JSON array of 3 intervention objects.
Each object has these exact keys: type, action, rationale, timeline, priority.
type values: Academic Support, Financial Aid, Counseling, Program Guidance, Peer Support
timeline values: Immediate, Within 2 weeks, This semester
Start your response with [ and end with ]"""

_USER_PER_STUDENT = """Student: {name} | {program} | {college}
Risk: {score}% {risk_label} | Top factor: {factors}
Exam: {exam_score} | HS GPA: {hs_gpa}

["""

_SYSTEM_COHORT = """You are a JSON API. Output only raw JSON. No explanation, no prose, no markdown.
Given cohort risk data, return a JSON array of 3 systemic issue objects.
Each object has these exact keys: issue, affected_count, description, recommended_action, priority.
Start your response with [ and end with ]"""

_USER_COHORT = """Cohort: {term} | At-risk: {total} (High={high}, Moderate={moderate})
Top factors: {factors_summary}
By college: {college_summary}

["""


# ── Ollama worker ─────────────────────────────────────────────────────────────

class _OllamaWorker(QThread):
    finished = pyqtSignal(str)    # raw JSON string
    error    = pyqtSignal(str)

    def __init__(self, system: str, user: str):
        super().__init__()
        self._system = system
        self._user   = user

    def run(self):
        try:
            import requests
            url   = SystemConfig.ollama_url().rstrip("/")
            model = SystemConfig.ollama_model()

            # Prefill the model response with "[" — forces JSON array output.
            # The model will continue from "[" rather than reasoning in prose.
            prefilled_prompt = (
                f"<|im_start|>system\n{self._system}<|im_end|>\n"
                f"<|im_start|>user\n{self._user}<|im_end|>\n"
                f"<|im_start|>assistant\n["
            )
            payload = {
                "model":  model,
                "prompt": prefilled_prompt,
                "stream": True,
                "raw":    True,   # bypass Ollama's template so prefill works
                "options": {
                    "temperature": 0.2,
                    "top_p":       0.9,
                    "num_predict": 4096,
                    "num_ctx":     4096,
                    "stop":        ["<|im_end|>"],
                },
            }
            resp = requests.post(
                f"{url}/api/generate",
                json=payload,
                stream=True,
                timeout=(10, None),  # 10s to connect, unlimited read
            )
            if resp.status_code != 200:
                self.error.emit(
                    f"Ollama returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
                return

            # Collect streamed tokens
            raw = ""
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    raw  += chunk.get("response", "")
                    if chunk.get("done"):
                        break
                except Exception:
                    continue

            # Strip <think>…</think> blocks (qwen3 chain-of-thought)
            raw  = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            # Strip markdown fences
            raw  = re.sub(r"```(?:json)?", "", raw).replace("```", "").strip()
            # We prefilled "[" — prepend it back so parsing works
            if not raw.strip().startswith("["):
                raw = "[" + raw
            self.finished.emit(raw)

        except Exception as e:
            self.error.emit(str(e))


# ── DB save worker ────────────────────────────────────────────────────────────

class _SaveWorker(QThread):
    finished = pyqtSignal()
    error    = pyqtSignal(str)

    def __init__(self, record: dict):
        super().__init__()
        self._record = record

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No database connection.")
            return
        try:
            from services.auth_service import AuthService
            user = AuthService.current_user() or {}
            counselor_id = user.get("user_id")

            r = self._record
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO public.interventions
                        (student_id, counselor_id, academic_year, semester,
                         mode, risk_score, risk_label, risk_factors,
                         recommendations, logged_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb, NOW())
                """, (
                    r.get("student_id"),
                    counselor_id,
                    r.get("academic_year"),
                    r.get("semester"),
                    r.get("mode", "per_student"),
                    r.get("risk_score"),
                    r.get("risk_label"),
                    r.get("risk_factors"),
                    json.dumps(r.get("recommendations", [])),
                ))
            conn.commit()
            self.finished.emit()
        except Exception as e:
            try:
                DataStore.get().db_conn.rollback()
            except Exception:
                pass
            self.error.emit(str(e))


# ── Intervention record loader ────────────────────────────────────────────────

class _InterventionRecordLoader(QThread):
    """Load all intervention records for a given term from DB."""
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

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
                cur.execute("""
                    SELECT
                        intervention_id, student_id, counselor_id,
                        academic_year, semester, mode,
                        risk_score, risk_label, risk_factors,
                        recommendations, notes, logged_at
                    FROM public.interventions
                    WHERE academic_year = %s AND semester = %s
                    ORDER BY logged_at ASC
                """, (self._ay, self._sem))
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            self.finished.emit(rows)
        except Exception as e:
            self.error.emit(str(e))


class _InterventionReportWorker(QThread):
    """Generate intervention PDF off the main thread."""
    finished = pyqtSignal(str)   # saved file path
    error    = pyqtSignal(str)

    def __init__(self, records, term_label, academic_year, semester, save_path):
        super().__init__()
        self._records       = records
        self._term_label    = term_label
        self._academic_year = academic_year
        self._semester      = semester
        self._save_path     = save_path

    def run(self):
        try:
            from services.report_generator import InterventionReportGenerator
            from services.system_config    import SystemConfig
            gen = InterventionReportGenerator(
                records       = self._records,
                term_label    = self._term_label,
                academic_year = self._academic_year,
                semester      = self._semester,
                institution   = SystemConfig.institution(),
            )
            buf = gen.build_bytes()
            with open(self._save_path, "wb") as f:
                f.write(buf.getvalue())
            self.finished.emit(self._save_path)
        except Exception as e:
            self.error.emit(str(e))


# ── Term/student loader ───────────────────────────────────────────────────────

class _TermLoader(QThread):
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No DB connection.")
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT t.academic_year, t.semester
                    FROM   public.fact_student_academic_risk fsr
                    JOIN   public.dim_academic_term t ON t.term_key = fsr.term_key
                    ORDER  BY t.academic_year DESC, t.semester DESC
                """)
                self.finished.emit(cur.fetchall())
        except Exception as e:
            self.error.emit(str(e))


class _StudentLoader(QThread):
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    _SQL = """
        SELECT
            ds.student_id,
            TRIM(COALESCE(ds.first_name,'') || ' ' ||
                 COALESCE(ds.last_name,''))             AS full_name,
            COALESCE(dp.program_name,'Unknown')         AS program,
            COALESCE(dp.college,'—')                    AS college,
            COALESCE(rl.risk_label,'Low')               AS risk_label,
            fsr.predicted_risk_score,
            fsr.entrance_exam_score,
            fsr.high_school_gpa,
            fsr.primary_factor
        FROM  public.fact_student_academic_risk fsr
        JOIN  public.dim_academic_term t
              ON t.term_key       = fsr.term_key
        JOIN  public.dim_student ds
              ON ds.student_key   = fsr.student_key
        LEFT JOIN public.dim_program dp
              ON dp.program_key   = fsr.program_key
        LEFT JOIN public.dim_risk_level rl
              ON rl.risk_level_id = fsr.risk_level_id
        WHERE t.academic_year = %s AND t.semester = %s
          AND rl.risk_label IN ('High','Medium')
        ORDER BY fsr.predicted_risk_score DESC NULLS LAST
    """

    def __init__(self, ay: str, sem: int):
        super().__init__()
        self._ay, self._sem = ay, sem

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No DB connection.")
            return
        try:
            with conn.cursor() as cur:
                cur.execute(self._SQL, (self._ay, self._sem))
                cols = [d[0] for d in cur.description]
                self.finished.emit(
                    [dict(zip(cols, r)) for r in cur.fetchall()])
        except Exception as e:
            self.error.emit(str(e))


# ── Helper widgets ────────────────────────────────────────────────────────────

def _risk_color(label: str) -> str:
    lc = label.lower()
    if "high" in lc:               return "#ff5b5b"
    if "medium" in lc or "mod" in lc: return "#f5b335"
    return "#34d399"


def _rec_card(rec: dict, idx: int) -> QWidget:
    """
    Clean recommendation row — no box, just a left accent stripe
    with generous whitespace between entries.
    """
    _p = rec.get("priority", idx + 1)
    priority = (
        1 if str(_p).lower() in ("high", "1") else
        2 if str(_p).lower() in ("medium", "moderate", "2") else
        3 if str(_p).lower() in ("low", "3") else
        int(_p) if str(_p).isdigit() else idx + 1
    )
    rtype    = rec.get("type",     "—")
    action   = rec.get("action",   "—")
    rat      = rec.get("rationale","—")
    timeline = rec.get("timeline", "—")

    type_colors = {
        "Academic Support": "#4f8cff",
        "Financial Aid":    "#f5b335",
        "Counseling":       "#a78bfa",
        "Program Guidance": "#34d399",
        "Peer Support":     "#f59e0b",
    }
    color = type_colors.get(rtype, "#8b949e")

    # Outer: horizontal — accent bar | content
    outer = QWidget()
    outer.setStyleSheet("background: transparent;")
    row = QHBoxLayout(outer)
    row.setContentsMargins(0, 6, 0, 6)
    row.setSpacing(0)

    # Left accent stripe (4 px wide, full height)
    stripe = QFrame()
    stripe.setFixedWidth(4)
    stripe.setStyleSheet(
        f"background:{color}; border-radius:2px; margin-right:0px;")
    row.addWidget(stripe)

    # Content area
    content_w = QWidget()
    content_w.setStyleSheet("background:transparent;")
    cl = QVBoxLayout(content_w)
    cl.setContentsMargins(18, 2, 8, 2)
    cl.setSpacing(5)

    # Type tag + timeline on same row
    meta = QHBoxLayout()
    meta.setSpacing(10)
    type_lbl = QLabel(rtype.upper())
    type_lbl.setStyleSheet(
        f"color:{color}; font-size:11px; font-weight:700; "
        "letter-spacing:0.6px; background:transparent;"
    )
    tl_lbl = QLabel(f"⏱  {timeline}")
    tl_lbl.setStyleSheet(
        "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;"
    )
    meta.addWidget(type_lbl)
    meta.addWidget(tl_lbl)
    meta.addStretch()
    cl.addLayout(meta)

    # Action — primary text
    action_lbl = QLabel(action)
    action_lbl.setWordWrap(True)
    action_lbl.setStyleSheet(
        "color:#e8eaf0; font-size:14px; font-weight:600; "
        "line-height:1.5; background:transparent;"
    )
    cl.addWidget(action_lbl)

    # Rationale — secondary text
    rat_lbl = QLabel(rat)
    rat_lbl.setWordWrap(True)
    rat_lbl.setStyleSheet(
        "color:rgba(255,255,255,0.50); font-size:12px; "
        "line-height:1.5; background:transparent;"
    )
    cl.addWidget(rat_lbl)
    row.addWidget(content_w, 1)
    return outer


def _cohort_card(issue: dict, idx: int) -> QFrame:
    """Single cohort-level issue card."""
    _p = issue.get("priority", idx + 1)
    priority = (
        1 if str(_p).lower() in ("high", "1") else
        2 if str(_p).lower() in ("medium", "moderate", "2") else
        3 if str(_p).lower() in ("low", "3") else
        int(_p) if str(_p).isdigit() else idx + 1
    )
    title    = issue.get("issue",              "—")
    count    = issue.get("affected_count",      0)
    desc     = issue.get("description",         "—")
    action   = issue.get("recommended_action",  "—")

    colors = ["#ff5b5b", "#f5b335", "#4f8cff", "#34d399", "#a78bfa"]
    color  = colors[min(idx, len(colors) - 1)]

    card = QFrame()
    card.setStyleSheet(f"""
        QFrame {{
            background: rgba(255,255,255,0.03);
            border: 1px solid {color}44;
            border-left: 3px solid {color};
            border-radius: 10px;
            margin-bottom: 8px;
        }}
    """)
    lo = QVBoxLayout(card)
    lo.setContentsMargins(16, 14, 16, 14)
    lo.setSpacing(6)

    hdr = QHBoxLayout()
    issue_lbl = QLabel(f"#{priority}  {title}")
    issue_lbl.setStyleSheet(
        f"color:{color}; font-size:14px; font-weight:700; background:transparent;"
    )
    count_lbl = QLabel(f"{count:,} students affected")
    count_lbl.setStyleSheet(
        "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;"
    )
    hdr.addWidget(issue_lbl)
    hdr.addStretch()
    hdr.addWidget(count_lbl)
    lo.addLayout(hdr)

    desc_lbl = QLabel(desc)
    desc_lbl.setWordWrap(True)
    desc_lbl.setStyleSheet(
        "color:rgba(255,255,255,0.55); font-size:12px; background:transparent;"
    )
    lo.addWidget(desc_lbl)

    act_lbl = QLabel(f"→  {action}")
    act_lbl.setWordWrap(True)
    act_lbl.setStyleSheet(
        "color:#e8eaf0; font-size:13px; font-weight:600; background:transparent;"
    )
    lo.addWidget(act_lbl)
    return card


def _cohort_row(issue: dict, idx: int) -> QWidget:
    """
    Clean cohort issue row — same stripe-based layout as _rec_card.
    """
    _p = issue.get("priority", idx + 1)
    priority = (
        1 if str(_p).lower() in ("high", "1") else
        2 if str(_p).lower() in ("medium", "moderate", "2") else
        3 if str(_p).lower() in ("low", "3") else
        int(_p) if str(_p).isdigit() else idx + 1
    )
    title  = issue.get("issue",             "—")
    count  = issue.get("affected_count",     0)
    desc   = issue.get("description",        "—")
    action = issue.get("recommended_action", "—")

    stripe_colors = ["#ff5b5b", "#f5b335", "#4f8cff", "#34d399", "#a78bfa"]
    color = stripe_colors[min(idx, len(stripe_colors) - 1)]

    outer = QWidget()
    outer.setStyleSheet("background:transparent;")
    row = QHBoxLayout(outer)
    row.setContentsMargins(0, 8, 0, 8)
    row.setSpacing(0)

    stripe = QFrame()
    stripe.setFixedWidth(4)
    stripe.setStyleSheet(f"background:{color}; border-radius:2px;")
    row.addWidget(stripe)

    content_w = QWidget()
    content_w.setStyleSheet("background:transparent;")
    cl = QVBoxLayout(content_w)
    cl.setContentsMargins(18, 2, 8, 2)
    cl.setSpacing(5)

    # Title + count
    meta = QHBoxLayout()
    title_lbl = QLabel(title)
    title_lbl.setStyleSheet(
        f"color:{color}; font-size:14px; font-weight:700; background:transparent;"
    )
    count_lbl = QLabel(
        f"{int(count):,} students" if str(count).isdigit() else str(count))
    count_lbl.setStyleSheet(
        "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;"
    )
    meta.addWidget(title_lbl)
    meta.addStretch()
    meta.addWidget(count_lbl)
    cl.addLayout(meta)

    # Description
    desc_lbl = QLabel(desc)
    desc_lbl.setWordWrap(True)
    desc_lbl.setStyleSheet(
        "color:rgba(255,255,255,0.50); font-size:12px; background:transparent;"
    )
    cl.addWidget(desc_lbl)

    # Action
    act_lbl = QLabel(action)
    act_lbl.setWordWrap(True)
    act_lbl.setStyleSheet(
        "color:#e8eaf0; font-size:13px; font-weight:600; background:transparent;"
    )
    cl.addWidget(act_lbl)
    row.addWidget(content_w, 1)
    return outer




# ── Intervention detail dialog ────────────────────────────────────────────────

class _InterventionDetailDialog(QDialog):
    """
    Shows full AI recommendations for a single intervention record.
    Frameless, draggable, closable with ✕ button.
    """

    def __init__(self, row: dict, recs: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Intervention Details")
        self.setModal(True)
        self.resize(640, 560)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._row  = row
        self._recs = recs
        self._drag_pos = None
        self._build_ui()
        self._apply_styles()

    # ── Drag support ──────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self._drag_pos is not None
                and event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)

        card = QFrame()
        card.setObjectName("detailCard")
        outer.addWidget(card)

        root = QVBoxLayout(card)
        root.setContentsMargins(28, 22, 28, 24)
        root.setSpacing(0)

        # ── Header ─────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(0)

        info_col = QVBoxLayout()
        info_col.setSpacing(4)

        mode    = self._row.get("mode", "per_student")
        sid     = self._row.get("student_id")
        name    = (str(self._row.get("student_name") or "").strip()
                   or str(sid or "—"))
        ay      = self._row.get("academic_year", "—")
        sem_n   = self._row.get("semester")
        sem_s   = "1st Semester" if sem_n == 1 else "2nd Semester" if sem_n == 2 else ""
        term    = f"{sem_s}  AY {ay}" if sem_s else ay
        risk_l  = str(self._row.get("risk_label") or "—")
        score   = self._row.get("risk_score")
        logged  = self._row.get("logged_at")
        logged_s = (logged.strftime("%B %d, %Y  %H:%M")
                    if hasattr(logged, "strftime") else str(logged or "")[:16])

        if mode == "cohort":
            headline = QLabel("Cohort Systemic Issues")
            headline.setObjectName("detailHeadline")
            sub_text = f"{term}  ·  {risk_l}"
        else:
            headline = QLabel(name)
            headline.setObjectName("detailHeadline")
            sub_text = f"ID {sid}  ·  {term}"
            if score:
                risk_color = (
                    "#ff5b5b" if "high"   in risk_l.lower() else
                    "#f5b335" if "medium" in risk_l.lower() else "#34d399"
                )
                score_lbl = QLabel(
                    f"● {risk_l}  {float(score):.1f}% risk")
                score_lbl.setStyleSheet(
                    f"color:{risk_color}; font-size:12px; "
                    "font-weight:600; background:transparent;"
                )
                info_col.addWidget(score_lbl)

        headline.setWordWrap(True)
        info_col.insertWidget(0, headline)

        sub = QLabel(sub_text)
        sub.setStyleSheet(
            "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;"
        )
        info_col.addWidget(sub)

        if logged_s:
            date_lbl = QLabel(f"Generated  {logged_s}")
            date_lbl.setStyleSheet(
                "color:rgba(255,255,255,0.25); font-size:10px; background:transparent;"
            )
            info_col.addWidget(date_lbl)

        hdr.addLayout(info_col, 1)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("detailCloseBtn")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setToolTip("Close")
        close_btn.clicked.connect(self.reject)
        hdr.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignTop)

        root.addLayout(hdr)
        root.addSpacing(14)

        # ── Divider ────────────────────────────────────────────────────
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("color:rgba(255,255,255,0.07);")
        root.addWidget(div)
        root.addSpacing(12)

        # ── Recommendations scroll ─────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { background:transparent; border:none; }"
        )

        host = QWidget()
        host.setStyleSheet("background:transparent;")
        host_lo = QVBoxLayout(host)
        host_lo.setContentsMargins(0, 0, 8, 0)
        host_lo.setSpacing(4)

        if not self._recs:
            empty = QLabel("No recommendation data available for this record.")
            empty.setStyleSheet(
                "color:rgba(255,255,255,0.30); font-size:12px; background:transparent;"
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            host_lo.addWidget(empty)
        else:
            for i, rec in enumerate(self._recs):
                if mode == "cohort":
                    host_lo.addWidget(_cohort_row(rec, i))
                else:
                    host_lo.addWidget(_rec_card(rec, i))
                if i < len(self._recs) - 1:
                    sep = QFrame()
                    sep.setFrameShape(QFrame.Shape.HLine)
                    sep.setStyleSheet("color:rgba(255,255,255,0.06);")
                    host_lo.addWidget(sep)

        host_lo.addStretch()
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        root.addSpacing(12)

        # ── Close button at bottom ─────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_bottom = QPushButton("Close")
        close_bottom.setObjectName("detailCloseBottomBtn")
        close_bottom.setFixedHeight(34)
        close_bottom.setMinimumWidth(100)
        close_bottom.setCursor(Qt.CursorShape.PointingHandCursor)
        close_bottom.clicked.connect(self.reject)
        btn_row.addWidget(close_bottom)
        root.addLayout(btn_row)

    def _apply_styles(self):
        self.setStyleSheet("""
            _InterventionDetailDialog { background: transparent; }
            #detailCard {
                background: #13172a;
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 16px;
            }
            #detailHeadline {
                color: #e8eaf0;
                font-size: 16px;
                font-weight: bold;
                background: transparent;
            }
            #detailCloseBtn {
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 7px;
                color: rgba(255,255,255,0.35);
                font-size: 13px; font-weight: bold;
            }
            #detailCloseBtn:hover {
                background: rgba(255,91,91,0.15);
                border-color: rgba(255,91,91,0.35);
                color: #ff5b5b;
            }
            #detailCloseBottomBtn {
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px;
                color: rgba(255,255,255,0.70);
                font-size: 12px; font-weight: 600;
                padding: 0 20px;
            }
            #detailCloseBottomBtn:hover {
                background: rgba(255,255,255,0.12);
                color: #e8eaf0;
            }
            QScrollBar:vertical { background:transparent; width:8px; }
            QScrollBar::handle:vertical {
                background:rgba(255,255,255,0.12);
                border-radius:4px; min-height:30px;
            }
            QScrollBar::handle:vertical:hover { background:rgba(255,255,255,0.22); }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical { height:0; }
        """)

# ── Intervention log workers ───────────────────────────────────────────────────

class _LogLoader(QThread):
    """Load all intervention log records with optional filters."""
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def __init__(self, filters: dict):
        super().__init__()
        self._filters = filters

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No database connection.")
            return
        try:
            f = self._filters
            clauses = []
            params  = []

            if f.get("academic_year"):
                clauses.append("i.academic_year = %s")
                params.append(f["academic_year"])
            if f.get("semester"):
                clauses.append("i.semester = %s")
                params.append(int(f["semester"]))
            if f.get("mode"):
                clauses.append("i.mode = %s")
                params.append(f["mode"])
            if f.get("student_id"):
                clauses.append("i.student_id ILIKE %s")
                params.append(f"%{f['student_id']}%")
            if f.get("student_name"):
                name_q = f"%{f['student_name']}%"
                clauses.append(
                    "(TRIM(COALESCE(ds.first_name,'') || ' ' || "
                    "COALESCE(ds.last_name,'')) ILIKE %s)"
                )
                params.append(name_q)
            if f.get("date_from"):
                clauses.append("i.logged_at >= %s")
                params.append(f["date_from"])
            if f.get("date_to"):
                clauses.append("i.logged_at <= %s")
                params.append(f["date_to"] + " 23:59:59")

            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

            sql = f"""
                SELECT
                    i.intervention_id,
                    i.student_id,
                    TRIM(COALESCE(ds.first_name,'') || ' ' ||
                         COALESCE(ds.last_name,''))         AS student_name,
                    i.academic_year,
                    i.semester,
                    i.mode,
                    i.risk_score,
                    i.risk_label,
                    i.risk_factors,
                    jsonb_array_length(
                        COALESCE(i.recommendations,'[]'::jsonb)
                    )                                       AS rec_count,
                    i.logged_at
                FROM public.interventions i
                LEFT JOIN public.dim_student ds
                       ON ds.student_id = i.student_id
                {where}
                ORDER BY i.logged_at DESC
            """
            with conn.cursor() as cur:
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description]
                self.finished.emit(
                    [dict(zip(cols, r)) for r in cur.fetchall()])
        except Exception as e:
            self.error.emit(str(e))


class _LogDeleter(QThread):
    """Delete a single intervention log record by ID."""
    finished = pyqtSignal(int)   # deleted intervention_id
    error    = pyqtSignal(str)

    def __init__(self, intervention_id: int):
        super().__init__()
        self._id = intervention_id

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No database connection.")
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM public.interventions WHERE intervention_id = %s",
                    (self._id,)
                )
            conn.commit()
            self.finished.emit(self._id)
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            self.error.emit(str(e))


# ── Intervention log dialog ────────────────────────────────────────────────────

class InterventionLogDialog(QDialog):
    """
    Full-featured intervention log viewer with search, filter,
    pagination and per-row delete.
    """
    PAGE_SIZE = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Intervention Logs")
        self.setModal(True)
        self.resize(1100, 680)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._all_rows:  list[dict] = []
        self._page:      int        = 0
        self._loader:    _LogLoader  | None = None
        self._deleter:   _LogDeleter | None = None

        self._drag_pos = None
        self._build_ui()
        self._apply_styles()
        self._load()

    # ── Dragging ───────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self._drag_pos is not None
                and event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    # ── UI ─────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)

        card = QFrame()
        card.setObjectName("logCard")
        outer.addWidget(card)

        root = QVBoxLayout(card)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        # ── Header ──────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        title = QLabel("Intervention Logs")
        title.setStyleSheet(
            "color:#e8eaf0; font-size:16px; font-weight:bold; background:transparent;")
        sub = QLabel("All AI-generated intervention records")
        sub.setStyleSheet(
            "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;")
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.addWidget(title)
        title_col.addWidget(sub)
        hdr.addLayout(title_col)
        hdr.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setObjectName("logCloseBtn")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.reject)
        hdr.addWidget(close_btn)
        root.addLayout(hdr)

        # ── Search + filter row 1 ────────────────────────────────────────
        f1 = QHBoxLayout()
        f1.setSpacing(10)

        self._sid_search = QLineEdit()
        self._sid_search.setObjectName("logSearch")
        self._sid_search.setPlaceholderText("🔍  Student ID")
        self._sid_search.setFixedWidth(150)
        self._sid_search.textChanged.connect(self._on_filter_changed)

        self._name_search = QLineEdit()
        self._name_search.setObjectName("logSearch")
        self._name_search.setPlaceholderText("🔍  Student Name")
        self._name_search.setFixedWidth(200)
        self._name_search.textChanged.connect(self._on_filter_changed)

        self._ay_filter = QComboBox()
        self._ay_filter.setObjectName("logCombo")
        self._ay_filter.addItem("All Terms")
        self._ay_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ay_filter.currentIndexChanged.connect(self._on_filter_changed)

        self._sem_filter = QComboBox()
        self._sem_filter.setObjectName("logCombo")
        self._sem_filter.addItems(["All Semesters", "1st Semester", "2nd Semester"])
        self._sem_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sem_filter.currentIndexChanged.connect(self._on_filter_changed)

        self._mode_filter = QComboBox()
        self._mode_filter.setObjectName("logCombo")
        self._mode_filter.addItems(["All Types", "per_student", "cohort"])
        self._mode_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mode_filter.currentIndexChanged.connect(self._on_filter_changed)

        self._date_from = QLineEdit()
        self._date_from.setObjectName("logSearch")
        self._date_from.setPlaceholderText("From (YYYY-MM-DD)")
        self._date_from.setFixedWidth(148)
        self._date_from.textChanged.connect(self._on_filter_changed)

        self._date_to = QLineEdit()
        self._date_to.setObjectName("logSearch")
        self._date_to.setPlaceholderText("To (YYYY-MM-DD)")
        self._date_to.setFixedWidth(148)
        self._date_to.textChanged.connect(self._on_filter_changed)

        clear_btn = QPushButton("✕  Clear")
        clear_btn.setObjectName("logClearBtn")
        clear_btn.setFixedHeight(32)
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._clear_filters)

        for w in [self._sid_search, self._name_search,
                  self._ay_filter, self._sem_filter, self._mode_filter,
                  self._date_from, self._date_to, clear_btn]:
            f1.addWidget(w)
        f1.addStretch()

        self._count_lbl = QLabel("")
        self._count_lbl.setObjectName("logCount")
        f1.addWidget(self._count_lbl)
        root.addLayout(f1)

        # ── Table ────────────────────────────────────────────────────────
        self._table = QTableWidget()
        self._table.setObjectName("logTable")
        self._table.setColumnCount(10)
        self._table.setHorizontalHeaderLabels([
            "ID", "Student ID", "Name", "Term",
            "Type", "Risk", "Recommendations", "Logged At",
            "View", "Delete",
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setHighlightSections(False)
        self._table.setShowGrid(False)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        for col, mode in [
            (0, QHeaderView.ResizeMode.ResizeToContents),
            (3, QHeaderView.ResizeMode.ResizeToContents),
            (4, QHeaderView.ResizeMode.ResizeToContents),
            (5, QHeaderView.ResizeMode.ResizeToContents),
            (6, QHeaderView.ResizeMode.ResizeToContents),
            (7, QHeaderView.ResizeMode.ResizeToContents),
            (8, QHeaderView.ResizeMode.Fixed),
            (9, QHeaderView.ResizeMode.Fixed),
        ]:
            self._table.horizontalHeader().setSectionResizeMode(col, mode)
        self._table.setColumnWidth(8, 56)
        self._table.setColumnWidth(9, 56)
        root.addWidget(self._table, 1)

        # ── Pagination ───────────────────────────────────────────────────
        pag = QHBoxLayout()
        pag.setSpacing(8)

        self._prev_btn = QPushButton("‹  Prev")
        self._prev_btn.setObjectName("logPagBtn")
        self._prev_btn.setFixedHeight(30)
        self._prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._prev_btn.clicked.connect(self._prev_page)
        self._prev_btn.setEnabled(False)

        self._page_lbl = QLabel("Page 1 of 1")
        self._page_lbl.setObjectName("logCount")
        self._page_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_lbl.setFixedWidth(110)

        self._next_btn = QPushButton("Next  ›")
        self._next_btn.setObjectName("logPagBtn")
        self._next_btn.setFixedHeight(30)
        self._next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_btn.clicked.connect(self._next_page)
        self._next_btn.setEnabled(False)

        self._status_lbl = QLabel("")
        self._status_lbl.setObjectName("logCount")

        pag.addWidget(self._prev_btn)
        pag.addWidget(self._page_lbl)
        pag.addWidget(self._next_btn)
        pag.addStretch()
        pag.addWidget(self._status_lbl)
        root.addLayout(pag)

    # ── Loading ─────────────────────────────────────────────────────────────────

    def _build_filters(self) -> dict:
        ay  = self._ay_filter.currentText()
        sem = self._sem_filter.currentIndex()   # 0=All, 1=1st, 2=2nd
        mode = self._mode_filter.currentText()
        return {
            "academic_year": ay  if ay  != "All Terms"     else "",
            "semester":      sem if sem != 0               else "",
            "mode":          mode if mode != "All Types"   else "",
            "student_id":    self._sid_search.text().strip(),
            "student_name":  self._name_search.text().strip(),
            "date_from":     self._date_from.text().strip(),
            "date_to":       self._date_to.text().strip(),
        }

    def _load(self):
        self._status_lbl.setText("Loading…")
        self._loader = _LogLoader(self._build_filters())
        self._loader.finished.connect(self._on_loaded)
        self._loader.error.connect(self._on_load_error)
        self._loader.finished.connect(self._loader.deleteLater)
        self._loader.error.connect(self._loader.deleteLater)
        self._loader.start()

    def _on_loaded(self, rows: list):
        self._all_rows = rows
        self._page     = 0
        self._populate_ay_filter(rows)
        self._render_page()
        self._status_lbl.setText("")

    def _on_load_error(self, msg: str):
        self._status_lbl.setText(f"⚠ {msg}")

    def _populate_ay_filter(self, rows: list):
        current = self._ay_filter.currentText()
        self._ay_filter.blockSignals(True)
        self._ay_filter.clear()
        self._ay_filter.addItem("All Terms")
        seen = []
        for r in rows:
            ay = str(r.get("academic_year", ""))
            if ay and ay not in seen:
                seen.append(ay)
                self._ay_filter.addItem(ay)
        idx = self._ay_filter.findText(current)
        self._ay_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self._ay_filter.blockSignals(False)

    def _on_filter_changed(self):
        """Debounce: reload 400ms after last keystroke."""
        if not hasattr(self, "_filter_timer"):
            self._filter_timer = QTimer(self)
            self._filter_timer.setSingleShot(True)
            self._filter_timer.timeout.connect(self._load)
        self._filter_timer.start(400)

    def _clear_filters(self):
        for w in (self._sid_search, self._name_search,
                  self._date_from, self._date_to):
            w.blockSignals(True); w.clear(); w.blockSignals(False)
        for c in (self._ay_filter, self._sem_filter, self._mode_filter):
            c.blockSignals(True); c.setCurrentIndex(0); c.blockSignals(False)
        self._load()

    # ── Pagination ──────────────────────────────────────────────────────────────

    def _total_pages(self) -> int:
        return max(1, (len(self._all_rows) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)

    def _prev_page(self):
        if self._page > 0:
            self._page -= 1
            self._render_page()

    def _next_page(self):
        if self._page < self._total_pages() - 1:
            self._page += 1
            self._render_page()

    # ── Table render ────────────────────────────────────────────────────────────

    def _render_page(self):
        start  = self._page * self.PAGE_SIZE
        end    = start + self.PAGE_SIZE
        rows   = self._all_rows[start:end]
        total  = len(self._all_rows)
        pages  = self._total_pages()

        self._count_lbl.setText(
            f"{total:,} record{'s' if total != 1 else ''}"
        )
        self._page_lbl.setText(f"Page {self._page+1} of {pages}")
        self._prev_btn.setEnabled(self._page > 0)
        self._next_btn.setEnabled(self._page < pages - 1)

        self._table.setRowCount(0)
        self._table.setRowCount(len(rows))
        self._table.setUpdatesEnabled(False)

        for ri, row in enumerate(rows):
            iid     = row.get("intervention_id", "")
            sid     = str(row.get("student_id")  or "—")
            name    = str(row.get("student_name") or "—").strip() or "—"
            ay      = str(row.get("academic_year") or "—")
            sem_n   = row.get("semester")
            sem_s   = ("1st" if sem_n == 1 else "2nd" if sem_n == 2 else "—")
            term    = f"{ay} S{sem_n}" if sem_n else ay
            mode    = str(row.get("mode") or "—")
            risk_l  = str(row.get("risk_label") or "—")
            rec_cnt = row.get("rec_count", 0)
            logged  = row.get("logged_at")
            logged_s = (logged.strftime("%b %d, %Y %H:%M")
                        if hasattr(logged, "strftime")
                        else str(logged or "—")[:16])

            risk_color = QColor(
                "#ff5b5b" if "high" in risk_l.lower() else
                "#f5b335" if "medium" in risk_l.lower() or "mod" in risk_l.lower()
                else "#34d399"
            )
            mode_label = "Per Student" if mode == "per_student" else "Cohort"

            cells = [
                (str(iid),        None),
                (sid,             None),
                (name,            None),
                (term,            None),
                (mode_label,      None),
                (risk_l,          risk_color),
                (f"{rec_cnt} recs", None),
                (logged_s,        None),
            ]

            for ci, (text, color) in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignVCenter |
                    Qt.AlignmentFlag.AlignLeft
                )
                if color:
                    item.setForeground(color)
                    item.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
                self._table.setItem(ri, ci, item)

            # View button — centered in cell
            view_btn = QPushButton("👁")
            view_btn.setObjectName("logViewBtn")
            view_btn.setFixedSize(36, 26)
            view_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            view_btn.setToolTip(f"View recommendations for intervention #{iid}")
            view_btn.clicked.connect(
                lambda _, r=row: self._on_view_clicked(r))
            view_cell = QWidget()
            view_cell.setStyleSheet("background:transparent;")
            view_cell_lo = QHBoxLayout(view_cell)
            view_cell_lo.setContentsMargins(0, 0, 0, 0)
            view_cell_lo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            view_cell_lo.addWidget(view_btn)
            self._table.setCellWidget(ri, 8, view_cell)

            # Delete button — centered in cell
            del_btn = QPushButton("🗑")
            del_btn.setObjectName("logDelBtn")
            del_btn.setFixedSize(36, 26)
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.setToolTip(f"Delete intervention #{iid}")
            del_btn.clicked.connect(
                lambda _, rid=iid: self._on_delete_clicked(rid))
            del_cell = QWidget()
            del_cell.setStyleSheet("background:transparent;")
            del_cell_lo = QHBoxLayout(del_cell)
            del_cell_lo.setContentsMargins(0, 0, 0, 0)
            del_cell_lo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            del_cell_lo.addWidget(del_btn)
            self._table.setCellWidget(ri, 9, del_cell)

        self._table.setUpdatesEnabled(True)

    # ── Delete ──────────────────────────────────────────────────────────────────

    def _on_view_clicked(self, row: dict):
        """Load full recommendations for this record and show detail dialog."""
        iid  = row.get("intervention_id")
        mode = row.get("mode", "per_student")
        recs = None

        # Recommendations may already be in the row if fully loaded
        if row.get("recommendations") is not None:
            recs = row["recommendations"]

        if recs is None:
            # Fetch from DB
            conn = DataStore.get().db_conn
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT recommendations FROM public.interventions "
                        "WHERE intervention_id = %s", (iid,))
                    db_row = cur.fetchone()
                    recs = db_row[0] if db_row else []
            except Exception as e:
                recs = []

        if isinstance(recs, str):
            try:
                recs = json.loads(recs)
            except Exception:
                recs = []

        dlg = _InterventionDetailDialog(row, recs or [], self)
        dlg.exec()

    def _on_delete_clicked(self, intervention_id: int):
        msg = QMessageBox(self)
        msg.setWindowTitle("Delete Intervention Log")
        msg.setText("Permanently delete this intervention record?")
        msg.setInformativeText(
            "Warning: This action will permanently delete the selected "
            "intervention log record. This operation cannot be undone. "
            "Are you sure you want to continue?"
        )
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setStandardButtons(
            QMessageBox.StandardButton.Cancel |
            QMessageBox.StandardButton.Yes
        )
        msg.setDefaultButton(QMessageBox.StandardButton.Cancel)
        msg.button(QMessageBox.StandardButton.Yes).setText("Delete")
        msg.setStyleSheet("""
            QMessageBox { background:#13172a; }
            QMessageBox QLabel {
                color:#e8eaf0; font-size:13px; background:transparent;
            }
            QMessageBox QPushButton {
                background:rgba(255,255,255,0.06);
                border:1px solid rgba(255,255,255,0.14);
                border-radius:8px; color:rgba(255,255,255,0.80);
                font-size:12px; font-weight:600;
                padding:8px 24px; min-width:80px;
            }
            QMessageBox QPushButton:hover {
                background:rgba(255,255,255,0.12);
            }
            QMessageBox QPushButton[text="Delete"] {
                background:rgba(255,91,91,0.15);
                border-color:rgba(255,91,91,0.40); color:#ff5b5b;
            }
            QMessageBox QPushButton[text="Delete"]:hover {
                background:rgba(255,91,91,0.28);
            }
        """)

        if msg.exec() != QMessageBox.StandardButton.Yes:
            return

        self._status_lbl.setText("Deleting…")
        self._deleter = _LogDeleter(intervention_id)
        self._deleter.finished.connect(self._on_deleted)
        self._deleter.error.connect(self._on_delete_error)
        self._deleter.finished.connect(self._deleter.deleteLater)
        self._deleter.error.connect(self._deleter.deleteLater)
        self._deleter.start()

    def _on_deleted(self, iid: int):
        self._status_lbl.setText(f"✓  Record #{iid} deleted.")
        self._all_rows = [
            r for r in self._all_rows if r.get("intervention_id") != iid]
        # Adjust page if last row on current page was removed
        if self._page >= self._total_pages():
            self._page = max(0, self._total_pages() - 1)
        self._render_page()
        QTimer.singleShot(
            3000, lambda: self._status_lbl.setText(""))

    def _on_delete_error(self, msg: str):
        self._status_lbl.setText(f"⚠ Delete failed: {msg[:80]}")

    # ── Styles ──────────────────────────────────────────────────────────────────

    def _apply_styles(self):
        self.setStyleSheet("""
            InterventionLogDialog { background: transparent; }
            #logCard {
                background:#13172a;
                border:1px solid rgba(255,255,255,0.10);
                border-radius:16px;
            }
            #logCloseBtn {
                background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.10);
                border-radius:7px; color:rgba(255,255,255,0.35);
                font-size:13px; font-weight:bold;
            }
            #logCloseBtn:hover {
                background:rgba(255,91,91,0.15);
                border-color:rgba(255,91,91,0.35); color:#ff5b5b;
            }
            QLineEdit#logSearch {
                background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.10);
                border-radius:8px; color:#e8eaf0;
                font-size:12px; padding:6px 10px;
            }
            QLineEdit#logSearch:focus {
                border-color:rgba(52,211,153,0.40);
            }
            QComboBox#logCombo {
                background:rgba(255,255,255,0.06);
                border:1px solid rgba(255,255,255,0.12);
                border-radius:8px; color:#e8eaf0;
                font-size:12px; padding:5px 10px; min-height:30px;
            }
            QComboBox#logCombo:hover { border-color:rgba(52,211,153,0.35); }
            QComboBox#logCombo::drop-down { border:none; width:16px; }
            QComboBox#logCombo QAbstractItemView {
                background:#1a1f35; color:#e8eaf0;
                selection-background-color:rgba(52,211,153,0.18);
            }
            QPushButton#logClearBtn {
                background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.10);
                border-radius:8px; color:rgba(255,255,255,0.50);
                font-size:11px; padding:0 12px;
            }
            QPushButton#logClearBtn:hover {
                background:rgba(255,255,255,0.10);
                color:rgba(255,255,255,0.80);
            }
            QTableWidget#logTable {
                background:transparent; border:none;
                color:rgba(255,255,255,0.85); font-size:12px;
                alternate-background-color:rgba(255,255,255,0.025);
                selection-background-color:rgba(79,140,255,0.15);
                selection-color:white; gridline-color:transparent;
            }
            QTableWidget#logTable QHeaderView::section {
                background:rgba(255,255,255,0.05);
                color:rgba(255,255,255,0.45);
                font-size:11px; font-weight:bold; border:none;
                border-right:1px solid rgba(255,255,255,0.06);
                padding:8px 10px;
            }
            QTableWidget#logTable QHeaderView::section:last {
                border-right:none;
            }
            QPushButton#logViewBtn {
                background:rgba(79,140,255,0.08);
                border:1px solid rgba(79,140,255,0.25);
                border-radius:6px; color:#4f8cff;
                font-size:13px;
            }
            QPushButton#logViewBtn:hover {
                background:rgba(79,140,255,0.20);
                border-color:rgba(79,140,255,0.50);
            }
            QPushButton#logDelBtn {
                background:rgba(255,91,91,0.08);
                border:1px solid rgba(255,91,91,0.25);
                border-radius:6px; color:#ff5b5b;
                font-size:13px;
            }
            QPushButton#logDelBtn:hover {
                background:rgba(255,91,91,0.20);
                border-color:rgba(255,91,91,0.50);
            }
            QPushButton#logPagBtn {
                background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.10);
                border-radius:7px; color:rgba(255,255,255,0.60);
                font-size:11px; padding:0 14px;
            }
            QPushButton#logPagBtn:hover {
                background:rgba(255,255,255,0.10);
                color:rgba(255,255,255,0.90);
            }
            QPushButton#logPagBtn:disabled {
                color:rgba(255,255,255,0.20);
                border-color:rgba(255,255,255,0.06);
            }
            #logCount {
                color:rgba(255,255,255,0.35); font-size:11px;
                background:transparent;
            }
            QScrollBar:vertical { background:transparent; width:8px; }
            QScrollBar::handle:vertical {
                background:rgba(255,255,255,0.12);
                border-radius:4px; min-height:30px;
            }
            QScrollBar::handle:vertical:hover { background:rgba(255,255,255,0.22); }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical { height:0; }
        """)



# ── Term selection dialog ─────────────────────────────────────────────────────

class _TermSelectDialog(QDialog):
    """Modal dialog: pick an academic term before exporting the report."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Academic Term")
        self.setModal(True)
        self.setFixedSize(420, 300)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._selected: tuple = ()
        self._build_ui()
        self._apply_styles()
        self._load_terms()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        card = QFrame()
        card.setObjectName("termSelCard")
        outer.addWidget(card)
        lo = QVBoxLayout(card)
        lo.setContentsMargins(28, 24, 28, 24)
        lo.setSpacing(12)

        title = QLabel("Export Intervention Report")
        title.setStyleSheet(
            "color:#e8eaf0; font-size:15px; font-weight:bold; background:transparent;")
        sub = QLabel("Select the academic term to include in the report.")
        sub.setWordWrap(True)
        sub.setStyleSheet(
            "color:rgba(255,255,255,0.40); font-size:11px; background:transparent;")
        lo.addWidget(title)
        lo.addWidget(sub)

        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("color:rgba(255,255,255,0.08);")
        lo.addWidget(div)

        lbl = QLabel("Academic Term")
        lbl.setStyleSheet(
            "color:rgba(255,255,255,0.55); font-size:11px; font-weight:600; background:transparent;")
        lo.addWidget(lbl)

        self._term_combo = QComboBox()
        self._term_combo.setObjectName("termSelCombo")
        self._term_combo.addItem("Loading…")
        self._term_combo.setEnabled(False)
        lo.addWidget(self._term_combo)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.35); font-size:10px; background:transparent;")
        lo.addWidget(self._status_lbl)
        lo.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("termSelCancelBtn")
        cancel_btn.setFixedHeight(36)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)

        self._confirm_btn = QPushButton("Export  →")
        self._confirm_btn.setObjectName("termSelConfirmBtn")
        self._confirm_btn.setFixedHeight(36)
        self._confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._confirm_btn.setEnabled(False)
        self._confirm_btn.clicked.connect(self._on_confirm)

        btn_row.addWidget(cancel_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._confirm_btn)
        lo.addLayout(btn_row)

    def _load_terms(self):
        conn = DataStore.get().db_conn
        if not conn:
            self._status_lbl.setText("No database connection.")
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT academic_year, semester
                    FROM   public.interventions
                    ORDER  BY academic_year DESC, semester DESC
                """)
                terms = cur.fetchall()
        except Exception as e:
            self._status_lbl.setText(f"Error: {e}")
            return

        self._term_combo.clear()
        if not terms:
            self._term_combo.addItem("No records found")
            self._status_lbl.setText("No intervention records yet. Run analyses first.")
            return

        self._term_combo.addItem("— Select a term —")
        for ay, sem in terms:
            sem_label = "1st Semester" if sem == 1 else "2nd Semester"
            self._term_combo.addItem(
                f"{sem_label}  ·  AY {ay}", userData=(ay, sem))
        self._term_combo.setEnabled(True)
        self._term_combo.currentIndexChanged.connect(self._on_term_changed)
        self._status_lbl.setText(f"{len(terms)} term(s) with intervention records.")

    def _on_term_changed(self, idx: int):
        data = self._term_combo.itemData(idx, Qt.ItemDataRole.UserRole)
        self._confirm_btn.setEnabled(data is not None)

    def _on_confirm(self):
        idx  = self._term_combo.currentIndex()
        data = self._term_combo.itemData(idx, Qt.ItemDataRole.UserRole)
        if data:
            self._selected = data
            self.accept()

    def selected_term(self) -> tuple:
        if not self._selected:
            return ("", 0, "")
        ay, sem = self._selected
        sem_label = "1st Semester" if sem == 1 else "2nd Semester"
        return (ay, sem, f"{sem_label}  AY {ay}")

    def _apply_styles(self):
        self.setStyleSheet("""
            #termSelCard {
                background:#13172a;
                border:1px solid rgba(255,255,255,0.10);
                border-radius:16px;
            }
            QComboBox#termSelCombo {
                background:rgba(255,255,255,0.06);
                border:1px solid rgba(255,255,255,0.14);
                border-radius:8px; color:#e8eaf0;
                font-size:13px; padding:8px 12px; min-height:36px;
            }
            QComboBox#termSelCombo:hover { border-color:rgba(52,211,153,0.40); }
            QComboBox#termSelCombo::drop-down { border:none; width:18px; }
            QComboBox#termSelCombo QAbstractItemView {
                background:#1a1f35; color:#e8eaf0;
                selection-background-color:rgba(52,211,153,0.18);
            }
            QPushButton#termSelCancelBtn {
                background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.12);
                border-radius:8px; color:rgba(255,255,255,0.60);
                font-size:12px; font-weight:600; padding:0 20px;
            }
            QPushButton#termSelCancelBtn:hover { background:rgba(255,255,255,0.10); }
            QPushButton#termSelConfirmBtn {
                background:#34d399; border:none;
                border-radius:8px; color:#0e1120;
                font-size:12px; font-weight:700; padding:0 24px;
            }
            QPushButton#termSelConfirmBtn:hover { background:rgba(52,211,153,0.85); }
            QPushButton#termSelConfirmBtn:disabled {
                background:rgba(255,255,255,0.06); color:rgba(255,255,255,0.25);
            }
        """)


# ── Main page ─────────────────────────────────────────────────────────────────

def _parse_json_response(raw: str) -> list:
    """Robustly extract a JSON array from whatever the model returns."""
    if not raw:
        return []
    # 1. Direct parse
    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return result
    except Exception:
        pass
    # 2. Strip think blocks and fences
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    cleaned = re.sub(r"```(?:json)?", "", cleaned).replace("```", "").strip()
    try:
        result = json.loads(cleaned)
        if isinstance(result, list):
            return result
    except Exception:
        pass
    # 3. Extract first [...] block character by character
    depth = 0
    start_idx = None
    for i, ch in enumerate(cleaned):
        if ch == "[":
            if start_idx is None:
                start_idx = i
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0 and start_idx is not None:
                candidate = cleaned[start_idx:i + 1]
                try:
                    result = json.loads(candidate)
                    if isinstance(result, list):
                        return result
                except Exception:
                    # Fix trailing commas
                    candidate = re.sub(",\\s*}", "}", candidate)
                    candidate = re.sub(",\\s*]", "]", candidate)
                    try:
                        result = json.loads(candidate)
                        if isinstance(result, list):
                            return result
                    except Exception:
                        pass
                break
    # Last resort: response was truncated mid-stream.
    # Walk backwards through "}" positions to find maximum complete objects.
    if start_idx is not None:
        search_end = len(cleaned)
        while search_end > start_idx:
            last_brace = cleaned.rfind("}", start_idx, search_end)
            if last_brace == -1:
                break
            truncated = cleaned[start_idx:last_brace + 1] + "]"
            try:
                result = json.loads(truncated)
                if isinstance(result, list) and result:
                    print(f"[Interventions] Recovered {len(result)} items from truncated response")
                    return result
            except Exception:
                pass
            search_end = last_brace  # try one } earlier

    print(f"[Interventions] Could not parse response: {raw[:300]}")
    return []


class InterventionsPage(QWidget):

    def __init__(self):
        super().__init__()
        self._students:     list[dict] = []
        self._filtered:     list[dict] = []
        self._current_student: dict | None = None
        self._ay:  str = ""
        self._sem: int = 1

        self._term_loader:    _TermLoader    | None = None
        self._student_loader: _StudentLoader | None = None
        self._ollama_worker:  _OllamaWorker  | None = None
        self._check_worker:   _OllamaWorker             | None = None
        self._save_worker:    _SaveWorker               | None = None
        self._record_loader:  _InterventionRecordLoader | None = None
        self._report_worker:  _InterventionReportWorker | None = None

        self._setup_ui()
        self._apply_styles()
        self._load_terms()

    # ── UI ────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 24, 30, 24)
        root.setSpacing(16)

        # ── Page header ───────────────────────────────────────────────
        hdr_row = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(3)
        title = QLabel("AI Intervention Advisor")
        title.setStyleSheet(
            "color:#e8eaf0; font-size:18px; font-weight:bold; background:transparent;"
        )
        sub = QLabel(
            "Powered by Ollama · " + SystemConfig.ollama_model() +
            " · Runs fully offline"
        )
        sub.setObjectName("intervSub")
        title_col.addWidget(title)
        title_col.addWidget(sub)
        hdr_row.addLayout(title_col, 1)

        # Ollama status pill
        self._status_pill = QLabel("⚫  Ollama not checked")
        self._status_pill.setObjectName("intervStatusPill")
        self._status_pill.setMinimumWidth(200)
        self._status_pill.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        hdr_row.addWidget(self._status_pill)
        root.addLayout(hdr_row)

        # ── Term + student selector card ──────────────────────────────
        sel_card = QFrame()
        sel_card.setObjectName("intervSelCard")
        sel_lo = QHBoxLayout(sel_card)
        sel_lo.setContentsMargins(20, 14, 20, 14)
        sel_lo.setSpacing(14)

        self._ay_combo = QComboBox()
        self._ay_combo.setObjectName("intervCombo")
        self._ay_combo.setMinimumWidth(130)
        self._ay_combo.addItem("Loading…")
        self._ay_combo.setEnabled(False)

        self._sem_combo = QComboBox()
        self._sem_combo.setObjectName("intervCombo")
        self._sem_combo.addItems(["1st Semester", "2nd Semester"])

        self._load_btn = QPushButton("⟳  Load Students")
        self._load_btn.setObjectName("intervLoadBtn")
        self._load_btn.setFixedHeight(34)
        self._load_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._load_btn.setEnabled(False)
        self._load_btn.clicked.connect(self._on_load_students)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color:rgba(255,255,255,0.10);")
        sep.setFixedHeight(24)

        # Student search
        self._student_search = QLineEdit()
        self._student_search.setObjectName("intervSearch")
        self._student_search.setPlaceholderText("🔍  Search student by name or ID…")
        self._student_search.setFixedWidth(260)
        self._student_search.textChanged.connect(self._filter_students)

        self._student_combo = QComboBox()
        self._student_combo.setObjectName("intervCombo")
        self._student_combo.setMinimumWidth(220)
        self._student_combo.addItem("— No students loaded —")
        self._student_combo.setEnabled(False)
        self._student_combo.currentIndexChanged.connect(self._on_student_selected)

        self._student_count = QLabel("")
        self._student_count.setObjectName("intervCount")

        for w in [
            QLabel("AY:"), self._ay_combo,
            QLabel("Sem:"), self._sem_combo,
            self._load_btn, sep,
            self._student_search, self._student_combo,
            self._student_count,
        ]:
            if isinstance(w, QLabel):
                w.setStyleSheet(
                    "color:rgba(255,255,255,0.40); font-size:11px; background:transparent;")
            sel_lo.addWidget(w)
        sel_lo.addStretch()
        root.addWidget(sel_card)

        # ── Action buttons ────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self._analyze_btn = QPushButton("🤖  Analyze Selected Student")
        self._analyze_btn.setObjectName("intervAnalyzeBtn")
        self._analyze_btn.setFixedHeight(38)
        self._analyze_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._analyze_btn.setEnabled(False)
        self._analyze_btn.clicked.connect(self._on_analyze_student)

        self._cohort_btn = QPushButton("📊  Cohort Summary — All At-Risk")
        self._cohort_btn.setObjectName("intervCohortBtn")
        self._cohort_btn.setFixedHeight(38)
        self._cohort_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cohort_btn.setEnabled(False)
        self._cohort_btn.clicked.connect(lambda: self._on_cohort_summary(filtered=False))

        self._cohort_filtered_btn = QPushButton("🔍  Cohort Summary — Filtered")
        self._cohort_filtered_btn.setObjectName("intervCohortFiltBtn")
        self._cohort_filtered_btn.setFixedHeight(38)
        self._cohort_filtered_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cohort_filtered_btn.setEnabled(False)
        self._cohort_filtered_btn.clicked.connect(
            lambda: self._on_cohort_summary(filtered=True))

        btn_row.addWidget(self._analyze_btn)
        btn_row.addWidget(self._cohort_btn)
        btn_row.addWidget(self._cohort_filtered_btn)
        self._export_btn = QPushButton("📄  Export Report")
        self._export_btn.setObjectName("intervExportBtn")
        self._export_btn.setFixedHeight(38)
        self._export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_btn.setEnabled(False)
        self._export_btn.setToolTip(
            "Export all intervention records for a selected term as PDF")
        self._export_btn.clicked.connect(self._on_export_report)
        btn_row.addWidget(self._export_btn)

        self._logs_btn = QPushButton("📋  Intervention Logs")
        self._logs_btn.setObjectName("intervLogsBtn")
        self._logs_btn.setFixedHeight(38)
        self._logs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._logs_btn.setToolTip("View, search and manage all intervention log records")
        self._logs_btn.clicked.connect(self._on_show_logs)
        btn_row.addWidget(self._logs_btn)

        btn_row.addStretch()

        self._progress_lbl = QLabel("")
        self._progress_lbl.setObjectName("intervCount")
        btn_row.addWidget(self._progress_lbl)
        root.addLayout(btn_row)

        # ── Main body: student card + results ─────────────────────────
        body = QHBoxLayout()
        body.setSpacing(16)

        # Left: student profile
        self._profile_frame = QFrame()
        self._profile_frame.setObjectName("intervProfileCard")
        self._profile_frame.setFixedWidth(310)
        self._profile_lo = QVBoxLayout(self._profile_frame)
        self._profile_lo.setContentsMargins(20, 18, 20, 18)
        self._profile_lo.setSpacing(10)
        self._profile_lo.addWidget(self._empty_profile())
        body.addWidget(self._profile_frame)

        # Right: results stack
        self._results_stack = QStackedWidget()

        # index 0 — empty
        empty_w = QWidget()
        el = QVBoxLayout(empty_w)
        el.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ei = QLabel("🤖")
        ei.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ei.setStyleSheet("font-size:52px;")
        em = QLabel(
            "Select a student and click Analyze,\n"
            "or run a Cohort Summary to identify systemic issues."
        )
        em.setAlignment(Qt.AlignmentFlag.AlignCenter)
        em.setStyleSheet(
            "color:rgba(255,255,255,0.28); font-size:13px; background:transparent;"
        )
        el.addWidget(ei)
        el.addSpacing(12)
        el.addWidget(em)
        self._results_stack.addWidget(empty_w)         # 0

        # index 1 — loading
        loading_w = QWidget()
        ll = QVBoxLayout(loading_w)
        ll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_lbl = QLabel("Generating recommendations…")
        self._loading_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.50); font-size:13px; background:transparent;"
        )
        self._loading_bar = QProgressBar()
        self._loading_bar.setRange(0, 0)   # indeterminate
        self._loading_bar.setFixedHeight(4)
        self._loading_bar.setFixedWidth(280)
        self._loading_bar.setTextVisible(False)
        self._loading_bar.setStyleSheet("""
            QProgressBar { background:rgba(255,255,255,0.08);
                border-radius:2px; border:none; }
            QProgressBar::chunk { background:#34d399; border-radius:2px; }
        """)
        ll.addWidget(self._loading_lbl)
        ll.addSpacing(14)
        ll.addWidget(self._loading_bar,
                     alignment=Qt.AlignmentFlag.AlignCenter)
        self._results_stack.addWidget(loading_w)       # 1

        # index 2 — per-student scroll
        ps_scroll = QScrollArea()
        ps_scroll.setWidgetResizable(True)
        ps_scroll.setFrameShape(QFrame.Shape.NoFrame)
        ps_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        ps_scroll.setStyleSheet(
            "QScrollArea { background:transparent; border:none; border-radius:0; }")
        self._ps_host = QWidget()
        self._ps_host.setStyleSheet("background:transparent;")
        self._ps_lo = QVBoxLayout(self._ps_host)
        self._ps_lo.setContentsMargins(8, 8, 8, 8)
        self._ps_lo.setSpacing(4)
        self._ps_lo.addStretch()
        ps_scroll.setWidget(self._ps_host)
        self._results_stack.addWidget(ps_scroll)       # 2

        # index 3 — cohort scroll
        co_scroll = QScrollArea()
        co_scroll.setWidgetResizable(True)
        co_scroll.setFrameShape(QFrame.Shape.NoFrame)
        co_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        co_scroll.setStyleSheet(
            "QScrollArea { background:transparent; border:none; border-radius:0; }")
        self._co_host = QWidget()
        self._co_host.setStyleSheet("background:transparent;")
        self._co_lo = QVBoxLayout(self._co_host)
        self._co_lo.setContentsMargins(8, 8, 8, 8)
        self._co_lo.setSpacing(4)
        self._co_lo.addStretch()
        co_scroll.setWidget(self._co_host)
        self._results_stack.addWidget(co_scroll)       # 3

        body.addWidget(self._results_stack, 1)
        root.addLayout(body, 1)

    def _empty_profile(self) -> QWidget:
        w = QWidget()
        lo = QVBoxLayout(w)
        lo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl = QLabel("No student selected")
        lbl.setStyleSheet(
            "color:rgba(255,255,255,0.25); font-size:12px; background:transparent;"
        )
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lo.addWidget(lbl)
        return w

    def _build_profile(self, s: dict) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        lo = QVBoxLayout(w)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(10)

        score_raw  = s.get("predicted_risk_score") or 0
        score      = round(float(score_raw) * 100, 1)
        risk_label = s.get("risk_label", "—")
        color      = _risk_color(risk_label)

        # Name + badge
        name_lbl = QLabel(s.get("full_name", "—"))
        name_lbl.setStyleSheet(
            "color:#e8eaf0; font-size:15px; font-weight:bold; background:transparent;"
        )
        name_lbl.setWordWrap(True)
        badge = QLabel(f"● {risk_label} — {score:.1f}%")
        badge.setStyleSheet(
            f"color:{color}; font-size:12px; font-weight:600; background:transparent;"
        )
        lo.addWidget(name_lbl)
        lo.addWidget(badge)

        # Divider
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("color:rgba(255,255,255,0.08);")
        lo.addWidget(div)

        # Details
        def _row(label, value):
            r = QHBoxLayout()
            r.setSpacing(8)
            lbl = QLabel(label)
            lbl.setStyleSheet(
                "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;"
            )
            lbl.setFixedWidth(110)
            val = QLabel(str(value) if value else "—")
            val.setStyleSheet(
                "color:rgba(255,255,255,0.75); font-size:11px; background:transparent;"
            )
            val.setWordWrap(True)
            r.addWidget(lbl)
            r.addWidget(val, 1)
            return r

        exam  = s.get("entrance_exam_score")
        gpa   = s.get("high_school_gpa")
        lo.addLayout(_row("Student ID",    s.get("student_id", "—")))
        lo.addLayout(_row("Program",       s.get("program", "—")))
        lo.addLayout(_row("College",       s.get("college", "—")))
        lo.addLayout(_row("Entrance Exam", f"{float(exam):.0f}" if exam else "—"))
        lo.addLayout(_row("HS GPA",        f"{float(gpa):.2f}" if gpa else "—"))
        lo.addLayout(_row("Top Factor",    s.get("primary_factor", "—")))
        lo.addStretch()
        return w

    # ── Term loading ──────────────────────────────────────────────────

    def _load_terms(self):
        self._term_loader = _TermLoader()
        self._term_loader.finished.connect(self._on_terms_loaded)
        self._term_loader.error.connect(
            lambda e: self._progress_lbl.setText(f"⚠ {e}"))
        self._term_loader.finished.connect(self._term_loader.deleteLater)
        self._term_loader.error.connect(self._term_loader.deleteLater)
        self._term_loader.start()

    def _on_terms_loaded(self, terms: list):
        self._ay_combo.clear()
        if not terms:
            self._ay_combo.addItem("No data")
            return
        seen = []
        for ay, _ in terms:
            if ay not in seen:
                seen.append(ay)
        self._ay_combo.addItems(seen)
        ay, sem = terms[0]
        self._ay_combo.setCurrentText(ay)
        self._sem_combo.setCurrentIndex(sem - 1)
        self._ay_combo.setEnabled(True)
        self._load_btn.setEnabled(True)

    # ── Student loading ───────────────────────────────────────────────

    def _on_load_students(self):
        ay  = self._ay_combo.currentText().strip()
        sem = self._sem_combo.currentIndex() + 1
        if not ay or ay == "No data":
            return
        self._ay, self._sem = ay, sem
        self._load_btn.setEnabled(False)
        self._load_btn.setText("Loading…")
        self._progress_lbl.setText("")

        self._student_loader = _StudentLoader(ay, sem)
        self._student_loader.finished.connect(self._on_students_loaded)
        self._student_loader.error.connect(self._on_load_error)
        self._student_loader.finished.connect(self._student_loader.deleteLater)
        self._student_loader.error.connect(self._student_loader.deleteLater)
        self._student_loader.start()

    def _on_students_loaded(self, students: list):
        self._load_btn.setEnabled(True)
        self._load_btn.setText("⟳  Load Students")
        self._students  = students
        self._filtered  = students
        self._student_count.setText(f"{len(students):,} at-risk")
        self._rebuild_student_combo(students)
        self._cohort_btn.setEnabled(bool(students))
        self._cohort_filtered_btn.setEnabled(bool(students))
        self._export_btn.setEnabled(True)
        self._check_ollama()

    def _on_load_error(self, msg: str):
        self._load_btn.setEnabled(True)
        self._load_btn.setText("⟳  Load Students")
        self._progress_lbl.setText(f"⚠ {msg}")

    def _filter_students(self, text: str):
        text = text.lower().strip()
        if not text:
            self._filtered = self._students
        else:
            self._filtered = [
                s for s in self._students
                if text in s.get("full_name","").lower()
                or text in str(s.get("student_id","")).lower()
            ]
        self._rebuild_student_combo(self._filtered)
        self._cohort_filtered_btn.setEnabled(bool(self._filtered))

    def _rebuild_student_combo(self, students: list):
        self._student_combo.blockSignals(True)
        self._student_combo.clear()
        if not students:
            self._student_combo.addItem("— No matches —")
            self._student_combo.setEnabled(False)
            self._analyze_btn.setEnabled(False)
        else:
            for s in students:
                score = round(float(s.get("predicted_risk_score") or 0)*100,1)
                self._student_combo.addItem(
                    f"{s['full_name']}  ({score:.0f}%)",
                    userData=s,
                )
            self._student_combo.setEnabled(True)
        self._student_combo.blockSignals(False)
        self._on_student_selected(0)

    def _on_student_selected(self, idx: int):
        data = self._student_combo.itemData(idx, Qt.ItemDataRole.UserRole)
        self._current_student = data
        self._analyze_btn.setEnabled(data is not None)

        # Rebuild profile panel
        while self._profile_lo.count():
            item = self._profile_lo.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._profile_lo.addWidget(
            self._build_profile(data) if data else self._empty_profile()
        )

    # ── Ollama check ──────────────────────────────────────────────────

    def _check_ollama(self):
        self._status_pill.setText("⟳  Checking Ollama…")
        self._check_worker = _OllamaWorker("You are a test.", "Reply with: OK")
        self._check_worker.finished.connect(
            lambda _: self._status_pill.setText(
                f"🟢  {SystemConfig.ollama_model()} — Ready"
            )
        )
        self._check_worker.error.connect(
            lambda e: self._status_pill.setText(f"🔴  Ollama offline: {e[:60]}")
        )
        self._check_worker.finished.connect(self._check_worker.deleteLater)
        self._check_worker.error.connect(self._check_worker.deleteLater)
        self._check_worker.start()

    # ── Per-student analysis ──────────────────────────────────────────

    def _on_analyze_student(self):
        s = self._current_student
        if not s:
            return

        score_raw  = s.get("predicted_risk_score") or 0
        score      = round(float(score_raw) * 100, 1)
        exam       = s.get("entrance_exam_score")
        gpa        = s.get("high_school_gpa")
        factor     = s.get("primary_factor", "Not available")

        prompt = _USER_PER_STUDENT.format(
            name       = s.get("full_name", "—"),
            program    = s.get("program", "—"),
            college    = s.get("college", "—"),
            score      = f"{score:.1f}",
            risk_label = s.get("risk_label", "—"),
            factors    = factor,
            exam_score = f"{float(exam):.0f}" if exam else "N/A",
            hs_gpa     = f"{float(gpa):.2f}"  if gpa  else "N/A",
        )

        self._set_loading("Generating intervention plan…")
        self._analyze_btn.setEnabled(False)

        self._ollama_worker = _OllamaWorker(_SYSTEM_PER_STUDENT, prompt)
        self._ollama_worker.finished.connect(
            lambda raw: self._on_per_student_done(raw, s))
        self._ollama_worker.error.connect(self._on_ollama_error)
        self._ollama_worker.finished.connect(self._ollama_worker.deleteLater)
        self._ollama_worker.error.connect(self._ollama_worker.deleteLater)
        self._ollama_worker.start()

    def _on_per_student_done(self, raw: str, s: dict):
        self._analyze_btn.setEnabled(True)
        self._stop_loading()
        print(f"[Interventions] Raw response ({len(raw)} chars):")
        print(raw[:500])
        recs = _parse_json_response(raw)
        if not recs:
            self._progress_lbl.setText("⚠ Could not parse AI response.")
            self._results_stack.setCurrentIndex(0)
            return

        # Render cards
        self._clear_layout(self._ps_lo)
        score = round(float(s.get("predicted_risk_score") or 0)*100, 1)

        header = QLabel(
            f"Intervention Plan — {s.get('full_name','—')}  "
            f"({score:.1f}% risk)"
        )
        header.setStyleSheet(
            "color:#e8eaf0; font-size:13px; font-weight:bold; background:transparent;"
        )
        self._ps_lo.addWidget(header)
        self._ps_lo.addSpacing(8)

        for i, rec in enumerate(recs):
            self._ps_lo.addWidget(_rec_card(rec, i))
            if i < len(recs) - 1:
                div = QFrame()
                div.setFrameShape(QFrame.Shape.HLine)
                div.setStyleSheet(
                    "color:rgba(255,255,255,0.06); margin:0 4px;")
                self._ps_lo.addWidget(div)
        self._ps_lo.addStretch()

        self._results_stack.setCurrentIndex(2)
        self._progress_lbl.setText(
            f"✓  {len(recs)} recommendations generated — saved to log")

        # Auto-save
        self._auto_save({
            "student_id":     str(s.get("student_id", "")),
            "academic_year":  self._ay,
            "semester":       self._sem,
            "mode":           "per_student",
            "risk_score":     round(float(s.get("predicted_risk_score") or 0)*100, 2),
            "risk_label":     s.get("risk_label", ""),
            "risk_factors":   s.get("primary_factor", ""),
            "recommendations": recs,
        })

    # ── Cohort summary ────────────────────────────────────────────────

    def _on_cohort_summary(self, filtered: bool = False):
        students = self._filtered if filtered else self._students
        if not students:
            self._progress_lbl.setText("⚠ No students to analyze.")
            return

        total    = len(students)
        high     = sum(1 for s in students
                       if "high" in s.get("risk_label","").lower())
        moderate = total - high

        # Factor frequency
        factor_counts: dict[str, int] = {}
        for s in students:
            f = s.get("primary_factor")
            if f:
                factor_counts[f] = factor_counts.get(f, 0) + 1

        top_factors = sorted(
            factor_counts.items(), key=lambda x: x[1], reverse=True)[:3]
        factors_summary = ", ".join(
            f"{f}({c})" for f, c in top_factors
        ) or "No factor data"

        # College breakdown — top 3 only
        college_counts: dict[str, int] = {}
        for s in students:
            c = s.get("college", "—")
            college_counts[c] = college_counts.get(c, 0) + 1

        top_colleges = sorted(
            college_counts.items(), key=lambda x: x[1], reverse=True)[:3]
        college_summary = ", ".join(
            f"{col}({cnt})" for col, cnt in top_colleges
        ) or "No college data"

        sem_label = "1st Semester" if self._sem == 1 else "2nd Semester"
        term = f"{self._ay} — {sem_label}"
        scope = "filtered subset" if filtered else "all at-risk students"

        prompt = _USER_COHORT.format(
            term=term,
            total=total,
            high=high,
            moderate=moderate,
            factors_summary=factors_summary,
            college_summary=college_summary,
        )

        self._set_loading(
            f"Analyzing {total:,} {scope}…"
        )
        self._cohort_btn.setEnabled(False)
        self._cohort_filtered_btn.setEnabled(False)

        self._ollama_worker = _OllamaWorker(_SYSTEM_COHORT, prompt)
        self._ollama_worker.finished.connect(
            lambda raw: self._on_cohort_done(raw, students, term, filtered))
        self._ollama_worker.error.connect(self._on_ollama_error)
        self._ollama_worker.finished.connect(self._ollama_worker.deleteLater)
        self._ollama_worker.error.connect(self._ollama_worker.deleteLater)
        self._ollama_worker.start()

    def _on_cohort_done(self, raw: str, students: list,
                         term: str, filtered: bool):
        self._cohort_btn.setEnabled(bool(self._students))
        self._cohort_filtered_btn.setEnabled(bool(self._filtered))
        self._stop_loading()
        print(f"[Interventions-cohort] Raw response ({len(raw)} chars):")
        print(raw[:500])
        issues = _parse_json_response(raw)
        if not issues:
            self._progress_lbl.setText("⚠ Could not parse AI response.")
            self._results_stack.setCurrentIndex(0)
            return

        self._clear_layout(self._co_lo)

        scope = "Filtered" if filtered else "All At-Risk"
        header = QLabel(
            f"Cohort Systemic Issues — {term}  ({scope},  {len(students):,} students)"
        )
        header.setStyleSheet(
            "color:#e8eaf0; font-size:13px; font-weight:bold; background:transparent;"
        )
        self._co_lo.addWidget(header)
        self._co_lo.addSpacing(8)

        for i, issue in enumerate(issues):
            self._co_lo.addWidget(_cohort_row(issue, i))
            if i < len(issues) - 1:
                div = QFrame()
                div.setFrameShape(QFrame.Shape.HLine)
                div.setStyleSheet(
                    "color:rgba(255,255,255,0.06); margin:0 4px;")
                self._co_lo.addWidget(div)
        self._co_lo.addStretch()

        self._results_stack.setCurrentIndex(3)
        self._progress_lbl.setText(
            f"✓  {len(issues)} systemic issues identified — saved to log")

        # Auto-save cohort record (student_id=NULL for cohort mode)
        self._auto_save({
            "student_id":      None,
            "academic_year":   self._ay,
            "semester":        self._sem,
            "mode":            "cohort",
            "risk_score":      None,
            "risk_label":      f"{len(students)} students · {scope}",
            "risk_factors":    term,
            "recommendations": issues,
        })

    # ── Error / loading helpers ───────────────────────────────────────

    def _on_ollama_error(self, msg: str):
        self._stop_loading()
        self._analyze_btn.setEnabled(self._current_student is not None)
        self._cohort_btn.setEnabled(bool(self._students))
        self._cohort_filtered_btn.setEnabled(bool(self._filtered))
        self._results_stack.setCurrentIndex(0)
        self._progress_lbl.setText(f"⚠ Ollama error: {msg[:80]}")

    def _set_loading(self, msg: str):
        self._loading_lbl.setText(msg)
        self._results_stack.setCurrentIndex(1)
        self._progress_lbl.setText("")
        # Animate dots so the counselor knows it's working
        self._dot_count = 0
        if not hasattr(self, "_dot_timer"):
            self._dot_timer = QTimer(self)
            self._dot_timer.timeout.connect(self._tick_dots)
        self._dot_msg = msg
        self._dot_timer.start(600)

    def _tick_dots(self):
        self._dot_count = (self._dot_count + 1) % 4
        dots = "." * self._dot_count
        self._loading_lbl.setText(f"{self._dot_msg}{dots}")

    def _stop_loading(self):
        if hasattr(self, "_dot_timer"):
            self._dot_timer.stop()

    def _clear_layout(self, lo):
        while lo.count() > 1:
            item = lo.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ── Auto-save ─────────────────────────────────────────────────────

    def _auto_save(self, record: dict):
        self._save_worker = _SaveWorker(record)
        self._save_worker.finished.connect(self._save_worker.deleteLater)
        self._save_worker.error.connect(
            lambda e: print(f"[Interventions] Save error: {e}"))
        self._save_worker.error.connect(self._save_worker.deleteLater)
        self._save_worker.start()

    # ── Export report ─────────────────────────────────────────────────

    def _on_export_report(self):
        """Show term-selection dialog then generate PDF."""
        # Build list of available terms from loaded data
        # (use the already-loaded term list or fall back to DB)
        dlg = _TermSelectDialog(self)
        if dlg.exec() != _TermSelectDialog.DialogCode.Accepted:
            return

        ay, sem, term_label = dlg.selected_term()
        if not ay:
            return

        from PyQt6.QtWidgets import QFileDialog
        ay_safe  = ay.replace("-", "_").replace(" ", "")
        default  = f"InterventionReport_{ay_safe}_Sem{sem}.pdf"
        path, _  = QFileDialog.getSaveFileName(
            self, "Save Intervention Report", default, "PDF Files (*.pdf)")
        if not path:
            return

        self._export_btn.setEnabled(False)
        self._export_btn.setText("Loading records…")
        self._progress_lbl.setText("")

        # Load intervention records for the selected term
        self._record_loader = _InterventionRecordLoader(ay, sem)
        self._record_loader.finished.connect(
            lambda rows: self._on_records_loaded(rows, ay, sem, term_label, path))
        self._record_loader.error.connect(self._on_export_error)
        self._record_loader.finished.connect(self._record_loader.deleteLater)
        self._record_loader.error.connect(self._record_loader.deleteLater)
        self._record_loader.start()

    def _on_records_loaded(self, rows: list, ay: str, sem: int,
                            term_label: str, path: str):
        if not rows:
            self._export_btn.setEnabled(True)
            self._export_btn.setText("📄  Export Report")
            self._progress_lbl.setText(
                f"⚠ No intervention records found for {term_label}.")
            return

        self._export_btn.setText("Generating PDF…")
        self._progress_lbl.setText(
            f"Generating report for {term_label} ({len(rows)} records)…")

        self._report_worker = _InterventionReportWorker(
            records       = rows,
            term_label    = term_label,
            academic_year = ay,
            semester      = sem,
            save_path     = path,
        )
        self._report_worker.finished.connect(self._on_export_done)
        self._report_worker.error.connect(self._on_export_error)
        self._report_worker.finished.connect(self._report_worker.deleteLater)
        self._report_worker.error.connect(self._report_worker.deleteLater)
        self._report_worker.start()

    def _on_export_done(self, path: str):
        self._export_btn.setEnabled(True)
        self._export_btn.setText("📄  Export Report")
        self._progress_lbl.setText(f"✓  Report saved.")

        # Open file location
        import subprocess, sys, os
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", path])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
        except Exception:
            pass

        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.information(
            self, "Report Exported",
            "Intervention report saved successfully.\n\n" + path
        )

    def _on_export_error(self, msg: str):
        self._export_btn.setEnabled(True)
        self._export_btn.setText("📄  Export Report")
        self._progress_lbl.setText(f"⚠ Export failed: {msg[:80]}")

    # ── Intervention logs ─────────────────────────────────────────────

    def _on_show_logs(self):
        dlg = InterventionLogDialog(self)
        dlg.exec()

    # ── Styles ────────────────────────────────────────────────────────

    def _apply_styles(self):
        self.setStyleSheet("""
            #intervSub {
                color: rgba(255,255,255,0.35); font-size:11px;
                background:transparent;
            }
            #intervStatusPill {
                color: rgba(255,255,255,0.55); font-size:11px;
                background: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 8px;
                padding: 5px 14px;
                min-width: 180px;
            }
            #intervSelCard {
                background: rgba(255,255,255,0.02);
                border: none;
                border-bottom: 1px solid rgba(255,255,255,0.06);
                border-radius: 0;
            }
            QComboBox#intervCombo {
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 7px; color: #e8eaf0;
                font-size: 12px; padding: 5px 10px; min-height: 30px;
            }
            QComboBox#intervCombo:hover {
                border-color: rgba(52,211,153,0.35);
            }
            QComboBox#intervCombo::drop-down { border:none; width:16px; }
            QComboBox#intervCombo QAbstractItemView {
                background: #1a1f35; border: 1px solid rgba(255,255,255,0.12);
                color: #e8eaf0;
                selection-background-color: rgba(52,211,153,0.18);
            }
            QPushButton#intervLoadBtn {
                background: #34d399; border:none; border-radius:7px;
                color: #0e1120; font-size:12px; font-weight:700;
                padding: 0 16px;
            }
            QPushButton#intervLoadBtn:hover { background: rgba(52,211,153,0.85); }
            QPushButton#intervLoadBtn:disabled {
                background: rgba(255,255,255,0.06); color:rgba(255,255,255,0.25);
            }
            QLineEdit#intervSearch {
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 8px; color: #e8eaf0;
                font-size: 12px; padding: 7px 12px;
            }
            QLineEdit#intervSearch:focus {
                border-color: rgba(52,211,153,0.40);
            }
            QPushButton#intervAnalyzeBtn {
                background: #4f8cff; border:none; border-radius:8px;
                color: white; font-size:12px; font-weight:700;
                padding: 0 20px;
            }
            QPushButton#intervAnalyzeBtn:hover {
                background: rgba(79,140,255,0.85);
            }
            QPushButton#intervAnalyzeBtn:disabled {
                background: rgba(255,255,255,0.06); color:rgba(255,255,255,0.25);
            }
            QPushButton#intervCohortBtn {
                background: rgba(167,139,250,0.12);
                border: 1px solid rgba(167,139,250,0.30);
                border-radius:8px; color:#a78bfa;
                font-size:12px; font-weight:600; padding: 0 18px;
            }
            QPushButton#intervCohortBtn:hover {
                background: rgba(167,139,250,0.22);
            }
            QPushButton#intervCohortBtn:disabled {
                background: rgba(255,255,255,0.03);
                border-color: rgba(255,255,255,0.08);
                color: rgba(255,255,255,0.20);
            }
            QPushButton#intervCohortFiltBtn {
                background: rgba(245,179,53,0.10);
                border: 1px solid rgba(245,179,53,0.28);
                border-radius:8px; color:#f5b335;
                font-size:12px; font-weight:600; padding: 0 18px;
            }
            QPushButton#intervCohortFiltBtn:hover {
                background: rgba(245,179,53,0.20);
            }
            QPushButton#intervCohortFiltBtn:disabled {
                background: rgba(255,255,255,0.03);
                border-color: rgba(255,255,255,0.08);
                color: rgba(255,255,255,0.20);
            }
            #intervProfileCard {
                background: rgba(255,255,255,0.02);
                border: none;
                border-right: 1px solid rgba(255,255,255,0.06);
                border-radius: 0;
            }
            #intervCount {
                color: rgba(255,255,255,0.35); font-size:11px;
                background:transparent;
            }
            QPushButton#intervExportBtn {
                background: rgba(52,211,153,0.10);
                border: 1px solid rgba(52,211,153,0.30);
                border-radius: 8px; color: #34d399;
                font-size: 12px; font-weight: 600; padding: 0 18px;
            }
            QPushButton#intervExportBtn:hover {
                background: rgba(52,211,153,0.20);
                border-color: rgba(52,211,153,0.55);
            }
            QPushButton#intervExportBtn:disabled {
                background: rgba(255,255,255,0.03);
                border-color: rgba(255,255,255,0.08);
                color: rgba(255,255,255,0.20);
            }
            QPushButton#intervLogsBtn {
                background: rgba(79,140,255,0.10);
                border: 1px solid rgba(79,140,255,0.28);
                border-radius: 8px; color: #4f8cff;
                font-size: 12px; font-weight: 600; padding: 0 18px;
            }
            QPushButton#intervLogsBtn:hover {
                background: rgba(79,140,255,0.20);
                border-color: rgba(79,140,255,0.50);
            }
        """)