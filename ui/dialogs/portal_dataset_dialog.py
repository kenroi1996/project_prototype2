"""View or edit the loaded portal dataset in a table dialog."""

import copy

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QMessageBox,
    QSizePolicy,
)
from PyQt6.QtCore import Qt

class PortalDatasetDialog(QDialog):
    """
    Show portal data in a table.

    readonly=True  → view only
    readonly=False → edit cells; Save persists to DataStore + database
    """

    def __init__(
        self,
        portal_title: str,
        headers: list,
        rows: list,
        accent: str = "#4f8cff",
        readonly: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self._accent = accent
        self._readonly = readonly
        self._orig_headers = list(headers)
        self._orig_rows = copy.deepcopy(rows)

        title_suffix = "View Dataset" if readonly else "Edit Dataset"
        self.setWindowTitle(f"{portal_title} — {title_suffix}")
        self.setMinimumSize(900, 520)
        self.resize(1000, 600)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        header_row = QHBoxLayout()
        title = QLabel(title_suffix.upper())
        title.setObjectName("portalDatasetTitle")
        self._meta_lbl = QLabel(
            f"{len(self._orig_rows):,} rows · {len(self._orig_headers)} columns"
        )
        self._meta_lbl.setObjectName("portalDatasetMeta")
        header_row.addWidget(title)
        header_row.addStretch()
        header_row.addWidget(self._meta_lbl)
        root.addLayout(header_row)

        if not readonly:
            hint = QLabel(
                "Double-click a cell to edit. Changes are saved to memory and "
                "PostgreSQL when you click Save Changes."
            )
            hint.setObjectName("portalDatasetHint")
            hint.setWordWrap(True)
            root.addWidget(hint)

        self._table = QTableWidget()
        self._table.setObjectName("portalDatasetTable")
        self._table.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        if readonly:
            self._table.setEditTriggers(
                QAbstractItemView.EditTrigger.NoEditTriggers
            )
        else:
            self._table.setEditTriggers(
                QAbstractItemView.EditTrigger.DoubleClicked
                | QAbstractItemView.EditTrigger.SelectedClicked
            )
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectItems
        )
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(True)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        root.addWidget(self._table, 1)

        self._populate_table(self._orig_headers, self._orig_rows)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        if not readonly:
            add_row_btn = QPushButton("+ Add Row")
            add_row_btn.setObjectName("portalDatasetSecondaryBtn")
            add_row_btn.clicked.connect(self._add_row)
            del_row_btn = QPushButton("− Delete Selected Rows")
            del_row_btn.setObjectName("portalDatasetDangerBtn")
            del_row_btn.clicked.connect(self._delete_selected_rows)
            btn_row.addWidget(add_row_btn)
            btn_row.addWidget(del_row_btn)
            btn_row.addStretch()

            save_btn = QPushButton("Save Changes")
            save_btn.setObjectName("portalDatasetPrimaryBtn")
            save_btn.clicked.connect(self._accept_save)
            btn_row.addWidget(save_btn)

        close_btn = QPushButton("Close" if readonly else "Cancel")
        close_btn.setObjectName("portalDatasetSecondaryBtn")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)

        root.addLayout(btn_row)
        self._apply_styles()

    def _apply_styles(self):
        a = self._accent
        self.setStyleSheet(f"""
            QDialog {{
                background-color: #13172a;
            }}
            #portalDatasetTitle {{
                color: #e8eaf0;
                font-size: 16px;
                font-weight: bold;
            }}
            #portalDatasetMeta, #portalDatasetHint {{
                color: rgba(255, 255, 255, 0.45);
                font-size: 12px;
            }}
            #portalDatasetTable {{
                background-color: rgba(0, 0, 0, 0.2);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
                color: #e8eaf0;
                gridline-color: rgba(255, 255, 255, 0.06);
                font-size: 12px;
            }}
            QHeaderView::section {{
                background-color: rgba(255, 255, 255, 0.06);
                color: rgba(255, 255, 255, 0.65);
                padding: 8px;
                border: none;
                font-weight: 600;
            }}
            QTableWidget::item:selected {{
                background-color: rgba(79, 140, 255, 0.25);
            }}
            #portalDatasetPrimaryBtn {{
                background-color: {a};
                border: none;
                border-radius: 8px;
                color: white;
                font-size: 12px;
                font-weight: 600;
                padding: 10px 22px;
            }}
            #portalDatasetPrimaryBtn:hover {{
                background-color: rgba(79, 140, 255, 0.85);
            }}
            #portalDatasetSecondaryBtn {{
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
                color: rgba(255, 255, 255, 0.8);
                font-size: 12px;
                padding: 10px 18px;
            }}
            #portalDatasetSecondaryBtn:hover {{
                background-color: rgba(255, 255, 255, 0.1);
            }}
            #portalDatasetDangerBtn {{
                background-color: rgba(255, 91, 91, 0.08);
                border: 1px solid rgba(255, 91, 91, 0.25);
                border-radius: 8px;
                color: #ff7b7b;
                font-size: 12px;
                padding: 10px 18px;
            }}
            #portalDatasetDangerBtn:hover {{
                background-color: rgba(255, 91, 91, 0.18);
            }}
        """)

    def _populate_table(self, headers: list, rows: list):
        self._table.clear()
        self._table.setColumnCount(len(headers))
        self._table.setRowCount(len(rows))
        self._table.setHorizontalHeaderLabels(headers)

        for row_i, row in enumerate(rows):
            for col_i in range(len(headers)):
                value = row[col_i] if col_i < len(row) else ""
                text = "" if value is None else str(value)
                item = QTableWidgetItem(text)
                if self._readonly:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row_i, col_i, item)

        self._meta_lbl.setText(f"{len(rows):,} rows · {len(headers)} columns")

    def _add_row(self):
        col_count = self._table.columnCount()
        row_i = self._table.rowCount()
        self._table.insertRow(row_i)
        for col_i in range(col_count):
            self._table.setItem(row_i, col_i, QTableWidgetItem(""))
        self._meta_lbl.setText(
            f"{self._table.rowCount():,} rows · {col_count} columns"
        )

    def _delete_selected_rows(self):
        selected = sorted(
            {idx.row() for idx in self._table.selectedIndexes()},
            reverse=True,
        )
        if not selected:
            QMessageBox.information(
                self,
                "No Selection",
                "Select one or more rows to delete.",
            )
            return

        reply = QMessageBox.question(
            self,
            "Delete Rows",
            f"Delete {len(selected)} row(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        for row_i in selected:
            self._table.removeRow(row_i)
        self._meta_lbl.setText(
            f"{self._table.rowCount():,} rows · "
            f"{self._table.columnCount()} columns"
        )

    def _collect_table_data(self) -> tuple[list, list]:
        headers = [
            self._table.horizontalHeaderItem(c).text()
            for c in range(self._table.columnCount())
        ]
        rows = []
        for r in range(self._table.rowCount()):
            row = []
            for c in range(self._table.columnCount()):
                item = self._table.item(r, c)
                row.append(item.text().strip() if item else "")
            rows.append(row)
        return headers, rows

    def _accept_save(self):
        headers, rows = self._collect_table_data()
        if not headers:
            QMessageBox.warning(self, "Invalid Data", "At least one column is required.")
            return

        empty_rows = sum(1 for row in rows if not any(cell.strip() for cell in row))
        if empty_rows == len(rows):
            QMessageBox.warning(self, "Invalid Data", "The dataset has no data rows.")
            return

        self._saved_headers = headers
        self._saved_rows = rows
        self.accept()

    @property
    def saved_headers(self) -> list:
        return getattr(self, "_saved_headers", self._orig_headers)

    @property
    def saved_rows(self) -> list:
        return getattr(self, "_saved_rows", self._orig_rows)
