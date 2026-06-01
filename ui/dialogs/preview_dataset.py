import csv
import openpyxl

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QWidget,
    QAbstractItemView,
    QGraphicsBlurEffect,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen

# Import the CleanDataWindow from your module
# Adjust this import path to match your project structure
from ui.dialogs.clean_data_window import CleanDataWindow  # <-- CHANGE THIS to your actual module path


# =====================================
# BACKGROUND LOADER THREAD
# =====================================

class _FileLoaderThread(QThread):
    finished = pyqtSignal(list, list, int, int, int)
    error    = pyqtSignal(str)

    def __init__(self, path: str):
        super().__init__()
        self._path = path

    def run(self):
        try:
            headers, rows, total, missing, dupes = _load_file(self._path)
            self.finished.emit(headers, rows, total, missing, dupes)
        except Exception as e:
            self.error.emit(str(e))


# =====================================
# SPINNER WIDGET
# =====================================

class _PreviewSpinner(QWidget):
    def __init__(self, size=56, color="#4f8cff", parent=None):
        super().__init__(parent)
        self._angle = 0
        self._color = QColor(color)
        self.setFixedSize(size, size)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._rotate)
        self._timer.start(16)

    def _rotate(self):
        self._angle = (self._angle + 5) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        margin = 8
        rect   = self.rect().adjusted(margin, margin, -margin, -margin)

        track = QPen(QColor(255, 255, 255, 20))
        track.setWidth(4)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track)
        painter.drawEllipse(rect)

        arc = QPen(self._color)
        arc.setWidth(4)
        arc.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc)
        painter.drawArc(rect, -self._angle * 16, -100 * 16)

        inner_rect = rect.adjusted(8, 8, -8, -8)
        arc2 = QPen(QColor(255, 255, 255, 40))
        arc2.setWidth(2)
        arc2.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc2)
        painter.drawArc(inner_rect, self._angle * 16, -80 * 16)

    def stop(self):
        self._timer.stop()


# =====================================
# HELPERS
# =====================================

def _load_file(path: str, max_rows: int = None):
    ext = path.rsplit(".", 1)[-1].lower()
    if ext in ("xlsx", "xls"):
        return _load_excel(path, max_rows)
    return _load_csv(path, max_rows)

def _load_csv(path: str, max_rows: int = None):
    all_rows = []
    headers  = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i == 0:
                headers = row
            else:
                all_rows.append(row)
    return _compute_stats(headers, all_rows, max_rows)

def _load_excel(path: str, max_rows: int = None):
    wb       = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws       = wb.active
    all_rows = []
    headers  = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        row = [str(cell) if cell is not None else "" for cell in row]
        if i == 0:
            headers = row
        else:
            all_rows.append(row)
    wb.close()
    return _compute_stats(headers, all_rows, max_rows)

def _compute_stats(headers, all_rows, max_rows):
    total   = len(all_rows)
    preview = all_rows[:max_rows] if max_rows else all_rows
    missing = sum(1 for row in all_rows for cell in row if cell.strip() == "")
    seen       = set()
    duplicates = 0
    for row in all_rows:
        key = tuple(row)
        if key in seen:
            duplicates += 1
        else:
            seen.add(key)
    return headers, preview, total, missing, duplicates


# =====================================
# STAT TILE
# =====================================

def _stat_tile(value: str, label: str, accent: str = "rgba(255,255,255,0.75)") -> QFrame:
    tile = QFrame()
    tile.setObjectName("previewStatTile")
    tile.setStyleSheet("""
        #previewStatTile {
            background-color: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 10px;
        }
    """)
    layout = QVBoxLayout(tile)
    layout.setContentsMargins(16, 12, 16, 12)
    layout.setSpacing(4)

    val = QLabel(value)
    val.setStyleSheet(f"color: {accent}; font-size: 18px; font-weight: bold;")

    lbl = QLabel(label)
    lbl.setStyleSheet("color: rgba(255,255,255,0.4); font-size: 11px;")

    layout.addWidget(val)
    layout.addWidget(lbl)
    return tile


