"""
ui/dialogs/report_customization.py
====================================
Combined report customization dialog for EarlyAlert.

Shows two tabs — one for the Cohort Risk Summary report and one for
the Intervention report. Each tab provides:
  - Custom report title input
  - Section toggles (include/exclude)
  - Data filters (college, risk level, mode, etc.)

Usage
-----
    from ui.dialogs.report_customization import ReportCustomizationDialog

    dlg = ReportCustomizationDialog(
        parent=self,
        colleges=["CTE", "CBAA", ...],   # unique colleges in the data
        programs=["BSIT", "BSBA", ...],  # unique programs in the data
    )
    if dlg.exec() == QDialog.DialogCode.Accepted:
        cohort_cfg     = dlg.cohort_config()
        interv_cfg     = dlg.intervention_config()

Config objects
--------------
Both configs are plain dicts with the following keys:

CohortReportConfig keys:
    title               str   — custom report title (empty = default)
    include_summary     bool  — Executive Summary stat cards
    include_distribution bool — Risk Distribution bar + legend
    include_tier_table  bool  — Indicator Averages by Risk Tier
    include_college     bool  — Risk Breakdown by College
    include_programs    bool  — Top 10 Programs by At-Risk Count
    filter_colleges     list  — [] means all colleges
    filter_risk_levels  list  — [] means all levels; values: "high_risk" etc.

InterventionReportConfig keys:
    title               str   — custom report title
    include_per_student bool  — Per-Student Interventions section
    include_cohort      bool  — Cohort Systemic Issues section
    filter_risk_labels  list  — [] means all; e.g. ["High", "Medium"]
    filter_mode         str   — "" | "per_student" | "cohort"
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFrame, QCheckBox, QLineEdit, QTabWidget, QScrollArea, QComboBox,
)
from PyQt6.QtCore import Qt


# ── Shared style tokens ───────────────────────────────────────────────────────

_CARD_BG   = "#13172a"
_BORDER    = "rgba(255,255,255,0.10)"
_TEXT      = "#e8eaf0"
_MUTED     = "rgba(255,255,255,0.40)"
_ACCENT    = "#4f8cff"
_GREEN     = "#34d399"
_SECTION   = "rgba(255,255,255,0.55)"


# ── Small reusable widgets ────────────────────────────────────────────────────

def _section_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setStyleSheet(
        f"color:{_SECTION}; font-size:10px; font-weight:700; "
        "letter-spacing:0.8px; background:transparent;")
    return lbl


def _divider() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet("color:rgba(255,255,255,0.07);")
    return f


def _toggle_row(label: str, sublabel: str = "", checked: bool = True) -> tuple[QWidget, QCheckBox]:
    """Returns (row_widget, checkbox) so callers can read .isChecked()."""
    row = QWidget()
    row.setStyleSheet("background:transparent;")
    lo = QHBoxLayout(row)
    lo.setContentsMargins(0, 6, 0, 6)
    lo.setSpacing(12)

    cb = QCheckBox()
    cb.setChecked(checked)
    cb.setFixedSize(18, 18)
    cb.setStyleSheet("""
        QCheckBox::indicator {
            width:16px; height:16px;
            border:2px solid rgba(255,255,255,0.20);
            border-radius:4px;
            background:rgba(255,255,255,0.05);
        }
        QCheckBox::indicator:checked {
            background:#4f8cff;
            border-color:#4f8cff;
        }
        QCheckBox::indicator:hover {
            border-color:rgba(79,140,255,0.60);
        }
    """)

    text_col = QVBoxLayout()
    text_col.setSpacing(1)

    main_lbl = QLabel(label)
    main_lbl.setStyleSheet(f"color:{_TEXT}; font-size:12px; background:transparent;")
    text_col.addWidget(main_lbl)

    if sublabel:
        sub = QLabel(sublabel)
        sub.setStyleSheet(f"color:{_MUTED}; font-size:10px; background:transparent;")
        text_col.addWidget(sub)

    lo.addWidget(cb)
    lo.addLayout(text_col, 1)
    return row, cb


def _combo(items: list[str], current: str = "") -> QComboBox:
    c = QComboBox()
    c.setObjectName("rptCombo")
    c.addItems(items)
    if current and current in items:
        c.setCurrentText(current)
    c.setCursor(Qt.CursorShape.PointingHandCursor)
    return c


def _chip_group(options: list[str], accent: str = _ACCENT) -> tuple[QWidget, dict[str, QPushButton]]:
    """
    Returns (host_widget, {label: btn}) for a toggleable chip group.
    All chips start selected.
    """
    host = QWidget()
    host.setStyleSheet("background:transparent;")
    lo = QHBoxLayout(host)
    lo.setContentsMargins(0, 0, 0, 0)
    lo.setSpacing(6)

    btns: dict[str, QPushButton] = {}
    for opt in options:
        btn = QPushButton(opt)
        btn.setCheckable(True)
        btn.setChecked(True)
        btn.setFixedHeight(26)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setObjectName("rptChip")
        btn.setProperty("accent", accent)
        btn.toggled.connect(lambda _, b=btn, a=accent: _update_chip(b, a))
        _update_chip(btn, accent)
        btns[opt] = btn
        lo.addWidget(btn)

    lo.addStretch()
    return host, btns


def _update_chip(btn: QPushButton, accent: str):
    if btn.isChecked():
        btn.setStyleSheet(f"""
            QPushButton#rptChip {{
                background:{accent};
                border:none; border-radius:6px;
                color:white; font-size:11px;
                font-weight:600; padding:0 12px;
            }}
        """)
    else:
        btn.setStyleSheet("""
            QPushButton#rptChip {
                background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.12);
                border-radius:6px; color:rgba(255,255,255,0.40);
                font-size:11px; font-weight:600; padding:0 12px;
            }
            QPushButton#rptChip:hover {
                background:rgba(255,255,255,0.10);
                color:rgba(255,255,255,0.70);
            }
        """)


# ── Tab: Cohort Report ────────────────────────────────────────────────────────

class _CohortTab(QWidget):
    def __init__(self, colleges: list[str], parent=None):
        super().__init__(parent)
        self._colleges = colleges
        self._college_chips: dict[str, QPushButton] = {}
        self._risk_chips:    dict[str, QPushButton] = {}
        self._build()

    def _build(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background:transparent; border:none; }")

        host = QWidget()
        host.setStyleSheet("background:transparent;")
        lo = QVBoxLayout(host)
        lo.setContentsMargins(4, 4, 12, 4)
        lo.setSpacing(14)

        # ── Custom title ──────────────────────────────────────────────
        lo.addWidget(_section_label("Report Title"))
        self._title_input = QLineEdit()
        self._title_input.setObjectName("rptTitleInput")
        self._title_input.setPlaceholderText(
            "Cohort Risk Summary Report  (leave blank for default)")
        self._title_input.setFixedHeight(36)
        lo.addWidget(self._title_input)

        lo.addWidget(_divider())

        # ── Sections ──────────────────────────────────────────────────
        lo.addWidget(_section_label("Sections to Include"))

        sections = [
            ("Executive Summary",           "Stat cards — total, high, moderate, low students",        True),
            ("Risk Distribution",           "Stacked bar chart with legend",                           True),
            ("Indicator Averages by Tier",  "Avg risk score, entrance exam, HS GPA per risk level",    True),
            ("Risk Breakdown by College",   "Per-college high / moderate / low counts",                True),
            ("Top Programs by At-Risk",     "Top 10 programs ranked by at-risk student count",         True),
        ]
        self._section_cbs: dict[str, QCheckBox] = {}
        for label, sub, default in sections:
            row, cb = _toggle_row(label, sub, default)
            self._section_cbs[label] = cb
            lo.addWidget(row)

        lo.addWidget(_divider())

        # ── Filter: colleges ──────────────────────────────────────────
        lo.addWidget(_section_label("Filter by College  (all selected = include all)"))
        if self._colleges:
            host_w, self._college_chips = _chip_group(self._colleges, _ACCENT)
            lo.addWidget(host_w)
        else:
            lo.addWidget(QLabel("No college data available."))

        lo.addSpacing(4)

        # ── Filter: risk levels ───────────────────────────────────────
        lo.addWidget(_section_label("Filter by Risk Level"))
        risk_host, self._risk_chips = _chip_group(
            ["High Risk", "Moderate Risk", "Low Risk"], "#ff5b5b")
        lo.addWidget(risk_host)

        lo.addStretch()
        scroll.setWidget(host)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # ── Public API ────────────────────────────────────────────────────

    def config(self) -> dict:
        # Colleges: empty list = all
        selected_colleges = [
            col for col, btn in self._college_chips.items()
            if btn.isChecked()
        ]
        all_colleges = len(selected_colleges) == len(self._college_chips)

        # Risk levels
        _risk_map = {
            "High Risk":     "high_risk",
            "Moderate Risk": "moderate_risk",
            "Low Risk":      "low_risk",
        }
        selected_risk = [
            _risk_map[lbl] for lbl, btn in self._risk_chips.items()
            if btn.isChecked()
        ]
        all_risk = len(selected_risk) == len(self._risk_chips)

        return {
            "title":                self._title_input.text().strip(),
            "include_summary":      self._section_cbs["Executive Summary"].isChecked(),
            "include_distribution": self._section_cbs["Risk Distribution"].isChecked(),
            "include_tier_table":   self._section_cbs["Indicator Averages by Tier"].isChecked(),
            "include_college":      self._section_cbs["Risk Breakdown by College"].isChecked(),
            "include_programs":     self._section_cbs["Top Programs by At-Risk"].isChecked(),
            "filter_colleges":      [] if all_colleges else selected_colleges,
            "filter_risk_levels":   [] if all_risk else selected_risk,
        }


# ── Tab: Intervention Report ──────────────────────────────────────────────────

class _InterventionTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background:transparent; border:none; }")

        host = QWidget()
        host.setStyleSheet("background:transparent;")
        lo = QVBoxLayout(host)
        lo.setContentsMargins(4, 4, 12, 4)
        lo.setSpacing(14)

        # ── Custom title ──────────────────────────────────────────────
        lo.addWidget(_section_label("Report Title"))
        self._title_input = QLineEdit()
        self._title_input.setObjectName("rptTitleInput")
        self._title_input.setPlaceholderText(
            "AI Intervention Recommendations Report  (leave blank for default)")
        self._title_input.setFixedHeight(36)
        lo.addWidget(self._title_input)

        lo.addWidget(_divider())

        # ── Sections ──────────────────────────────────────────────────
        lo.addWidget(_section_label("Sections to Include"))

        sections = [
            ("Per-Student Interventions",
             "Individual AI recommendations for each at-risk student", True),
            ("Cohort Systemic Issues",
             "AI-identified patterns and systemic recommendations",     True),
        ]
        self._section_cbs: dict[str, QCheckBox] = {}
        for label, sub, default in sections:
            row, cb = _toggle_row(label, sub, default)
            self._section_cbs[label] = cb
            lo.addWidget(row)

        lo.addWidget(_divider())

        # ── Filter: mode ──────────────────────────────────────────────
        lo.addWidget(_section_label("Filter by Intervention Type"))
        mode_host, self._mode_chips = _chip_group(
            ["Per Student", "Cohort"], _GREEN)
        lo.addWidget(mode_host)

        lo.addSpacing(4)

        # ── Filter: risk label ────────────────────────────────────────
        lo.addWidget(_section_label("Filter by Risk Level"))
        risk_host, self._risk_chips = _chip_group(
            ["High", "Medium", "Low"], "#ff5b5b")
        lo.addWidget(risk_host)

        lo.addStretch()
        scroll.setWidget(host)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # ── Public API ────────────────────────────────────────────────────

    def config(self) -> dict:
        # Mode filter
        mode_map = {"Per Student": "per_student", "Cohort": "cohort"}
        selected_modes = [
            mode_map[lbl] for lbl, btn in self._mode_chips.items()
            if btn.isChecked()
        ]
        # If both checked, no filter needed
        filter_mode = (
            "" if len(selected_modes) == 2
            else (selected_modes[0] if selected_modes else "per_student")
        )

        # Risk label filter
        selected_risk = [
            lbl for lbl, btn in self._risk_chips.items()
            if btn.isChecked()
        ]
        all_risk = len(selected_risk) == len(self._risk_chips)

        return {
            "title":                self._title_input.text().strip(),
            "include_per_student":  self._section_cbs["Per-Student Interventions"].isChecked(),
            "include_cohort":       self._section_cbs["Cohort Systemic Issues"].isChecked(),
            "filter_mode":          filter_mode,
            "filter_risk_labels":   [] if all_risk else selected_risk,
        }


# ══════════════════════════════════════════════════════════════════════════════
# Main dialog
# ══════════════════════════════════════════════════════════════════════════════

class ReportCustomizationDialog(QDialog):
    """
    Combined report customization dialog with two tabs.

    Parameters
    ----------
    colleges : list[str]
        Unique college names present in the current prediction data.
        Shown as filter chips on the Cohort tab.

    Usage
    -----
        dlg = ReportCustomizationDialog(parent=self, colleges=["CTE", ...])
        if dlg.exec() == QDialog.DialogCode.Accepted:
            c_cfg = dlg.cohort_config()
            i_cfg = dlg.intervention_config()
    """

    def __init__(self, parent=None, colleges: list[str] = None):
        super().__init__(parent)
        self.setModal(True)
        self.setFixedSize(580, 620)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._drag_pos = None
        self._colleges = sorted(colleges or [])

        self._build_ui()
        self._apply_styles()

    # ── Drag support ──────────────────────────────────────────────────

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
        self._drag_pos = None
        super().mouseReleaseEvent(e)

    # ── Build ─────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)

        card = QFrame()
        card.setObjectName("rptCard")
        outer.addWidget(card)

        root = QVBoxLayout(card)
        root.setContentsMargins(28, 22, 28, 24)
        root.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(4)

        title = QLabel("Export Report")
        title.setStyleSheet(
            f"color:{_TEXT}; font-size:15px; font-weight:bold; background:transparent;")

        sub = QLabel(
            "Customize which sections and data to include before exporting.")
        sub.setStyleSheet(
            f"color:{_MUTED}; font-size:11px; background:transparent;")

        title_col.addWidget(title)
        title_col.addWidget(sub)
        hdr.addLayout(title_col, 1)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("rptCloseBtn")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.reject)
        hdr.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignTop)

        root.addLayout(hdr)
        root.addSpacing(16)
        root.addWidget(_divider())
        root.addSpacing(14)

        # ── Tabs ──────────────────────────────────────────────────────
        self._tabs = QTabWidget()
        self._tabs.setObjectName("rptTabs")

        self._cohort_tab = _CohortTab(self._colleges)
        self._interv_tab = _InterventionTab()

        self._tabs.addTab(self._cohort_tab, "📊  Cohort Report")
        self._tabs.addTab(self._interv_tab, "🤖  Intervention Report")

        root.addWidget(self._tabs, 1)
        root.addSpacing(16)
        root.addWidget(_divider())
        root.addSpacing(14)

        # ── Footer ────────────────────────────────────────────────────
        footer = QHBoxLayout()
        footer.setSpacing(10)

        reset_btn = QPushButton("↺  Reset Defaults")
        reset_btn.setObjectName("rptResetBtn")
        reset_btn.setFixedHeight(36)
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.clicked.connect(self._reset_current_tab)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("rptCancelBtn")
        cancel_btn.setFixedHeight(36)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)

        self._export_btn = QPushButton("📄  Export Report")
        self._export_btn.setObjectName("rptExportBtn")
        self._export_btn.setFixedHeight(36)
        self._export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_btn.clicked.connect(self._on_export)

        footer.addWidget(reset_btn)
        footer.addStretch()
        footer.addWidget(cancel_btn)
        footer.addWidget(self._export_btn)

        root.addLayout(footer)

    # ── Slots ─────────────────────────────────────────────────────────

    def _reset_current_tab(self):
        """Rebuild whichever tab is currently active to restore defaults."""
        idx = self._tabs.currentIndex()
        if idx == 0:
            new_tab = _CohortTab(self._colleges)
            self._tabs.removeTab(0)
            self._tabs.insertTab(0, new_tab, "📊  Cohort Report")
            self._tabs.setCurrentIndex(0)
            self._cohort_tab = new_tab
        else:
            new_tab = _InterventionTab()
            self._tabs.removeTab(1)
            self._tabs.insertTab(1, new_tab, "🤖  Intervention Report")
            self._tabs.setCurrentIndex(1)
            self._interv_tab = new_tab

    def _on_export(self):
        self.accept()

    # ── Public API ────────────────────────────────────────────────────

    def cohort_config(self) -> dict:
        """Return the cohort report configuration dict."""
        return self._cohort_tab.config()

    def intervention_config(self) -> dict:
        """Return the intervention report configuration dict."""
        return self._interv_tab.config()

    # ── Styles ────────────────────────────────────────────────────────

    def _apply_styles(self):
        self.setStyleSheet(f"""
            QFrame#rptCard {{
                background:{_CARD_BG};
                border:1px solid {_BORDER};
                border-radius:16px;
            }}

            /* Close button */
            QPushButton#rptCloseBtn {{
                background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.10);
                border-radius:7px;
                color:rgba(255,255,255,0.35);
                font-size:13px; font-weight:bold;
            }}
            QPushButton#rptCloseBtn:hover {{
                background:rgba(255,91,91,0.15);
                border-color:rgba(255,91,91,0.35);
                color:#ff5b5b;
            }}

            /* Tabs */
            QTabWidget#rptTabs::pane {{
                background:rgba(255,255,255,0.02);
                border:1px solid rgba(255,255,255,0.07);
                border-radius:10px;
                padding:12px;
            }}
            QTabBar::tab {{
                background:transparent;
                color:rgba(255,255,255,0.40);
                font-size:12px; font-weight:600;
                padding:8px 20px;
                border:none;
                border-bottom:2px solid transparent;
            }}
            QTabBar::tab:selected {{
                color:{_TEXT};
                border-bottom:2px solid {_ACCENT};
            }}
            QTabBar::tab:hover:!selected {{
                color:rgba(255,255,255,0.70);
            }}

            /* Title input */
            QLineEdit#rptTitleInput {{
                background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.12);
                border-radius:8px;
                color:{_TEXT};
                font-size:12px;
                padding:0 12px;
            }}
            QLineEdit#rptTitleInput:focus {{
                border-color:rgba(79,140,255,0.45);
            }}

            /* Combo */
            QComboBox#rptCombo {{
                background:rgba(255,255,255,0.06);
                border:1px solid rgba(255,255,255,0.12);
                border-radius:7px;
                color:{_TEXT};
                font-size:12px;
                padding:5px 10px;
                min-height:30px;
            }}
            QComboBox#rptCombo:hover {{
                border-color:rgba(79,140,255,0.40);
            }}
            QComboBox#rptCombo::drop-down {{ border:none; width:16px; }}
            QComboBox#rptCombo QAbstractItemView {{
                background:#1a1f35; color:{_TEXT};
                selection-background-color:rgba(79,140,255,0.18);
            }}

            /* Footer buttons */
            QPushButton#rptResetBtn {{
                background:rgba(255,255,255,0.04);
                border:1px solid rgba(255,255,255,0.10);
                border-radius:8px;
                color:rgba(255,255,255,0.45);
                font-size:12px; padding:0 16px;
            }}
            QPushButton#rptResetBtn:hover {{
                background:rgba(255,255,255,0.09);
                color:rgba(255,255,255,0.75);
            }}
            QPushButton#rptCancelBtn {{
                background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.12);
                border-radius:8px;
                color:rgba(255,255,255,0.60);
                font-size:12px; font-weight:600; padding:0 20px;
            }}
            QPushButton#rptCancelBtn:hover {{
                background:rgba(255,255,255,0.10);
            }}
            QPushButton#rptExportBtn {{
                background:{_ACCENT};
                border:none; border-radius:8px;
                color:white; font-size:12px;
                font-weight:700; padding:0 24px;
            }}
            QPushButton#rptExportBtn:hover {{
                background:rgba(79,140,255,0.85);
            }}
        """)