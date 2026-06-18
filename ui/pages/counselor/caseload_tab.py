"""
Counselor Portal — Tab 0: Caseload
====================================
Loads ALL at-risk students from fact_student_academic_risk for the
selected term. Every student is visible to every counselor.

Layout
------
  ┌─ Term selector ─────────────────────────────────────────────┐
  │  Academic Year [combo]  Semester [combo]  [Load]            │
  └─────────────────────────────────────────────────────────────┘
  ┌─ Stats row ──────────────────────────────────────────────────┐
  │  [Total At-Risk]  [High Risk]  [Moderate Risk]  [Avg Score]  │
  └─────────────────────────────────────────────────────────────┘
  ┌─ Filter / search ────────────────────────────────────────────┐
  │  🔍 Search  │ Risk Level ▾ │ Program ▾ │ College ▾           │
  └─────────────────────────────────────────────────────────────┘
  ┌─ Student cards (scrollable) ─────────────────────────────────┐
  │  [Card] [Card] [Card] ...                                    │
  └─────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QScrollArea, QLineEdit, QComboBox, QStackedWidget,
    QSizePolicy, QGridLayout,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QColor

from services.data_store import DataStore
from services.system_config import SystemConfig
from ui.dialogs.confirmation_dialog import show_error


# ── Background loader ─────────────────────────────────────────────────────────

class _CaseloadLoader(QThread):
    """
    Queries fact_student_academic_risk → dim_student → dim_program →
    dim_risk_level for a given term, returning only at-risk students
    (High Risk + Moderate Risk).
    """
    finished = pyqtSignal(list, str)   # (rows, term_label)
    error    = pyqtSignal(str)

    # Assumption columns — same as prediction_history_page
    _SQL = """
        SELECT
            ds.student_id,
            TRIM(COALESCE(ds.first_name,'') || ' ' ||
                 COALESCE(ds.last_name, ''))             AS full_name,
            ds.first_name,
            ds.last_name,
            COALESCE(dp.program_name, 'Unknown')         AS program,
            COALESCE(dp.college,      '—')               AS college,
            COALESCE(rl.risk_label,   'Low Risk')        AS risk_label,
            fsr.predicted_risk_score,
            fsr.entrance_exam_score,
            fsr.high_school_gpa,
            fsr.predicted_at,
            t.academic_year || ' — Sem ' || t.semester::text AS term_label
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
          AND (rl.risk_label ILIKE '%%high%%'
               OR rl.risk_label ILIKE '%%moderate%%'
               OR rl.risk_label ILIKE '%%medium%%')
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
            tl = (rows[0]["term_label"]
                  if rows else f"{self._ay} Sem {self._sem}")
            self.finished.emit(rows, tl)
        except Exception as e:
            self.error.emit(str(e))


