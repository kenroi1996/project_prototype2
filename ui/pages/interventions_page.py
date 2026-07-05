"""
ui/pages/interventions_page.py
================================
Counselor Portal — AI Intervention Advisor (UI only).

All backend logic (Ollama workers, DB workers, prompt strings) lives in:
  services/interventions_service.py

Layout
------
  ┌─ Term selector ──────────────────────────────────────────────────┐
  │  AY [combo]  Sem [combo]  [Load High-Risk Students]  N loaded   │
  └──────────────────────────────────────────────────────────────────┘
  [🤖 Analyze All]  [✕ Cancel]  [📊 Cohort Summary]
  [📄 Export]  [📋 Logs]
  ════ Progress bar (visible during batch) ════
  ┌─ Results scroll ─────────────────────────────────────────────────┐
  │  Collapsible card per student, appended live as AI completes     │
  └──────────────────────────────────────────────────────────────────┘

Change log
----------
  - Added _ProgramSelectorDialog: multi-select program filter shown
    before AI analysis starts. Counselor picks which programs to
    analyze instead of always processing all high-risk students.
"""
from __future__ import annotations
import json

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QComboBox, QLineEdit, QScrollArea, QStackedWidget,
    QSizePolicy, QProgressBar, QDialog, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont

from services.interventions_service import (
    OllamaWorker, BatchWorker, SaveWorker,
    TermLoader, StudentLoader,
    InterventionRecordLoader, InterventionReportWorker,
    LogLoader, LogDeleter, BatchLogDeleter,
    parse_json_response, build_cohort_prompt,
    SYSTEM_COHORT, _safe_cleanup,
)
from services.data_store    import DataStore
from services.system_config import SystemConfig


# ══════════════════════════════════════════════════════════════════════════════
# Colour helper
# ══════════════════════════════════════════════════════════════════════════════

def _risk_color(label: str) -> str:
    lc = label.lower()
    if "high" in lc:                   return "#ff5b5b"
    if "medium" in lc or "mod" in lc:  return "#f5b335"
    return "#34d399"


# ══════════════════════════════════════════════════════════════════════════════
# Reusable stripe-style row widgets
# ══════════════════════════════════════════════════════════════════════════════

def _rec_card(rec: dict, idx: int) -> QWidget:
    rtype    = rec.get("type",      "—")
    action   = rec.get("action",    "—")
    rat      = rec.get("rationale", "—")
    timeline = rec.get("timeline",  "—")
    color = {
        "Academic Support": "#4f8cff", "Financial Aid": "#f5b335",
        "Counseling": "#a78bfa", "Program Guidance": "#34d399",
        "Peer Support": "#f59e0b",
    }.get(rtype, "#8b949e")

    outer = QWidget()
    outer.setStyleSheet("background:transparent;")
    row = QHBoxLayout(outer)
    row.setContentsMargins(0, 6, 0, 6)
    row.setSpacing(0)
    stripe = QFrame()
    stripe.setFixedWidth(4)
    stripe.setStyleSheet(f"background:{color}; border-radius:2px;")
    row.addWidget(stripe)
    cw = QWidget()
    cw.setStyleSheet("background:transparent;")
    cl = QVBoxLayout(cw)
    cl.setContentsMargins(18, 2, 8, 2)
    cl.setSpacing(5)
    meta = QHBoxLayout()
    meta.setSpacing(10)
    tl = QLabel(rtype.upper())
    tl.setStyleSheet(
        f"color:{color}; font-size:11px; font-weight:700; "
        "letter-spacing:0.6px; background:transparent;")
    tll = QLabel(f"⏱  {timeline}")
    tll.setStyleSheet(
        "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;")
    meta.addWidget(tl); meta.addWidget(tll); meta.addStretch()
    cl.addLayout(meta)
    al = QLabel(action)
    al.setWordWrap(True)
    al.setStyleSheet(
        "color:#e8eaf0; font-size:13px; font-weight:600; background:transparent;")
    cl.addWidget(al)
    rl = QLabel(rat)
    rl.setWordWrap(True)
    rl.setStyleSheet(
        "color:rgba(255,255,255,0.50); font-size:12px; background:transparent;")
    cl.addWidget(rl)
    row.addWidget(cw, 1)
    return outer


def _cohort_row(issue: dict, idx: int) -> QWidget:
    title  = issue.get("issue",             "—")
    count  = issue.get("affected_count",     0)
    desc   = issue.get("description",        "—")
    action = issue.get("recommended_action", "—")
    color  = ["#ff5b5b","#f5b335","#4f8cff","#34d399","#a78bfa"][min(idx, 4)]

    outer = QWidget()
    outer.setStyleSheet("background:transparent;")
    row = QHBoxLayout(outer)
    row.setContentsMargins(0, 8, 0, 8)
    row.setSpacing(0)
    stripe = QFrame()
    stripe.setFixedWidth(4)
    stripe.setStyleSheet(f"background:{color}; border-radius:2px;")
    row.addWidget(stripe)
    cw = QWidget()
    cw.setStyleSheet("background:transparent;")
    cl = QVBoxLayout(cw)
    cl.setContentsMargins(18, 2, 8, 2)
    cl.setSpacing(5)
    meta = QHBoxLayout()
    tl = QLabel(title)
    tl.setStyleSheet(
        f"color:{color}; font-size:14px; font-weight:700; background:transparent;")
    cl_ = QLabel(f"{int(count):,} students" if str(count).isdigit() else str(count))
    cl_.setStyleSheet(
        "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;")
    meta.addWidget(tl); meta.addStretch(); meta.addWidget(cl_)
    cl.addLayout(meta)
    dl = QLabel(desc)
    dl.setWordWrap(True)
    dl.setStyleSheet(
        "color:rgba(255,255,255,0.50); font-size:12px; background:transparent;")
    cl.addWidget(dl)
    al = QLabel(action)
    al.setWordWrap(True)
    al.setStyleSheet(
        "color:#e8eaf0; font-size:13px; font-weight:600; background:transparent;")
    cl.addWidget(al)
    row.addWidget(cw, 1)
    return outer


# ══════════════════════════════════════════════════════════════════════════════
# Program Selector Dialog  (NEW)
# ══════════════════════════════════════════════════════════════════════════════

