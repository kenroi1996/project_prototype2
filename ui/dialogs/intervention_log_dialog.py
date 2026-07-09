"""
ui/dialogs/intervention_log_dialog.py
=======================================
Full-featured intervention log viewer: search, filter, pagination,
checkbox multi-select, and batch delete.

Extracted verbatim from ui/pages/interventions_page.py — no logic changes.
"""
from __future__ import annotations
import json

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QComboBox, QLineEdit, QScrollArea, QDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont

from services.interventions_service import (
    LogLoader, LogDeleter, BatchLogDeleter, _safe_cleanup,
)
from services.data_store import DataStore
from ui.dialogs.intervention_detail_dialog import _InterventionDetailDialog


class InterventionLogDialog(QDialog):
    """Full-featured log viewer with search, filter, pagination,
    checkbox multi-select, and batch delete."""

    PAGE_SIZE = 20
    _COL_CHK  = 0
    _COL_ID   = 1
    _COL_SID  = 2
    _COL_TERM = 3
    _COL_TYPE = 4
    _COL_RISK = 5
    _COL_RECS = 6
    _COL_LOG  = 7
    _COL_VIEW = 8
    _COL_DEL  = 9

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
        self._ay_filter   = self._cb(["All Terms"])
        self._sem_filter  = self._cb(["All Semesters","1st Semester","2nd Semester"])
        self._mode_filter = self._cb(["All Types","per_student","cohort"])
        self._date_from   = self._inp("From (YYYY-MM-DD)", 148)
        self._date_to     = self._inp("To (YYYY-MM-DD)",   148)
        clr = QPushButton("✕  Clear"); clr.setObjectName("logClearBtn")
        clr.setFixedHeight(32); clr.setCursor(Qt.CursorShape.PointingHandCursor)
        clr.clicked.connect(self._clear_filters)
        for w in [self._sid_search, self._ay_filter,
                  self._sem_filter, self._mode_filter, self._date_from,
                  self._date_to, clr]:
            f1.addWidget(w)
        f1.addStretch()
        self._count_lbl = QLabel(""); self._count_lbl.setObjectName("logCount")
        f1.addWidget(self._count_lbl); root.addLayout(f1)
        for w in (self._sid_search,
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
        self._table.setColumnCount(10)
        self._table.setHorizontalHeaderLabels([
            "☐", "ID", "Student ID", "Term",
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
        for w in (self._sid_search,
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
                (str(iid), None), (sid, None), (term, None),
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