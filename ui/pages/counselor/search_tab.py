"""
Counselor Portal — Tab 1: Student Search
==========================================
Search ALL students (not just at-risk) across any saved term.
Results show in a scrollable table; clicking a row opens the
existing StudentProfileDrawer.

Layout
------
  ┌─ Term selector ──────────────────────────────────────────────┐
  │  AY [combo]  Sem [combo]  [Load]  ── term status            │
  └─────────────────────────────────────────────────────────────┘
  ┌─ Search + filters ───────────────────────────────────────────┐
  │  🔍 name / ID  │ Risk Level ▾ │ Program ▾ │ College ▾        │
  └─────────────────────────────────────────────────────────────┘
  ┌─ Results table ──────────────────────────────────────────────┐
  │  ID | Name | Program | College | Risk Level | Score | Factor │
  │  ── clickable rows → profile drawer ──────────────────────── │
  └─────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QLineEdit, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QStackedWidget,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QFont

from services.data_store import DataStore
from ui.dialogs.confirmation_dialog import show_error


# ── Background loader ─────────────────────────────────────────────────────────

class _SearchLoader(QThread):
    """
    Loads ALL students (every risk level) for a term.
    Unlike CaseloadTab which filters to at-risk only.
    """
    finished = pyqtSignal(list, str)
    error    = pyqtSignal(str)

    _SQL = """
        SELECT
            ds.student_id,
            TRIM(COALESCE(ds.first_name,'') || ' ' ||
                 COALESCE(ds.last_name, ''))              AS full_name,
            COALESCE(dp.program_name, 'Unknown')          AS program,
            COALESCE(dp.college,      '—')                AS college,
            COALESCE(rl.risk_label,   'Low Risk')         AS risk_label,
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
            tl = rows[0]["term_label"] if rows else f"{self._ay} Sem {self._sem}"
            self.finished.emit(rows, tl)
        except Exception as e:
            self.error.emit(str(e))


