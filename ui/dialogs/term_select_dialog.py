"""
ui/dialogs/term_select_dialog.py
==================================
Academic-term picker dialog shown before exporting an intervention report.

Extracted verbatim from ui/pages/interventions_page.py — no logic changes.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QFrame, QComboBox, QDialog,
)
from PyQt6.QtCore import Qt

from services.data_store import DataStore


class _TermSelectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setModal(True); self.setFixedSize(420, 300)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._selected: tuple = ()
        self._build_ui(); self._apply_styles(); self._load_terms()

    def _build_ui(self):
        outer = QVBoxLayout(self); outer.setContentsMargins(16,16,16,16)
        card = QFrame(); card.setObjectName("termSelCard"); outer.addWidget(card)
        lo = QVBoxLayout(card); lo.setContentsMargins(28,24,28,24); lo.setSpacing(12)
        for text, style in [
            ("Export Intervention Report",
             "color:#e8eaf0; font-size:15px; font-weight:bold; background:transparent;"),
            ("Select the academic term to include in the report.",
             "color:rgba(255,255,255,0.40); font-size:11px; background:transparent;")]:
            lbl = QLabel(text); lbl.setStyleSheet(style); lbl.setWordWrap(True)
            lo.addWidget(lbl)
        div = QFrame(); div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("color:rgba(255,255,255,0.08);"); lo.addWidget(div)
        lbl = QLabel("Academic Term")
        lbl.setStyleSheet(
            "color:rgba(255,255,255,0.55); font-size:11px; font-weight:600; background:transparent;")
        lo.addWidget(lbl)
        self._term_combo = QComboBox(); self._term_combo.setObjectName("termSelCombo")
        self._term_combo.addItem("Loading…"); self._term_combo.setEnabled(False)
        lo.addWidget(self._term_combo)
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.35); font-size:10px; background:transparent;")
        lo.addWidget(self._status_lbl); lo.addStretch()
        br = QHBoxLayout(); br.setSpacing(10)
        cb = QPushButton("Cancel"); cb.setObjectName("termSelCancelBtn")
        cb.setFixedHeight(36); cb.setCursor(Qt.CursorShape.PointingHandCursor)
        cb.clicked.connect(self.reject)
        self._confirm_btn = QPushButton("Export  →")
        self._confirm_btn.setObjectName("termSelConfirmBtn"); self._confirm_btn.setFixedHeight(36)
        self._confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._confirm_btn.setEnabled(False); self._confirm_btn.clicked.connect(self._on_confirm)
        br.addWidget(cb); br.addStretch(); br.addWidget(self._confirm_btn); lo.addLayout(br)

    def _load_terms(self):
        conn = DataStore.get().db_conn
        if not conn:
            self._status_lbl.setText("No database connection."); return
        try:
            with conn.cursor() as cur:
                cur.execute("""SELECT DISTINCT academic_year, semester
                    FROM public.interventions ORDER BY academic_year DESC, semester DESC""")
                terms = cur.fetchall()
        except Exception as e:
            self._status_lbl.setText(f"Error: {e}"); return
        self._term_combo.clear()
        if not terms:
            self._term_combo.addItem("No records found")
            self._status_lbl.setText("No intervention records yet."); return
        self._term_combo.addItem("— Select a term —")
        for ay, sem in terms:
            sl = "1st Semester" if sem == 1 else "2nd Semester"
            self._term_combo.addItem(f"{sl}  ·  AY {ay}", userData=(ay, sem))
        self._term_combo.setEnabled(True)
        self._term_combo.currentIndexChanged.connect(self._on_term_changed)
        self._status_lbl.setText(f"{len(terms)} term(s) with records.")

    def _on_term_changed(self, idx):
        self._confirm_btn.setEnabled(
            self._term_combo.itemData(idx, Qt.ItemDataRole.UserRole) is not None)

    def _on_confirm(self):
        idx = self._term_combo.currentIndex()
        data = self._term_combo.itemData(idx, Qt.ItemDataRole.UserRole)
        if data: self._selected = data; self.accept()

    def selected_term(self) -> tuple:
        if not self._selected: return ("",0,"")
        ay, sem = self._selected
        return (ay, sem, f"{'1st' if sem==1 else '2nd'} Semester  AY {ay}")

    def _apply_styles(self):
        self.setStyleSheet("""
            #termSelCard { background:#13172a; border:1px solid rgba(255,255,255,0.10);
                border-radius:16px; }
            QComboBox#termSelCombo { background:rgba(255,255,255,0.06);
                border:1px solid rgba(255,255,255,0.14); border-radius:8px;
                color:#e8eaf0; font-size:13px; padding:8px 12px; min-height:36px; }
            QComboBox#termSelCombo:hover { border-color:rgba(52,211,153,0.40); }
            QComboBox#termSelCombo::drop-down { border:none; width:18px; }
            QComboBox#termSelCombo QAbstractItemView { background:#1a1f35; color:#e8eaf0;
                selection-background-color:rgba(52,211,153,0.18); }
            QPushButton#termSelCancelBtn { background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.12); border-radius:8px;
                color:rgba(255,255,255,0.60); font-size:12px; font-weight:600; padding:0 20px; }
            QPushButton#termSelCancelBtn:hover { background:rgba(255,255,255,0.10); }
            QPushButton#termSelConfirmBtn { background:#34d399; border:none;
                border-radius:8px; color:#0e1120; font-size:12px; font-weight:700; padding:0 24px; }
            QPushButton#termSelConfirmBtn:hover { background:rgba(52,211,153,0.85); }
            QPushButton#termSelConfirmBtn:disabled { background:rgba(255,255,255,0.06);
                color:rgba(255,255,255,0.25); }
        """)