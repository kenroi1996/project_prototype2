"""
ui/dialogs/full_dataset_dialog.py
====================================
Full-screen modal dialog for viewing the complete unified/engineered dataset,
with client-side search and filtered CSV export.

Extracted verbatim from ui/pages/data_merge_pipeline_page.py — no logic changes.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QLineEdit, QDialog, QFileDialog,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont


class FullDatasetDialog(QDialog):
    """Full-screen modal dialog for viewing the complete unified dataset."""

    def __init__(self, headers, rows, parent=None):
        super().__init__(parent)
        self._headers = headers
        self._all_rows = list(rows)
        self._filtered_rows = list(rows)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)

        if parent:
            self.resize(parent.window().size())
        else:
            self.resize(1400, 900)

        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        self._container = QFrame(self)
        self._container.setObjectName("datasetDialogContainer")
        self._container.setGeometry(self.rect().adjusted(40, 40, -40, -40))

        layout = QVBoxLayout(self._container)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        header_row = QHBoxLayout()
        header_row.setSpacing(16)

        title = QLabel("Unified Dataset")
        title.setObjectName("datasetDialogTitle")
        self._title_lbl = title

        self._row_count_lbl = QLabel(f"{len(self._all_rows):,} rows")
        self._row_count_lbl.setObjectName("datasetDialogCount")

        self._search = QLineEdit()
        self._search.setObjectName("datasetDialogSearch")
        self._search.setPlaceholderText("Search by any column…")
        self._search.textChanged.connect(self._on_search)

        header_row.addWidget(title)
        header_row.addWidget(self._row_count_lbl)
        header_row.addStretch()
        header_row.addWidget(self._search, 1)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("datasetDialogCloseBtn")
        close_btn.setFixedSize(36, 36)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.close)

        header_row.addWidget(close_btn)
        layout.addLayout(header_row)

        self._table = QTableWidget()
        self._table.setObjectName("datasetDialogTable")
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setHighlightSections(False)
        self._table.setShowGrid(False)
        self._table.setColumnCount(len(self._headers))
        self._table.setHorizontalHeaderLabels(self._headers)

        self._populate_table(self._filtered_rows)
        layout.addWidget(self._table, 1)

        footer = QHBoxLayout()
        self._status_lbl = QLabel(f"Showing all {len(self._all_rows):,} rows")
        self._status_lbl.setObjectName("datasetDialogStatus")
        footer.addWidget(self._status_lbl)
        footer.addStretch()

        export_btn = QPushButton("⬇ Export Filtered CSV")
        export_btn.setObjectName("datasetDialogExportBtn")
        export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        export_btn.clicked.connect(self._export_csv)
        footer.addWidget(export_btn)

        layout.addLayout(footer)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_container') and self._container:
            self._container.setGeometry(self.rect().adjusted(40, 40, -40, -40))

    def _setup_title(self, title: str):
        try:
            container_layout = self._container.layout()
            header_layout = container_layout.itemAt(0).layout()
            title_lbl = header_layout.itemAt(0).widget()
            if isinstance(title_lbl, QLabel):
                title_lbl.setText(title)
        except Exception:
            pass

    def _populate_table(self, rows):
        self._table.setRowCount(len(rows))
        for row_i, row in enumerate(rows):
            for col_i, cell in enumerate(row):
                value = str(cell).strip()
                is_empty = value == ""
                item = QTableWidgetItem("—" if is_empty else value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                if is_empty:
                    item.setForeground(QColor("#f5b335"))
                    item.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
                self._table.setItem(row_i, col_i, item)

        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)

    def _on_search(self, text: str):
        try:
            text = text.lower().strip()
            print(f">>> Search text: '{text}'")

            if not text:
                self._filtered_rows = list(self._all_rows)
            else:
                self._filtered_rows = []
                for row_idx, row in enumerate(self._all_rows):
                    try:
                        if not hasattr(row, '__iter__'):
                            print(f"  ✗ Row {row_idx} is not iterable: {type(row)} = {row}")
                            continue

                        row_matches = any(
                            text in str(cell).lower()
                            for cell in row
                        )
                        if row_matches:
                            self._filtered_rows.append(row)
                    except Exception as row_e:
                        print(f"  ✗ Row {row_idx} failed: {row_e}")
                        continue

            print(f"  ✓ Filtered: {len(self._filtered_rows)} rows")
            self._populate_table(self._filtered_rows)
            self._row_count_lbl.setText(f"{len(self._filtered_rows):,} rows")
            self._status_lbl.setText(
                f"Showing {len(self._filtered_rows):,} of {len(self._all_rows):,} rows"
                if text else f"Showing all {len(self._all_rows):,} rows"
            )
        except Exception as e:
            print(f">>> SEARCH CRASH: {e}")
            import traceback
            traceback.print_exc()

    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Filtered Dataset",
            "filtered_dataset.csv",
            "CSV Files (*.csv)",
        )
        if not path:
            return
        try:
            import csv
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(self._headers)
                writer.writerows(self._filtered_rows)
            self._status_lbl.setText(f"✓ Exported to {path.split('/')[-1]}")
        except Exception as e:
            self._status_lbl.setText(f"Export failed: {e}")

    def _apply_styles(self):
        self.setStyleSheet("""
            #datasetDialogContainer {
                background-color: #13172a;
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 16px;
            }
            #datasetDialogTitle {
                color: #e8eaf0;
                font-size: 18px;
                font-weight: bold;
                background: transparent;
            }
            #datasetDialogCount {
                color: rgba(255,255,255,0.4);
                font-size: 13px;
                background: transparent;
            }
            #datasetDialogSearch {
                background-color: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 10px;
                color: #e8eaf0;
                font-size: 13px;
                padding: 10px 14px;
            }
            #datasetDialogSearch:focus {
                border-color: rgba(79,140,255,0.5);
            }
            #datasetDialogCloseBtn {
                background-color: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 10px;
                color: rgba(255,255,255,0.6);
                font-size: 16px;
                font-weight: bold;
            }
            #datasetDialogCloseBtn:hover {
                background-color: rgba(255,91,91,0.15);
                border-color: rgba(255,91,91,0.3);
                color: #ff5b5b;
            }
            #datasetDialogTable {
                background-color: transparent;
                border: none;
                color: rgba(255,255,255,0.85);
                font-size: 12px;
                alternate-background-color: rgba(255,255,255,0.02);
                selection-background-color: rgba(79,140,255,0.15);
                selection-color: white;
            }
            #datasetDialogTable QHeaderView::section {
                background-color: rgba(255,255,255,0.06);
                color: rgba(255,255,255,0.5);
                font-size: 11px;
                font-weight: bold;
                border: none;
                border-right: 1px solid rgba(255,255,255,0.06);
                padding: 10px 12px;
            }
            #datasetDialogTable QHeaderView::section:last {
                border-right: none;
            }
            #datasetDialogStatus {
                color: rgba(255,255,255,0.35);
                font-size: 12px;
                background: transparent;
            }
            #datasetDialogExportBtn {
                background-color: rgba(79,140,255,0.12);
                border: 1px solid rgba(79,140,255,0.30);
                border-radius: 8px;
                color: #6eb5ff;
                font-size: 12px;
                font-weight: 600;
                padding: 9px 18px;
            }
            #datasetDialogExportBtn:hover {
                background-color: rgba(79,140,255,0.22);
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255,255,255,0.12);
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255,255,255,0.22);
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0;
            }
        """)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        super().keyPressEvent(event)