class _TermLoader(QThread):
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
                    JOIN   public.dim_academic_term t
                           ON t.term_key = fsr.term_key
                    ORDER  BY t.academic_year DESC, t.semester DESC
                """)
                self.finished.emit(cur.fetchall())
        except Exception as e:
            self.error.emit(str(e))


# ── Risk helpers ──────────────────────────────────────────────────────────────

def _cat(label: str) -> str:
    lc = label.lower()
    if "high"     in lc: return "high_risk"
    if "moderate" in lc or "medium" in lc: return "moderate_risk"
    return "low_risk"

def _risk_color(cat: str) -> str:
    return {
        "high_risk":     "#ff5b5b",
        "moderate_risk": "#f5b335",
        "low_risk":      "#34d399",
    }.get(cat, "#8b949e")


# ── Search Tab ────────────────────────────────────────────────────────────────

class SearchTab(QWidget):

    def __init__(self):
        super().__init__()
        self._rows:        list[dict] = []
        self._loader:      _SearchLoader | None = None
        self._term_loader: _TermLoader   | None = None
        self._profile_drawer = None
        self._search_timer   = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_filter)

        self._setup_ui()
        self._apply_styles()
        self._load_terms()

    # ── UI ────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 24, 30, 24)
        root.setSpacing(16)

        # ── Header ────────────────────────────────────────────────────
        header_row = QHBoxLayout()
        page_title = QLabel("Student Search")
        page_title.setObjectName("searchPageTitle")
        page_sub = QLabel(
            "Search all scored students across any saved term. "
            "Click a row to view the full student profile."
        )
        page_sub.setObjectName("searchPageSub")
        hdr_col = QVBoxLayout()
        hdr_col.setSpacing(3)
        hdr_col.addWidget(page_title)
        hdr_col.addWidget(page_sub)
        header_row.addLayout(hdr_col, 1)
        root.addLayout(header_row)

        # ── Term selector ─────────────────────────────────────────────
        term_card = QFrame()
        term_card.setObjectName("searchTermCard")
        term_lo = QHBoxLayout(term_card)
        term_lo.setContentsMargins(20, 14, 20, 14)
        term_lo.setSpacing(12)

        ay_lbl = QLabel("Academic Year")
        ay_lbl.setObjectName("searchFieldLbl")
        self._ay_combo = QComboBox()
        self._ay_combo.setObjectName("searchCombo")
        self._ay_combo.setMinimumWidth(130)
        self._ay_combo.setCursor(Qt.CursorShape.PointingHandCursor)

        sem_lbl = QLabel("Semester")
        sem_lbl.setObjectName("searchFieldLbl")
        self._sem_combo = QComboBox()
        self._sem_combo.setObjectName("searchCombo")
        self._sem_combo.addItems(["1st Semester", "2nd Semester"])
        self._sem_combo.setCursor(Qt.CursorShape.PointingHandCursor)

        self._load_btn = QPushButton("⟳  Load")
        self._load_btn.setObjectName("searchLoadBtn")
        self._load_btn.setFixedHeight(34)
        self._load_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._load_btn.clicked.connect(self._on_load)

        self._term_status = QLabel("Select a term to search students.")
        self._term_status.setObjectName("searchTermStatus")

        for w in [ay_lbl, self._ay_combo, sem_lbl,
                  self._sem_combo, self._load_btn]:
            term_lo.addWidget(w)
        term_lo.addStretch()
        term_lo.addWidget(self._term_status)
        root.addWidget(term_card)

        # ── Search + filter bar ───────────────────────────────────────
        filter_card = QFrame()
        filter_card.setObjectName("searchFilterCard")
        filter_lo = QHBoxLayout(filter_card)
        filter_lo.setContentsMargins(16, 12, 16, 12)
        filter_lo.setSpacing(10)

        self._search = QLineEdit()
        self._search.setObjectName("searchInput")
        self._search.setPlaceholderText("🔍  Search by name or student ID…")
        self._search.textChanged.connect(
            lambda _: self._search_timer.start(200)
        )

        self._risk_filter = QComboBox()
        self._risk_filter.setObjectName("searchCombo")
        self._risk_filter.addItem("All Risk Levels")
        self._risk_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self._risk_filter.currentTextChanged.connect(self._apply_filter)

        self._prog_filter = QComboBox()
        self._prog_filter.setObjectName("searchCombo")
        self._prog_filter.addItem("All Programs")
        self._prog_filter.setMinimumWidth(150)
        self._prog_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self._prog_filter.currentTextChanged.connect(self._apply_filter)

        self._college_filter = QComboBox()
        self._college_filter.setObjectName("searchCombo")
        self._college_filter.addItem("All Colleges")
        self._college_filter.setMinimumWidth(140)
        self._college_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self._college_filter.currentTextChanged.connect(self._apply_filter)

        clear_btn = QPushButton("✕  Clear")
        clear_btn.setObjectName("searchClearBtn")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setFixedHeight(32)
        clear_btn.clicked.connect(self._clear_filters)

        self._result_count = QLabel("")
        self._result_count.setObjectName("searchResultCount")

        filter_lo.addWidget(self._search, 2)
        filter_lo.addWidget(self._risk_filter)
        filter_lo.addWidget(self._prog_filter)
        filter_lo.addWidget(self._college_filter)
        filter_lo.addWidget(clear_btn)
        filter_lo.addWidget(self._result_count)
        root.addWidget(filter_card)

        # ── Content stack ─────────────────────────────────────────────
        self._stack = QStackedWidget()

        # Empty state
        empty = QWidget()
        elo   = QVBoxLayout(empty)
        elo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ei = QLabel("🔍")
        ei.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ei.setStyleSheet("font-size:48px;")
        em = QLabel("Load a term to start searching.")
        em.setAlignment(Qt.AlignmentFlag.AlignCenter)
        em.setObjectName("searchEmptyMsg")
        elo.addWidget(ei)
        elo.addSpacing(10)
        elo.addWidget(em)
        self._stack.addWidget(empty)           # index 0

        # Table
        table_frame = QFrame()
        table_frame.setObjectName("searchTableFrame")
        tlo = QVBoxLayout(table_frame)
        tlo.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget()
        self._table.setObjectName("searchTable")
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels([
            "Student ID", "Name", "Program",
            "College", "Risk Level", "Score", "Predicted At",
        ])
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setHighlightSections(False)
        self._table.setShowGrid(False)
        self._table.setMouseTracking(True)
        self._table.setCursor(Qt.CursorShape.PointingHandCursor)

        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)

        # Click row → open profile
        self._table.cellClicked.connect(self._on_row_clicked)

        tlo.addWidget(self._table)
        self._stack.addWidget(table_frame)     # index 1

        root.addWidget(self._stack, 1)

        # ── Click hint ────────────────────────────────────────────────
        hint = QLabel("💡  Click any row to view the student's full risk profile")
        hint.setObjectName("searchClickHint")
        root.addWidget(hint)

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
                "No prediction data found. Contact the administrator."
            )
            return

        seen = []
        for ay, _ in terms:
            if ay not in seen:
                seen.append(ay)
        self._ay_combo.addItems(seen)
        ay, sem = terms[0]
        self._ay_combo.setCurrentText(ay)
        self._sem_combo.setCurrentIndex(sem - 1)
        self._load_btn.setEnabled(True)
        self._term_status.setText(
            f"{len(terms)} term(s) available."
        )

    # ── Data loading ──────────────────────────────────────────────────

    def _on_load(self):
        ay  = self._ay_combo.currentText().strip()
        sem = self._sem_combo.currentIndex() + 1
        if not ay or ay == "No data":
            return

        self._load_btn.setEnabled(False)
        self._load_btn.setText("Loading…")
        self._term_status.setText(f"Loading {ay} Sem {sem}…")

        self._loader = _SearchLoader(ay, sem)
        self._loader.finished.connect(self._on_rows_loaded)
        self._loader.error.connect(self._on_load_error)
        self._loader.finished.connect(self._loader.deleteLater)
        self._loader.error.connect(self._loader.deleteLater)
        self._loader.start()

    def _on_rows_loaded(self, rows: list, term_label: str):
        self._load_btn.setEnabled(True)
        self._load_btn.setText("⟳  Load")
        self._rows = rows

        if not rows:
            self._term_status.setText(f"No students found for {term_label}.")
            self._stack.setCurrentIndex(0)
            return

        self._term_status.setText(
            f"📅  {term_label}  ·  {len(rows):,} students"
        )
        self._populate_filter_dropdowns()
        self._apply_filter()
        self._stack.setCurrentIndex(1)

    def _on_load_error(self, msg: str):
        self._load_btn.setEnabled(True)
        self._load_btn.setText("⟳  Load")
        self._term_status.setText(f"⚠ Load failed: {msg}")
        show_error(self, "Load Error",
                   "Could not load students for the selected term.", msg)

    # ── Filters ───────────────────────────────────────────────────────

    def _populate_filter_dropdowns(self):
        risk_levels = sorted({
            str(r.get("risk_label","")).strip()
            for r in self._rows if r.get("risk_label")
        })
        programs = sorted({
            str(r.get("program","")).strip()
            for r in self._rows
            if r.get("program") and r["program"] != "Unknown"
        })
        colleges = sorted({
            str(r.get("college","")).strip()
            for r in self._rows
            if r.get("college") and r["college"] not in ("—","")
        })
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

    def _clear_filters(self):
        for w in (self._search, ):
            w.blockSignals(True)
            w.clear()
            w.blockSignals(False)
        for combo in (self._risk_filter, self._prog_filter,
                      self._college_filter):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self._apply_filter()

    def _apply_filter(self):
        text   = self._search.text().lower().strip()
        risk_s = self._risk_filter.currentText()
        prog_s = self._prog_filter.currentText()
        coll_s = self._college_filter.currentText()

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
                hay = (f"{r.get('full_name','')} "
                       f"{r.get('student_id','')}").lower()
                if text not in hay:
                    continue
            filtered.append(r)

        self._populate_table(filtered)
        total = len(self._rows)
        shown = len(filtered)
        self._result_count.setText(
            f"{shown:,} of {total:,}" if shown != total
            else f"{total:,} students"
        )

    # ── Table ─────────────────────────────────────────────────────────

    def _populate_table(self, rows: list):
        self._table.setRowCount(0)
        self._table.setRowCount(len(rows))

        for ri, row in enumerate(rows):
            cat   = _cat(row.get("risk_label",""))
            color = _risk_color(cat)
            score_raw = row.get("predicted_risk_score")
            score = round(float(score_raw)*100, 1) if score_raw else 0.0
            ts    = row.get("predicted_at")
            ts_s  = (ts.strftime("%b %d, %Y")
                     if hasattr(ts, "strftime") else str(ts)[:10]
                     if ts else "—")

            cells = [
                (str(row.get("student_id","—")),  "rgba(255,255,255,0.45)", False),
                (str(row.get("full_name","—")),    "#e8eaf0",               True),
                (str(row.get("program","—")),      "rgba(255,255,255,0.70)", False),
                (str(row.get("college","—")),      "rgba(255,255,255,0.55)", False),
                (str(row.get("risk_label","—")),   color,                   True),
                (f"{score:.1f}%",                  color,                   False),
                (ts_s,                             "rgba(255,255,255,0.35)", False),
            ]

            for ci, (text, fg, bold) in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setForeground(QColor(fg))
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                if bold:
                    f = QFont()
                    f.setBold(True)
                    item.setFont(f)
                # Store full row dict in first column's UserRole
                if ci == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row)
                self._table.setItem(ri, ci, item)

            self._table.setRowHeight(ri, 40)

    def _on_row_clicked(self, row: int, _col: int):
        item = self._table.item(row, 0)
        if not item:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if data:
            self._open_profile(self._to_profile_dict(data))

    def _to_profile_dict(self, r: dict) -> dict:
        cat   = _cat(r.get("risk_label",""))
        score_raw = r.get("predicted_risk_score")
        score = round(float(score_raw)*100, 1) if score_raw else 0.0
        return {
            "name":              r.get("full_name","—"),
            "id":                str(r.get("student_id","—")),
            "program":           r.get("program","—"),
            "college":           r.get("college","—"),
            "score":             score,
            "category":          cat,
            "label":             r.get("risk_label","—"),
            "factor":            "—",
            "shap_factors":      [],
            "entrance_exam_score": r.get("entrance_exam_score",""),
            "hs_gpa":              r.get("high_school_gpa",""),
        }

    # ── Profile drawer ────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        self._ensure_drawer()

    def _ensure_drawer(self):
        if self._profile_drawer is not None:
            return
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
            /* ── Page title ─────────────────────────────────────── */
            #searchPageTitle {
                color: #e8eaf0;
                font-size: 17px; font-weight: bold;
                background: transparent;
            }
            #searchPageSub {
                color: rgba(255,255,255,0.35);
                font-size: 12px; background: transparent;
            }

            /* ── Term card ──────────────────────────────────────── */
            #searchTermCard {
                background: #13172a;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 12px;
            }
            #searchFieldLbl {
                color: rgba(255,255,255,0.40);
                font-size: 11px; background: transparent;
            }
            #searchTermStatus {
                color: rgba(255,255,255,0.35);
                font-size: 11px; background: transparent;
            }

            /* ── Filter card ────────────────────────────────────── */
            #searchFilterCard {
                background: #13172a;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 12px;
            }

            /* ── Load button ────────────────────────────────────── */
            QPushButton#searchLoadBtn {
                background: #34d399; border: none;
                border-radius: 7px; color: #0e1120;
                font-size: 12px; font-weight: 700;
                padding: 0 18px;
            }
            QPushButton#searchLoadBtn:hover {
                background: rgba(52,211,153,0.85);
            }
            QPushButton#searchLoadBtn:disabled {
                background: rgba(255,255,255,0.06);
                color: rgba(255,255,255,0.25);
            }

            /* ── Clear button ───────────────────────────────────── */
            QPushButton#searchClearBtn {
                background: transparent;
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 7px;
                color: rgba(255,255,255,0.45);
                font-size: 11px; padding: 0 12px;
            }
            QPushButton#searchClearBtn:hover {
                background: rgba(255,255,255,0.06);
                color: rgba(255,255,255,0.80);
            }

            /* ── Combos ─────────────────────────────────────────── */
            QComboBox#searchCombo {
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 8px; color: #e8eaf0;
                font-size: 12px; padding: 5px 12px;
                min-height: 30px;
            }
            QComboBox#searchCombo:hover {
                border-color: rgba(52,211,153,0.35);
            }
            QComboBox#searchCombo::drop-down {
                border: none; width: 18px;
            }
            QComboBox#searchCombo QAbstractItemView {
                background: #1a1f35;
                border: 1px solid rgba(255,255,255,0.12);
                color: #e8eaf0;
                selection-background-color: rgba(52,211,153,0.18);
            }

            /* ── Search input ───────────────────────────────────── */
            QLineEdit#searchInput {
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 8px; color: #e8eaf0;
                font-size: 13px; padding: 8px 14px;
            }
            QLineEdit#searchInput:focus {
                border-color: rgba(52,211,153,0.40);
            }

            /* ── Result count ───────────────────────────────────── */
            #searchResultCount {
                color: rgba(255,255,255,0.30);
                font-size: 11px; background: transparent;
            }

            /* ── Empty state ────────────────────────────────────── */
            #searchEmptyMsg {
                color: rgba(255,255,255,0.30);
                font-size: 13px; background: transparent;
            }

            /* ── Table frame ────────────────────────────────────── */
            #searchTableFrame {
                background: #13172a;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 14px;
            }

            /* ── Table ──────────────────────────────────────────── */
            QTableWidget#searchTable {
                background: transparent; border: none;
                color: rgba(255,255,255,0.82); font-size: 12px;
                alternate-background-color: rgba(255,255,255,0.025);
                selection-background-color: rgba(52,211,153,0.12);
                selection-color: white;
                gridline-color: transparent;
            }
            QTableWidget#searchTable QHeaderView::section {
                background: rgba(255,255,255,0.04);
                color: rgba(255,255,255,0.35);
                font-size: 10px; font-weight: bold;
                border: none;
                border-right: 1px solid rgba(255,255,255,0.05);
                padding: 10px 10px;
            }
            QTableWidget#searchTable::item {
                padding: 0 10px;
                border-bottom: 1px solid rgba(255,255,255,0.04);
            }
            QTableWidget#searchTable::item:hover {
                background: rgba(52,211,153,0.07);
            }
            QTableWidget#searchTable::item:selected {
                background: rgba(52,211,153,0.14);
            }

            /* ── Click hint ─────────────────────────────────────── */
            #searchClickHint {
                color: rgba(255,255,255,0.22);
                font-size: 11px; background: transparent;
                padding: 2px 0;
            }
        """)