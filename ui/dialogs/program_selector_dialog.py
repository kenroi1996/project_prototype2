"""
ui/dialogs/program_selector_dialog.py
=======================================
Multi-select program filter dialog shown before AI batch analysis starts.

Extracted verbatim from ui/pages/interventions_page.py — no logic changes.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QScrollArea, QDialog, QCheckBox,
)
from PyQt6.QtCore import Qt


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