class _ProgramSelectorDialog(QDialog):
    """
    Multi-select program filter shown before AI analysis starts.

    The counselor picks which programs to include — only students
    from the selected programs are passed to BatchWorker.

    Returns selected_programs() as a list[str] after accept().
    """

    def __init__(self, students: list[dict], parent=None):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Collect unique programs and their at-risk student counts
        self._program_counts: dict[str, int] = {}
        for s in students:
            prog = str(s.get("program") or "Unknown").strip() or "Unknown"
            self._program_counts[prog] = self._program_counts.get(prog, 0) + 1

        self._checkboxes: dict[str, QCheckBox] = {}
        self._total_students = len(students)

        self._build_ui()
        self._apply_styles()
        self._update_summary()

    # ── Build ─────────────────────────────────────────────────────────

    def _build_ui(self):
        # Size dialog to fit content — taller when many programs
        n = len(self._program_counts)
        h = min(120 + n * 44 + 120, 620)
        self.setFixedSize(500, h)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)

        card = QFrame()
        card.setObjectName("progSelCard")
        outer.addWidget(card)

        root = QVBoxLayout(card)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────
        hdr_row = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(4)

        title = QLabel("Select Programs to Analyze")
        title.setStyleSheet(
            "color:#e8eaf0; font-size:15px; font-weight:bold; background:transparent;")

        sub = QLabel(
            "Choose which programs the AI should generate\n"
            "intervention plans for."
        )
        sub.setStyleSheet(
            "color:rgba(255,255,255,0.40); font-size:11px; background:transparent;")

        title_col.addWidget(title)
        title_col.addWidget(sub)
        hdr_row.addLayout(title_col, 1)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("progSelCloseBtn")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.reject)
        hdr_row.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignTop)

        root.addLayout(hdr_row)
        root.addSpacing(16)

        # ── Divider ───────────────────────────────────────────────────
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("color:rgba(255,255,255,0.08);")
        root.addWidget(div)
        root.addSpacing(14)

        # ── Select all / none row ─────────────────────────────────────
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(8)

        sel_all = QPushButton("Select All")
        sel_all.setObjectName("progSelCtrlBtn")
        sel_all.setFixedHeight(26)
        sel_all.setCursor(Qt.CursorShape.PointingHandCursor)
        sel_all.clicked.connect(self._select_all)

        sel_none = QPushButton("Clear All")
        sel_none.setObjectName("progSelCtrlBtn")
        sel_none.setFixedHeight(26)
        sel_none.setCursor(Qt.CursorShape.PointingHandCursor)
        sel_none.clicked.connect(self._select_none)

        self._summary_lbl = QLabel("")
        self._summary_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;")

        ctrl_row.addWidget(sel_all)
        ctrl_row.addWidget(sel_none)
        ctrl_row.addStretch()
        ctrl_row.addWidget(self._summary_lbl)
        root.addLayout(ctrl_row)
        root.addSpacing(10)

        # ── Scrollable program list ───────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background:transparent; border:none; }")

        list_host = QWidget()
        list_host.setStyleSheet("background:transparent;")
        list_lo = QVBoxLayout(list_host)
        list_lo.setContentsMargins(0, 0, 8, 0)
        list_lo.setSpacing(4)

        # Sort by student count descending so highest-risk programs appear first
        for prog, count in sorted(
            self._program_counts.items(),
            key=lambda x: x[1], reverse=True
        ):
            row_w = QWidget()
            row_w.setObjectName("progSelRow")
            row_w.setStyleSheet("""
                QWidget#progSelRow {
                    background: rgba(255,255,255,0.03);
                    border: 1px solid rgba(255,255,255,0.07);
                    border-radius: 8px;
                }
                QWidget#progSelRow:hover {
                    background: rgba(255,255,255,0.06);
                    border-color: rgba(79,140,255,0.25);
                }
            """)
            row_lo = QHBoxLayout(row_w)
            row_lo.setContentsMargins(14, 10, 14, 10)
            row_lo.setSpacing(12)

            cb = QCheckBox()
            cb.setChecked(True)   # default: all selected
            cb.setFixedSize(18, 18)
            cb.toggled.connect(self._update_summary)
            cb.setStyleSheet("""
                QCheckBox::indicator {
                    width: 16px; height: 16px;
                    border: 2px solid rgba(255,255,255,0.20);
                    border-radius: 4px;
                    background: rgba(255,255,255,0.05);
                }
                QCheckBox::indicator:checked {
                    background: #4f8cff;
                    border-color: #4f8cff;
                }
                QCheckBox::indicator:hover {
                    border-color: rgba(79,140,255,0.60);
                }
            """)
            self._checkboxes[prog] = cb

            # Make whole row clickable by forwarding click to checkbox
            row_w.mousePressEvent = lambda e, c=cb: c.setChecked(not c.isChecked())

            prog_lbl = QLabel(prog)
            prog_lbl.setStyleSheet(
                "color:#e8eaf0; font-size:12px; font-weight:600; background:transparent;")

            count_pill = QLabel(f"{count} student{'s' if count != 1 else ''}")
            count_pill.setStyleSheet(
                "color:#ff5b5b; font-size:10px; font-weight:600; "
                "background:rgba(255,91,91,0.12); "
                "border:1px solid rgba(255,91,91,0.25); "
                "border-radius:6px; padding:2px 8px;")

            row_lo.addWidget(cb)
            row_lo.addWidget(prog_lbl, 1)
            row_lo.addWidget(count_pill)
            list_lo.addWidget(row_w)

        list_lo.addStretch()
        scroll.setWidget(list_host)
        root.addWidget(scroll, 1)
        root.addSpacing(16)

        # ── Divider ───────────────────────────────────────────────────
        div2 = QFrame()
        div2.setFrameShape(QFrame.Shape.HLine)
        div2.setStyleSheet("color:rgba(255,255,255,0.08);")
        root.addWidget(div2)
        root.addSpacing(14)

        # ── Footer buttons ────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("progSelCancelBtn")
        cancel_btn.setFixedHeight(36)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)

        self._start_btn = QPushButton("🤖  Start Analysis")
        self._start_btn.setObjectName("progSelStartBtn")
        self._start_btn.setFixedHeight(36)
        self._start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._start_btn.clicked.connect(self._on_start)

        btn_row.addWidget(cancel_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._start_btn)
        root.addLayout(btn_row)

    # ── Helpers ───────────────────────────────────────────────────────

    def _select_all(self):
        for cb in self._checkboxes.values():
            cb.setChecked(True)

    def _select_none(self):
        for cb in self._checkboxes.values():
            cb.setChecked(False)

    def _update_summary(self):
        selected_progs = self.selected_programs()
        student_count  = sum(
            self._program_counts[p] for p in selected_progs
        )
        prog_count = len(selected_progs)

        if prog_count == 0:
            self._summary_lbl.setText("Nothing selected")
            self._start_btn.setEnabled(False)
        else:
            self._summary_lbl.setText(
                f"{prog_count} program{'s' if prog_count != 1 else ''}  ·  "
                f"{student_count} student{'s' if student_count != 1 else ''}"
            )
            self._start_btn.setEnabled(True)

    def _on_start(self):
        if self.selected_programs():
            self.accept()

    # ── Public API ────────────────────────────────────────────────────

    def selected_programs(self) -> list[str]:
        """Return list of program names whose checkbox is checked."""
        return [prog for prog, cb in self._checkboxes.items() if cb.isChecked()]

    # ── Styles ────────────────────────────────────────────────────────

    def _apply_styles(self):
        self.setStyleSheet("""
            #progSelCard {
                background: #13172a;
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 16px;
            }
            QPushButton#progSelCloseBtn {
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 7px;
                color: rgba(255,255,255,0.35);
                font-size: 13px; font-weight: bold;
            }
            QPushButton#progSelCloseBtn:hover {
                background: rgba(255,91,91,0.15);
                border-color: rgba(255,91,91,0.35);
                color: #ff5b5b;
            }
            QPushButton#progSelCtrlBtn {
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 6px;
                color: rgba(255,255,255,0.55);
                font-size: 11px; padding: 0 12px;
            }
            QPushButton#progSelCtrlBtn:hover {
                background: rgba(255,255,255,0.10);
                color: rgba(255,255,255,0.85);
            }
            QPushButton#progSelCancelBtn {
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px;
                color: rgba(255,255,255,0.60);
                font-size: 12px; font-weight: 600; padding: 0 20px;
            }
            QPushButton#progSelCancelBtn:hover {
                background: rgba(255,255,255,0.10);
            }
            QPushButton#progSelStartBtn {
                background: #4f8cff;
                border: none; border-radius: 8px;
                color: white; font-size: 12px;
                font-weight: 700; padding: 0 24px;
            }
            QPushButton#progSelStartBtn:hover {
                background: rgba(79,140,255,0.85);
            }
            QPushButton#progSelStartBtn:disabled {
                background: rgba(255,255,255,0.06);
                color: rgba(255,255,255,0.25);
            }
        """)


# ══════════════════════════════════════════════════════════════════════════════
# Collapsible student result card
# ══════════════════════════════════════════════════════════════════════════════

class _StudentResultCard(QFrame):
    def __init__(self, student: dict, recs: list, skipped: bool = False, parent=None):
        super().__init__(parent)
        self._student  = student
        self._recs     = recs
        self._skipped  = skipped
        self._expanded = False
        self._build()

    def _build(self):
        self.setObjectName("studentResultCard")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        hdr = QFrame()
        hdr.setObjectName("studentResultHdr")
        hdr.setCursor(Qt.CursorShape.PointingHandCursor)
        hdr_lo = QHBoxLayout(hdr)
        hdr_lo.setContentsMargins(16, 12, 16, 12)
        hdr_lo.setSpacing(12)

        s         = self._student
        score_raw = s.get("predicted_risk_score") or 0
        score     = round(float(score_raw) * 100, 1)
        color     = _risk_color(s.get("risk_label", "High"))

        dot = QLabel("●")
        dot.setFixedWidth(14)
        dot.setStyleSheet(f"color:{color}; font-size:11px; background:transparent;")
        name_lbl = QLabel(s.get("full_name", "—"))
        name_lbl.setStyleSheet(
            "color:#e8eaf0; font-size:13px; font-weight:600; background:transparent;")
        meta_lbl = QLabel(f"{s.get('program','—')}  ·  {s.get('college','—')}")
        meta_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.38); font-size:11px; background:transparent;")
        score_lbl = QLabel(f"{score:.1f}%")
        score_lbl.setStyleSheet(
            f"color:{color}; font-size:13px; font-weight:700; background:transparent;")
        score_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        rc = len(self._recs)

        if self._skipped:
            pill = QLabel("✓ Already analyzed")
            pill.setStyleSheet(
                "color:rgba(52,211,153,0.60); font-size:10px; font-weight:600; "
                "background:rgba(52,211,153,0.08); "
                "border:1px solid rgba(52,211,153,0.20); "
                "border-radius:8px; padding:2px 10px;")
        elif rc:
            pill = QLabel(f"{rc} recommendations")
            pill.setStyleSheet(
                "color:#4f8cff; font-size:10px; font-weight:600; "
                "background:rgba(79,140,255,0.15); "
                "border:1px solid rgba(79,140,255,0.30); "
                "border-radius:8px; padding:2px 10px;")
        else:
            pill = QLabel("No recommendations")
            pill.setStyleSheet(
                "color:rgba(255,255,255,0.30); font-size:10px; font-weight:600; "
                "background:rgba(255,255,255,0.04); "
                "border:1px solid rgba(255,255,255,0.10); "
                "border-radius:8px; padding:2px 10px;")

        self._arrow = QLabel("▶")
        self._arrow.setStyleSheet(
            "color:rgba(255,255,255,0.25); font-size:10px; background:transparent;")

        for w in [dot, name_lbl, meta_lbl]:
            hdr_lo.addWidget(w)
        hdr_lo.addStretch()
        for w in [score_lbl, pill, self._arrow]:
            hdr_lo.addWidget(w)
        root.addWidget(hdr)

        self._body = QFrame()
        self._body.setObjectName("studentResultBody")
        self._body.setVisible(False)
        body_lo = QVBoxLayout(self._body)
        body_lo.setContentsMargins(20, 8, 20, 14)
        body_lo.setSpacing(0)

        if not self._recs:
            empty = QLabel("AI could not generate recommendations for this student.")
            empty.setStyleSheet(
                "color:rgba(255,255,255,0.30); font-size:12px; background:transparent;")
            body_lo.addWidget(empty)
        else:
            if self._skipped:
                note = QLabel("Previously saved recommendations — Ollama was not called again.")
                note.setStyleSheet(
                    "color:rgba(52,211,153,0.50); font-size:11px; "
                    "background:transparent; padding-bottom:6px;")
                body_lo.addWidget(note)
            for i, rec in enumerate(self._recs):
                body_lo.addWidget(_rec_card(rec, i))
                if i < len(self._recs) - 1:
                    sep = QFrame()
                    sep.setFrameShape(QFrame.Shape.HLine)
                    sep.setStyleSheet("color:rgba(255,255,255,0.06);")
                    body_lo.addWidget(sep)

        root.addWidget(self._body)
        hdr.mousePressEvent = lambda _: self._toggle()
        self._update_style()

    def _toggle(self):
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        self._arrow.setText("▼" if self._expanded else "▶")
        self._update_style()

    def _update_style(self):
        a = "0.12" if self._expanded else "0.07"
        self.setStyleSheet(f"""
            QFrame#studentResultCard {{
                background:rgba(255,255,255,0.02);
                border:1px solid rgba(255,255,255,{a});
                border-radius:10px; margin-bottom:6px;
            }}
            QFrame#studentResultHdr:hover {{
                background:rgba(255,255,255,0.03); border-radius:10px;
            }}
            QFrame#studentResultBody {{
                background:transparent; border:none;
                border-top:1px solid rgba(255,255,255,0.06);
            }}
        """)


# ══════════════════════════════════════════════════════════════════════════════
# Detail dialog
# ══════════════════════════════════════════════════════════════════════════════