# =====================================
# DATASET PREVIEW DIALOG
# =====================================

class DatasetPreviewDialog(QDialog):
    """
    Inspect an uploaded CSV before processing.

    The background (parent window) is blurred while this dialog is shown.
    The dialog itself stays sharp and fully interactive.

    Clicking "Clean Data" opens CleanDataWindow with the current preview data.
    When cleaning is applied, the preview table refreshes automatically.

    Returns
    -------
    QDialog.accepted  → user clicked Continue
    QDialog.rejected  → user clicked Cancel

    Usage
    -----
        dialog = DatasetPreviewDialog(file_path, portal_config, parent=self)
        if dialog.exec():
            self._process_upload()
    """

    def __init__(self, file_path: str, config: dict, parent=None):
        super().__init__(parent)

        self._file_path = file_path
        self._config    = config
        self._accent    = config.get("accent", "#4f8cff")

        # Frameless, translucent, modal
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setMinimumSize(860, 580)

        # Data placeholders
        self._headers    = []
        self._rows       = []
        self._total      = 0
        self._missing    = 0
        self._dupes      = 0
        self._load_error = None
        self._drag_pos   = None

        # Store full dataset for cleaning
        self._full_rows = []

        # Blur effect storage
        self._parent_blur_effect = None

        # Build loading UI first
        self._build_loading_ui()
        self._apply_loading_styles()

        # Start background file loader
        self._loader = _FileLoaderThread(file_path)
        self._loader.finished.connect(self._on_load_finished)
        self._loader.error.connect(self._on_load_error)
        self._loader.start()

    # ------------------------------------------------------------------
    # Background blur management
    # ------------------------------------------------------------------

    def _apply_background_blur(self):
        """Apply QGraphicsBlurEffect to the parent window."""
        parent = self.parent()
        if parent is not None:
            self._parent_blur_effect = QGraphicsBlurEffect(self)
            self._parent_blur_effect.setBlurRadius(20)
            self._parent_blur_effect.setBlurHints(
                QGraphicsBlurEffect.BlurHint.QualityHint
            )
            parent.setGraphicsEffect(self._parent_blur_effect)

    def _remove_background_blur(self):
        """Remove the blur effect from the parent window."""
        parent = self.parent()
        if parent is not None and self._parent_blur_effect is not None:
            parent.setGraphicsEffect(None)
            self._parent_blur_effect = None

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_background_blur()

    def hideEvent(self, event):
        self._remove_background_blur()
        super().hideEvent(event)

    def done(self, r):
        self._remove_background_blur()
        super().done(r)

    # ------------------------------------------------------------------
    # Mouse drag (frameless window movement)
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()

    # ------------------------------------------------------------------
    # Loading UI
    # ------------------------------------------------------------------

    def _build_loading_ui(self):
        self._loading_layout = QVBoxLayout(self)
        self._loading_layout.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setObjectName("previewCard")
        card.setFixedSize(340, 260)

        card_layout = QVBoxLayout(card)
        card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.setSpacing(20)
        card_layout.setContentsMargins(40, 40, 40, 40)

        spinner_row = QHBoxLayout()
        spinner_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._spinner = _PreviewSpinner(size=56, color=self._accent)
        spinner_row.addWidget(self._spinner)
        card_layout.addLayout(spinner_row)

        self._loading_label = QLabel("Reading dataset...")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label.setStyleSheet("""
            color: #e8eaf0;
            font-size: 14px;
            font-weight: 600;
            background: transparent;
        """)
        card_layout.addWidget(self._loading_label)

        self._loading_sub = QLabel("Please wait")
        self._loading_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_sub.setStyleSheet("""
            color: rgba(255,255,255,0.4);
            font-size: 12px;
            background: transparent;
        """)
        card_layout.addWidget(self._loading_sub)

        self._dot_index = 0
        self._dot_timer = QTimer(self)
        self._dot_timer.timeout.connect(self._cycle_dots)
        self._dot_timer.start(500)

        self._loading_layout.addWidget(card, 0, Qt.AlignmentFlag.AlignCenter)

    def _cycle_dots(self):
        dots = ["Please wait", "Please wait.", "Please wait..", "Please wait..."]
        self._dot_index = (self._dot_index + 1) % len(dots)
        self._loading_sub.setText(dots[self._dot_index])

    def _apply_loading_styles(self):
        self.setStyleSheet("""
            #previewCard {
                background-color: #13172a;
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 16px;
            }
        """)

    def _clear_layout(self, layout):
        """Recursively remove all widgets from a layout."""
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            else:
                sub = item.layout()
                if sub is not None:
                    self._clear_layout(sub)

    def _on_load_finished(self, headers, rows, total, missing, dupes):
        self._spinner.stop()
        self._dot_timer.stop()

        self._headers = headers
        self._rows    = rows
        self._total   = total
        self._missing = missing
        self._dupes   = dupes

        # Store full dataset for cleaning
        self._full_rows = list(rows)

        self._clear_layout(self._loading_layout)
        QWidget().setLayout(self._loading_layout)
        self._loading_layout = None

        self._build_ui()
        self._apply_styles()
        self.setMinimumSize(860, 580)
        self.adjustSize()

    def _on_load_error(self, error_msg):
        self._spinner.stop()
        self._dot_timer.stop()
        self._load_error = error_msg

        self._clear_layout(self._loading_layout)
        QWidget().setLayout(self._loading_layout)
        self._loading_layout = None

        self._build_ui()
        self._apply_styles()
        self.setMinimumSize(860, 580)
        self.adjustSize()

    # ------------------------------------------------------------------
    # Main UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setObjectName("previewCard")

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        # ── Title bar ──
        title_bar = QFrame()
        title_bar.setObjectName("previewTitleBar")
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(24, 18, 24, 18)
        title_layout.setSpacing(12)

        icon = QLabel("📋")
        icon.setStyleSheet("font-size: 18px;")

        title_col = QVBoxLayout()
        title_col.setSpacing(2)

        title = QLabel("Dataset Preview")
        title.setObjectName("previewTitle")

        office = self._config.get("office", "")
        hint   = QLabel(f"{office}  ·  {self._file_path.replace(chr(92), '/').split('/')[-1]}")
        hint.setObjectName("previewSubtitle")

        title_col.addWidget(title)
        title_col.addWidget(hint)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("previewCloseBtn")
        close_btn.setFixedSize(32, 32)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.reject)

        title_layout.addWidget(icon)
        title_layout.addLayout(title_col, 1)
        title_layout.addWidget(close_btn)

        card_layout.addWidget(title_bar)
        card_layout.addWidget(self._divider())

        # ── Stats row ──
        stats_bar = QWidget()
        stats_bar.setObjectName("previewStatsBar")
        stats_layout = QHBoxLayout(stats_bar)
        stats_layout.setContentsMargins(24, 16, 24, 16)
        stats_layout.setSpacing(12)

        missing_accent = "#ff5b5b" if self._missing > 0 else "#34d399"
        dupes_accent   = "#f5b335" if self._dupes > 0   else "#34d399"

        stats_layout.addWidget(_stat_tile(f"{self._total:,}", "Total Rows", self._accent))
        stats_layout.addWidget(_stat_tile(str(len(self._headers)), "Columns", self._accent))
        stats_layout.addWidget(_stat_tile(str(self._missing), "Missing Values", missing_accent))
        stats_layout.addWidget(_stat_tile(str(self._dupes), "Duplicate Rows", dupes_accent))
        stats_layout.addWidget(_stat_tile(
            f"{min(100, self._total):,} / {self._total:,}",
            "Rows Previewed",
            "rgba(255,255,255,0.6)",
        ))

        card_layout.addWidget(stats_bar)

        # ── Warning banners ──
        if self._load_error:
            card_layout.addWidget(
                self._banner(f"⚠  Could not read file: {self._load_error}", "danger")
            )
        else:
            if self._missing > 0:
                card_layout.addWidget(
                    self._banner(
                        f"⚠  {self._missing} missing value(s) detected. "
                        "Consider cleaning before processing.",
                        "warning",
                    )
                )
            if self._dupes > 0:
                card_layout.addWidget(
                    self._banner(
                        f"⚠  {self._dupes} duplicate row(s) found. "
                        "Use 'Clean Data' to remove them.",
                        "warning",
                    )
                )
            expected = self._config.get("fields", [])
            missing_cols = [f for f in expected if f not in self._headers]
            if missing_cols:
                card_layout.addWidget(
                    self._banner(
                        f"✕  Missing expected columns: {', '.join(missing_cols)}",
                        "danger",
                    )
                )

        # ── Table ──
        card_layout.addWidget(self._divider())

        table_wrapper = QWidget()
        table_wrapper.setObjectName("previewTableWrapper")
        tw_layout = QVBoxLayout(table_wrapper)
        tw_layout.setContentsMargins(24, 16, 24, 0)
        tw_layout.setSpacing(8)

        table_label = QLabel("PREVIEW  —  FIRST 100 ROWS")
        table_label.setObjectName("previewTableLabel")

        self._table = self._build_table()

        tw_layout.addWidget(table_label)
        tw_layout.addWidget(self._table)

        card_layout.addWidget(table_wrapper, 1)

        # ── Footer ──
        card_layout.addWidget(self._divider())

        footer = QWidget()
        footer.setObjectName("previewFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(24, 16, 24, 20)
        footer_layout.setSpacing(10)

        self._rows_note = QLabel(
            f"Showing {min(100, self._total):,} of {self._total:,} rows  ·  "
            f"{len(self._headers)} columns detected"
        )
        self._rows_note.setObjectName("previewFooterNote")

        clean_btn = QPushButton("Clean Data")
        clean_btn.setObjectName("previewCleanBtn")
        clean_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clean_btn.clicked.connect(self._on_clean)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("previewCancelBtn")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)

        continue_btn = QPushButton("Continue →")
        continue_btn.setObjectName("previewContinueBtn")
        continue_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        continue_btn.clicked.connect(self.accept)

        if self._load_error:
            continue_btn.setEnabled(False)

        footer_layout.addWidget(self._rows_note)
        footer_layout.addStretch()
        footer_layout.addWidget(clean_btn)
        footer_layout.addWidget(cancel_btn)
        footer_layout.addWidget(continue_btn)

        card_layout.addWidget(footer)

        outer.addWidget(card)

    # ------------------------------------------------------------------
    # Clean Data Button Handler
    # ------------------------------------------------------------------

    def _on_clean(self):
        """
        Open CleanDataWindow with the current dataset.
        If user applies cleaning, refresh the preview with cleaned data.
        """
        # Temporarily remove blur from parent so the clean window can have its own treatment
        self._remove_background_blur()

        # Load the FULL dataset (not just the 100-row preview) for cleaning
        try:
            full_headers, full_rows, total, missing, dupes = _load_file(self._file_path)
        except Exception as e:
            # If full load fails, fall back to current preview data
            full_headers = list(self._headers)
            full_rows = [list(r) for r in self._rows]

        # Open the cleaning window
        clean_window = CleanDataWindow(
            headers=full_headers,
            rows=full_rows,
            config=self._config,
            parent=self,  # Modal child of this dialog
        )

        if clean_window.exec() == QDialog.DialogCode.Accepted:
            # User clicked "Continue" (Apply Cleaning) — refresh preview with cleaned data
            self._headers = clean_window.cleaned_headers
            self._rows = clean_window.cleaned_rows
            self._total = len(self._rows)

            # Recompute stats
            self._missing = sum(
                1 for row in self._rows for cell in row if cell.strip() == ""
            )
            seen = set()
            self._dupes = 0
            for row in self._rows:
                key = tuple(row)
                if key in seen:
                    self._dupes += 1
                else:
                    seen.add(key)

            # Refresh the UI
            self._refresh_preview()

        # Re-apply blur when returning to this dialog
        self._apply_background_blur()

    def _refresh_preview(self):
        """Rebuild table and stats after cleaning."""
        # Rebuild table
        self._table.clear()
        self._table.setColumnCount(len(self._headers))
        self._table.setRowCount(len(self._rows))
        self._table.setHorizontalHeaderLabels(self._headers)

        expected_fields = set(self._config.get("fields", []))

        for col, h in enumerate(self._headers):
            item = self._table.horizontalHeaderItem(col)
            if item and expected_fields and h not in expected_fields:
                item.setForeground(QColor("#f5b335"))

        for row_i, row in enumerate(self._rows):
            for col_i, cell in enumerate(row):
                value = cell.strip()
                is_empty = value == ""

                item = QTableWidgetItem("—" if is_empty else value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

                if is_empty:
                    item.setForeground(QColor("#ff5b5b"))
                    item.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))

                self._table.setItem(row_i, col_i, item)

        # Update footer note
        self._rows_note.setText(
            f"Showing {min(100, self._total):,} of {self._total:,} rows  ·  "
            f"{len(self._headers)} columns detected"
        )

        # Update stats bar
        stats_bar = self.findChild(QWidget, "previewStatsBar")
        if stats_bar:
            layout = stats_bar.layout()
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            missing_accent = "#ff5b5b" if self._missing > 0 else "#34d399"
            dupes_accent = "#f5b335" if self._dupes > 0 else "#34d399"

            layout.addWidget(_stat_tile(f"{self._total:,}", "Total Rows", self._accent))
            layout.addWidget(_stat_tile(str(len(self._headers)), "Columns", self._accent))
            layout.addWidget(_stat_tile(str(self._missing), "Missing Values", missing_accent))
            layout.addWidget(_stat_tile(str(self._dupes), "Duplicate Rows", dupes_accent))
            layout.addWidget(_stat_tile(
                f"{min(100, self._total):,} / {self._total:,}",
                "Rows Previewed",
                "rgba(255,255,255,0.6)",
            ))

    # ------------------------------------------------------------------
    # Table
    # ------------------------------------------------------------------

    def _build_table(self) -> QTableWidget:
        table = QTableWidget()
        table.setObjectName("previewTable")
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setHighlightSections(False)
        table.setShowGrid(False)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        if not self._headers:
            table.setColumnCount(1)
            table.setRowCount(1)
            table.setHorizontalHeaderLabels(["Error"])
            table.setItem(0, 0, QTableWidgetItem("No data to display."))
            return table

        table.setColumnCount(len(self._headers))
        table.setRowCount(len(self._rows))
        table.setHorizontalHeaderLabels(self._headers)

        expected_fields = set(self._config.get("fields", []))

        for col, h in enumerate(self._headers):
            item = table.horizontalHeaderItem(col)
            if item and expected_fields and h not in expected_fields:
                item.setForeground(QColor("#f5b335"))

        for row_i, row in enumerate(self._rows):
            for col_i, cell in enumerate(row):
                value = cell.strip()
                is_empty = value == ""

                item = QTableWidgetItem("—" if is_empty else value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

                if is_empty:
                    item.setForeground(QColor("#ff5b5b"))
                    item.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))

                table.setItem(row_i, col_i, item)

        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.setMinimumHeight(300)

        return table

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: rgba(255,255,255,0.07); margin: 0;")
        return line

    def _banner(self, text: str, level: str = "warning") -> QFrame:
        colors = {
            "warning": ("rgba(245,179,53,0.10)",  "rgba(245,179,53,0.35)", "#e8c97a"),
            "danger":  ("rgba(255,91,91,0.10)",   "rgba(255,91,91,0.35)",  "#ff7b7b"),
        }
        bg, border, fg = colors.get(level, colors["warning"])

        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {bg};
                border-left: 3px solid {border};
                border-radius: 0px;
                margin: 0px;
                padding: 0px;
            }}
        """)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(24, 10, 24, 10)

        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {fg}; font-size: 12px; background: transparent;")
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        return frame

    # ------------------------------------------------------------------
    # Styles
    # ------------------------------------------------------------------

    def _apply_styles(self):
        accent = self._accent
        self.setStyleSheet(f"""
            #previewCard {{
                background-color: #13172a;
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 16px;
            }}
            #previewTitleBar {{
                background: transparent;
            }}
            #previewTitle {{
                color: #e8eaf0;
                font-size: 16px;
                font-weight: bold;
                background: transparent;
            }}
            #previewSubtitle {{
                color: rgba(255,255,255,0.4);
                font-size: 12px;
                background: transparent;
            }}
            #previewCloseBtn {{
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 8px;
                color: rgba(255,255,255,0.5);
                font-size: 13px;
                padding: 0;
            }}
            #previewCloseBtn:hover {{
                background: rgba(255,255,255,0.12);
                color: white;
            }}
            #previewStatsBar {{
                background: transparent;
            }}
            #previewTableWrapper {{
                background: transparent;
            }}
            #previewTableLabel {{
                color: rgba(255,255,255,0.35);
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1px;
                background: transparent;
            }}
            #previewTable {{
                background-color: transparent;
                border: none;
                gridline-color: transparent;
                color: rgba(255,255,255,0.80);
                font-size: 12px;
                alternate-background-color: rgba(255,255,255,0.025);
                selection-background-color: rgba(79,140,255,0.18);
                selection-color: white;
            }}
            #previewTable QHeaderView::section {{
                background-color: rgba(255,255,255,0.05);
                color: rgba(255,255,255,0.5);
                font-size: 11px;
                font-weight: bold;
                border: none;
                border-right: 1px solid rgba(255,255,255,0.06);
                padding: 8px 10px;
            }}
            #previewTable QHeaderView::section:last {{
                border-right: none;
            }}
            #previewTable QScrollBar:vertical {{
                background: transparent;
                width: 8px;
            }}
            #previewTable QScrollBar::handle:vertical {{
                background: rgba(255,255,255,0.15);
                border-radius: 4px;
                min-height: 30px;
            }}
            #previewTable QScrollBar::handle:vertical:hover {{
                background: rgba(255,255,255,0.28);
            }}
            #previewTable QScrollBar:horizontal {{
                background: transparent;
                height: 8px;
            }}
            #previewTable QScrollBar::handle:horizontal {{
                background: rgba(255,255,255,0.15);
                border-radius: 4px;
            }}
            #previewTable QScrollBar::add-line:vertical,
            #previewTable QScrollBar::sub-line:vertical,
            #previewTable QScrollBar::add-line:horizontal,
            #previewTable QScrollBar::sub-line:horizontal {{
                height: 0; width: 0;
            }}
            #previewFooter {{
                background: transparent;
            }}
            #previewFooterNote {{
                color: rgba(255,255,255,0.35);
                font-size: 12px;
                background: transparent;
            }}
            #previewCleanBtn {{
                background-color: rgba(245,179,53,0.10);
                border: 1px solid rgba(245,179,53,0.35);
                border-radius: 8px;
                color: #f5b335;
                font-size: 12px;
                font-weight: 600;
                padding: 8px 18px;
            }}
            #previewCleanBtn:hover {{
                background-color: rgba(245,179,53,0.20);
            }}
            #previewCancelBtn {{
                background-color: transparent;
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px;
                color: rgba(255,255,255,0.55);
                font-size: 12px;
                padding: 8px 18px;
            }}
            #previewCancelBtn:hover {{
                background-color: rgba(255,255,255,0.06);
                color: white;
            }}
            #previewContinueBtn {{
                background-color: {accent};
                border: none;
                border-radius: 8px;
                color: white;
                font-size: 12px;
                font-weight: 600;
                padding: 8px 22px;
            }}
            #previewContinueBtn:hover {{
                background-color: rgba(79,140,255,0.85);
            }}
            #previewContinueBtn:disabled {{
                background-color: rgba(255,255,255,0.08);
                color: rgba(255,255,255,0.25);
            }}
        """)