class _TermLoader(QThread):
    """Load distinct (academic_year, semester) pairs that have saved predictions."""
    finished = pyqtSignal(list)
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
                    JOIN   public.dim_academic_term t ON t.term_key = fsr.term_key
                    ORDER  BY t.academic_year DESC, t.semester DESC
                """)
                self.finished.emit(cur.fetchall())
        except Exception as e:
            self.error.emit(str(e))


# ── Risk helpers ──────────────────────────────────────────────────────────────

def _cat(label: str) -> str:
    lc = label.lower()
    if "high" in lc:     return "high_risk"
    if "moderate" in lc or "medium" in lc: return "moderate_risk"
    return "low_risk"

def _risk_color(cat: str) -> str:
    return {"high_risk":"#ff5b5b","moderate_risk":"#f5b335"}.get(cat,"#34d399")

def _risk_bg(cat: str) -> str:
    return {
        "high_risk":     "rgba(255,91,91,0.08)",
        "moderate_risk": "rgba(245,179,53,0.08)",
    }.get(cat, "rgba(52,211,153,0.06)")


# ── Student card ──────────────────────────────────────────────────────────────

class _StudentCard(QFrame):
    """
    Compact card for one at-risk student.
    Shows: name, ID · program · college, risk badge, score,
    top factor, and a View Profile button.
    """

    profile_requested = pyqtSignal(dict)   # emits student dict

    def __init__(self, student: dict, parent=None):
        super().__init__(parent)
        self._student = student
        self._build()

    def _build(self):
        cat   = _cat(self._student.get("risk_label", ""))
        color = _risk_color(cat)
        bg    = _risk_bg(cat)
        score_raw = self._student.get("predicted_risk_score")
        score = round(float(score_raw) * 100, 1) if score_raw is not None else 0.0

        self.setObjectName("caseloadCard")
        self.setStyleSheet(f"""
            QFrame#caseloadCard {{
                background-color: {bg};
                border: 1px solid {color}33;
                border-left: 3px solid {color};
                border-radius: 12px;
            }}
            QFrame#caseloadCard:hover {{
                background-color: {bg.replace('0.08','0.14')
                                      .replace('0.06','0.10')};
                border-color: {color}66;
            }}
        """)

        lo = QHBoxLayout(self)
        lo.setContentsMargins(20, 16, 16, 16)
        lo.setSpacing(16)

        # ── Left: identity ────────────────────────────────────────────
        info = QVBoxLayout()
        info.setSpacing(4)

        name_row = QHBoxLayout()
        name_row.setSpacing(10)

        name_lbl = QLabel(self._student.get("full_name", "—"))
        name_lbl.setStyleSheet(
            "color:#e8eaf0; font-size:14px; font-weight:bold; background:transparent;"
        )

        badge = QLabel(self._student.get("risk_label", "—"))
        badge.setStyleSheet(f"""
            color:{color};
            background:{color}18;
            border:1px solid {color}44;
            border-radius:8px;
            font-size:10px; font-weight:700;
            padding:2px 9px;
        """)

        name_row.addWidget(name_lbl)
        name_row.addWidget(badge)
        name_row.addStretch()

        meta_lbl = QLabel(
            f"{self._student.get('student_id','—')}  ·  "
            f"{self._student.get('program','—')}  ·  "
            f"{self._student.get('college','—')}"
        )
        meta_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.40); font-size:11px; background:transparent;"
        )

        # Pre-enrollment indicators
        exam  = self._student.get("entrance_exam_score")
        gpa   = self._student.get("high_school_gpa")
        parts = []
        if exam is not None:
            parts.append(f"Entrance Exam: {float(exam):.0f}")
        if gpa is not None:
            parts.append(f"HS GPA: {float(gpa):.2f}")
        indicators_lbl = QLabel("  ·  ".join(parts) if parts else "")
        indicators_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.30); font-size:11px; background:transparent;"
        )

        info.addLayout(name_row)
        info.addWidget(meta_lbl)
        if parts:
            info.addWidget(indicators_lbl)

        # ── Right: score + action ─────────────────────────────────────
        right = QVBoxLayout()
        right.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        right.setSpacing(8)

        score_lbl = QLabel(f"{score:.1f}%")
        score_lbl.setStyleSheet(
            f"color:{color}; font-size:20px; font-weight:bold; background:transparent;"
        )
        score_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)

        score_sub = QLabel("risk score")
        score_sub.setStyleSheet(
            "color:rgba(255,255,255,0.25); font-size:10px; background:transparent;"
        )
        score_sub.setAlignment(Qt.AlignmentFlag.AlignRight)

        view_btn = QPushButton("View Profile →")
        view_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        view_btn.setFixedHeight(30)
        view_btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent;
                border:1px solid {color}55;
                border-radius:7px;
                color:{color};
                font-size:11px; font-weight:600;
                padding:0 14px;
            }}
            QPushButton:hover {{
                background:{color}18;
            }}
        """)
        view_btn.clicked.connect(
            lambda: self.profile_requested.emit(self._to_profile_dict())
        )

        right.addWidget(score_lbl)
        right.addWidget(score_sub)
        right.addWidget(view_btn)

        lo.addLayout(info, 1)
        lo.addLayout(right)

    def _to_profile_dict(self) -> dict:
        """Map DB row dict to the shape StudentProfileDrawer expects."""
        s     = self._student
        cat   = _cat(s.get("risk_label", ""))
        score_raw = s.get("predicted_risk_score")
        score = round(float(score_raw) * 100, 1) if score_raw is not None else 0.0
        return {
            "name":     s.get("full_name", "—"),
            "id":       str(s.get("student_id", "—")),
            "program":  s.get("program", "—"),
            "college":  s.get("college", "—"),
            "score":    score,
            "category": cat,
            "label":    s.get("risk_label", "—"),
            "factor":   "—",
            "shap_factors": [],
            # pre-enrollment fields for background tags
            "entrance_exam_score": s.get("entrance_exam_score", ""),
            "hs_gpa":              s.get("high_school_gpa", ""),
        }