class _InterventionDetailDialog(QDialog):
    def __init__(self, row: dict, recs: list, parent=None):
        super().__init__(parent)
        self.setModal(True)
        self.resize(640, 560)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._row = row; self._recs = recs; self._drag_pos = None
        self._build_ui(); self._apply_styles()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None; super().mouseReleaseEvent(e)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        card = QFrame(); card.setObjectName("detailCard"); outer.addWidget(card)
        root = QVBoxLayout(card)
        root.setContentsMargins(28, 22, 28, 24); root.setSpacing(0)

        hdr = QHBoxLayout(); ic = QVBoxLayout(); ic.setSpacing(4)
        mode   = self._row.get("mode", "per_student")
        sid    = self._row.get("student_id")
        name   = str(self._row.get("student_name") or "").strip() or str(sid or "—")
        ay     = self._row.get("academic_year", "—")
        sem_n  = self._row.get("semester")
        sem_s  = "1st Semester" if sem_n == 1 else "2nd Semester" if sem_n == 2 else ""
        term   = f"{sem_s}  AY {ay}" if sem_s else ay
        risk_l = str(self._row.get("risk_label") or "—")
        score  = self._row.get("risk_score")
        logged = self._row.get("logged_at")
        ls     = (logged.strftime("%B %d, %Y  %H:%M")
                  if hasattr(logged, "strftime") else str(logged or "")[:16])

        hl = QLabel("Cohort Systemic Issues" if mode == "cohort" else name)
        hl.setObjectName("detailHeadline"); hl.setWordWrap(True)
        if mode != "cohort" and score:
            rc = ("#ff5b5b" if "high" in risk_l.lower() else
                  "#f5b335" if "medium" in risk_l.lower() else "#34d399")
            sl = QLabel(f"● {risk_l}  {float(score):.1f}% risk")
            sl.setStyleSheet(
                f"color:{rc}; font-size:12px; font-weight:600; background:transparent;")
            ic.addWidget(sl)
        ic.insertWidget(0, hl)
        for text, style in [
            (f"ID {sid}  ·  {term}",
             "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;"),
            (f"Generated  {ls}" if ls else "",
             "color:rgba(255,255,255,0.25); font-size:10px; background:transparent;"),
        ]:
            if text:
                lbl = QLabel(text); lbl.setStyleSheet(style); ic.addWidget(lbl)
        hdr.addLayout(ic, 1)
        cb = QPushButton("✕"); cb.setObjectName("detailCloseBtn")
        cb.setFixedSize(28, 28); cb.setCursor(Qt.CursorShape.PointingHandCursor)
        cb.clicked.connect(self.reject)
        hdr.addWidget(cb, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(hdr); root.addSpacing(14)
        div = QFrame(); div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("color:rgba(255,255,255,0.07);"); root.addWidget(div)
        root.addSpacing(12)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background:transparent; border:none; }")
        host = QWidget(); host.setStyleSheet("background:transparent;")
        hl2 = QVBoxLayout(host); hl2.setContentsMargins(0, 0, 8, 0); hl2.setSpacing(4)
        if not self._recs:
            el = QLabel("No recommendation data available.")
            el.setStyleSheet(
                "color:rgba(255,255,255,0.30); font-size:12px; background:transparent;")
            el.setAlignment(Qt.AlignmentFlag.AlignCenter); hl2.addWidget(el)
        else:
            for i, rec in enumerate(self._recs):
                hl2.addWidget(_cohort_row(rec, i) if mode == "cohort"
                              else _rec_card(rec, i))
                if i < len(self._recs) - 1:
                    sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
                    sep.setStyleSheet("color:rgba(255,255,255,0.06);"); hl2.addWidget(sep)
        hl2.addStretch(); scroll.setWidget(host); root.addWidget(scroll, 1)
        root.addSpacing(12)
        br = QHBoxLayout(); br.addStretch()
        cl = QPushButton("Close"); cl.setObjectName("detailCloseBottomBtn")
        cl.setFixedHeight(34); cl.setMinimumWidth(100)
        cl.setCursor(Qt.CursorShape.PointingHandCursor); cl.clicked.connect(self.reject)
        br.addWidget(cl); root.addLayout(br)

    def _apply_styles(self):
        self.setStyleSheet("""
            #detailCard { background:#13172a;
                border:1px solid rgba(255,255,255,0.10); border-radius:16px; }
            #detailHeadline { color:#e8eaf0; font-size:16px; font-weight:bold;
                background:transparent; }
            #detailCloseBtn { background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.10); border-radius:7px;
                color:rgba(255,255,255,0.35); font-size:13px; font-weight:bold; }
            #detailCloseBtn:hover { background:rgba(255,91,91,0.15);
                border-color:rgba(255,91,91,0.35); color:#ff5b5b; }
            #detailCloseBottomBtn { background:rgba(255,255,255,0.06);
                border:1px solid rgba(255,255,255,0.12); border-radius:8px;
                color:rgba(255,255,255,0.70); font-size:12px; font-weight:600;
                padding:0 20px; }
            #detailCloseBottomBtn:hover { background:rgba(255,255,255,0.12);
                color:#e8eaf0; }
        """)


# ══════════════════════════════════════════════════════════════════════════════
# Log dialog
# ══════════════════════════════════════════════════════════════════════════════

