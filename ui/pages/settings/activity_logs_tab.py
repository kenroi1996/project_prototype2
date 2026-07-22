"""
ui/pages/settings/activity_logs_tab.py
=========================================
Settings page — Tab 5: Activity Logs.
Filter, page through, and clean up all activity_log entries.

Extracted verbatim from ui/pages/settings_page.py — no logic changes.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, QDate
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QLineEdit, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QDateEdit,
)

from ui.dialogs.confirmation_dialog import ConfirmationDialog
from workers.settings_workers import _AllActivityLoader, _ActivityLogCleaner
from ui.helpers.settings_render import _section_title, _card, _ghost_btn, _primary_btn, _input


class _ActivityLogsTab(QWidget):
    PAGE_SIZE = 25

    _ACTIONS = [
        "All Actions",
        "LOGIN", "LOGOUT", "LOGIN_FAILED",
        "PREDICTION_RUN", "MODEL_TRAINED",
        "DATA_UPLOAD", "DATA_MERGE",
        "INTERVENTION_SAVED", "PASSWORD_CHANGED",
        "USER_CREATED", "ROLE_CHANGED", "USER_DISABLED",
    ]
    _STATUSES = ["All Statuses", "SUCCESS", "FAILED", "INFO", "WARNING"]

    # Sentinel "no filter" date — see _date_picker() below.
    _NO_DATE = QDate(2000, 1, 1)

    def __init__(self):
        super().__init__()
        self._all_rows: list[dict]          = []
        self._page      = 0
        self._loader:   _AllActivityLoader  | None = None
        self._cleaner:  _ActivityLogCleaner | None = None
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(20)

        filter_card, filter_lo = _card()
        filter_lo.addWidget(_section_title("FILTER LOGS"))
        filter_lo.addSpacing(4)

        row1 = QHBoxLayout(); row1.setSpacing(12)

        self._f_username  = _input("Username")
        self._f_username.setFixedHeight(32)

        _cb_ss = """
            QComboBox {
                background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.10);
                border-radius:7px; color:#e8eaf0;
                font-size:12px; padding:0 10px;
            }
            QComboBox:hover { border-color:rgba(79,140,255,0.40); }
            QComboBox::drop-down { border:none; width:16px; }
            QComboBox QAbstractItemView {
                background:#1a1f35; color:#e8eaf0;
                selection-background-color:rgba(79,140,255,0.18);
            }
        """
        self._f_action = QComboBox()
        self._f_action.addItems(self._ACTIONS)
        self._f_action.setFixedHeight(32)
        self._f_action.setStyleSheet(_cb_ss)

        self._f_status = QComboBox()
        self._f_status.addItems(self._STATUSES)
        self._f_status.setFixedHeight(32)
        self._f_status.setStyleSheet(_cb_ss)

        self._f_date_from = self._date_picker("From date")
        self._f_date_from.setFixedHeight(32)
        self._f_date_to   = self._date_picker("To date")
        self._f_date_to.setFixedHeight(32)

        clear_btn = _ghost_btn("✕  Clear")
        clear_btn.setFixedHeight(32)
        clear_btn.clicked.connect(self._clear_filters)

        load_btn = _primary_btn("🔍  Search")
        load_btn.setFixedHeight(32)
        load_btn.clicked.connect(self._load)

        for lbl_txt, w in [
            ("Username",  self._f_username),
            ("Action",    self._f_action),
            ("Status",    self._f_status),
            ("Date From", self._f_date_from),
            ("Date To",   self._f_date_to),
        ]:
            col = QVBoxLayout(); col.setSpacing(3)
            lbl = QLabel(lbl_txt)
            lbl.setStyleSheet(
                "color:rgba(255,255,255,0.40); font-size:10px; background:transparent;")
            col.addWidget(lbl); col.addWidget(w)
            row1.addLayout(col)

        row1.addWidget(clear_btn, 0, Qt.AlignmentFlag.AlignBottom)
        row1.addWidget(load_btn,  0, Qt.AlignmentFlag.AlignBottom)
        row1.addStretch()
        filter_lo.addLayout(row1)
        root.addWidget(filter_card)

        self._batch_frame = QFrame()
        self._batch_frame.setObjectName("logBatchBar")
        self._batch_frame.setStyleSheet("""
            QFrame#logBatchBar {
                background:rgba(255,91,91,0.06);
                border:1px solid rgba(255,91,91,0.18); border-radius:8px;
            }
        """)
        self._batch_frame.setVisible(False)
        bb = QHBoxLayout(self._batch_frame)
        bb.setContentsMargins(12, 8, 12, 8); bb.setSpacing(10)

        self._sel_lbl = QLabel("0 selected")
        self._sel_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.55); font-size:12px; background:transparent;")

        self._del_sel_btn = QPushButton("🗑  Delete Selected")
        self._del_sel_btn.setFixedHeight(30)
        self._del_sel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._del_sel_btn.setStyleSheet("""
            QPushButton {
                background:rgba(255,91,91,0.14);
                border:1px solid rgba(255,91,91,0.35); border-radius:7px;
                color:#ff5b5b; font-size:12px; font-weight:600; padding:0 14px;
            }
            QPushButton:hover { background:rgba(255,91,91,0.26); }
        """)
        self._del_sel_btn.clicked.connect(self._on_delete_selected)

        self._del_all_btn = QPushButton("🗑  Delete All Filtered")
        self._del_all_btn.setFixedHeight(30)
        self._del_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._del_all_btn.setStyleSheet("""
            QPushButton {
                background:rgba(255,91,91,0.08);
                border:1px solid rgba(255,91,91,0.25); border-radius:7px;
                color:rgba(255,91,91,0.75); font-size:12px;
                font-weight:600; padding:0 14px;
            }
            QPushButton:hover { background:rgba(255,91,91,0.20); }
        """)
        self._del_all_btn.clicked.connect(self._on_delete_all_filtered)

        desel_btn = _ghost_btn("✕  Deselect All")
        desel_btn.setFixedHeight(30)
        desel_btn.clicked.connect(self._deselect_all)

        bb.addWidget(self._sel_lbl)
        bb.addWidget(self._del_sel_btn)
        bb.addWidget(self._del_all_btn)
        bb.addStretch()
        bb.addWidget(desel_btn)
        root.addWidget(self._batch_frame)

        log_card, log_lo = _card()
        log_hdr = QHBoxLayout()
        log_hdr.addWidget(_section_title("ACTIVITY LOGS  (latest 500)"))
        log_hdr.addStretch()
        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;")
        log_hdr.addWidget(self._count_lbl)
        log_lo.addLayout(log_hdr)

        self._table = QTableWidget()
        self._table.setObjectName("settingsTable")
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels([
            "☐", "Timestamp", "User", "Action", "Description", "Status"
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setHighlightSections(False)
        self._table.setShowGrid(False)
        self._table.horizontalHeader().sectionClicked.connect(
            self._on_header_clicked)

        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for col, mode, width in [
            (0, QHeaderView.ResizeMode.Fixed,            32),
            (1, QHeaderView.ResizeMode.ResizeToContents, 0),
            (2, QHeaderView.ResizeMode.Fixed,            110),
            (3, QHeaderView.ResizeMode.ResizeToContents, 0),
            (5, QHeaderView.ResizeMode.ResizeToContents, 0),
        ]:
            hh.setSectionResizeMode(col, mode)
            if width:
                self._table.setColumnWidth(col, width)

        self._table.setStyleSheet("""
            QTableWidget#settingsTable {
                background:transparent; border:none;
                color:#e8eaf0; font-size:12px;
                alternate-background-color:rgba(255,255,255,0.025);
                selection-background-color:transparent;
                gridline-color:transparent;
            }
            QTableWidget#settingsTable QHeaderView::section {
                background:rgba(255,255,255,0.05);
                color:rgba(255,255,255,0.55); font-size:11px;
                font-weight:bold; border:none; padding:8px 6px;
            }
        """)
        self._table.setMinimumHeight(320)
        log_lo.addWidget(self._table, 1)

        pag = QHBoxLayout(); pag.setSpacing(8)
        self._prev_btn = _ghost_btn("‹  Prev")
        self._prev_btn.setFixedHeight(28)
        self._prev_btn.clicked.connect(self._prev_page)
        self._prev_btn.setEnabled(False)

        self._page_lbl = QLabel("Page 1 of 1")
        self._page_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;")
        self._page_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_lbl.setFixedWidth(100)

        self._next_btn = _ghost_btn("Next  ›")
        self._next_btn.setFixedHeight(28)
        self._next_btn.clicked.connect(self._next_page)
        self._next_btn.setEnabled(False)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.35); font-size:11px; background:transparent;")

        pag.addWidget(self._prev_btn)
        pag.addWidget(self._page_lbl)
        pag.addWidget(self._next_btn)
        pag.addStretch()
        pag.addWidget(self._status_lbl)
        log_lo.addLayout(pag)
        root.addWidget(log_card)

        QTimer.singleShot(100, self._load)

    def _checked_ids(self) -> list[int]:
        ids   = []
        start = self._page * self.PAGE_SIZE
        for ri in range(self._table.rowCount()):
            cell = self._table.cellWidget(ri, 0)
            cb   = (cell if isinstance(cell, QPushButton)
                    else cell.findChild(QPushButton) if cell else None)
            if cb and cb.isChecked():
                row = self._all_rows[start + ri]
                if row.get("log_id") is not None:
                    ids.append(int(row["log_id"]))
        return ids

    def _update_batch_bar(self):
        n = len(self._checked_ids())
        self._sel_lbl.setText(
            f"{n} row{'s' if n != 1 else ''} selected on this page")
        self._batch_frame.setVisible(n > 0)

    def _on_header_clicked(self, col: int):
        if col != 0:
            return
        def _get_cb(r):
            cell = self._table.cellWidget(r, 0)
            if isinstance(cell, QPushButton): return cell
            return cell.findChild(QPushButton) if cell else None
        any_unchecked = any(
            not cb.isChecked()
            for r in range(self._table.rowCount())
            if (cb := _get_cb(r)) is not None
        )
        for r in range(self._table.rowCount()):
            cb = _get_cb(r)
            if cb:
                cb.setChecked(any_unchecked)
        self._update_batch_bar()

    def _deselect_all(self):
        for r in range(self._table.rowCount()):
            cell = self._table.cellWidget(r, 0)
            cb   = (cell if isinstance(cell, QPushButton)
                    else cell.findChild(QPushButton) if cell else None)
            if cb:
                cb.setChecked(False)
        self._update_batch_bar()

    def _date_picker(self, placeholder: str) -> QDateEdit:
        d = QDateEdit()
        d.setCalendarPopup(True)
        d.setDisplayFormat("yyyy-MM-dd")
        d.setMinimumDate(self._NO_DATE)
        d.setMaximumDate(QDate(2100, 1, 1))
        d.setSpecialValueText(placeholder)
        d.setDate(self._NO_DATE)   # starts unset, shows `placeholder`
        d.setStyleSheet("""
            QDateEdit {
                background-color: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 8px;
                color: #e8eaf0;
                font-size: 12px;
                padding: 0 8px;
            }
            QDateEdit:focus { border-color: rgba(79,140,255,0.40); }
            QDateEdit::drop-down {
                subcontrol-origin: padding; subcontrol-position: right center;
                width: 22px; border-left: 1px solid rgba(255,255,255,0.10);
            }
            QDateEdit::down-arrow { width: 10px; height: 10px; }
            QCalendarWidget { background: #1a1f35; }
            QCalendarWidget QToolButton {
                color: #e8eaf0; background: transparent; font-size: 12px;
            }
            QCalendarWidget QMenu { background: #1a1f35; color: #e8eaf0; }
            QCalendarWidget QSpinBox { background: #13172a; color: #e8eaf0; }
            QCalendarWidget QAbstractItemView:enabled {
                background: #13172a; color: #e8eaf0;
                selection-background-color: rgba(79,140,255,0.30);
                selection-color: #e8eaf0;
            }
            QCalendarWidget QAbstractItemView:disabled { color: rgba(255,255,255,0.25); }
        """)
        return d

    def _date_str(self, picker: QDateEdit) -> str:
        """'' if the picker is still at its unset sentinel, else 'yyyy-MM-dd'."""
        return "" if picker.date() == self._NO_DATE else picker.date().toString("yyyy-MM-dd")

    def _build_filters(self) -> dict:
        action = self._f_action.currentText()
        status = self._f_status.currentText()
        return {
            "username":  self._f_username.text().strip(),
            "action":    action if action != "All Actions"  else "",
            "status":    status if status != "All Statuses" else "",
            "date_from": self._date_str(self._f_date_from),
            "date_to":   self._date_str(self._f_date_to),
        }

    def _clear_filters(self):
        self._f_username.clear()
        self._f_date_from.setDate(self._NO_DATE)
        self._f_date_to.setDate(self._NO_DATE)
        self._f_action.setCurrentIndex(0)
        self._f_status.setCurrentIndex(0)
        self._load()

    def _load(self):
        if self._loader is not None:
            try:
                self._loader.finished.disconnect()
                self._loader.error.disconnect()
                if self._loader.isRunning():
                    self._loader.quit(); self._loader.wait(1000)
                self._loader.deleteLater()
            except RuntimeError:
                pass
            self._loader = None
        self._status_lbl.setText("Loading…")
        self._loader = _AllActivityLoader(self._build_filters())
        self._loader.finished.connect(self._on_loaded)
        self._loader.error.connect(lambda e: self._status_lbl.setText(f"⚠ {e}"))
        self._loader.finished.connect(self._clear_loader)
        self._loader.error.connect(self._clear_loader)
        self._loader.start()

    def _clear_loader(self):
        w = self._loader
        self._loader = None
        if w is not None:
            try: w.deleteLater()
            except RuntimeError: pass

    def _on_loaded(self, rows: list):
        self._all_rows = rows
        self._page     = 0
        self._batch_frame.setVisible(False)
        self._render_page()
        self._status_lbl.setText("")

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
        total = len(self._all_rows)
        pages = self._total_pages()

        self._count_lbl.setText(f"{total:,} record{'s' if total != 1 else ''}")
        self._page_lbl.setText(f"Page {self._page+1} of {pages}")
        self._prev_btn.setEnabled(self._page > 0)
        self._next_btn.setEnabled(self._page < pages - 1)

        self._table.setRowCount(0)
        self._table.setRowCount(len(rows))
        self._table.setUpdatesEnabled(False)

        _action_colors = {
            "LOGIN": "#34d399", "LOGOUT": "#8b949e", "LOGIN_FAILED": "#ff5b5b",
            "PREDICTION_RUN": "#4f8cff", "MODEL_TRAINED": "#4f8cff",
            "DATA_UPLOAD": "#a78bfa", "DATA_MERGE": "#a78bfa",
            "INTERVENTION_SAVED": "#f5b335", "PASSWORD_CHANGED": "#f5b335",
            "USER_CREATED": "#34d399", "ROLE_CHANGED": "#f5b335",
            "USER_DISABLED": "#ff5b5b",
        }
        _status_colors = {
            "SUCCESS": "#34d399", "FAILED": "#ff5b5b",
            "INFO": "#4f8cff",    "WARNING": "#f5b335",
        }

        for ri, row in enumerate(rows):
            ts     = row.get("log_timestamp")
            ts_str = (ts.strftime("%b %d, %Y %H:%M")
                      if hasattr(ts, "strftime") else str(ts or "—")[:16])
            user   = str(row.get("user_name")    or "—")
            action = str(row.get("action")        or "—")
            desc   = str(row.get("description")   or "—")
            status = str(row.get("status")         or "—")
            a_color = _action_colors.get(action, "#8b949e")
            s_color = _status_colors.get(status,  "#8b949e")

            cb = QPushButton()
            cb.setObjectName("logCheckBtn")
            cb.setCheckable(True)
            cb.setFixedSize(18, 18)
            cb.setStyleSheet("""
                QPushButton#logCheckBtn {
                    background:rgba(255,255,255,0.06);
                    border:1px solid rgba(255,255,255,0.18);
                    border-radius:4px; color:transparent;
                }
                QPushButton#logCheckBtn:hover {
                    background:rgba(255,255,255,0.12);
                    border-color:rgba(255,91,91,0.40);
                }
                QPushButton#logCheckBtn:checked {
                    background:#ff5b5b; border-color:#ff5b5b;
                }
            """)
            cb.toggled.connect(lambda _: self._update_batch_bar())
            cb_cell = QWidget()
            cb_cell.setStyleSheet("background:transparent;")
            cb_lo = QHBoxLayout(cb_cell)
            cb_lo.setContentsMargins(0, 0, 0, 0)
            cb_lo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_lo.addWidget(cb)
            self._table.setCellWidget(ri, 0, cb_cell)

            for ci, (text, color) in enumerate([
                (ts_str, "#a0aabe"),
                (user,   "#e8eaf0"),
                (action, a_color),
                (desc,   "#e8eaf0"),
                (status, s_color),
            ], 1):
                item = QTableWidgetItem(text)
                item.setForeground(QColor(color))
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                self._table.setItem(ri, ci, item)

            self._table.setRowHeight(ri, 36)

        self._table.setUpdatesEnabled(True)
        self._update_batch_bar()

    def _confirm_delete(self, title: str, text: str, detail: str = "") -> bool:
        dlg = ConfirmationDialog(title, text, detail=detail, parent=self)
        return bool(dlg.exec())

    def _on_delete_selected(self):
        ids = self._checked_ids()
        if not ids:
            return
        if not self._confirm_delete(
            "Delete Selected Logs",
            f"Permanently delete {len(ids):,} selected log "
            f"{'entry' if len(ids) == 1 else 'entries'}?",
            "This cannot be undone.",
        ):
            return
        self._run_delete(ids=ids)

    def _on_delete_all_filtered(self):
        total = len(self._all_rows)
        if not total:
            return
        if not self._confirm_delete(
            "Delete All Filtered Logs",
            f"Permanently delete all {total:,} filtered log "
            f"{'entry' if total == 1 else 'entries'}?",
            "This deletes every log currently shown — across all pages. "
            "This cannot be undone.",
        ):
            return
        self._run_delete(ids=None, filters=self._build_filters())

    def _run_delete(self, ids: list[int] | None, filters: dict | None = None):
        self._status_lbl.setText("Deleting…")
        self._del_sel_btn.setEnabled(False)
        self._del_all_btn.setEnabled(False)
        self._cleaner = _ActivityLogCleaner(ids=ids, filters=filters)
        self._cleaner.finished.connect(self._on_deleted)
        self._cleaner.error.connect(lambda e: (
            self._status_lbl.setText(f"⚠ {e}"),
            self._del_sel_btn.setEnabled(True),
            self._del_all_btn.setEnabled(True),
        ))
        self._cleaner.finished.connect(self._clear_cleaner)
        self._cleaner.error.connect(self._clear_cleaner)
        self._cleaner.start()

    def _clear_cleaner(self):
        w = self._cleaner
        self._cleaner = None
        if w is not None:
            try: w.deleteLater()
            except RuntimeError: pass

    def _on_deleted(self, n: int):
        self._del_sel_btn.setEnabled(True)
        self._del_all_btn.setEnabled(True)
        self._status_lbl.setText(
            f"✓  {n:,} log {'entry' if n == 1 else 'entries'} deleted.")
        QTimer.singleShot(3000, lambda: self._status_lbl.setText(""))
        self._load()