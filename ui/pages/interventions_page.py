"""
ui/pages/interventions_page.py
================================
Counselor Portal — AI Intervention Advisor (UI only).

All backend logic (Ollama workers, DB workers, prompt strings) lives in:
  services/interventions_service.py

Dialogs, the results card widget, and shared render helpers used by this
page have been split out into their own modules to keep this file focused
on the page itself:
  ui/helpers/intervention_render.py     -> _risk_color, _rec_card, _cohort_row
  ui/widgets/student_result_card.py     -> _StudentResultCard
  ui/dialogs/program_selector_dialog.py -> _ProgramSelectorDialog
  ui/dialogs/intervention_detail_dialog.py -> _InterventionDetailDialog
  ui/dialogs/intervention_log_dialog.py -> InterventionLogDialog
  ui/dialogs/term_select_dialog.py      -> _TermSelectDialog

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
  - Split dialogs/widgets/render-helpers into their own modules
    (see module docstring above). No logic changes.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QComboBox, QScrollArea, QStackedWidget,
    QSizePolicy, QProgressBar, QDialog, QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer

from services.interventions_service import (
    OllamaWorker, BatchWorker, SaveWorker,
    TermLoader, StudentLoader,
    InterventionRecordLoader, InterventionReportWorker,
    parse_json_response, build_cohort_prompt,
    SYSTEM_COHORT, _safe_cleanup,
)
from services.data_store    import DataStore
from services.system_config import SystemConfig

from ui.helpers.intervention_render import _cohort_row
from ui.widgets.student_result_card import _StudentResultCard
from ui.dialogs.program_selector_dialog import _ProgramSelectorDialog
from ui.dialogs.intervention_log_dialog import InterventionLogDialog
from ui.dialogs.term_select_dialog import _TermSelectDialog


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