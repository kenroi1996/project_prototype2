"""
Prediction History Page
=======================
Loads past prediction runs from fact_student_academic_risk and displays
them in a browsable, filterable table. Users select an academic year and
semester from dropdowns; clicking Load reconstructs the result from DB rows
and updates all summary cards and the student table.

Changes
-------
- Added "🗑 Delete Term" button beside search bar
- Search bar constrained to fixed width (no longer stretches full row)
- _DeleteTermWorker: deletes all fact rows for the selected term from DB
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QLineEdit,
    QStackedWidget, QGridLayout, QSizePolicy, QMessageBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont

from services.data_store import DataStore


# ─────────────────────────────────────────────────────────────────────────────
# RISK helpers
# ─────────────────────────────────────────────────────────────────────────────

def _category_from_label(label: str) -> str:
    lc = label.lower()
    if "high" in lc:
        return "high_risk"
    if "moderate" in lc or "medium" in lc:
        return "moderate_risk"
    return "low_risk"


def _badge_style(category: str) -> str:
    return {
        "high_risk":     "color:#ff5b5b; background:rgba(255,91,91,0.12); "
                         "border:1px solid rgba(255,91,91,0.30); border-radius:8px; "
                         "font-size:11px; font-weight:600; padding:3px 10px;",
        "moderate_risk": "color:#f5b335; background:rgba(245,179,53,0.12); "
                         "border:1px solid rgba(245,179,53,0.30); border-radius:8px; "
                         "font-size:11px; font-weight:600; padding:3px 10px;",
        "low_risk":      "color:#34d399; background:rgba(52,211,153,0.12); "
                         "border:1px solid rgba(52,211,153,0.30); border-radius:8px; "
                         "font-size:11px; font-weight:600; padding:3px 10px;",
    }.get(category, "color:rgba(255,255,255,0.5);")


# ─────────────────────────────────────────────────────────────────────────────
# BACKGROUND WORKERS
# ─────────────────────────────────────────────────────────────────────────────

class _HistoryLoader(QThread):
    finished = pyqtSignal(list, str)
    error    = pyqtSignal(str)

    _SQL = """
        SELECT
            ds.student_id,
            TRIM(COALESCE(ds.first_name, '') || ' ' ||
                 COALESCE(ds.last_name,  ''))             AS full_name,
            ds.first_name,
            ds.last_name,
            COALESCE(dp.program_name, 'Unknown')          AS program,
            COALESCE(dp.college,      '—')                AS college,
            COALESCE(rl.risk_label,   'Low Risk')         AS risk_label,
            fsr.predicted_risk_score,
            fsr.prediction_confidence,
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
        self._academic_year = academic_year
        self._semester      = semester

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No database connection. Please log in first.")
            return
        try:
            with conn.cursor() as cur:
                cur.execute(self._SQL, (self._academic_year, self._semester))
                rows = cur.fetchall()
                cols = [desc[0] for desc in cur.description]
            term_label = (
                rows[0][cols.index("term_label")]
                if rows else f"{self._academic_year} Sem {self._semester}"
            )
            self.finished.emit([dict(zip(cols, r)) for r in rows], term_label)
        except Exception as exc:
            self.error.emit(str(exc))


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
                    JOIN   public.dim_academic_term t ON t.term_key = fsr.term_key
                    ORDER  BY t.academic_year DESC, t.semester DESC
                """)
                self.finished.emit(cur.fetchall())
        except Exception as exc:
            self.error.emit(str(exc))


class _DeleteTermWorker(QThread):
    """
    Deletes all fact_student_academic_risk rows for a given term.
    Does NOT delete the dim_academic_term row itself — just the fact data.
    """
    finished = pyqtSignal(int)   # number of rows deleted
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
                    DELETE FROM public.fact_student_academic_risk
                    WHERE term_key IN (
                        SELECT term_key
                        FROM   public.dim_academic_term
                        WHERE  academic_year = %s
                          AND  semester      = %s
                    )
                """, (self._ay, self._sem))
                deleted = cur.rowcount
            conn.commit()
            self.finished.emit(deleted)
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            self.error.emit(str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY STAT TILE
# ─────────────────────────────────────────────────────────────────────────────

def _stat_tile(value: str, label: str,
               accent: str = "#e8eaf0") -> tuple[QFrame, QLabel]:
    tile = QFrame()
    tile.setObjectName("histStatTile")
    layout = QVBoxLayout(tile)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(4)
    val_lbl = QLabel(value)
    val_lbl.setStyleSheet(
        f"color:{accent}; font-size:22px; font-weight:bold; background:transparent;"
    )
    lbl = QLabel(label)
    lbl.setStyleSheet(
        "color:rgba(255,255,255,0.4); font-size:11px; background:transparent;"
    )
    layout.addWidget(val_lbl)
    layout.addWidget(lbl)
    return tile, val_lbl


# ─────────────────────────────────────────────────────────────────────────────
# REPORT WORKER
# ─────────────────────────────────────────────────────────────────────────────

class _ReportWorker(QThread):
    finished = pyqtSignal(str)
    error    = pyqtSignal(str)

    def __init__(self, rows, term_label, academic_year, semester, save_path, config=None):
        super().__init__()
        self._rows          = rows
        self._term_label    = term_label
        self._academic_year = academic_year
        self._semester      = semester
        self._save_path     = save_path
        self._config        = config          # ← add this

    def run(self):
        try:
            from services.report_generator import CohortReportGenerator
            gen = CohortReportGenerator(
                rows          = self._rows,
                term_label    = self._term_label,
                academic_year = self._academic_year,
                semester      = self._semester,
                config        = self._config,  # ← add this
            )
            buf = gen.build_bytes()
            with open(self._save_path, "wb") as f:
                f.write(buf.getvalue())
            self.finished.emit(self._save_path)
        except Exception as exc:
            self.error.emit(str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# PAGE
# ─────────────────────────────────────────────────────────────────────────────

class PredictionHistoryPage(QWidget):

    def __init__(self):
        super().__init__()
        self._rows:          list[dict]              = []
        self._loader:        _HistoryLoader  | None  = None
        self._silent_loader: _HistoryLoader  | None  = None
        self._term_worker:   _TermLoader     | None  = None
        self._report_worker: _ReportWorker   | None  = None
        self._delete_worker: _DeleteTermWorker | None = None
        self._setup_ui()
        self._apply_styles()
        DataStore.get().add_listener(self._on_store_updated)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(300, self._load_available_terms)

    def _on_store_updated(self, key: str):
        if key not in ("predictions", "last_prediction_run", "all"):
            return
        self._load_available_terms(auto_reload=True)

    # ─────────────────────────────────────────────────────────────────
    # UI construction
    # ─────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 30, 30, 30)
        root.setSpacing(24)

        # ── Page header ───────────────────────────────────────────────
        header_card = QFrame()
        header_card.setObjectName("histHeaderCard")
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(24, 20, 24, 20)
        header_layout.setSpacing(4)

        title_row = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(4)

        title = QLabel("Prediction History")
        title.setObjectName("histTitle")
        sub = QLabel("Browse and restore past prediction runs from the database")
        sub.setObjectName("histSubtitle")
        title_col.addWidget(title)
        title_col.addWidget(sub)
        title_row.addLayout(title_col, 1)

        # Term selector
        selector_frame = QFrame()
        selector_frame.setObjectName("histSelectorFrame")
        sel_layout = QHBoxLayout(selector_frame)
        sel_layout.setContentsMargins(16, 12, 16, 12)
        sel_layout.setSpacing(12)

        ay_lbl = QLabel("Academic Year")
        ay_lbl.setObjectName("histSelectorLabel")

        self._ay_combo = QComboBox()
        self._ay_combo.setObjectName("histCombo")
        self._ay_combo.setMinimumWidth(140)
        self._ay_combo.setCursor(Qt.CursorShape.PointingHandCursor)

        sem_lbl = QLabel("Semester")
        sem_lbl.setObjectName("histSelectorLabel")

        self._sem_combo = QComboBox()
        self._sem_combo.setObjectName("histCombo")
        self._sem_combo.setMinimumWidth(100)
        self._sem_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sem_combo.addItems(["1st Semester", "2nd Semester"])

        self._load_btn = QPushButton("⟳  Load")
        self._load_btn.setObjectName("histLoadBtn")
        self._load_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._load_btn.setFixedHeight(36)
        self._load_btn.clicked.connect(self._on_load)

        self._export_btn = QPushButton("⬇  Export PDF")
        self._export_btn.setObjectName("histExportBtn")
        self._export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_btn.setFixedHeight(36)
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._on_export_report)

        sel_layout.addWidget(ay_lbl)
        sel_layout.addWidget(self._ay_combo)
        sel_layout.addWidget(sem_lbl)
        sel_layout.addWidget(self._sem_combo)
        sel_layout.addWidget(self._load_btn)
        sel_layout.addWidget(self._export_btn)

        title_row.addWidget(selector_frame)
        header_layout.addLayout(title_row)
        root.addWidget(header_card)

        # ── Term label ────────────────────────────────────────────────
        self._term_lbl = QLabel(
            "Select an academic year and semester, then click Load.")
        self._term_lbl.setObjectName("histTermLabel")
        root.addWidget(self._term_lbl)

        # ── Stat tiles ────────────────────────────────────────────────
        tiles_row = QHBoxLayout()
        tiles_row.setSpacing(12)
        tile1, self._stat_total    = _stat_tile("—", "Total Students")
        tile2, self._stat_high     = _stat_tile("—", "High Risk",     "#ff5b5b")
        tile3, self._stat_moderate = _stat_tile("—", "Moderate Risk", "#f5b335")
        tile4, self._stat_low      = _stat_tile("—", "Low Risk",      "#34d399")
        tile5, self._stat_avg      = _stat_tile("—", "Avg Risk Score","#4f8cff")
        for tile, _ in [(tile1, None),(tile2, None),(tile3, None),
                        (tile4, None),(tile5, None)]:
            tiles_row.addWidget(tile, 1)
        root.addLayout(tiles_row)

        # ── Search + Delete row ───────────────────────────────────────
        search_row = QHBoxLayout()
        search_row.setSpacing(10)

        self._search = QLineEdit()
        self._search.setObjectName("histSearch")
        self._search.setPlaceholderText("🔍  Search by name or student ID…")
        self._search.setFixedWidth(340)           # constrained — not full-width
        self._search.textChanged.connect(self._apply_filter)

        self._result_count_lbl = QLabel("")
        self._result_count_lbl.setObjectName("histResultCount")

        # ── Delete Term button ────────────────────────────────────────
        self._delete_btn = QPushButton("🗑  Delete Term Data")
        self._delete_btn.setObjectName("histDeleteBtn")
        self._delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete_btn.setFixedHeight(36)
        self._delete_btn.setEnabled(False)        # enabled only after rows load
        self._delete_btn.setToolTip(
            "Permanently delete all prediction records for the selected term "
            "from the database.")
        self._delete_btn.clicked.connect(self._on_delete_term)

        # Hide delete button for counselors — admin only action
        from services.auth_service import AuthService
        _role = (AuthService.current_role() or "").strip().lower()
        if _role == "counselor":
            self._delete_btn.hide()

        search_row.addWidget(self._search)
        search_row.addWidget(self._result_count_lbl)
        search_row.addStretch()
        search_row.addWidget(self._delete_btn)
        root.addLayout(search_row)

        # ── Filter row ────────────────────────────────────────────────
        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)

        filter_lbl = QLabel("Filter by:")
        filter_lbl.setObjectName("histSelectorLabel")
        filter_row.addWidget(filter_lbl)

        self._risk_filter = QComboBox()
        self._risk_filter.setObjectName("histCombo")
        self._risk_filter.addItem("All Risk Levels")
        self._risk_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self._risk_filter.currentTextChanged.connect(self._apply_filter)
        filter_row.addWidget(self._risk_filter)

        self._program_filter = QComboBox()
        self._program_filter.setObjectName("histCombo")
        self._program_filter.addItem("All Programs")
        self._program_filter.setMinimumWidth(160)
        self._program_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self._program_filter.currentTextChanged.connect(self._apply_filter)
        filter_row.addWidget(self._program_filter)

        self._college_filter = QComboBox()
        self._college_filter.setObjectName("histCombo")
        self._college_filter.addItem("All Colleges")
        self._college_filter.setMinimumWidth(160)
        self._college_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self._college_filter.currentTextChanged.connect(self._apply_filter)
        filter_row.addWidget(self._college_filter)

        clear_btn = QPushButton("✕  Clear Filters")
        clear_btn.setObjectName("histClearBtn")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setFixedHeight(34)
        clear_btn.clicked.connect(self._clear_filters)
        filter_row.addWidget(clear_btn)

        filter_row.addStretch()
        root.addLayout(filter_row)

        # ── Content stack ─────────────────────────────────────────────
        self._stack = QStackedWidget()

        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_icon = QLabel("📂")
        empty_icon.setStyleSheet("font-size:48px;")
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_msg = QLabel(
            "No prediction history loaded.\n"
            "Select a term above and click Load.")
        empty_msg.setObjectName("histEmptyMsg")
        empty_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_icon)
        empty_layout.addSpacing(12)
        empty_layout.addWidget(empty_msg)
        self._stack.addWidget(empty)      # index 0

        table_frame = QFrame()
        table_frame.setObjectName("histTableFrame")
        table_layout = QVBoxLayout(table_frame)
        table_layout.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget()
        self._table.setObjectName("histTable")
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels([
            "Student ID", "Name", "Program", "College",
            "Risk Level", "Score", "Predicted At",
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setHighlightSections(False)
        self._table.setShowGrid(False)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(
            6, QHeaderView.ResizeMode.ResizeToContents)

        table_layout.addWidget(self._table)
        self._stack.addWidget(table_frame)   # index 1
        root.addWidget(self._stack, 1)

    # ─────────────────────────────────────────────────────────────────
    # Term loading
    # ─────────────────────────────────────────────────────────────────

    def _load_available_terms(self, auto_reload: bool = False):
        self._auto_reload_on_terms = auto_reload
        self._term_worker = _TermLoader()
        self._term_worker.finished.connect(self._on_terms_loaded)
        self._term_worker.error.connect(self._on_terms_error)
        self._term_worker.finished.connect(self._term_worker.deleteLater)
        self._term_worker.error.connect(self._term_worker.deleteLater)
        self._term_worker.start()

    def _on_terms_loaded(self, terms: list):
        auto_reload = getattr(self, "_auto_reload_on_terms", False)
        self._auto_reload_on_terms = False

        prev_ay  = self._ay_combo.currentText()
        prev_sem = self._sem_combo.currentIndex() + 1

        self._ay_combo.blockSignals(True)
        self._ay_combo.clear()

        if not terms:
            self._ay_combo.addItem("No data available")
            self._ay_combo.blockSignals(False)
            self._term_lbl.setText(
                "No prediction runs found in the database yet. "
                "Run a prediction first.")
            self._load_btn.setEnabled(False)
            self._delete_btn.setEnabled(False)
            return

        seen_years = []
        for ay, sem in terms:
            if ay not in seen_years:
                seen_years.append(ay)

        self._ay_combo.addItems(seen_years)
        self._available_terms = terms

        if auto_reload and prev_ay in seen_years:
            self._ay_combo.setCurrentText(prev_ay)
            self._ay_combo.blockSignals(False)
            self._sem_combo.setCurrentIndex(prev_sem - 1)
            self._load_btn.setEnabled(True)
            if self._rows:
                self._silent_reload()
            return

        latest_ay, latest_sem = terms[0]
        self._ay_combo.setCurrentText(latest_ay)
        self._ay_combo.blockSignals(False)
        self._sem_combo.setCurrentIndex(latest_sem - 1)
        self._load_btn.setEnabled(True)
        self._term_lbl.setText(
            f"Found {len(terms)} term(s) with prediction data. "
            "Select a term and click Load.")

    def _on_terms_error(self, msg: str):
        self._term_lbl.setText(f"⚠ Could not load terms: {msg}")

    # ─────────────────────────────────────────────────────────────────
    # Load / silent reload
    # ─────────────────────────────────────────────────────────────────

    def _on_load(self):
        ay  = self._ay_combo.currentText().strip()
        sem = self._sem_combo.currentIndex() + 1
        if not ay or ay == "No data available":
            return

        self._load_btn.setEnabled(False)
        self._load_btn.setText("Loading…")
        self._term_lbl.setText(f"Loading {ay} — Semester {sem}…")

        self._loader = _HistoryLoader(ay, sem)
        self._loader.finished.connect(self._on_rows_loaded)
        self._loader.error.connect(self._on_load_error)
        self._loader.finished.connect(self._loader.deleteLater)
        self._loader.error.connect(self._loader.deleteLater)
        self._loader.start()

    def _on_rows_loaded(self, rows: list[dict], term_label: str):
        self._load_btn.setEnabled(True)
        self._load_btn.setText("⟳  Load")

        if not rows:
            self._term_lbl.setText(
                f"No prediction records found for {term_label}.")
            self._stack.setCurrentIndex(0)
            self._clear_stats()
            self._delete_btn.setEnabled(False)
            return

        self._rows = rows
        self._term_lbl.setText(
            f"📅  {term_label}  ·  {len(rows):,} students")
        self._update_stats(rows)
        self._populate_filter_dropdowns()
        self._apply_filter()
        self._stack.setCurrentIndex(1)
        self._export_btn.setEnabled(True)
        self._delete_btn.setEnabled(True)

    def _silent_reload(self):
        ay  = self._ay_combo.currentText().strip()
        sem = self._sem_combo.currentIndex() + 1
        if not ay or ay == "No data available":
            return
        self._silent_loader = _HistoryLoader(ay, sem)
        self._silent_loader.finished.connect(self._on_silent_reload_done)
        self._silent_loader.error.connect(lambda _: None)
        self._silent_loader.finished.connect(self._silent_loader.deleteLater)
        self._silent_loader.error.connect(self._silent_loader.deleteLater)
        self._silent_loader.start()

    def _on_silent_reload_done(self, rows: list[dict], term_label: str):
        if not rows:
            return
        self._rows = rows
        self._term_lbl.setText(
            f"📅  {term_label}  ·  {len(rows):,} students  ↻ Updated just now")
        self._update_stats(rows)
        self._populate_filter_dropdowns()
        self._apply_filter()
        self._stack.setCurrentIndex(1)
        self._export_btn.setEnabled(True)
        self._delete_btn.setEnabled(True)

    def _on_load_error(self, msg: str):
        self._load_btn.setEnabled(True)
        self._load_btn.setText("⟳  Load")
        self._term_lbl.setText(f"⚠ Load failed: {msg}")
        from ui.dialogs.confirmation_dialog import show_error
        show_error(self, "Load Error",
                   "Could not load prediction history.", msg)

    # ─────────────────────────────────────────────────────────────────
    # Delete term
    # ─────────────────────────────────────────────────────────────────

    def _on_delete_term(self):
        ay  = self._ay_combo.currentText().strip()
        sem = self._sem_combo.currentIndex() + 1
        sem_label = "1st" if sem == 1 else "2nd"

        if not ay or ay == "No data available":
            return

        # ── Confirmation dialog ───────────────────────────────────────
        msg = QMessageBox(self)
        msg.setWindowTitle("Delete Term Data")
        msg.setText(
            f"Permanently delete all prediction records for\n"
            f"{ay} — {sem_label} Semester?"
        )
        msg.setInformativeText(
            f"This will remove {len(self._rows):,} student records from the "
            f"database. This action cannot be undone."
        )
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes |
            QMessageBox.StandardButton.Cancel
        )
        msg.setDefaultButton(QMessageBox.StandardButton.Cancel)
        msg.setStyleSheet("""
            QMessageBox {
                background-color: #13172a;
            }
            QMessageBox QLabel {
                color: #e8eaf0;
                font-size: 13px;
                background: transparent;
            }
            QMessageBox QPushButton {
                background-color: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.14);
                border-radius: 8px;
                color: rgba(255,255,255,0.80);
                font-size: 12px;
                font-weight: 600;
                padding: 8px 24px;
                min-width: 80px;
            }
            QMessageBox QPushButton:hover {
                background-color: rgba(255,255,255,0.12);
            }
            QMessageBox QPushButton[text="Yes"] {
                background-color: rgba(255,91,91,0.15);
                border-color: rgba(255,91,91,0.40);
                color: #ff5b5b;
            }
            QMessageBox QPushButton[text="Yes"]:hover {
                background-color: rgba(255,91,91,0.28);
            }
        """)

        if msg.exec() != QMessageBox.StandardButton.Yes:
            return

        # ── Run deletion ──────────────────────────────────────────────
        self._delete_btn.setEnabled(False)
        self._delete_btn.setText("Deleting…")
        self._load_btn.setEnabled(False)

        self._delete_worker = _DeleteTermWorker(ay, sem)
        self._delete_worker.finished.connect(self._on_delete_done)
        self._delete_worker.error.connect(self._on_delete_error)
        self._delete_worker.finished.connect(self._delete_worker.deleteLater)
        self._delete_worker.error.connect(self._delete_worker.deleteLater)
        self._delete_worker.start()

    def _on_delete_done(self, deleted: int):
        ay  = self._ay_combo.currentText().strip()
        sem = self._sem_combo.currentIndex() + 1
        sem_label = "1st" if sem == 1 else "2nd"

        # Reset UI to empty state
        self._rows = []
        self._clear_stats()
        self._table.setRowCount(0)
        self._stack.setCurrentIndex(0)
        self._export_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)
        self._delete_btn.setText("🗑  Delete Term Data")
        self._load_btn.setEnabled(True)

        self._term_lbl.setText(
            f"✓  Deleted {deleted:,} records for "
            f"{ay} — {sem_label} Semester."
        )

        # Refresh the term dropdown — the deleted term may now have 0 rows
        self._load_available_terms()

        QMessageBox.information(
            self,
            "Deleted",
            f"Successfully deleted {deleted:,} prediction records for\n"
            f"{ay} — {sem_label} Semester.\n\n"
            f"The academic term entry is kept; only the prediction data was removed.",
        )

    def _on_delete_error(self, msg: str):
        self._delete_btn.setEnabled(True)
        self._delete_btn.setText("🗑  Delete Term Data")
        self._load_btn.setEnabled(True)
        self._term_lbl.setText(f"⚠ Delete failed: {msg}")
        from ui.dialogs.confirmation_dialog import show_error
        show_error(self, "Delete Failed",
                   "Could not delete prediction records.", msg)

    # ─────────────────────────────────────────────────────────────────
    # Stats
    # ─────────────────────────────────────────────────────────────────

    def _update_stats(self, rows: list[dict]):
        total    = len(rows)
        high     = sum(1 for r in rows if "high"     in r["risk_label"].lower())
        moderate = sum(1 for r in rows if "moderate" in r["risk_label"].lower()
                                       or "medium"   in r["risk_label"].lower())
        low      = total - high - moderate
        scores   = [
            float(r["predicted_risk_score"]) * 100
            for r in rows if r["predicted_risk_score"] is not None
        ]
        avg = round(sum(scores) / len(scores), 1) if scores else 0.0
        self._stat_total.setText(f"{total:,}")
        self._stat_high.setText(f"{high:,}")
        self._stat_moderate.setText(f"{moderate:,}")
        self._stat_low.setText(f"{low:,}")
        self._stat_avg.setText(f"{avg:.1f}%")

    def _clear_stats(self):
        for lbl in (self._stat_total, self._stat_high,
                    self._stat_moderate, self._stat_low, self._stat_avg):
            lbl.setText("—")

    # ─────────────────────────────────────────────────────────────────
    # Filters
    # ─────────────────────────────────────────────────────────────────

    def _clear_filters(self):
        for w in (self._search,):
            w.blockSignals(True)
            w.clear()
            w.blockSignals(False)
        for combo in (self._risk_filter, self._program_filter,
                      self._college_filter):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self._apply_filter()

    def _populate_filter_dropdowns(self):
        risk_levels = sorted({
            str(r.get("risk_label", "")).strip()
            for r in self._rows if r.get("risk_label")
        })
        programs = sorted({
            str(r.get("program", "")).strip()
            for r in self._rows
            if r.get("program") and r.get("program") != "Unknown"
        })
        colleges = sorted({
            str(r.get("college", "")).strip()
            for r in self._rows
            if r.get("college") and r.get("college") not in ("—", "")
        })
        for combo, default, items in [
            (self._risk_filter,    "All Risk Levels", risk_levels),
            (self._program_filter, "All Programs",    programs),
            (self._college_filter, "All Colleges",    colleges),
        ]:
            combo.blockSignals(True)
            current = combo.currentText()
            combo.clear()
            combo.addItem(default)
            combo.addItems(items)
            idx = combo.findText(current)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

    def _apply_filter(self):
        text        = self._search.text().lower().strip()
        risk_sel    = self._risk_filter.currentText()
        program_sel = self._program_filter.currentText()
        college_sel = self._college_filter.currentText()

        filtered = []
        for row in self._rows:
            label   = str(row.get("risk_label", "")).strip()
            program = str(row.get("program",    "")).strip()
            college = str(row.get("college",    "")).strip()

            if risk_sel != "All Risk Levels":
                if _category_from_label(label) != _category_from_label(risk_sel):
                    continue
            if program_sel != "All Programs":
                if program != program_sel:
                    continue
            if college_sel != "All Colleges":
                if college != college_sel:
                    continue
            if text:
                haystack = " ".join([
                    str(row.get("student_id", "")),
                    str(row.get("full_name",  "")),
                    str(row.get("first_name", "")),
                    str(row.get("last_name",  "")),
                ]).lower()
                if text not in haystack:
                    continue
            filtered.append(row)

        self._populate_table(filtered)
        total = len(self._rows)
        shown = len(filtered)
        self._result_count_lbl.setText(
            f"{shown:,} of {total:,} students"
            if shown != total else f"{total:,} students"
        )

    def _populate_table(self, rows: list[dict]):
        self._table.setRowCount(0)
        self._table.setRowCount(len(rows))
        for row_i, row in enumerate(rows):
            category  = _category_from_label(row.get("risk_label", ""))
            score_raw = row.get("predicted_risk_score")
            score_pct = (
                f"{float(score_raw) * 100:.1f}%"
                if score_raw is not None else "—"
            )
            predicted_at = row.get("predicted_at")
            ts_str = (
                predicted_at.strftime("%b %d, %Y %H:%M")
                if hasattr(predicted_at, "strftime")
                else str(predicted_at)[:16] if predicted_at else "—"
            )
            cells = [
                str(row.get("student_id", "—")),
                str(row.get("full_name",  "—")),
                str(row.get("program",    "—")),
                str(row.get("college",    "—")),
                str(row.get("risk_label", "—")),
                score_pct,
                ts_str,
            ]
            for col_i, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                if col_i == 4:
                    color = {
                        "high_risk":     QColor("#ff5b5b"),
                        "moderate_risk": QColor("#f5b335"),
                        "low_risk":      QColor("#34d399"),
                    }.get(category, QColor("rgba(255,255,255,0.5)"))
                    item.setForeground(color)
                    item.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
                if col_i == 5 and score_raw is not None:
                    pct = float(score_raw) * 100
                    item.setForeground(QColor(
                        "#ff5b5b" if pct >= 50 else
                        "#f5b335" if pct >= 25 else "#34d399"
                    ))
                self._table.setItem(row_i, col_i, item)

    # ─────────────────────────────────────────────────────────────────
    # Export PDF
    # ─────────────────────────────────────────────────────────────────

    def _on_export_report(self):
        if not self._rows:
            from ui.dialogs.confirmation_dialog import show_warning
            show_warning(self, "No Data", "Load a term first before exporting.")
            return

        # ── Show customization dialog first ──────────────────────────────
        from ui.dialogs.report_customization import ReportCustomizationDialog
        colleges = sorted({
            str(r.get("college", "")).strip()
            for r in self._rows if r.get("college") and r.get("college") != "—"
        })
        dlg = ReportCustomizationDialog(parent=self, colleges=colleges)
        if dlg.exec() != ReportCustomizationDialog.DialogCode.Accepted:
            return
        config = dlg.cohort_config()

        # ── Then ask where to save ────────────────────────────────────────
        from PyQt6.QtWidgets import QFileDialog
        ay      = self._ay_combo.currentText().strip()
        sem     = self._sem_combo.currentIndex() + 1
        ay_safe = ay.replace("-", "_").replace(" ", "")
        default = f"CohortRiskSummary_{ay_safe}_Sem{sem}.pdf"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Report", default, "PDF Files (*.pdf)")
        if not path:
            return

        self._export_btn.setEnabled(False)
        self._export_btn.setText("Generating…")
        self._report_worker = _ReportWorker(
            rows          = self._rows,
            term_label    = self._term_lbl.text()
                                .replace("📅  ", "").split("  ·")[0].strip(),
            academic_year = ay,
            semester      = sem,
            save_path     = path,
            config        = config,              # ← pass config
        )
        self._report_worker.finished.connect(self._on_export_done)
        self._report_worker.error.connect(self._on_export_error)
        self._report_worker.finished.connect(self._report_worker.deleteLater)
        self._report_worker.error.connect(self._report_worker.deleteLater)
        self._report_worker.start()

    def _on_export_done(self, path: str):
        self._export_btn.setEnabled(True)
        self._export_btn.setText("⬇  Export PDF")
        from ui.dialogs.confirmation_dialog import show_info
        show_info(self, "Report Exported",
                  "Cohort Risk Summary report saved successfully.", path)

    def _on_export_error(self, msg: str):
        self._export_btn.setEnabled(True)
        self._export_btn.setText("⬇  Export PDF")
        from ui.dialogs.confirmation_dialog import show_error
        show_error(self, "Export Failed",
                   "Could not generate the PDF report.", msg)

    # ─────────────────────────────────────────────────────────────────
    # Styles
    # ─────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        # Stop all running worker threads before destruction
        for attr in ("_term_worker", "_loader", "_silent_loader",
                     "_delete_worker", "_report_worker"):
            worker = getattr(self, attr, None)
            if worker is None:
                continue
            try:
                worker.finished.disconnect()
            except Exception:
                pass
            try:
                worker.error.disconnect()
            except Exception:
                pass
            try:
                if worker.isRunning():
                    worker.quit()
                    worker.wait(2000)
            except RuntimeError:
                pass
            except Exception:
                pass
        DataStore.get().remove_listener(self._on_store_updated)
        super().closeEvent(event)

    def _apply_styles(self):
        self.setStyleSheet("""
            #histHeaderCard {
                background-color: #13172a;
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 16px;
            }
            #histTitle {
                color: #e8eaf0; font-size:18px; font-weight:bold;
                background:transparent;
            }
            #histSubtitle {
                color: rgba(255,255,255,0.40); font-size:12px;
                background:transparent;
            }
            #histSelectorFrame {
                background-color: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 12px;
            }
            #histSelectorLabel {
                color: rgba(255,255,255,0.45); font-size:11px;
                background:transparent;
            }
            QComboBox#histCombo {
                background-color: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px; color:#e8eaf0;
                font-size:12px; padding:6px 12px; min-height:32px;
            }
            QComboBox#histCombo:hover {
                border-color: rgba(79,140,255,0.4);
            }
            QComboBox#histCombo::drop-down { border:none; width:20px; }
            QComboBox#histCombo QAbstractItemView {
                background-color: #1a1f35;
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px; color:#e8eaf0;
                selection-background-color: rgba(79,140,255,0.20);
            }
            QPushButton#histLoadBtn {
                background-color: #4f8cff; border:none;
                border-radius:8px; color:white;
                font-size:12px; font-weight:600; padding:0 20px;
            }
            QPushButton#histLoadBtn:hover { background-color: rgba(79,140,255,0.85); }
            QPushButton#histLoadBtn:disabled {
                background-color: rgba(255,255,255,0.06);
                color: rgba(255,255,255,0.25);
            }
            QPushButton#histExportBtn {
                background-color: rgba(52,211,153,0.12);
                border: 1px solid rgba(52,211,153,0.35);
                border-radius:8px; color:#34d399;
                font-size:12px; font-weight:600; padding:0 16px;
            }
            QPushButton#histExportBtn:hover { background-color: rgba(52,211,153,0.22); }
            QPushButton#histExportBtn:disabled {
                background-color: rgba(255,255,255,0.03);
                border-color: rgba(255,255,255,0.08);
                color: rgba(255,255,255,0.20);
            }

            /* ── Delete Term button ─────────────────────────────── */
            QPushButton#histDeleteBtn {
                background-color: rgba(255,91,91,0.08);
                border: 1px solid rgba(255,91,91,0.30);
                border-radius: 8px;
                color: #ff5b5b;
                font-size: 12px;
                font-weight: 600;
                padding: 0 16px;
            }
            QPushButton#histDeleteBtn:hover {
                background-color: rgba(255,91,91,0.18);
                border-color: rgba(255,91,91,0.55);
            }
            QPushButton#histDeleteBtn:disabled {
                background-color: rgba(255,255,255,0.03);
                border-color: rgba(255,255,255,0.08);
                color: rgba(255,255,255,0.20);
            }

            QPushButton#histClearBtn {
                background-color: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius:8px; color:rgba(255,255,255,0.50);
                font-size:11px; padding:0 14px;
            }
            QPushButton#histClearBtn:hover {
                background-color: rgba(255,255,255,0.09);
                color: rgba(255,255,255,0.80);
                border-color: rgba(255,255,255,0.20);
            }
            #histTermLabel {
                color: rgba(255,255,255,0.50); font-size:12px; background:transparent;
            }
            #histStatTile {
                background-color: #13172a;
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 12px;
            }
            QLineEdit#histSearch {
                background-color: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 10px; color:#e8eaf0;
                font-size:13px; padding:10px 14px;
            }
            QLineEdit#histSearch:focus { border-color: rgba(79,140,255,0.50); }
            #histResultCount {
                color: rgba(255,255,255,0.35); font-size:12px; background:transparent;
            }
            #histEmptyMsg {
                color: rgba(255,255,255,0.35); font-size:13px; background:transparent;
            }
            #histTableFrame {
                background-color: #13172a;
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 14px;
            }
            QTableWidget#histTable {
                background-color: transparent; border:none;
                color: rgba(255,255,255,0.85); font-size:12px;
                alternate-background-color: rgba(255,255,255,0.025);
                selection-background-color: rgba(79,140,255,0.15);
                selection-color: white; gridline-color: transparent;
            }
            QTableWidget#histTable QHeaderView::section {
                background-color: rgba(255,255,255,0.05);
                color: rgba(255,255,255,0.45);
                font-size:11px; font-weight:bold; border:none;
                border-right: 1px solid rgba(255,255,255,0.06);
                padding: 10px 12px;
            }
            QTableWidget#histTable QHeaderView::section:last {
                border-right: none;
            }
            QScrollBar:vertical { background:transparent; width:8px; }
            QScrollBar::handle:vertical {
                background: rgba(255,255,255,0.12);
                border-radius:4px; min-height:30px;
            }
            QScrollBar::handle:vertical:hover { background: rgba(255,255,255,0.22); }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical { height:0; }
        """)