# ── Stat tile ─────────────────────────────────────────────────────────────────

def _stat_tile(value: str, label: str,
               accent: str = "#e8eaf0") -> tuple[QFrame, QLabel]:
    f  = QFrame()
    f.setObjectName("caseloadStatTile")
    lo = QVBoxLayout(f)
    lo.setContentsMargins(20, 16, 20, 16)
    lo.setSpacing(4)
    v = QLabel(value)
    v.setStyleSheet(
        f"color:{accent}; font-size:22px; font-weight:bold; background:transparent;"
    )
    l = QLabel(label)
    l.setStyleSheet(
        "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;"
    )
    lo.addWidget(v)
    lo.addWidget(l)
    return f, v


# ── Main tab widget ───────────────────────────────────────────────────────────

class CaseloadTab(QWidget):
    def __init__(self):
        super().__init__()
        self._rows:           list[dict] = []
        self._cards:          list[_StudentCard] = []
        self._loader:         _CaseloadLoader | None = None
        self._term_loader:    _TermLoader     | None = None
        self._profile_drawer = None
        self._setup_ui()
        self._apply_styles()
        self._load_terms()

    # ── UI construction ───────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 24, 30, 24)
        root.setSpacing(20)

        # ── Term selector card ────────────────────────────────────────
        term_card = QFrame()
        term_card.setObjectName("caseloadTermCard")
        term_lo = QHBoxLayout(term_card)
        term_lo.setContentsMargins(20, 14, 20, 14)
        term_lo.setSpacing(14)

        page_title = QLabel("At-Risk Student Caseload")
        page_title.setObjectName("caseloadPageTitle")

        term_lo.addWidget(page_title, 1)

        # AY combo
        ay_lbl = QLabel("Academic Year")
        ay_lbl.setObjectName("caseloadFieldLbl")
        self._ay_combo = QComboBox()
        self._ay_combo.setObjectName("caseloadCombo")
        self._ay_combo.setMinimumWidth(130)
        self._ay_combo.setCursor(Qt.CursorShape.PointingHandCursor)

        # Semester combo
        sem_lbl = QLabel("Semester")
        sem_lbl.setObjectName("caseloadFieldLbl")
        self._sem_combo = QComboBox()
        self._sem_combo.setObjectName("caseloadCombo")
        self._sem_combo.addItems(["1st Semester", "2nd Semester"])
        self._sem_combo.setCursor(Qt.CursorShape.PointingHandCursor)

        self._load_btn = QPushButton("⟳  Load Students")
        self._load_btn.setObjectName("caseloadLoadBtn")
        self._load_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._load_btn.setFixedHeight(36)
        self._load_btn.clicked.connect(self._on_load)

        self._term_status = QLabel("Select a term to load at-risk students.")
        self._term_status.setObjectName("caseloadTermStatus")

        for w in [ay_lbl, self._ay_combo, sem_lbl,
                  self._sem_combo, self._load_btn]:
            term_lo.addWidget(w)

        root.addWidget(term_card)
        root.addWidget(self._term_status)

        # ── Stat tiles ────────────────────────────────────────────────
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        tile1, self._stat_total    = _stat_tile("—", "At-Risk Students")
        tile2, self._stat_high     = _stat_tile("—", "High Risk",     "#ff5b5b")
        tile3, self._stat_moderate = _stat_tile("—", "Moderate Risk", "#f5b335")
        tile4, self._stat_avg      = _stat_tile("—", "Avg Risk Score","#4f8cff")
        for t in [tile1, tile2, tile3, tile4]:
            stats_row.addWidget(t, 1)
        root.addLayout(stats_row)

        # ── Filter bar ────────────────────────────────────────────────
        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)

        self._search = QLineEdit()
        self._search.setObjectName("caseloadSearch")
        self._search.setPlaceholderText("🔍  Search by name or student ID…")
        self._search.textChanged.connect(self._apply_filter)

        self._risk_filter = QComboBox()
        self._risk_filter.setObjectName("caseloadCombo")
        self._risk_filter.addItem("All Risk Levels")
        self._risk_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self._risk_filter.currentTextChanged.connect(self._apply_filter)

        self._prog_filter = QComboBox()
        self._prog_filter.setObjectName("caseloadCombo")
        self._prog_filter.addItem("All Programs")
        self._prog_filter.setMinimumWidth(150)
        self._prog_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self._prog_filter.currentTextChanged.connect(self._apply_filter)

        self._college_filter = QComboBox()
        self._college_filter.setObjectName("caseloadCombo")
        self._college_filter.addItem("All Colleges")
        self._college_filter.setMinimumWidth(150)
        self._college_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self._college_filter.currentTextChanged.connect(self._apply_filter)

        self._result_lbl = QLabel("")
        self._result_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.30); font-size:11px; background:transparent;"
        )

        filter_row.addWidget(self._search, 1)
        filter_row.addWidget(self._risk_filter)
        filter_row.addWidget(self._prog_filter)
        filter_row.addWidget(self._college_filter)
        filter_row.addWidget(self._result_lbl)
        root.addLayout(filter_row)

        # ── Content stack: empty / cards ──────────────────────────────
        self._content_stack = QStackedWidget()

        # Empty state
        empty_w = QWidget()
        empty_lo = QVBoxLayout(empty_w)
        empty_lo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_icon = QLabel("📋")
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_icon.setStyleSheet("font-size:48px;")
        empty_msg = QLabel(
            "No caseload loaded.\n"
            "Select a term and click Load Students."
        )
        empty_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_msg.setStyleSheet(
            "color:rgba(255,255,255,0.30); font-size:13px; background:transparent;"
        )
        empty_lo.addWidget(empty_icon)
        empty_lo.addSpacing(12)
        empty_lo.addWidget(empty_msg)
        self._content_stack.addWidget(empty_w)   # index 0

        # Scrollable cards area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { background:transparent; border:none; }"
        )

        self._cards_host = QWidget()
        self._cards_host.setStyleSheet("background:transparent;")
        self._cards_lo = QVBoxLayout(self._cards_host)
        self._cards_lo.setContentsMargins(0, 0, 0, 0)
        self._cards_lo.setSpacing(10)
        self._cards_lo.addStretch()

        scroll.setWidget(self._cards_host)
        self._content_stack.addWidget(scroll)   # index 1

        root.addWidget(self._content_stack, 1)

    # ── Term loading ──────────────────────────────────────────────────

    def _load_terms(self):
        self._term_loader = _TermLoader()
        self._term_loader.finished.connect(self._on_terms_loaded)
        self._term_loader.error.connect(
            lambda e: self._term_status.setText(f"⚠ {e}")
        )
        self._term_loader.finished.connect(self._term_loader.deleteLater)
        self._term_loader.error.connect(self._term_loader.deleteLater)
        self._term_loader.start()

    def _on_terms_loaded(self, terms: list):
        self._ay_combo.clear()
        if not terms:
            self._ay_combo.addItem("No data")
            self._load_btn.setEnabled(False)
            self._term_status.setText(
                "No prediction data found. Ask the administrator to run a prediction first."
            )
            return

        seen = []
        for ay, sem in terms:
            if ay not in seen:
                seen.append(ay)
        self._ay_combo.addItems(seen)

        # Pre-select most recent
        ay, sem = terms[0]
        self._ay_combo.setCurrentText(ay)
        self._sem_combo.setCurrentIndex(sem - 1)
        self._load_btn.setEnabled(True)
        self._term_status.setText(
            f"{len(terms)} term(s) available — select one and click Load Students."
        )

    # ── Caseload loading ──────────────────────────────────────────────

    def _on_load(self):
        ay  = self._ay_combo.currentText().strip()
        sem = self._sem_combo.currentIndex() + 1
        if not ay or ay == "No data":
            return

        self._load_btn.setEnabled(False)
        self._load_btn.setText("Loading…")
        self._term_status.setText(f"Loading {ay} — Semester {sem}…")

        self._loader = _CaseloadLoader(ay, sem)
        self._loader.finished.connect(self._on_rows_loaded)
        self._loader.error.connect(self._on_load_error)
        self._loader.finished.connect(self._loader.deleteLater)
        self._loader.error.connect(self._loader.deleteLater)
        self._loader.start()

    def _on_rows_loaded(self, rows: list, term_label: str):
        self._load_btn.setEnabled(True)
        self._load_btn.setText("⟳  Load Students")
        self._rows = rows

        if not rows:
            self._term_status.setText(
                f"No at-risk students found for {term_label}."
            )
            self._clear_stats()
            self._content_stack.setCurrentIndex(0)
            return

        self._term_status.setText(
            f"📅  {term_label}  ·  {len(rows):,} at-risk students"
        )
        self._update_stats(rows)
        self._populate_filter_dropdowns(rows)
        self._apply_filter()
        self._content_stack.setCurrentIndex(1)

    def _on_load_error(self, msg: str):
        self._load_btn.setEnabled(True)
        self._load_btn.setText("⟳  Load Students")
        self._term_status.setText(f"⚠ {msg}")
        show_error(self, "Load Error",
                   "Could not load caseload data.", msg)

    # ── Stats ─────────────────────────────────────────────────────────

    def _update_stats(self, rows: list):
        high = sum(1 for r in rows if "high" in r.get("risk_label","").lower())
        mod  = len(rows) - high
        scores = [float(r["predicted_risk_score"])*100
                  for r in rows if r.get("predicted_risk_score") is not None]
        avg = round(sum(scores)/len(scores),1) if scores else 0.0
        self._stat_total.setText(f"{len(rows):,}")
        self._stat_high.setText(f"{high:,}")
        self._stat_moderate.setText(f"{mod:,}")
        self._stat_avg.setText(f"{avg:.1f}%")

    def _clear_stats(self):
        for lbl in (self._stat_total, self._stat_high,
                    self._stat_moderate, self._stat_avg):
            lbl.setText("—")

    # ── Filters ───────────────────────────────────────────────────────

    def _populate_filter_dropdowns(self, rows: list):
        risk_levels = sorted({str(r.get("risk_label","")).strip()
                               for r in rows if r.get("risk_label")})
        programs    = sorted({str(r.get("program","")).strip()
                               for r in rows
                               if r.get("program") and r["program"] != "Unknown"})
        colleges    = sorted({str(r.get("college","")).strip()
                               for r in rows
                               if r.get("college") and r["college"] not in ("—","")})

        for combo, default, items in [
            (self._risk_filter,    "All Risk Levels", risk_levels),
            (self._prog_filter,    "All Programs",    programs),
            (self._college_filter, "All Colleges",    colleges),
        ]:
            combo.blockSignals(True)
            prev = combo.currentText()
            combo.clear()
            combo.addItem(default)
            combo.addItems(items)
            idx = combo.findText(prev)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

    def _apply_filter(self):
        text    = self._search.text().lower().strip()
        risk_s  = self._risk_filter.currentText()
        prog_s  = self._prog_filter.currentText()
        coll_s  = self._college_filter.currentText()

        filtered = []
        for r in self._rows:
            if risk_s != "All Risk Levels":
                if risk_s.lower() not in r.get("risk_label","").lower():
                    continue
            if prog_s != "All Programs":
                if r.get("program","") != prog_s:
                    continue
            if coll_s != "All Colleges":
                if r.get("college","") != coll_s:
                    continue
            if text:
                hay = f"{r.get('full_name','')} {r.get('student_id','')}".lower()
                if text not in hay:
                    continue
            filtered.append(r)

        self._rebuild_cards(filtered)
        total = len(self._rows)
        shown = len(filtered)
        self._result_lbl.setText(
            f"{shown:,} of {total:,} students"
            if shown != total else f"{total:,} students"
        )

    # ── Card rendering ────────────────────────────────────────────────

    def _rebuild_cards(self, rows: list):
        # Remove old cards (keep the trailing stretch)
        while self._cards_lo.count() > 1:
            item = self._cards_lo.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cards.clear()

        for row in rows:
            card = _StudentCard(row)
            card.profile_requested.connect(self._open_profile)
            self._cards.append(card)
            self._cards_lo.insertWidget(self._cards_lo.count() - 1, card)

    # ── Profile drawer ────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        self._ensure_drawer()

    def _ensure_drawer(self):
        if self._profile_drawer is not None:
            return
        # Find the QStackedWidget host (same pattern as RiskAlertsPage)
        widget = self.parent()
        while widget:
            parent = widget.parent()
            if parent and parent.metaObject().className() == "QStackedWidget":
                from ui.pages.student_profile_drawer import StudentProfileDrawer
                self._profile_drawer = StudentProfileDrawer(widget)
                return
            widget = parent

    def _open_profile(self, student: dict):
        self._ensure_drawer()
        if self._profile_drawer:
            self._profile_drawer.open_drawer(student)

    # ── Styles ────────────────────────────────────────────────────────

    def _apply_styles(self):
        self.setStyleSheet("""
            /* ── Term card ─────────────────────────────────────── */
            #caseloadTermCard {
                background-color: #13172a;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 14px;
            }
            #caseloadPageTitle {
                color: #e8eaf0;
                font-size: 17px;
                font-weight: bold;
                background: transparent;
            }
            #caseloadFieldLbl {
                color: rgba(255,255,255,0.40);
                font-size: 11px;
                background: transparent;
            }
            #caseloadTermStatus {
                color: rgba(255,255,255,0.40);
                font-size: 12px;
                background: transparent;
            }

            /* ── Combos ────────────────────────────────────────── */
            QComboBox#caseloadCombo {
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 8px;
                color: #e8eaf0;
                font-size: 12px;
                padding: 6px 12px;
                min-height: 32px;
            }
            QComboBox#caseloadCombo:hover {
                border-color: rgba(52,211,153,0.35);
            }
            QComboBox#caseloadCombo::drop-down {
                border: none; width: 20px;
            }
            QComboBox#caseloadCombo QAbstractItemView {
                background: #1a1f35;
                border: 1px solid rgba(255,255,255,0.12);
                color: #e8eaf0;
                selection-background-color: rgba(52,211,153,0.18);
            }

            /* ── Load button ───────────────────────────────────── */
            QPushButton#caseloadLoadBtn {
                background: #34d399;
                border: none; border-radius: 8px;
                color: #0e1120;
                font-size: 12px; font-weight: 700;
                padding: 0 20px;
            }
            QPushButton#caseloadLoadBtn:hover {
                background: rgba(52,211,153,0.85);
            }
            QPushButton#caseloadLoadBtn:disabled {
                background: rgba(255,255,255,0.06);
                color: rgba(255,255,255,0.25);
            }

            /* ── Stat tiles ────────────────────────────────────── */
            #caseloadStatTile {
                background: #13172a;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 12px;
            }

            /* ── Search ────────────────────────────────────────── */
            QLineEdit#caseloadSearch {
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 10px;
                color: #e8eaf0;
                font-size: 13px;
                padding: 9px 14px;
            }
            QLineEdit#caseloadSearch:focus {
                border-color: rgba(52,211,153,0.40);
            }
        """)