class InterventionLogDialog(QDialog):
    """Full-featured log viewer with search, filter, pagination,
    checkbox multi-select, and batch delete."""

    PAGE_SIZE = 20
    _COL_CHK  = 0
    _COL_ID   = 1
    _COL_SID  = 2
    _COL_NAME = 3
    _COL_TERM = 4
    _COL_TYPE = 5
    _COL_RISK = 6
    _COL_RECS = 7
    _COL_LOG  = 8
    _COL_VIEW = 9
    _COL_DEL  = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setModal(True); self.resize(1200, 700)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._all_rows:       list[dict]        = []
        self._page            = 0
        self._loader:         LogLoader         | None = None
        self._deleter:        LogDeleter        | None = None
        self._batch_deleter:  BatchLogDeleter   | None = None
        self._drag_pos        = None

        self._build_ui()
        self._apply_styles()
        self._load()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                e.globalPosition().toPoint() - self.frameGeometry().topLeft())
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None; super().mouseReleaseEvent(e)

    def _build_ui(self):
        outer = QVBoxLayout(self); outer.setContentsMargins(16, 16, 16, 16)
        card = QFrame(); card.setObjectName("logCard"); outer.addWidget(card)
        root = QVBoxLayout(card)
        root.setContentsMargins(24, 20, 24, 20); root.setSpacing(14)

        hdr = QHBoxLayout(); tc = QVBoxLayout(); tc.setSpacing(2)
        for text, style in [
            ("Intervention Logs",
             "color:#e8eaf0; font-size:16px; font-weight:bold; background:transparent;"),
            ("All AI-generated intervention records",
             "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;"),
        ]:
            lbl = QLabel(text); lbl.setStyleSheet(style); tc.addWidget(lbl)
        hdr.addLayout(tc); hdr.addStretch()
        xb = QPushButton("✕"); xb.setObjectName("logCloseBtn")
        xb.setFixedSize(28, 28); xb.setCursor(Qt.CursorShape.PointingHandCursor)
        xb.clicked.connect(self.reject)
        hdr.addWidget(xb); root.addLayout(hdr)

        f1 = QHBoxLayout(); f1.setSpacing(10)
        self._sid_search  = self._inp("🔍  Student ID",    150)
        self._name_search = self._inp("🔍  Student Name",  200)
        self._ay_filter   = self._cb(["All Terms"])
        self._sem_filter  = self._cb(["All Semesters","1st Semester","2nd Semester"])
        self._mode_filter = self._cb(["All Types","per_student","cohort"])
        self._date_from   = self._inp("From (YYYY-MM-DD)", 148)
        self._date_to     = self._inp("To (YYYY-MM-DD)",   148)
        clr = QPushButton("✕  Clear"); clr.setObjectName("logClearBtn")
        clr.setFixedHeight(32); clr.setCursor(Qt.CursorShape.PointingHandCursor)
        clr.clicked.connect(self._clear_filters)
        for w in [self._sid_search, self._name_search, self._ay_filter,
                  self._sem_filter, self._mode_filter, self._date_from,
                  self._date_to, clr]:
            f1.addWidget(w)
        f1.addStretch()
        self._count_lbl = QLabel(""); self._count_lbl.setObjectName("logCount")
        f1.addWidget(self._count_lbl); root.addLayout(f1)
        for w in (self._sid_search, self._name_search,
                  self._date_from, self._date_to):
            w.textChanged.connect(self._on_filter_changed)
        for c in (self._ay_filter, self._sem_filter, self._mode_filter):
            c.currentIndexChanged.connect(self._on_filter_changed)

        self._batch_bar_frame = QFrame()
        self._batch_bar_frame.setObjectName("logBatchBar")
        self._batch_bar_frame.setVisible(False)
        bb = QHBoxLayout(self._batch_bar_frame)
        bb.setContentsMargins(12, 8, 12, 8); bb.setSpacing(10)

        self._sel_lbl = QLabel("0 selected")
        self._sel_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.55); font-size:12px; background:transparent;")

        self._del_selected_btn = QPushButton("🗑  Delete Selected")
        self._del_selected_btn.setObjectName("logBatchDelBtn")
        self._del_selected_btn.setFixedHeight(30)
        self._del_selected_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._del_selected_btn.clicked.connect(self._on_delete_selected)

        self._del_all_btn = QPushButton("🗑  Delete All Filtered")
        self._del_all_btn.setObjectName("logBatchDelAllBtn")
        self._del_all_btn.setFixedHeight(30)
        self._del_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._del_all_btn.clicked.connect(self._on_delete_all_filtered)

        desel_btn = QPushButton("✕  Deselect All")
        desel_btn.setObjectName("logClearBtn")
        desel_btn.setFixedHeight(30)
        desel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        desel_btn.clicked.connect(self._deselect_all)

        bb.addWidget(self._sel_lbl)
        bb.addWidget(self._del_selected_btn)
        bb.addWidget(self._del_all_btn)
        bb.addStretch()
        bb.addWidget(desel_btn)
        root.addWidget(self._batch_bar_frame)

        self._table = QTableWidget(); self._table.setObjectName("logTable")
        self._table.setColumnCount(11)
        self._table.setHorizontalHeaderLabels([
            "☐", "ID", "Student ID", "Name", "Term",
            "Type", "Risk", "Recs", "Logged At", "View", "Delete",
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setHighlightSections(False)
        self._table.setShowGrid(False)
        self._table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)

        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for col, m, w in [
            (self._COL_CHK,  QHeaderView.ResizeMode.Fixed,            32),
            (self._COL_ID,   QHeaderView.ResizeMode.Fixed,            44),
            (self._COL_SID,  QHeaderView.ResizeMode.Fixed,            80),
            (self._COL_NAME, QHeaderView.ResizeMode.Fixed,           160),
            (self._COL_TERM, QHeaderView.ResizeMode.ResizeToContents, 0),
            (self._COL_TYPE, QHeaderView.ResizeMode.ResizeToContents, 0),
            (self._COL_RISK, QHeaderView.ResizeMode.ResizeToContents, 0),
            (self._COL_RECS, QHeaderView.ResizeMode.ResizeToContents, 0),
            (self._COL_LOG,  QHeaderView.ResizeMode.ResizeToContents, 0),
            (self._COL_VIEW, QHeaderView.ResizeMode.Fixed,            56),
            (self._COL_DEL,  QHeaderView.ResizeMode.Fixed,            56),
        ]:
            hh.setSectionResizeMode(col, m)
            if w:
                self._table.setColumnWidth(col, w)

        root.addWidget(self._table, 1)

        pag = QHBoxLayout(); pag.setSpacing(8)
        self._prev_btn = QPushButton("‹  Prev")
        self._prev_btn.setObjectName("logPagBtn"); self._prev_btn.setFixedHeight(30)
        self._prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._prev_btn.clicked.connect(self._prev_page)
        self._prev_btn.setEnabled(False)

        self._page_lbl = QLabel("Page 1 of 1")
        self._page_lbl.setObjectName("logCount")
        self._page_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_lbl.setFixedWidth(110)

        self._next_btn = QPushButton("Next  ›")
        self._next_btn.setObjectName("logPagBtn"); self._next_btn.setFixedHeight(30)
        self._next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_btn.clicked.connect(self._next_page)
        self._next_btn.setEnabled(False)

        self._status_lbl = QLabel(""); self._status_lbl.setObjectName("logCount")

        pag.addWidget(self._prev_btn); pag.addWidget(self._page_lbl)
        pag.addWidget(self._next_btn); pag.addStretch()
        pag.addWidget(self._status_lbl); root.addLayout(pag)

    @staticmethod
    def _inp(ph: str, w: int) -> QLineEdit:
        e = QLineEdit(); e.setObjectName("logSearch")
        e.setPlaceholderText(ph); e.setFixedWidth(w); return e

    @staticmethod
    def _cb(items: list) -> QComboBox:
        c = QComboBox(); c.setObjectName("logCombo")
        c.addItems(items); c.setCursor(Qt.CursorShape.PointingHandCursor); return c

    def _checked_ids(self) -> list[int]:
        ids = []
        start = self._page * self.PAGE_SIZE
        for ri in range(self._table.rowCount()):
            cb = self._table.cellWidget(ri, self._COL_CHK)
            if cb and cb.isChecked():
                row = self._all_rows[start + ri]
                ids.append(row.get("intervention_id"))
        return [i for i in ids if i is not None]

    def _update_batch_bar(self):
        ids = self._checked_ids()
        n   = len(ids)
        self._sel_lbl.setText(
            f"{n} row{'s' if n != 1 else ''} selected on this page")
        self._batch_bar_frame.setVisible(n > 0)
        self._del_selected_btn.setEnabled(n > 0)

    def _on_header_clicked(self, col: int):
        if col != self._COL_CHK:
            return
        any_unchecked = False
        for r in range(self._table.rowCount()):
            cb = self._table.cellWidget(r, self._COL_CHK)
            if cb and not cb.isChecked():
                any_unchecked = True
                break
        for r in range(self._table.rowCount()):
            cb = self._table.cellWidget(r, self._COL_CHK)
            if cb:
                cb.setChecked(any_unchecked)
        self._update_batch_bar()

    def _deselect_all(self):
        for r in range(self._table.rowCount()):
            cb = self._table.cellWidget(r, self._COL_CHK)
            if cb:
                cb.setChecked(False)
        self._update_batch_bar()

    def _build_filters(self) -> dict:
        ay = self._ay_filter.currentText()
        sem = self._sem_filter.currentIndex()
        mode = self._mode_filter.currentText()
        return {
            "academic_year": ay   if ay   != "All Terms"  else "",
            "semester":      sem  if sem  != 0            else "",
            "mode":          mode if mode != "All Types"  else "",
            "student_id":    self._sid_search.text().strip(),
            "student_name":  self._name_search.text().strip(),
            "date_from":     self._date_from.text().strip(),
            "date_to":       self._date_to.text().strip(),
        }

    def _load(self):
        self._status_lbl.setText("Loading…")
        self._loader = LogLoader(self._build_filters())
        self._loader.finished.connect(self._on_loaded)
        self._loader.error.connect(lambda m: (
            self._status_lbl.setText(f"⚠ {m}"),
            _safe_cleanup(self._loader),
        ))
        self._loader.finished.connect(self._loader.deleteLater)
        self._loader.start()

    def _on_loaded(self, rows: list):
        self._all_rows = rows; self._page = 0
        self._populate_ay_filter(rows); self._render_page()
        self._status_lbl.setText("")
        self._batch_bar_frame.setVisible(False)

    def _populate_ay_filter(self, rows):
        cur = self._ay_filter.currentText()
        self._ay_filter.blockSignals(True); self._ay_filter.clear()
        self._ay_filter.addItem("All Terms")
        seen = []
        for r in rows:
            ay = str(r.get("academic_year", ""))
            if ay and ay not in seen:
                seen.append(ay); self._ay_filter.addItem(ay)
        idx = self._ay_filter.findText(cur)
        self._ay_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self._ay_filter.blockSignals(False)

    def _on_filter_changed(self):
        if not hasattr(self, "_ft"):
            self._ft = QTimer(self); self._ft.setSingleShot(True)
            self._ft.timeout.connect(self._load)
        self._ft.start(400)

    def _clear_filters(self):
        for w in (self._sid_search, self._name_search,
                  self._date_from, self._date_to):
            w.blockSignals(True); w.clear(); w.blockSignals(False)
        for c in (self._ay_filter, self._sem_filter, self._mode_filter):
            c.blockSignals(True); c.setCurrentIndex(0); c.blockSignals(False)
        self._load()

    def _total_pages(self):
        return max(1, (len(self._all_rows) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)

    def _prev_page(self):
        if self._page > 0:
            self._page -= 1; self._render_page()

    def _next_page(self):
        if self._page < self._total_pages() - 1:
            self._page += 1; self._render_page()

    def _render_page(self):
        start = self._page * self.PAGE_SIZE
        rows  = self._all_rows[start:start + self.PAGE_SIZE]
        total = len(self._all_rows); pages = self._total_pages()

        self._count_lbl.setText(f"{total:,} record{'s' if total != 1 else ''}")
        self._page_lbl.setText(f"Page {self._page+1} of {pages}")
        self._prev_btn.setEnabled(self._page > 0)
        self._next_btn.setEnabled(self._page < pages - 1)

        self._table.setRowCount(0)
        self._table.setRowCount(len(rows))
        self._table.setUpdatesEnabled(False)

        for ri, row in enumerate(rows):
            iid     = row.get("intervention_id", "")
            sid     = str(row.get("student_id") or "—")
            name    = str(row.get("student_name") or "—").strip() or "—"
            if len(name) > 22:
                name = name[:20] + "…"
            ay      = str(row.get("academic_year") or "—")
            sem_n   = row.get("semester")
            term    = f"{ay} S{sem_n}" if sem_n else ay
            mode    = str(row.get("mode") or "—")
            risk_l  = str(row.get("risk_label") or "—")
            rec_cnt = row.get("rec_count", 0)
            logged  = row.get("logged_at")
            ls      = (logged.strftime("%b %d, %Y %H:%M")
                       if hasattr(logged, "strftime")
                       else str(logged or "—")[:16])
            rc = QColor(
                "#ff5b5b" if "high" in risk_l.lower() else
                "#f5b335" if "medium" in risk_l.lower() or "mod" in risk_l.lower()
                else "#34d399")
            ml = "Per Student" if mode == "per_student" else "Cohort"

            cb = QPushButton()
            cb.setObjectName("logCheckBtn")
            cb.setCheckable(True)
            cb.setFixedSize(20, 20)
            cb.setToolTip("Select row")
            cb.toggled.connect(lambda _: self._update_batch_bar())
            self._table.setCellWidget(ri, self._COL_CHK, cb)

            for ci, (text, color) in enumerate([
                (str(iid), None), (sid, None), (name, None), (term, None),
                (ml, None), (risk_l, rc), (f"{rec_cnt} recs", None), (ls, None),
            ], 1):
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                if color:
                    item.setForeground(color)
                    item.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
                self._table.setItem(ri, ci, item)

            for col_idx, (obj, lbl, hdl) in enumerate([
                ("logViewBtn", "👁", lambda _, r=row: self._on_view(r)),
                ("logDelBtn",  "🗑", lambda _, rid=iid: self._on_del(rid)),
            ], self._COL_VIEW):
                btn = QPushButton(lbl); btn.setObjectName(obj)
                btn.setFixedSize(38, 28)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(hdl)
                cell = QWidget(); cell.setStyleSheet("background:transparent;")
                cl = QHBoxLayout(cell); cl.setContentsMargins(4, 2, 4, 2)
                cl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cl.addWidget(btn)
                self._table.setCellWidget(ri, col_idx, cell)

        self._table.setUpdatesEnabled(True)

        for r in range(self._table.rowCount()):
            self._table.setRowHeight(r, 38)

        self._update_batch_bar()

    def _on_view(self, row: dict):
        iid = row.get("intervention_id"); recs = row.get("recommendations")
        if recs is None:
            conn = DataStore.get().db_conn
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT recommendations FROM public.interventions "
                        "WHERE intervention_id = %s", (iid,))
                    dr = cur.fetchone(); recs = dr[0] if dr else []
            except Exception:
                recs = []
        if isinstance(recs, str):
            try: recs = json.loads(recs)
            except Exception: recs = []
        _InterventionDetailDialog(row, recs or [], self).exec()

    def _on_del(self, intervention_id: int):
        if not self._confirm_delete(
                "Delete Intervention Log",
                "Permanently delete this intervention record?",
                "This cannot be undone."):
            return
        self._status_lbl.setText("Deleting…")
        self._deleter = LogDeleter(intervention_id)
        self._deleter.finished.connect(lambda iid: self._remove_ids([iid], "record"))
        self._deleter.error.connect(lambda m: (
            self._status_lbl.setText(f"⚠ {m[:80]}"),
            _safe_cleanup(self._deleter),
        ))
        self._deleter.finished.connect(self._deleter.deleteLater)
        self._deleter.start()

    def _on_delete_selected(self):
        ids = self._checked_ids()
        if not ids:
            return
        if not self._confirm_delete(
                "Delete Selected",
                f"Permanently delete {len(ids):,} selected "
                f"intervention record{'s' if len(ids) != 1 else ''}?",
                "This cannot be undone."):
            return
        self._run_batch_delete(ids)

    def _on_delete_all_filtered(self):
        total = len(self._all_rows)
        if not total:
            return
        if not self._confirm_delete(
                "Delete All Filtered Records",
                f"Permanently delete all {total:,} filtered "
                f"intervention record{'s' if total != 1 else ''}?",
                "This will delete every record currently shown — "
                "across all pages. This cannot be undone."):
            return
        ids = [r.get("intervention_id") for r in self._all_rows
               if r.get("intervention_id") is not None]
        self._run_batch_delete(ids)

    def _run_batch_delete(self, ids: list[int]):
        self._status_lbl.setText(f"Deleting {len(ids):,} records…")
        self._del_selected_btn.setEnabled(False)
        self._del_all_btn.setEnabled(False)
        self._batch_deleter = BatchLogDeleter(ids)
        self._batch_deleter.finished.connect(
            lambda deleted: self._remove_ids(deleted, "records"))
        self._batch_deleter.error.connect(lambda m: (
            self._status_lbl.setText(f"⚠ Delete failed: {m[:80]}"),
            self._del_selected_btn.setEnabled(True),
            self._del_all_btn.setEnabled(True),
            _safe_cleanup(self._batch_deleter),
        ))
        self._batch_deleter.finished.connect(self._batch_deleter.deleteLater)
        self._batch_deleter.start()

    def _remove_ids(self, deleted_ids: list, noun: str):
        deleted_set = set(deleted_ids)
        self._all_rows = [r for r in self._all_rows
                          if r.get("intervention_id") not in deleted_set]
        if self._page >= self._total_pages():
            self._page = max(0, self._total_pages() - 1)
        n = len(deleted_ids)
        self._status_lbl.setText(f"✓  {n:,} {noun} deleted.")
        self._del_selected_btn.setEnabled(True)
        self._del_all_btn.setEnabled(True)
        self._render_page()
        QTimer.singleShot(3000, lambda: self._status_lbl.setText(""))

    def _confirm_delete(self, title: str, text: str, info: str) -> bool:
        msg = QMessageBox(self)
        msg.setWindowTitle(title)
        msg.setText(text)
        msg.setInformativeText(info)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setStandardButtons(
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
        msg.setDefaultButton(QMessageBox.StandardButton.Cancel)
        msg.button(QMessageBox.StandardButton.Yes).setText("Delete")
        msg.setStyleSheet("""
            QMessageBox { background:#13172a; }
            QMessageBox QLabel { color:#e8eaf0; font-size:13px; background:transparent; }
            QMessageBox QPushButton {
                background:rgba(255,255,255,0.06);
                border:1px solid rgba(255,255,255,0.14); border-radius:8px;
                color:rgba(255,255,255,0.80); font-size:12px; font-weight:600;
                padding:8px 24px; min-width:80px; }
            QMessageBox QPushButton:hover { background:rgba(255,255,255,0.12); }
            QMessageBox QPushButton[text="Delete"] {
                background:rgba(255,91,91,0.15);
                border-color:rgba(255,91,91,0.40); color:#ff5b5b; }
            QMessageBox QPushButton[text="Delete"]:hover {
                background:rgba(255,91,91,0.28); }
        """)
        return msg.exec() == QMessageBox.StandardButton.Yes

    def _apply_styles(self):
        self.setStyleSheet("""
            #logCard { background:#13172a;
                border:1px solid rgba(255,255,255,0.10); border-radius:16px; }
            #logCloseBtn { background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.10); border-radius:7px;
                color:rgba(255,255,255,0.35); font-size:13px; font-weight:bold; }
            #logCloseBtn:hover { background:rgba(255,91,91,0.15);
                border-color:rgba(255,91,91,0.35); color:#ff5b5b; }
            #logBatchBar { background:rgba(255,91,91,0.06);
                border:1px solid rgba(255,91,91,0.18); border-radius:8px; }
            QPushButton#logBatchDelBtn {
                background:rgba(255,91,91,0.14);
                border:1px solid rgba(255,91,91,0.35);
                border-radius:7px; color:#ff5b5b;
                font-size:12px; font-weight:600; padding:0 14px; }
            QPushButton#logBatchDelBtn:hover { background:rgba(255,91,91,0.26); }
            QPushButton#logBatchDelBtn:disabled {
                background:rgba(255,255,255,0.04);
                border-color:rgba(255,255,255,0.08);
                color:rgba(255,255,255,0.20); }
            QPushButton#logBatchDelAllBtn {
                background:rgba(255,91,91,0.08);
                border:1px solid rgba(255,91,91,0.25);
                border-radius:7px; color:rgba(255,91,91,0.75);
                font-size:12px; font-weight:600; padding:0 14px; }
            QPushButton#logBatchDelAllBtn:hover { background:rgba(255,91,91,0.20); }
            QPushButton#logCheckBtn {
                background:rgba(255,255,255,0.06);
                border:1px solid rgba(255,255,255,0.18);
                border-radius:4px; color:transparent; }
            QPushButton#logCheckBtn:hover {
                background:rgba(255,255,255,0.12);
                border-color:rgba(255,91,91,0.40); }
            QPushButton#logCheckBtn:checked {
                background:#ff5b5b; border-color:#ff5b5b; color:white; }
            QLineEdit#logSearch { background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.10); border-radius:8px;
                color:#e8eaf0; font-size:12px; padding:6px 10px; }
            QLineEdit#logSearch:focus { border-color:rgba(52,211,153,0.40); }
            QComboBox#logCombo { background:rgba(255,255,255,0.06);
                border:1px solid rgba(255,255,255,0.12); border-radius:8px;
                color:#e8eaf0; font-size:12px; padding:5px 10px; min-height:30px; }
            QComboBox#logCombo:hover { border-color:rgba(52,211,153,0.35); }
            QComboBox#logCombo::drop-down { border:none; width:16px; }
            QComboBox#logCombo QAbstractItemView { background:#1a1f35; color:#e8eaf0;
                selection-background-color:rgba(52,211,153,0.18); }
            QPushButton#logClearBtn { background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.10); border-radius:8px;
                color:rgba(255,255,255,0.50); font-size:11px; padding:0 12px; }
            QPushButton#logClearBtn:hover { background:rgba(255,255,255,0.10);
                color:rgba(255,255,255,0.80); }
            QTableWidget#logTable { background:transparent; border:none;
                color:rgba(255,255,255,0.85); font-size:12px;
                alternate-background-color:rgba(255,255,255,0.025);
                selection-background-color:transparent;
                gridline-color:transparent; }
            QTableWidget#logTable QHeaderView::section {
                background:rgba(255,255,255,0.05); color:rgba(255,255,255,0.45);
                font-size:11px; font-weight:bold; border:none;
                border-right:1px solid rgba(255,255,255,0.06); padding:8px 6px; }
            QTableWidget#logTable QHeaderView::section:first {
                color:rgba(255,255,255,0.30); font-size:13px; }
            QPushButton#logViewBtn { background:rgba(79,140,255,0.08);
                border:1px solid rgba(79,140,255,0.25);
                border-radius:6px; color:#4f8cff; font-size:13px; }
            QPushButton#logViewBtn:hover { background:rgba(79,140,255,0.20);
                border-color:rgba(79,140,255,0.50); }
            QPushButton#logDelBtn { background:rgba(255,91,91,0.08);
                border:1px solid rgba(255,91,91,0.25);
                border-radius:6px; color:#ff5b5b; font-size:13px; }
            QPushButton#logDelBtn:hover { background:rgba(255,91,91,0.20);
                border-color:rgba(255,91,91,0.50); }
            QPushButton#logPagBtn { background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.10); border-radius:7px;
                color:rgba(255,255,255,0.60); font-size:11px; padding:0 14px; }
            QPushButton#logPagBtn:hover { background:rgba(255,255,255,0.10);
                color:rgba(255,255,255,0.90); }
            QPushButton#logPagBtn:disabled { color:rgba(255,255,255,0.20);
                border-color:rgba(255,255,255,0.06); }
            #logCount { color:rgba(255,255,255,0.35); font-size:11px;
                background:transparent; }
        """)


# ══════════════════════════════════════════════════════════════════════════════
# Term-select dialog
# ══════════════════════════════════════════════════════════════════════════════

class _TermSelectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setModal(True); self.setFixedSize(420, 300)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._selected: tuple = ()
        self._build_ui(); self._apply_styles(); self._load_terms()

    def _build_ui(self):
        outer = QVBoxLayout(self); outer.setContentsMargins(16,16,16,16)
        card = QFrame(); card.setObjectName("termSelCard"); outer.addWidget(card)
        lo = QVBoxLayout(card); lo.setContentsMargins(28,24,28,24); lo.setSpacing(12)
        for text, style in [
            ("Export Intervention Report",
             "color:#e8eaf0; font-size:15px; font-weight:bold; background:transparent;"),
            ("Select the academic term to include in the report.",
             "color:rgba(255,255,255,0.40); font-size:11px; background:transparent;")]:
            lbl = QLabel(text); lbl.setStyleSheet(style); lbl.setWordWrap(True)
            lo.addWidget(lbl)
        div = QFrame(); div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("color:rgba(255,255,255,0.08);"); lo.addWidget(div)
        lbl = QLabel("Academic Term")
        lbl.setStyleSheet(
            "color:rgba(255,255,255,0.55); font-size:11px; font-weight:600; background:transparent;")
        lo.addWidget(lbl)
        self._term_combo = QComboBox(); self._term_combo.setObjectName("termSelCombo")
        self._term_combo.addItem("Loading…"); self._term_combo.setEnabled(False)
        lo.addWidget(self._term_combo)
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.35); font-size:10px; background:transparent;")
        lo.addWidget(self._status_lbl); lo.addStretch()
        br = QHBoxLayout(); br.setSpacing(10)
        cb = QPushButton("Cancel"); cb.setObjectName("termSelCancelBtn")
        cb.setFixedHeight(36); cb.setCursor(Qt.CursorShape.PointingHandCursor)
        cb.clicked.connect(self.reject)
        self._confirm_btn = QPushButton("Export  →")
        self._confirm_btn.setObjectName("termSelConfirmBtn"); self._confirm_btn.setFixedHeight(36)
        self._confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._confirm_btn.setEnabled(False); self._confirm_btn.clicked.connect(self._on_confirm)
        br.addWidget(cb); br.addStretch(); br.addWidget(self._confirm_btn); lo.addLayout(br)

    def _load_terms(self):
        conn = DataStore.get().db_conn
        if not conn:
            self._status_lbl.setText("No database connection."); return
        try:
            with conn.cursor() as cur:
                cur.execute("""SELECT DISTINCT academic_year, semester
                    FROM public.interventions ORDER BY academic_year DESC, semester DESC""")
                terms = cur.fetchall()
        except Exception as e:
            self._status_lbl.setText(f"Error: {e}"); return
        self._term_combo.clear()
        if not terms:
            self._term_combo.addItem("No records found")
            self._status_lbl.setText("No intervention records yet."); return
        self._term_combo.addItem("— Select a term —")
        for ay, sem in terms:
            sl = "1st Semester" if sem == 1 else "2nd Semester"
            self._term_combo.addItem(f"{sl}  ·  AY {ay}", userData=(ay, sem))
        self._term_combo.setEnabled(True)
        self._term_combo.currentIndexChanged.connect(self._on_term_changed)
        self._status_lbl.setText(f"{len(terms)} term(s) with records.")

    def _on_term_changed(self, idx):
        self._confirm_btn.setEnabled(
            self._term_combo.itemData(idx, Qt.ItemDataRole.UserRole) is not None)

    def _on_confirm(self):
        idx = self._term_combo.currentIndex()
        data = self._term_combo.itemData(idx, Qt.ItemDataRole.UserRole)
        if data: self._selected = data; self.accept()

    def selected_term(self) -> tuple:
        if not self._selected: return ("",0,"")
        ay, sem = self._selected
        return (ay, sem, f"{'1st' if sem==1 else '2nd'} Semester  AY {ay}")

    def _apply_styles(self):
        self.setStyleSheet("""
            #termSelCard { background:#13172a; border:1px solid rgba(255,255,255,0.10);
                border-radius:16px; }
            QComboBox#termSelCombo { background:rgba(255,255,255,0.06);
                border:1px solid rgba(255,255,255,0.14); border-radius:8px;
                color:#e8eaf0; font-size:13px; padding:8px 12px; min-height:36px; }
            QComboBox#termSelCombo:hover { border-color:rgba(52,211,153,0.40); }
            QComboBox#termSelCombo::drop-down { border:none; width:18px; }
            QComboBox#termSelCombo QAbstractItemView { background:#1a1f35; color:#e8eaf0;
                selection-background-color:rgba(52,211,153,0.18); }
            QPushButton#termSelCancelBtn { background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.12); border-radius:8px;
                color:rgba(255,255,255,0.60); font-size:12px; font-weight:600; padding:0 20px; }
            QPushButton#termSelCancelBtn:hover { background:rgba(255,255,255,0.10); }
            QPushButton#termSelConfirmBtn { background:#34d399; border:none;
                border-radius:8px; color:#0e1120; font-size:12px; font-weight:700; padding:0 24px; }
            QPushButton#termSelConfirmBtn:hover { background:rgba(52,211,153,0.85); }
            QPushButton#termSelConfirmBtn:disabled { background:rgba(255,255,255,0.06);
                color:rgba(255,255,255,0.25); }
        """)


# ══════════════════════════════════════════════════════════════════════════════
# Main page
# ══════════════════════════════════════════════════════════════════════════════

class InterventionsPage(QWidget):

    def __init__(self):
        super().__init__()
        self._students:   list[dict] = []
        self._ay:  str = ""
        self._sem: int = 1
        self._term_loader:    TermLoader               | None = None
        self._student_loader: StudentLoader            | None = None
        self._batch_worker:   BatchWorker              | None = None
        self._cohort_worker:  OllamaWorker             | None = None
        self._check_worker:   OllamaWorker             | None = None
        self._save_workers:   list[SaveWorker]         = []
        self._record_loader:  InterventionRecordLoader | None = None
        self._report_worker:  InterventionReportWorker | None = None
        self._setup_ui(); self._apply_styles(); self._load_terms()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 24, 30, 24); root.setSpacing(16)

        hdr_row = QHBoxLayout(); tc = QVBoxLayout(); tc.setSpacing(3)
        title = QLabel("AI Intervention Advisor")
        title.setStyleSheet(
            "color:#e8eaf0; font-size:18px; font-weight:bold; background:transparent;")
        sub = QLabel("Powered by Ollama  ·  " + SystemConfig.ollama_model() +
                     "  ·  Runs fully offline")
        sub.setObjectName("intervSub"); tc.addWidget(title); tc.addWidget(sub)
        hdr_row.addLayout(tc, 1)
        self._status_pill = QLabel("⚫  Ollama not checked")
        self._status_pill.setObjectName("intervStatusPill")
        self._status_pill.setMinimumWidth(220)
        self._status_pill.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        hdr_row.addWidget(self._status_pill); root.addLayout(hdr_row)

        sel = QFrame(); sel.setObjectName("intervSelCard")
        sl = QHBoxLayout(sel); sl.setContentsMargins(20,14,20,14); sl.setSpacing(14)
        for lbl_txt in ("AY:", "Sem:"):
            lbl = QLabel(lbl_txt)
            lbl.setStyleSheet(
                "color:rgba(255,255,255,0.40); font-size:11px; background:transparent;")
            sl.addWidget(lbl)
            if lbl_txt == "AY:":
                self._ay_combo = QComboBox(); self._ay_combo.setObjectName("intervCombo")
                self._ay_combo.setMinimumWidth(130); self._ay_combo.addItem("Loading…")
                self._ay_combo.setEnabled(False); sl.addWidget(self._ay_combo)
            else:
                self._sem_combo = QComboBox(); self._sem_combo.setObjectName("intervCombo")
                self._sem_combo.addItems(["1st Semester","2nd Semester"])
                sl.addWidget(self._sem_combo)
        self._load_btn = QPushButton("⟳  Load High-Risk Students")
        self._load_btn.setObjectName("intervLoadBtn"); self._load_btn.setFixedHeight(34)
        self._load_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._load_btn.setEnabled(False); self._load_btn.clicked.connect(self._on_load_students)
        self._student_count = QLabel(""); self._student_count.setObjectName("intervCount")
        sl.addWidget(self._load_btn); sl.addWidget(self._student_count); sl.addStretch()
        root.addWidget(sel)

        btn_row = QHBoxLayout(); btn_row.setSpacing(12)
        self._analyze_btn = QPushButton("🤖  Analyze High-Risk Students")
        self._analyze_btn.setObjectName("intervAnalyzeBtn"); self._analyze_btn.setFixedHeight(38)
        self._analyze_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._analyze_btn.setEnabled(False)
        self._analyze_btn.clicked.connect(self._on_analyze_all)
        self._cancel_btn = QPushButton("✕  Cancel")
        self._cancel_btn.setObjectName("intervCancelBtn"); self._cancel_btn.setFixedHeight(38)
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.setVisible(False); self._cancel_btn.clicked.connect(self._on_cancel_batch)
        self._cohort_btn = QPushButton("📊  Cohort Summary")
        self._cohort_btn.setObjectName("intervCohortBtn"); self._cohort_btn.setFixedHeight(38)
        self._cohort_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cohort_btn.setEnabled(False); self._cohort_btn.clicked.connect(self._on_cohort_summary)
        self._export_btn = QPushButton("📄  Export Report")
        self._export_btn.setObjectName("intervExportBtn"); self._export_btn.setFixedHeight(38)
        self._export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_btn.setEnabled(False); self._export_btn.clicked.connect(self._on_export_report)
        self._logs_btn = QPushButton("📋  Intervention Logs")
        self._logs_btn.setObjectName("intervLogsBtn"); self._logs_btn.setFixedHeight(38)
        self._logs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._logs_btn.clicked.connect(self._on_show_logs)
        for w in [self._analyze_btn, self._cancel_btn, self._cohort_btn,
                  self._export_btn, self._logs_btn]:
            btn_row.addWidget(w)
        btn_row.addStretch()
        self._progress_lbl = QLabel(""); self._progress_lbl.setObjectName("intervCount")
        btn_row.addWidget(self._progress_lbl); root.addLayout(btn_row)

        self._batch_bar = QProgressBar(); self._batch_bar.setFixedHeight(4)
        self._batch_bar.setTextVisible(False); self._batch_bar.setRange(0,100)
        self._batch_bar.setValue(0); self._batch_bar.setVisible(False)
        self._batch_bar.setStyleSheet("""
            QProgressBar { background:rgba(255,255,255,0.08); border-radius:2px; border:none; }
            QProgressBar::chunk { background:#4f8cff; border-radius:2px; }""")
        root.addWidget(self._batch_bar)

        self._results_stack = QStackedWidget()
        empty_w = QWidget(); el = QVBoxLayout(empty_w)
        el.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ei = QLabel("🤖"); ei.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ei.setStyleSheet("font-size:52px;")
        em = QLabel("Load high-risk students for a term, then click\n"
                    "\"Analyze High-Risk Students\" to choose programs\n"
                    "and generate personalized AI intervention plans.")
        em.setAlignment(Qt.AlignmentFlag.AlignCenter)
        em.setStyleSheet(
            "color:rgba(255,255,255,0.28); font-size:13px; background:transparent;")
        el.addWidget(ei); el.addSpacing(12); el.addWidget(em)
        self._results_stack.addWidget(empty_w)   # 0

        for attr, lo_attr in [("_batch_host","_batch_lo"),("_co_host","_co_lo")]:
            scroll = QScrollArea(); scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setStyleSheet("QScrollArea { background:transparent; border:none; }")
            host = QWidget(); host.setStyleSheet("background:transparent;")
            lo = QVBoxLayout(host); lo.setContentsMargins(0,0,8,0); lo.setSpacing(0)
            lo.addStretch(); scroll.setWidget(host)
            setattr(self, attr, host); setattr(self, lo_attr, lo)
            self._results_stack.addWidget(scroll)  # 1, 2

        root.addWidget(self._results_stack, 1)

    def _load_terms(self):
        self._term_loader = TermLoader()
        self._term_loader.finished.connect(self._on_terms_loaded)
        self._term_loader.error.connect(lambda e: (
            self._progress_lbl.setText(f"⚠ {e}"),
            _safe_cleanup(self._term_loader),
        ))
        self._term_loader.finished.connect(self._term_loader.deleteLater)
        self._term_loader.start()

    def _on_terms_loaded(self, terms: list):
        self._ay_combo.clear()
        if not terms: self._ay_combo.addItem("No data"); return
        seen = []
        for ay, _ in terms:
            if ay not in seen: seen.append(ay)
        self._ay_combo.addItems(seen)
        ay, sem = terms[0]; self._ay_combo.setCurrentText(ay)
        self._sem_combo.setCurrentIndex(sem - 1)
        self._ay_combo.setEnabled(True); self._load_btn.setEnabled(True)

    def _on_load_students(self):
        ay = self._ay_combo.currentText().strip()
        sem = self._sem_combo.currentIndex() + 1
        if not ay or ay == "No data": return
        self._ay, self._sem = ay, sem
        self._load_btn.setEnabled(False); self._load_btn.setText("Loading…")
        self._progress_lbl.setText(""); self._student_count.setText("")
        self._student_loader = StudentLoader(ay, sem)
        self._student_loader.finished.connect(self._on_students_loaded)
        self._student_loader.error.connect(lambda e: (
            self._on_load_error(e),
            _safe_cleanup(self._student_loader),
        ))
        self._student_loader.finished.connect(self._student_loader.deleteLater)
        self._student_loader.start()

    def _on_students_loaded(self, students: list):
        self._load_btn.setEnabled(True); self._load_btn.setText("⟳  Load High-Risk Students")
        self._students = students; count = len(students)
        self._student_count.setText(
            f"{count:,} high-risk student{'s' if count != 1 else ''} loaded")
        self._analyze_btn.setEnabled(bool(students))
        self._cohort_btn.setEnabled(bool(students))
        self._export_btn.setEnabled(True)
        self._results_stack.setCurrentIndex(0); self._check_ollama()

    def _on_load_error(self, msg: str):
        self._load_btn.setEnabled(True); self._load_btn.setText("⟳  Load High-Risk Students")
        self._progress_lbl.setText(f"⚠ {msg}")

    def _check_ollama(self):
        self._status_pill.setText("⟳  Checking Ollama…")
        self._check_worker = OllamaWorker("You are a test.", "Reply: OK")
        self._check_worker.finished.connect(
            lambda _: self._status_pill.setText(f"🟢  {SystemConfig.ollama_model()} — Ready"))
        self._check_worker.error.connect(lambda e: (
            self._status_pill.setText(
                "🔴  Ollama offline — is Ollama running?"
                if "Connection" in e or "Max retri" in e or "refused" in e.lower()
                else f"🔴  Ollama error: {e[:80]}"),
            _safe_cleanup(self._check_worker),
        ))
        self._check_worker.finished.connect(self._check_worker.deleteLater)
        self._check_worker.start()

    # ── Analyze — shows program selector first ────────────────────────

    def _on_analyze_all(self):
        if not self._students:
            return

        # Show program selector dialog before starting analysis
        dlg = _ProgramSelectorDialog(self._students, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected_programs = dlg.selected_programs()
        if not selected_programs:
            return

        # Filter students to only the selected programs
        selected_set = set(selected_programs)
        filtered_students = [
            s for s in self._students
            if str(s.get("program") or "Unknown").strip() in selected_set
        ]

        if not filtered_students:
            self._progress_lbl.setText("⚠ No students matched the selected programs.")
            return

        count = len(filtered_students)
        prog_count = len(selected_programs)

        # Confirm if large batch
        if count > 20:
            msg = QMessageBox(self)
            msg.setWindowTitle("Start AI Analysis")
            msg.setText(
                f"Generate intervention plans for {count:,} students "
                f"across {prog_count} program{'s' if prog_count != 1 else ''}?"
            )
            msg.setInformativeText(
                f"This will make {count:,} sequential Ollama calls.\n"
                f"Estimated time: {count*10//60}–{count*15//60} minutes.\n\n"
                "You can cancel at any time."
            )
            msg.setIcon(QMessageBox.Icon.Question)
            msg.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
            msg.setDefaultButton(QMessageBox.StandardButton.Yes)
            msg.button(QMessageBox.StandardButton.Yes).setText("Start Analysis")
            msg.setStyleSheet("""
                QMessageBox { background:#13172a; }
                QMessageBox QLabel { color:#e8eaf0; font-size:13px; background:transparent; }
                QMessageBox QPushButton { background:rgba(255,255,255,0.06);
                    border:1px solid rgba(255,255,255,0.14); border-radius:8px;
                    color:rgba(255,255,255,0.80); font-size:12px; font-weight:600;
                    padding:8px 24px; min-width:80px; }
                QMessageBox QPushButton:hover { background:rgba(255,255,255,0.12); }
                QMessageBox QPushButton[text="Start Analysis"] {
                    background:#4f8cff; border:none; color:white; }
                QMessageBox QPushButton[text="Start Analysis"]:hover {
                    background:rgba(79,140,255,0.85); }
            """)
            if msg.exec() != QMessageBox.StandardButton.Yes:
                return

        self._start_batch(filtered_students, selected_programs)

    def _start_batch(self, students: list, programs: list):
        """Launch BatchWorker for the given filtered student list."""
        count = len(students)
        self._clear_batch(); self._results_stack.setCurrentIndex(1)

        sem_lbl = "1st Semester" if self._sem == 1 else "2nd Semester"
        prog_summary = (
            programs[0] if len(programs) == 1
            else f"{len(programs)} programs"
        )
        hdr = QLabel(
            f"Intervention Plans  ·  {sem_lbl} AY {self._ay}  ·  "
            f"{prog_summary}  ·  {count:,} student{'s' if count != 1 else ''}"
        )
        hdr.setStyleSheet(
            "color:#e8eaf0; font-size:13px; font-weight:bold; "
            "background:transparent; padding-bottom:8px;")
        self._batch_lo.insertWidget(self._batch_lo.count() - 1, hdr)

        self._batch_bar.setValue(0); self._batch_bar.setVisible(True)
        self._analyze_btn.setEnabled(False); self._cancel_btn.setVisible(True)
        self._cohort_btn.setEnabled(False)
        self._progress_lbl.setText(f"0 / {count:,}  —  Starting…")

        self._batch_worker = BatchWorker(students, self._ay, self._sem)
        self._batch_worker.progress.connect(self._on_batch_progress)
        self._batch_worker.one_done.connect(self._on_one_done)
        self._batch_worker.finished.connect(self._on_batch_finished)
        self._batch_worker.cancelled.connect(self._on_batch_cancelled)
        self._batch_worker.error.connect(self._on_batch_error)
        self._batch_worker.finished.connect(self._batch_worker.deleteLater)
        self._batch_worker.start()
        self._batch_new_count     = 0
        self._batch_skipped_count = 0

    def _on_batch_progress(self, done: int, total: int, name: str):
        self._batch_bar.setValue(int(done / max(total,1) * 100))
        if name:
            self._progress_lbl.setText(f"{done:,} / {total:,}  —  Checking {name}…")

    def _on_one_done(self, student: dict, recs: list, skipped: bool):
        self._batch_lo.insertWidget(
            self._batch_lo.count() - 1,
            _StudentResultCard(student, recs, skipped=skipped))
        if skipped:
            self._batch_skipped_count += 1
        else:
            self._batch_new_count += 1
            score_raw = student.get("predicted_risk_score") or 0
            self._auto_save({
                "student_id":      str(student.get("student_id","")),
                "academic_year":   self._ay, "semester": self._sem,
                "mode":            "per_student",
                "risk_score":      round(float(score_raw)*100, 2),
                "risk_label":      student.get("risk_label",""),
                "risk_factors":    student.get("primary_factor",""),
                "recommendations": recs,
            })

    def _on_batch_finished(self):
        self._batch_bar.setValue(100); self._batch_bar.setVisible(False)
        self._cancel_btn.setVisible(False); self._analyze_btn.setEnabled(True)
        self._cohort_btn.setEnabled(bool(self._students))
        new     = self._batch_new_count
        skipped = self._batch_skipped_count
        parts   = []
        if new:
            parts.append(f"{new:,} new plan{'s' if new != 1 else ''} generated")
        if skipped:
            parts.append(f"{skipped:,} already analyzed (skipped)")
        self._progress_lbl.setText("✓  " + ("  ·  ".join(parts) if parts else "Done"))
        self._batch_worker = None

    def _on_batch_cancelled(self):
        self._batch_bar.setVisible(False); self._cancel_btn.setVisible(False)
        self._analyze_btn.setEnabled(True); self._cohort_btn.setEnabled(bool(self._students))
        self._progress_lbl.setText("Analysis cancelled.")
        w = self._batch_worker; self._batch_worker = None
        if w:
            try: w.wait(3000)
            except RuntimeError: pass
            QTimer.singleShot(0, w.deleteLater)

    def _on_batch_error(self, msg: str):
        self._batch_bar.setVisible(False); self._cancel_btn.setVisible(False)
        self._analyze_btn.setEnabled(True); self._cohort_btn.setEnabled(bool(self._students))
        self._progress_lbl.setText(f"⚠ {msg[:80]}")
        w = self._batch_worker; self._batch_worker = None
        if w:
            try: w.wait(3000)
            except RuntimeError: pass
            QTimer.singleShot(0, w.deleteLater)

    def _on_cancel_batch(self):
        if self._batch_worker:
            self._batch_worker.cancel(); self._cancel_btn.setEnabled(False)
            self._progress_lbl.setText("Cancelling after current student…")

    def _clear_batch(self):
        while self._batch_lo.count() > 1:
            item = self._batch_lo.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    def _on_cohort_summary(self):
        if not self._students: return
        self._cohort_btn.setEnabled(False)
        self._progress_lbl.setText("Generating cohort summary…")
        prompt = build_cohort_prompt(self._students, self._ay, self._sem)
        self._cohort_worker = OllamaWorker(SYSTEM_COHORT, prompt)
        self._cohort_worker.finished.connect(
            lambda raw: self._on_cohort_done(raw, self._students))
        self._cohort_worker.error.connect(lambda e: (
            self._on_cohort_error(e),
            _safe_cleanup(self._cohort_worker),
        ))
        self._cohort_worker.finished.connect(self._cohort_worker.deleteLater)
        self._cohort_worker.start()

    def _on_cohort_done(self, raw: str, students: list):
        self._cohort_btn.setEnabled(bool(self._students))
        issues = parse_json_response(raw)
        if not issues:
            self._progress_lbl.setText("⚠ Could not parse cohort response."); return
        while self._co_lo.count() > 1:
            item = self._co_lo.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        sem_lbl = "1st Semester" if self._sem == 1 else "2nd Semester"
        term = f"{self._ay} — {sem_lbl}"
        hdr = QLabel(f"Cohort Systemic Issues  ·  {term}  ·  {len(students):,} high-risk students")
        hdr.setStyleSheet(
            "color:#e8eaf0; font-size:13px; font-weight:bold; "
            "background:transparent; padding-bottom:8px;")
        self._co_lo.insertWidget(0, hdr)
        for i, issue in enumerate(issues):
            self._co_lo.insertWidget(i+1, _cohort_row(issue, i))
            if i < len(issues) - 1:
                sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
                sep.setStyleSheet("color:rgba(255,255,255,0.06);")
                self._co_lo.insertWidget(i+2, sep)
        self._results_stack.setCurrentIndex(2)
        self._progress_lbl.setText(
            f"✓  {len(issues)} systemic issues identified — saved to log")
        self._auto_save({
            "student_id": None, "academic_year": self._ay, "semester": self._sem,
            "mode": "cohort", "risk_score": None,
            "risk_label": f"{len(students)} high-risk students",
            "risk_factors": term, "recommendations": issues,
        })

    def _on_cohort_error(self, msg: str):
        self._cohort_btn.setEnabled(bool(self._students))
        self._progress_lbl.setText(f"⚠ Cohort error: {msg[:80]}")

    def _auto_save(self, record: dict):
        worker = SaveWorker(record)
        worker.error.connect(lambda e: (
            print(f"[Interventions] Save error: {e}"),
            _safe_cleanup(worker),
        ))
        worker.finished.connect(
            lambda: self._save_workers.remove(worker) if worker in self._save_workers else None)
        worker.finished.connect(worker.deleteLater)
        self._save_workers.append(worker); worker.start()

    def _on_export_report(self):
        dlg = _TermSelectDialog(self)
        if dlg.exec() != _TermSelectDialog.DialogCode.Accepted: return
        ay, sem, term_label = dlg.selected_term()
        if not ay: return
        from PyQt6.QtWidgets import QFileDialog
        ay_safe = ay.replace("-","_").replace(" ","")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Intervention Report",
            f"InterventionReport_{ay_safe}_Sem{sem}.pdf", "PDF Files (*.pdf)")
        if not path: return
        self._export_btn.setEnabled(False); self._export_btn.setText("Loading records…")
        self._record_loader = InterventionRecordLoader(ay, sem)
        self._record_loader.finished.connect(
            lambda rows: self._on_records_loaded(rows, ay, sem, term_label, path))
        self._record_loader.error.connect(lambda e: (
            self._on_export_error(e),
            _safe_cleanup(self._record_loader),
        ))
        self._record_loader.finished.connect(self._record_loader.deleteLater)
        self._record_loader.start()

    def _on_records_loaded(self, rows, ay, sem, term_label, path):
        if not rows:
            self._export_btn.setEnabled(True); self._export_btn.setText("📄  Export Report")
            self._progress_lbl.setText(f"⚠ No records found for {term_label}."); return
        self._export_btn.setText("Generating PDF…")

        from ui.dialogs.report_customization import ReportCustomizationDialog
        colleges = list({r.get("college","") for r in rows if r.get("college")})
        dlg = ReportCustomizationDialog(parent=self, colleges=sorted(colleges))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            self._export_btn.setEnabled(True)
            self._export_btn.setText("📄  Export Report")
            return
        self._report_worker = InterventionReportWorker(
            rows, term_label, ay, sem, path,
            config=dlg.intervention_config()
        )
        
        self._report_worker.finished.connect(self._on_export_done)
        self._report_worker.error.connect(lambda e: (
            self._on_export_error(e),
            _safe_cleanup(self._report_worker),
        ))
        self._report_worker.finished.connect(self._report_worker.deleteLater)
        self._report_worker.start()

    def _on_export_done(self, path: str):
        self._export_btn.setEnabled(True); self._export_btn.setText("📄  Export Report")
        self._progress_lbl.setText("✓  Report saved.")
        import subprocess, sys, os
        try:
            if sys.platform == "win32": subprocess.Popen(["explorer","/select,",path])
            elif sys.platform == "darwin": subprocess.Popen(["open","-R",path])
            else: subprocess.Popen(["xdg-open", os.path.dirname(path)])
        except Exception: pass
        QMessageBox.information(self,"Report Exported",
            "Intervention report saved successfully.\n\n" + path)

    def _on_export_error(self, msg: str):
        self._export_btn.setEnabled(True); self._export_btn.setText("📄  Export Report")
        self._progress_lbl.setText(f"⚠ Export failed: {msg[:80]}")

    def _on_show_logs(self):
        InterventionLogDialog(self).exec()

    def closeEvent(self, event):
        if self._batch_worker is not None:
            try: self._batch_worker.cancelled.disconnect()
            except RuntimeError: pass
            self._batch_worker.cancel()
            try: self._batch_worker.wait(5000)
            except RuntimeError: pass
            try: self._batch_worker.deleteLater()
            except RuntimeError: pass
            self._batch_worker = None
        for attr in ("_term_loader","_student_loader","_check_worker",
                     "_cohort_worker","_record_loader","_report_worker"):
            w = getattr(self, attr, None)
            if w is None: continue
            try:
                w.finished.disconnect(); w.error.disconnect()
                if w.isRunning(): w.quit(); w.wait(2000)
            except (RuntimeError, Exception): pass
        for w in list(self._save_workers):
            try:
                if w.isRunning(): w.quit(); w.wait(1000)
            except Exception: pass
        super().closeEvent(event)

    def _apply_styles(self):
        self.setStyleSheet("""
            #intervSub { color:rgba(255,255,255,0.35); font-size:11px; background:transparent; }
            #intervStatusPill { color:rgba(255,255,255,0.70); font-size:11px;
                background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.10);
                border-radius:8px; padding:5px 16px; min-width:220px; }
            #intervSelCard { background:rgba(255,255,255,0.02); border:none;
                border-bottom:1px solid rgba(255,255,255,0.06); border-radius:0; }
            QComboBox#intervCombo { background:rgba(255,255,255,0.06);
                border:1px solid rgba(255,255,255,0.12); border-radius:7px;
                color:#e8eaf0; font-size:12px; padding:5px 10px; min-height:30px; }
            QComboBox#intervCombo:hover { border-color:rgba(52,211,153,0.35); }
            QComboBox#intervCombo::drop-down { border:none; width:16px; }
            QComboBox#intervCombo QAbstractItemView { background:#1a1f35; color:#e8eaf0;
                selection-background-color:rgba(52,211,153,0.18); }
            QPushButton#intervLoadBtn { background:#34d399; border:none; border-radius:7px;
                color:#0e1120; font-size:12px; font-weight:700; padding:0 16px; }
            QPushButton#intervLoadBtn:hover { background:rgba(52,211,153,0.85); }
            QPushButton#intervLoadBtn:disabled { background:rgba(255,255,255,0.06);
                color:rgba(255,255,255,0.25); }
            QPushButton#intervAnalyzeBtn { background:#4f8cff; border:none; border-radius:8px;
                color:white; font-size:12px; font-weight:700; padding:0 20px; }
            QPushButton#intervAnalyzeBtn:hover { background:rgba(79,140,255,0.85); }
            QPushButton#intervAnalyzeBtn:disabled { background:rgba(255,255,255,0.06);
                color:rgba(255,255,255,0.25); }
            QPushButton#intervCancelBtn { background:rgba(255,91,91,0.12);
                border:1px solid rgba(255,91,91,0.30); border-radius:8px; color:#ff5b5b;
                font-size:12px; font-weight:600; padding:0 16px; }
            QPushButton#intervCancelBtn:hover { background:rgba(255,91,91,0.22); }
            QPushButton#intervCohortBtn { background:rgba(167,139,250,0.12);
                border:1px solid rgba(167,139,250,0.30); border-radius:8px; color:#a78bfa;
                font-size:12px; font-weight:600; padding:0 18px; }
            QPushButton#intervCohortBtn:hover { background:rgba(167,139,250,0.22); }
            QPushButton#intervCohortBtn:disabled { background:rgba(255,255,255,0.03);
                border-color:rgba(255,255,255,0.08); color:rgba(255,255,255,0.20); }
            QPushButton#intervExportBtn { background:rgba(52,211,153,0.10);
                border:1px solid rgba(52,211,153,0.30); border-radius:8px; color:#34d399;
                font-size:12px; font-weight:600; padding:0 18px; }
            QPushButton#intervExportBtn:hover { background:rgba(52,211,153,0.20); }
            QPushButton#intervExportBtn:disabled { background:rgba(255,255,255,0.03);
                border-color:rgba(255,255,255,0.08); color:rgba(255,255,255,0.20); }
            QPushButton#intervLogsBtn { background:rgba(79,140,255,0.10);
                border:1px solid rgba(79,140,255,0.28); border-radius:8px; color:#4f8cff;
                font-size:12px; font-weight:600; padding:0 18px; }
            QPushButton#intervLogsBtn:hover { background:rgba(79,140,255,0.20); }
            #intervCount { color:rgba(255,255,255,0.35); font-size:11px; background:transparent; }
        """)