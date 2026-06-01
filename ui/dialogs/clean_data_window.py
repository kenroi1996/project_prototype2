"""Clean Data Window — UI only. Cleaning logic lives in preprocessing_service."""

import copy

from PyQt6.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QFrame,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QScrollArea,
    QSplitter,
    QComboBox,
    QTextEdit,
    QProgressBar,
    QFileDialog,
    QListWidget,
    QListWidgetItem,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor

from services.preprocessing_service import (
    CleaningEngine,
    compute_issues,
    compute_quality_score,
    get_unique_column_values,
    filter_rows_by_values,
    save_dataset,
)


def _divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: rgba(255,255,255,0.07);")
    return line


def _section_header(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("cleanSectionHeader")
    return lbl


# =====================================
# CLEAN DATA WINDOW
# =====================================

class CleanDataWindow(QDialog):
    """
    Power Query-inspired preprocessing window.

    exec() returns QDialog.Accepted on "Continue".
    Read results via:
        window.cleaned_headers
        window.cleaned_rows
    """

    def __init__(self, headers: list, rows: list, config: dict, parent=None):
        super().__init__(parent)

        self._orig_headers = list(headers)
        self._orig_rows = [list(r) for r in rows]
        self._headers = list(headers)
        self._rows = [list(r) for r in rows]
        self._config = config
        self._accent = config.get("accent", "#4f8cff")
        self._steps: list = []
        self._drag_pos = None
        self._encoded_cols: list = []
        self._filtered_rows: list = []
        self._filter_active: bool = False
        self._issue_data: dict = {}
        self._filter_col_combo = None

        self.cleaned_headers: list = []
        self.cleaned_rows: list = []

        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setMinimumSize(1160, 740)
        self.resize(1280, 800)

        self._build_ui()
        self._apply_styles()
        self._refresh_all()

    # ------------------------------------------------------------------
    # Drag
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()

    # ------------------------------------------------------------------
    # UI BUILD
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        card = QFrame()
        card.setObjectName("cleanCard")
        root.addWidget(card)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        card_layout.addWidget(self._build_title_bar())
        card_layout.addWidget(_divider())
        card_layout.addWidget(self._build_quality_bar())
        card_layout.addWidget(_divider())

        # Main splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("cleanSplitter")
        splitter.setHandleWidth(1)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_center_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([300, 680, 280])

        card_layout.addWidget(splitter, 1)
        card_layout.addWidget(_divider())
        card_layout.addWidget(self._build_footer())

    # ── Title bar ────────────────────────────────────────────────────

    def _build_title_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("cleanTitleBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(12)

        icon = QLabel("🧹")
        icon.setStyleSheet("font-size: 20px;")

        col = QVBoxLayout()
        col.setSpacing(2)

        title = QLabel("Clean Data Center")
        title.setObjectName("cleanTitle")

        office = self._config.get("office", "")
        sub = QLabel(
            f"{office}  ·  {len(self._orig_rows):,} rows  ·  "
            f"{len(self._orig_headers)} columns loaded"
        )
        sub.setObjectName("cleanSubtitle")

        col.addWidget(title)
        col.addWidget(sub)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("cleanCloseBtn")
        close_btn.setFixedSize(32, 32)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.reject)

        layout.addWidget(icon)
        layout.addLayout(col, 1)
        layout.addWidget(close_btn)
        return bar

    # ── Quality bar (4 QFrame cards + progress) ──────────────────────

    def _build_quality_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("cleanQualityBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 14, 24, 14)
        layout.setSpacing(12)

        # Quality cards container
        self._qcard_layout = QHBoxLayout()
        self._qcard_layout.setSpacing(10)
        layout.addLayout(self._qcard_layout, 1)

        # Data quality progress
        prog_col = QVBoxLayout()
        prog_col.setSpacing(4)

        prog_header = QHBoxLayout()
        prog_lbl = QLabel("DATA QUALITY SCORE")
        prog_lbl.setObjectName("cleanQualityLabel")
        self._quality_pct = QLabel("—%")
        self._quality_pct.setObjectName("cleanQualityPct")
        prog_header.addWidget(prog_lbl)
        prog_header.addStretch()
        prog_header.addWidget(self._quality_pct)

        self._quality_bar = QProgressBar()
        self._quality_bar.setObjectName("cleanQualityProgress")
        self._quality_bar.setFixedHeight(8)
        self._quality_bar.setTextVisible(False)
        self._quality_bar.setRange(0, 100)

        prog_col.addLayout(prog_header)
        prog_col.addWidget(self._quality_bar)

        prog_wrap = QWidget()
        prog_wrap.setFixedWidth(220)
        prog_wrap.setLayout(prog_col)
        layout.addWidget(prog_wrap)

        return bar

    # ── Left panel: Issues + Actions ─────────────────────────────────

    def _build_left_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("cleanLeftPanel")

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("cleanLeftScroll")

        inner = QWidget()
        inner.setObjectName("cleanLeftInner")
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(18, 18, 18, 18)
        inner_layout.setSpacing(16)

        # CREATE these FIRST before using them
        self._filter_frame = QFrame()
        self._filter_frame.setObjectName("cleanFilterFrame")
        filter_layout = QVBoxLayout(self._filter_frame)
        filter_layout.setContentsMargins(14, 8, 14, 8)
        filter_layout.setSpacing(6)

        self._issues_layout = QGridLayout()
        self._issues_layout.setSpacing(8)

        # ── ISSUES PANEL ─────────────────────────────────────────────
        inner_layout.addWidget(_section_header("ISSUES PANEL"))
        inner_layout.addLayout(self._issues_layout)
        inner_layout.addWidget(self._filter_frame)
        inner_layout.addWidget(_divider())

        # ── VALUE FILTER ─────────────────────────────────────────────
        inner_layout.addWidget(_section_header("FILTER BY VALUE"))

        # Column selector for filtering
        filter_col_row = QHBoxLayout()
        filter_col_lbl = QLabel("Column")
        filter_col_lbl.setObjectName("cleanConfigLabel")
        self._filter_col_combo = QComboBox()
        self._filter_col_combo.setObjectName("cleanCombo")
        self._filter_col_combo.addItem("(select column)")
        for h in self._headers:
            self._filter_col_combo.addItem(h)
        self._filter_col_combo.currentTextChanged.connect(self._on_filter_col_changed)
        filter_col_row.addWidget(filter_col_lbl)
        filter_col_row.addWidget(self._filter_col_combo, 1)
        inner_layout.addLayout(filter_col_row)

        # Value checklist (multi-select)
        filter_val_lbl = QLabel("Values")
        filter_val_lbl.setObjectName("cleanConfigLabel")
        inner_layout.addWidget(filter_val_lbl)

        self._filter_val_list = QListWidget()
        self._filter_val_list.setObjectName("cleanFilterList")
        self._filter_val_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self._filter_val_list.setMaximumHeight(140)
        self._filter_val_list.setEnabled(False)
        inner_layout.addWidget(self._filter_val_list)

        # Select / Deselect all buttons
        filter_select_row = QHBoxLayout()
        filter_select_row.setSpacing(8)

        select_all_btn = QPushButton("Select All")
        select_all_btn.setObjectName("cleanSmallBtn")
        select_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        select_all_btn.clicked.connect(self._act_select_all_values)

        deselect_all_btn = QPushButton("Clear All")
        deselect_all_btn.setObjectName("cleanSmallBtn")
        deselect_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        deselect_all_btn.clicked.connect(self._act_deselect_all_values)

        filter_select_row.addWidget(select_all_btn)
        filter_select_row.addWidget(deselect_all_btn)
        filter_select_row.addStretch()
        inner_layout.addLayout(filter_select_row)

        # Filter buttons
        filter_btn_row = QHBoxLayout()
        filter_btn_row.setSpacing(8)

        apply_filter_btn = QPushButton("Apply Filter")
        apply_filter_btn.setObjectName("cleanActionBtn")
        apply_filter_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_filter_btn.clicked.connect(self._act_apply_value_filter)

        clear_filter_btn = QPushButton("Clear")
        clear_filter_btn.setObjectName("cleanUndoBtn")
        clear_filter_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_filter_btn.clicked.connect(self._show_all_rows)

        filter_btn_row.addWidget(apply_filter_btn)
        filter_btn_row.addWidget(clear_filter_btn)
        inner_layout.addLayout(filter_btn_row)

        inner_layout.addWidget(_divider())
        # ── CLEANING ACTIONS ─────────────────────────────────────────
        inner_layout.addWidget(_section_header("CLEANING ACTIONS"))

        col_row = QHBoxLayout()
        col_lbl = QLabel("Target Column")
        col_lbl.setObjectName("cleanConfigLabel")
        self._col_combo = QComboBox()
        self._col_combo.setObjectName("cleanCombo")
        self._col_combo.addItem("(all columns)")
        for h in self._headers:
            self._col_combo.addItem(h)
        col_row.addWidget(col_lbl)
        col_row.addWidget(self._col_combo, 1)
        inner_layout.addLayout(col_row)

        actions = [
            ("⬜  Fill Missing — Mean", self._act_fill_mean),
            ("⬜  Fill Missing — Median", self._act_fill_median),
            ("⬜  Fill Missing — Mode", self._act_fill_mode),
            ("🗑  Remove Duplicates", self._act_remove_dupes),
            ("🗑  Remove Empty Rows", self._act_remove_empty),
            ("📐  Normalize Column", self._act_normalize),
            ("🔢  Encode Categorical", self._act_encode),
            ("📊  Remove Outliers (3σ)", self._act_outliers),
            ("✂  Drop Column", self._act_drop_col),
        ]
        for label, slot in actions:
            btn = QPushButton(label)
            btn.setObjectName("cleanActionBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(slot)
            inner_layout.addWidget(btn)

        inner_layout.addWidget(_divider())

        # ── ENCODED COLUMNS ──────────────────────────────────────────
        inner_layout.addWidget(_section_header("ENCODED COLUMNS"))

        self._encoded_frame = QFrame()
        self._encoded_frame.setObjectName("cleanEncodedCard")
        self._encoded_layout = QVBoxLayout(self._encoded_frame)
        self._encoded_layout.setContentsMargins(12, 10, 12, 10)
        self._encoded_layout.setSpacing(4)

        self._encoded_placeholder = QLabel("No columns encoded yet.")
        self._encoded_placeholder.setObjectName("cleanMutedLabel")
        self._encoded_layout.addWidget(self._encoded_placeholder)
        inner_layout.addWidget(self._encoded_frame)

        undo_btn = QPushButton("↩  Undo Last Step")
        undo_btn.setObjectName("cleanUndoBtn")
        undo_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        undo_btn.clicked.connect(self._act_undo)
        inner_layout.addWidget(undo_btn)

        reset_btn = QPushButton("↺  Reset All")
        reset_btn.setObjectName("cleanResetBtn")
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.clicked.connect(self._act_reset)
        inner_layout.addWidget(reset_btn)

        inner_layout.addStretch()
        scroll.setWidget(inner)

        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return panel

    # ── Center panel: Dataset Preview ────────────────────────────────

    def _build_center_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("cleanCenterPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        # Dataset summary (QGridLayout)
        summary_card = QFrame()
        summary_card.setObjectName("cleanSummaryCard")
        self._summary_grid = QGridLayout(summary_card)
        self._summary_grid.setContentsMargins(16, 12, 16, 12)
        self._summary_grid.setHorizontalSpacing(32)
        self._summary_grid.setVerticalSpacing(6)
        layout.addWidget(summary_card)

        # Table label
        tbl_header_row = QHBoxLayout()
        tbl_lbl = QLabel("DATASET PREVIEW")
        tbl_lbl.setObjectName("cleanTableLabel")

        self._row_count_lbl = QLabel("")
        self._row_count_lbl.setObjectName("cleanMutedLabel")

        tbl_header_row.addWidget(tbl_lbl)
        tbl_header_row.addStretch()
        tbl_header_row.addWidget(self._row_count_lbl)
        layout.addLayout(tbl_header_row)

        # Table
        self._table = QTableWidget()
        self._table.setObjectName("cleanTable")
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setHighlightSections(True)
        self._table.setShowGrid(False)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(self._table, 1)

        return panel

    # ── Right panel: Cleaning Log ─────────────────────────────────────

    def _build_right_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("cleanRightPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        # Cleaning log
        layout.addWidget(_section_header("CLEANING LOG"))

        self._log = QTextEdit()
        self._log.setObjectName("cleanLog")
        self._log.setReadOnly(True)
        self._log.setPlaceholderText("Cleaning steps will appear here...")
        layout.addWidget(self._log, 1)

        # Status indicator
        layout.addWidget(_divider())
        layout.addWidget(_section_header("STATUS"))

        self._status_indicator = QLabel("● Ready")
        self._status_indicator.setObjectName("cleanStatusReady")
        layout.addWidget(self._status_indicator)

        self._step_count_lbl = QLabel("0 steps applied")
        self._step_count_lbl.setObjectName("cleanMutedLabel")
        layout.addWidget(self._step_count_lbl)

        layout.addSpacing(4)

        return panel

    # ── Footer ───────────────────────────────────────────────────────

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        footer.setObjectName("cleanFooter")
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(24, 12, 24, 16)
        layout.setSpacing(10)

        self._footer_note = QLabel("")
        self._footer_note.setObjectName("cleanFooterNote")

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("cleanCancelBtn")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)

        save_btn = QPushButton("Save Clean Dataset")
        save_btn.setObjectName("cleanSaveBtn")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self._act_save)

        continue_btn = QPushButton("✓  Continue")
        continue_btn.setObjectName("cleanApplyBtn")
        continue_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        continue_btn.clicked.connect(self._act_apply)

        layout.addWidget(self._footer_note)
        layout.addStretch()
        layout.addWidget(cancel_btn)
        layout.addWidget(save_btn)
        layout.addWidget(continue_btn)
        return footer

    # ------------------------------------------------------------------
    # REFRESH HELPERS
    # ------------------------------------------------------------------

    def _refresh_all(self):
        self._refresh_issues()
        self._refresh_quality_bar()
        self._refresh_summary()
        self._refresh_table()
        self._refresh_encoded_panel()
        self._refresh_footer_note()

    def _refresh_issues(self):
        # Clear issues grid
        while self._issues_layout.count():
            item = self._issues_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Clear filter buttons
        layout = self._filter_frame.layout()
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        issues = compute_issues(self._headers, self._rows)

        # Store issue data for filtering
        self._issue_data = issues

        rows_data = [
            ("Missing Values", issues["missing"], issues.get("missing_rows", []),
             "#ff5b5b" if issues["missing"] else "#34d399"),
            ("Duplicate Rows", issues["duplicates"], issues.get("dupes_rows", []),
             "#f5b335" if issues["duplicates"] else "#34d399"),
            ("Empty Rows", issues["empty_rows"], issues.get("empty_rows_indices", []),
             "#f5b335" if issues["empty_rows"] else "#34d399"),
        ]

        for i, (label, count, row_indices, color) in enumerate(rows_data):
            dot = QLabel("●")
            dot.setFixedWidth(14)
            dot.setStyleSheet(f"color: {color}; font-size: 10px; background: transparent;")

            lbl = QLabel(label)
            lbl.setObjectName("cleanIssueLabel")

            val = QLabel(str(count))
            val.setAlignment(Qt.AlignmentFlag.AlignRight)
            val.setStyleSheet(
                f"color: {color}; font-size: 13px; font-weight: bold; background: transparent;"
            )

            # VIEW button
            view_btn = QPushButton("View")
            view_btn.setObjectName("cleanViewBtn")
            view_btn.setFixedWidth(50)
            view_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            view_btn.setEnabled(count > 0)
            view_btn.clicked.connect(
                lambda checked, indices=row_indices, name=label: self._show_filtered_rows(indices, name)
            )

            self._issues_layout.addWidget(dot, i, 0)
            self._issues_layout.addWidget(lbl, i, 1)
            self._issues_layout.addWidget(val, i, 2)
            self._issues_layout.addWidget(view_btn, i, 3)

        # Add "Show All" filter button
        show_all_btn = QPushButton("↺  Show All Rows")
        show_all_btn.setObjectName("cleanShowAllBtn")
        show_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        show_all_btn.clicked.connect(self._show_all_rows)
        self._filter_frame.layout().addWidget(show_all_btn)

        self._issues_layout.setColumnStretch(1, 1)

    def _show_filtered_rows(self, row_indices: list, filter_name: str):
        """Temporarily preview specific rows without adding a pipeline step."""
        if not row_indices:
            return

        # This is a VIEW-only filter (preview), not a data transformation
        self._filtered_rows = row_indices
        self._filter_active = True

        self._row_count_lbl.setText(
            f"Showing {len(row_indices)} filtered rows · {filter_name}"
        )

        self._refresh_table()
        self._refresh_log(f"Preview: {filter_name} ({len(row_indices)} rows)", "info")

    def _show_all_rows(self):
        """Remove all filter_by_values steps and restore full dataset view."""
        # Remove all filter steps from the pipeline
        filter_steps = [s for s in self._steps if s["op"] == "filter_by_values"]
        for step in filter_steps:
            self._steps.remove(step)

        # Re-run pipeline without filters
        self._headers, self._rows = CleaningEngine.apply(
            self._orig_headers, self._orig_rows, self._steps
        )

        self._filtered_rows = []
        self._filter_active = False
        self._refresh_all()
        self._refresh_log("Cleared all filters — showing all rows", "info")

    def _on_filter_col_changed(self, col_name: str):
        """Populate value checklist when filter column changes."""
        self._filter_val_list.clear()

        if not col_name or col_name == "(select column)":
            self._filter_val_list.setEnabled(False)
            return

        if col_name not in self._headers:
            self._filter_val_list.setEnabled(False)
            return

        sorted_vals = get_unique_column_values(self._rows, self._headers, col_name)

        for val in sorted_vals:
            item = QListWidgetItem(val)
            item.setCheckState(Qt.CheckState.Unchecked)
            self._filter_val_list.addItem(item)

        self._filter_val_list.setEnabled(True)

    def _act_select_all_values(self):
        """Check all items in the filter value list."""
        for i in range(self._filter_val_list.count()):
            item = self._filter_val_list.item(i)
            item.setCheckState(Qt.CheckState.Checked)

    def _act_deselect_all_values(self):
        """Uncheck all items in the filter value list."""
        for i in range(self._filter_val_list.count()):
            item = self._filter_val_list.item(i)
            item.setCheckState(Qt.CheckState.Unchecked)

    def _act_apply_value_filter(self):
        """Filter rows where selected column matches any checked value — adds as a pipeline step."""
        col_name = self._filter_col_combo.currentText()

        if col_name == "(select column)":
            self._toast("Select a column to filter.")
            return

        if col_name not in self._headers:
            return

        # Collect checked values
        selected_values = set()
        for i in range(self._filter_val_list.count()):
            item = self._filter_val_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                selected_values.add(item.text())

        if not selected_values:
            self._toast("Select at least one value to filter.")
            return

        # Add as a pipeline step so it stacks with other filters/cleaning actions
        val_summary = ", ".join(sorted(selected_values)[:3])
        if len(selected_values) > 3:
            val_summary += f" (+{len(selected_values) - 3} more)"

        self._run_step({
            "op": "filter_by_values",
            "params": {
                "col": col_name,
                "values": list(selected_values),
            },
            "label": f"Filter {col_name} ∈ [{val_summary}]",
        })

        # Clear the filter UI selections after applying
        self._filter_val_list.clear()
        self._filter_col_combo.setCurrentIndex(0)
        self._on_filter_col_changed("(select column)")
    
    def _refresh_quality_bar(self):
        score = compute_quality_score(self._headers, self._rows)

        self._quality_bar.setValue(score)
        self._quality_pct.setText(f"{score}%")

        color = "#34d399" if score >= 85 else "#f5b335" if score >= 60 else "#ff5b5b"
        self._quality_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: rgba(255,255,255,0.08);
                border-radius: 4px;
                border: none;
            }}
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 4px;
            }}
        """)
        self._quality_pct.setStyleSheet(
            f"color: {color}; font-size: 13px; font-weight: bold; background: transparent;"
        )

        # Refresh quality cards
        while self._qcard_layout.count():
            item = self._qcard_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        issues = compute_issues(self._headers, self._rows)
        removed = len(self._orig_rows) - len(self._rows)

        for value, label, color in [
            (f"{len(self._rows):,}", "Rows", self._accent),
            (str(len(self._headers)), "Columns", self._accent),
            (str(issues["missing"]), "Missing Values",
             "#ff5b5b" if issues["missing"] else "#34d399"),
            (str(issues["duplicates"]), "Duplicates",
             "#f5b335" if issues["duplicates"] else "#34d399"),
            (f"-{removed}", "Rows Removed",
             "#f5b335" if removed else "#34d399"),
        ]:
            self._qcard_layout.addWidget(self._quality_card(value, label, color))

    def _refresh_summary(self):
        while self._summary_grid.count():
            item = self._summary_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        data = [
            ("Original Rows", f"{len(self._orig_rows):,}"),
            ("Current Rows", f"{len(self._rows):,}"),
            ("Columns", str(len(self._headers))),
            ("Steps Applied", str(len(self._steps))),
        ]

        for col, (label, value) in enumerate(data):
            lbl = QLabel(label)
            lbl.setObjectName("cleanSummaryLabel")
            val = QLabel(value)
            val.setObjectName("cleanSummaryValue")
            self._summary_grid.addWidget(lbl, 0, col)
            self._summary_grid.addWidget(val, 1, col)

    def _refresh_table(self):
        self._table.clear()

        if not self._headers:
            return

        # Priority: preview filter (issue view buttons) > pipeline-filtered data
        if self._filter_active and self._filtered_rows:
            display_rows = [(i, self._rows[i]) for i in self._filtered_rows if i < len(self._rows)]
            count_text = f"Showing {len(display_rows)} filtered rows (preview)"
        else:
            display_rows = list(enumerate(self._rows))
            count_text = f"Showing {len(self._rows):,} rows · {len(self._headers)} columns"

        # ── THIS WAS MISSING ──
        self._table.setColumnCount(len(self._headers))
        self._table.setRowCount(len(display_rows))
        self._table.setHorizontalHeaderLabels(self._headers)

        for new_i, (orig_i, row) in enumerate(display_rows):
            for col_i in range(len(self._headers)):
                cell = row[col_i] if col_i < len(row) else ""
                value = cell.strip()
                is_empty = value == ""

                item = QTableWidgetItem("—" if is_empty else value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                if is_empty:
                    item.setForeground(QColor("#ff5b5b"))

                if self._filter_active:
                    item.setToolTip(f"Original row: {orig_i + 1}")

                self._table.setItem(new_i, col_i, item)

        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)

        self._row_count_lbl.setText(count_text)

    def _refresh_encoded_panel(self):
        while self._encoded_layout.count():
            item = self._encoded_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._encoded_cols:
            lbl = QLabel("No columns encoded yet.")
            lbl.setObjectName("cleanMutedLabel")
            self._encoded_layout.addWidget(lbl)
            return

        for col in self._encoded_cols:
            pill = QLabel(f"🔢  {col}")
            pill.setObjectName("cleanEncodedPill")
            self._encoded_layout.addWidget(pill)

    def _refresh_log(self, message: str, level: str = "info"):
        colors = {"info": "#b8bcc8", "success": "#34d399", "warning": "#f5b335", "error": "#ff5b5b"}
        color = colors.get(level, "#b8bcc8")
        step_n = len(self._steps)
        self._log.append(
            f'<span style="color:rgba(255,255,255,0.3); font-size:11px;">'
            f'[{step_n:02d}]</span> '
            f'<span style="color:{color};">{message}</span>'
        )

    def _refresh_status(self, text: str, level: str = "ready"):
        styles = {
            "ready": ("● Ready", "#34d399"),
            "working": ("● Working", "#f5b335"),
            "done": ("● Done", "#4f8cff"),
            "error": ("● Error", "#ff5b5b"),
        }
        label, color = styles.get(level, styles["ready"])
        self._status_indicator.setText(f"{label}  {text}" if text else label)
        self._status_indicator.setStyleSheet(
            f"color: {color}; font-size: 13px; font-weight: 600; background: transparent;"
        )
        self._step_count_lbl.setText(f"{len(self._steps)} step(s) applied")

    def _refresh_footer_note(self):
        removed = len(self._orig_rows) - len(self._rows)
        self._footer_note.setText(
            f"{len(self._rows):,} rows  ·  {len(self._headers)} columns  ·  "
            f"-{removed} rows from original  ·  {len(self._steps)} step(s)"
        )

    def _refresh_col_combo(self):
        current = self._col_combo.currentText()
        self._col_combo.clear()
        self._col_combo.addItem("(all columns)")
        for h in self._headers:
            self._col_combo.addItem(h)
        idx = self._col_combo.findText(current)
        if idx >= 0:
            self._col_combo.setCurrentIndex(idx)

        if self._filter_col_combo is not None:
            filter_current = self._filter_col_combo.currentText()
            self._filter_col_combo.clear()
            self._filter_col_combo.addItem("(select column)")
            for h in self._headers:
                self._filter_col_combo.addItem(h)
            f_idx = self._filter_col_combo.findText(filter_current)
            if f_idx >= 0:
                self._filter_col_combo.setCurrentIndex(f_idx)
            else:
                self._on_filter_col_changed("(select column)")

    @staticmethod
    def _quality_card(value: str, label: str, color: str) -> QFrame:
        card = QFrame()
        card.setObjectName("cleanQCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)

        val = QLabel(value)
        val.setStyleSheet(
            f"color: {color}; font-size: 16px; font-weight: bold; background: transparent;"
        )
        lbl = QLabel(label)
        lbl.setStyleSheet(
            "color: rgba(255,255,255,0.38); font-size: 10px; background: transparent;"
        )
        layout.addWidget(val)
        layout.addWidget(lbl)
        return card

    # ------------------------------------------------------------------
    # STEP RUNNER
    # ------------------------------------------------------------------

    def _run_step(self, step: dict):
        self._steps.append(step)
        self._headers, self._rows = CleaningEngine.apply(
            self._orig_headers, self._orig_rows, self._steps
        )
        self._refresh_log(step["label"], "success")
        self._refresh_status(step["label"], "done")
        self._refresh_all()
        self._refresh_col_combo()

    def _selected_col(self) -> str | None:
        text = self._col_combo.currentText()
        return None if text == "(all columns)" else text

    # ------------------------------------------------------------------
    # ACTION SLOTS
    # ------------------------------------------------------------------

    def _act_fill_mean(self):
        col = self._selected_col()
        if not col:
            self._toast("Select a target column first.")
            return
        self._run_step({"op": "fill_missing_mean", "params": {"col": col},
                        "label": f"Fill Missing (Mean) → {col}"})

    def _act_fill_median(self):
        col = self._selected_col()
        if not col:
            self._toast("Select a target column first.")
            return
        self._run_step({"op": "fill_missing_median", "params": {"col": col},
                        "label": f"Fill Missing (Median) → {col}"})

    def _act_fill_mode(self):
        col = self._selected_col()
        if not col:
            self._toast("Select a target column first.")
            return
        self._run_step({"op": "fill_missing_mode", "params": {"col": col},
                        "label": f"Fill Missing (Mode) → {col}"})

    def _act_remove_dupes(self):
        self._run_step({"op": "remove_duplicates", "params": {},
                        "label": "Remove Duplicates"})

    def _act_remove_empty(self):
        self._run_step({"op": "remove_empty_rows", "params": {},
                        "label": "Remove Empty Rows"})

    def _act_normalize(self):
        col = self._selected_col()
        if not col:
            self._toast("Select a target column first.")
            return
        self._run_step({"op": "normalize", "params": {"col": col},
                        "label": f"Normalize → {col}"})

    def _act_encode(self):
        col = self._selected_col()
        if not col:
            self._toast("Select a target column first.")
            return
        if col not in self._encoded_cols:
            self._encoded_cols.append(col)
        self._run_step({"op": "encode_categorical", "params": {"col": col},
                        "label": f"Encode Categorical → {col}"})

    def _act_outliers(self):
        col = self._selected_col()
        if not col:
            self._toast("Select a target column first.")
            return
        self._run_step({"op": "remove_outliers", "params": {"col": col},
                        "label": f"Remove Outliers (3σ) → {col}"})

    def _act_drop_col(self):
        col = self._selected_col()
        if not col:
            self._toast("Select a target column first.")
            return
        if col in self._encoded_cols:
            self._encoded_cols.remove(col)
        self._run_step({"op": "drop_column", "params": {"col": col},
                        "label": f"Drop Column → {col}"})

    def _act_undo(self):
        if not self._steps:
            return
        removed_step = self._steps.pop()
        if removed_step["op"] == "encode_categorical":
            col = removed_step["params"].get("col")
            if col in self._encoded_cols:
                self._encoded_cols.remove(col)
        self._headers, self._rows = CleaningEngine.apply(
            self._orig_headers, self._orig_rows, self._steps
        )
        self._refresh_log(f"Undone: {removed_step['label']}", "warning")
        self._refresh_status("Undo applied", "working")
        self._refresh_all()
        self._refresh_col_combo()

    def _act_reset(self):
        self._steps = []
        self._encoded_cols = []
        self._headers = list(self._orig_headers)
        self._rows = [list(r) for r in self._orig_rows]
        self._log.clear()
        self._refresh_log("All steps reset.", "warning")
        self._refresh_status("Reset", "ready")
        self._refresh_all()
        self._refresh_col_combo()

    def _act_save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Clean Dataset", "cleaned_dataset.csv",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path:
            return
        try:
            save_dataset(path, self._headers, self._rows)
            self._toast(f"Saved: {path.replace(chr(92), '/').split('/')[-1]}")
            self._refresh_log("Dataset saved.", "success")
        except Exception as e:
            self._toast(f"Save failed: {e}")

    def _act_apply(self):
        self.cleaned_headers = list(self._headers)
        self.cleaned_rows = [list(r) for r in self._rows]
        self.accept()

    # ------------------------------------------------------------------
    # TOAST
    # ------------------------------------------------------------------

    def _toast(self, message: str):
        self._footer_note.setText(f"ℹ  {message}")
        QTimer.singleShot(3000, lambda: self._refresh_footer_note()
                          if not self.isHidden() else None)

    # ------------------------------------------------------------------
    # STYLES
    # ------------------------------------------------------------------

    def _apply_styles(self):
        accent = self._accent
        self.setStyleSheet(f"""
            * {{ font-family: 'Segoe UI'; color: white; }}

            #cleanCard {{
                background-color: #13172a;
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 16px;
            }}
            #cleanTitleBar, #cleanQualityBar, #cleanFooter {{
                background: transparent;
            }}
            #cleanTitle {{
                color: #e8eaf0;
                font-size: 16px;
                font-weight: bold;
                background: transparent;
            }}
            #cleanSubtitle {{
                color: rgba(255,255,255,0.4);
                font-size: 12px;
                background: transparent;
            }}
            #cleanCloseBtn {{
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 8px;
                color: rgba(255,255,255,0.5);
                font-size: 13px;
                padding: 0;
            }}
            #cleanCloseBtn:hover {{
                background: rgba(255,255,255,0.12);
                color: white;
            }}

            /* Quality bar */
            #cleanQualityBar {{
                background: rgba(0,0,0,0.12);
                border-bottom: 1px solid rgba(255,255,255,0.05);
            }}
            #cleanQCard {{
                background-color: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.07);
                border-radius: 10px;
            }}
            #cleanQualityLabel {{
                color: rgba(255,255,255,0.35);
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1px;
                background: transparent;
            }}

            /* Splitter */
            #cleanSplitter::handle {{
                background: rgba(255,255,255,0.07);
                width: 1px;
            }}

            /* Left panel */
            #cleanLeftPanel {{
                background: rgba(0,0,0,0.15);
                border: none;
            }}
            #cleanLeftScroll, #cleanLeftInner {{
                background: transparent;
                border: none;
            }}
            #cleanSectionHeader {{
                color: rgba(255,255,255,0.32);
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1.2px;
                background: transparent;
            }}

            /* Issues card */
            #cleanIssuesCard {{
                background-color: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
            }}
            #cleanIssueLabel {{
                color: rgba(255,255,255,0.65);
                font-size: 12px;
                background: transparent;
            }}

            /* Encoded card */
            #cleanEncodedCard {{
                background-color: rgba(79,140,255,0.05);
                border: 1px solid rgba(79,140,255,0.18);
                border-radius: 10px;
            }}
            #cleanEncodedPill {{
                background-color: rgba(79,140,255,0.12);
                border: 1px solid rgba(79,140,255,0.30);
                border-radius: 12px;
                color: #6eb5ff;
                font-size: 11px;
                padding: 4px 10px;
            }}

            /* Config */
            #cleanConfigLabel {{
                color: rgba(255,255,255,0.45);
                font-size: 11px;
                background: transparent;
                min-width: 90px;
            }}
            #cleanCombo {{
                background-color: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px;
                color: rgba(255,255,255,0.85);
                font-size: 12px;
                padding: 7px 12px;
            }}
            #cleanCombo::drop-down {{ border: none; width: 20px; }}
            #cleanCombo QAbstractItemView {{
                background-color: #1a1d2e;
                color: white;
                border: 1px solid rgba(255,255,255,0.12);
                selection-background-color: rgba(79,140,255,0.25);
            }}

            /* Action buttons */
            #cleanActionBtn {{
                background-color: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.09);
                border-radius: 8px;
                color: rgba(255,255,255,0.72);
                font-size: 12px;
                padding: 8px 12px;
                text-align: left;
            }}
            #cleanActionBtn:hover {{
                background-color: rgba(79,140,255,0.12);
                border-color: rgba(79,140,255,0.35);
                color: white;
            }}

            /* Undo / Reset */
            #cleanUndoBtn {{
                background-color: rgba(245,179,53,0.08);
                border: 1px solid rgba(245,179,53,0.25);
                border-radius: 8px;
                color: #f5b335;
                font-size: 12px;
                padding: 8px 12px;
            }}
            #cleanUndoBtn:hover {{ background-color: rgba(245,179,53,0.18); }}
            #cleanResetBtn {{
                background-color: rgba(255,91,91,0.08);
                border: 1px solid rgba(255,91,91,0.25);
                border-radius: 8px;
                color: #ff5b5b;
                font-size: 12px;
                padding: 8px 12px;
            }}
            #cleanResetBtn:hover {{ background-color: rgba(255,91,91,0.18); }}

            /* Center panel */
            #cleanCenterPanel {{ background: transparent; border: none; }}
            #cleanSummaryCard {{
                background-color: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.07);
                border-radius: 10px;
            }}
            #cleanSummaryLabel {{
                color: rgba(255,255,255,0.38);
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 0.5px;
                background: transparent;
            }}
            #cleanSummaryValue {{
                color: #e8eaf0;
                font-size: 15px;
                font-weight: bold;
                background: transparent;
            }}
            #cleanTableLabel {{
                color: rgba(255,255,255,0.32);
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1px;
                background: transparent;
            }}
            #cleanMutedLabel {{
                color: rgba(255,255,255,0.35);
                font-size: 11px;
                background: transparent;
            }}

            /* Table */
            #cleanTable {{
                background-color: transparent;
                border: none;
                gridline-color: transparent;
                color: rgba(255,255,255,0.80);
                font-size: 12px;
                alternate-background-color: rgba(255,255,255,0.025);
                selection-background-color: rgba(79,140,255,0.18);
                selection-color: white;
            }}
            #cleanTable QHeaderView::section {{
                background-color: rgba(255,255,255,0.05);
                color: rgba(255,255,255,0.45);
                font-size: 11px;
                font-weight: bold;
                border: none;
                border-right: 1px solid rgba(255,255,255,0.06);
                padding: 8px 10px;
            }}
            #cleanTable QScrollBar:vertical {{
                background: transparent; width: 8px;
            }}
            #cleanTable QScrollBar::handle:vertical {{
                background: rgba(255,255,255,0.15);
                border-radius: 4px; min-height: 30px;
            }}
            #cleanTable QScrollBar::handle:vertical:hover {{
                background: rgba(255,255,255,0.28);
            }}
            #cleanTable QScrollBar:horizontal {{
                background: transparent; height: 8px;
            }}
            #cleanTable QScrollBar::handle:horizontal {{
                background: rgba(255,255,255,0.15); border-radius: 4px;
            }}
            #cleanTable QScrollBar::add-line:vertical,
            #cleanTable QScrollBar::sub-line:vertical,
            #cleanTable QScrollBar::add-line:horizontal,
            #cleanTable QScrollBar::sub-line:horizontal {{
                height: 0; width: 0;
            }}

            /* Right panel */
            #cleanRightPanel {{
                background: rgba(0,0,0,0.12);
                border: none;
                border-left: 1px solid rgba(255,255,255,0.06);
            }}
            #cleanLog {{
                background-color: rgba(0,0,0,0.25);
                border: 1px solid rgba(255,255,255,0.07);
                border-radius: 8px;
                color: #b8bcc8;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
                padding: 8px;
            }}
            #cleanStatusReady {{
                color: #34d399;
                font-size: 13px;
                font-weight: 600;
                background: transparent;
            }}

            /* Footer */
            #cleanFooterNote {{
                color: rgba(255,255,255,0.32);
                font-size: 12px;
                background: transparent;
            }}
            #cleanCancelBtn {{
                background-color: transparent;
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px;
                color: rgba(255,255,255,0.55);
                font-size: 12px;
                padding: 9px 20px;
            }}
            #cleanCancelBtn:hover {{
                background-color: rgba(255,255,255,0.06);
                color: white;
            }}
            #cleanSaveBtn {{
                background-color: rgba(52,211,153,0.10);
                border: 1px solid rgba(52,211,153,0.35);
                border-radius: 8px;
                color: #34d399;
                font-size: 12px;
                font-weight: 600;
                padding: 9px 20px;
            }}
            #cleanSaveBtn:hover {{ background-color: rgba(52,211,153,0.20); }}
            #cleanApplyBtn {{
                background-color: {accent};
                border: none;
                border-radius: 8px;
                color: white;
                font-size: 12px;
                font-weight: 600;
                padding: 9px 28px;
            }}
            #cleanApplyBtn:hover {{ background-color: rgba(79,140,255,0.85); }}

            /* Global scrollbars */
            QScrollBar:vertical {{
                background: transparent; width: 8px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255,255,255,0.10);
                border-radius: 4px; min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: rgba(255,255,255,0.20);
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{ height: 0; }}
               /* View buttons in issues panel */
            #cleanViewBtn {{
                background-color: rgba(79,140,255,0.10);
                border: 1px solid rgba(79,140,255,0.25);
                border-radius: 6px;
                color: #6eb5ff;
                font-size: 10px;
                font-weight: 600;
                padding: 2px 8px;
                min-height: 22px;
            }}
            #cleanViewBtn:hover {{
                background-color: rgba(79,140,255,0.20);
                border-color: rgba(79,140,255,0.40);
            }}
            #cleanViewBtn:disabled {{
                background-color: transparent;
                border-color: rgba(255,255,255,0.06);
                color: rgba(255,255,255,0.20);
            }}

            /* Show All button */
            #cleanShowAllBtn {{
                background-color: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 8px;
                color: rgba(255,255,255,0.60);
                font-size: 11px;
                padding: 6px 12px;
            }}
            #cleanShowAllBtn:hover {{
                background-color: rgba(255,255,255,0.08);
                color: white;
            }}

            /* Filter frame */
            #cleanFilterFrame {{
                background-color: rgba(255,255,255,0.02);
                border: 1px solid rgba(255,255,255,0.06);
                border-radius: 8px;
            }}
            #cleanFilterList {{
                background-color: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 8px;
                color: rgba(255,255,255,0.80);
                font-size: 12px;
                padding: 4px;
            }}
            #cleanFilterList::item {{
                padding: 4px 8px;
                border-radius: 4px;
            }}
            #cleanFilterList::item:hover {{
                background-color: rgba(79,140,255,0.10);
            }}
            #cleanFilterList::item:selected {{
                background-color: rgba(79,140,255,0.20);
                color: white;
            }}
            #cleanSmallBtn {{
                background-color: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 6px;
                color: rgba(255,255,255,0.55);
                font-size: 11px;
                padding: 4px 10px;
            }}
            #cleanSmallBtn:hover {{
                background-color: rgba(255,255,255,0.10);
                color: white;
            }}
        """)