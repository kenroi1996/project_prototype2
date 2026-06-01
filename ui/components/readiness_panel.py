from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QProgressBar,
)
from PyQt6.QtCore import Qt

from services.data_store import DataStore


# =====================================
# READINESS PANEL WIDGET
# =====================================

class ReadinessPanelWidget(QFrame):
    """
    Live data source readiness panel.
    Auto-updates whenever any portal saves data to DataStore.

    Usage
    -----
        panel = ReadinessPanelWidget(
            on_ready_callback=lambda: self._navigate_to_merge()
        )
        layout.addWidget(panel)
    """

    PORTAL_META = {
        "mis":       ("MIS",       "Academic Records",    "#4f8cff"),
        "sao":       ("SAO",       "Student Affairs",     "#34d399"),
        "guidance":  ("Guidance",  "Psych & Counseling",  "#f59e0b"),
        "registrar": ("Registrar", "Biographical Data",   "#a78bfa"),
    }

    def __init__(self, on_ready_callback=None, parent=None):
        super().__init__(parent)
        self._on_ready_callback = on_ready_callback
        self._build_ui()
        DataStore.get().add_listener(self._on_store_updated)
        self._refresh()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.setObjectName("readinessCard")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        # ── Header row ───────────────────────────────────────────────
        header_row = QHBoxLayout()
        header_row.setSpacing(10)

        title_col = QVBoxLayout()
        title_col.setSpacing(3)

        title = QLabel("DATA SOURCE READINESS")
        title.setObjectName("readinessSectionTitle")

        self._summary_lbl = QLabel("Waiting for uploads…")
        self._summary_lbl.setObjectName("readinessSummaryLabel")

        title_col.addWidget(title)
        title_col.addWidget(self._summary_lbl)

        # Overall progress bar on the right
        prog_col = QVBoxLayout()
        prog_col.setSpacing(4)
        prog_col.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self._prog_pct = QLabel("0 / 4")
        self._prog_pct.setObjectName("readinessPctLabel")
        self._prog_pct.setAlignment(Qt.AlignmentFlag.AlignRight)

        self._progress = QProgressBar()
        self._progress.setRange(0, 4)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        self._progress.setFixedWidth(140)
        self._progress.setObjectName("readinessProgress")

        prog_col.addWidget(self._prog_pct)
        prog_col.addWidget(self._progress)

        header_row.addLayout(title_col, 1)
        header_row.addLayout(prog_col)
        root.addLayout(header_row)

        # Divider
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("color: rgba(255,255,255,0.07);")
        root.addWidget(div)

        # ── Portal rows ──────────────────────────────────────────────
        self._portal_widgets: dict = {}

        portals_layout = QVBoxLayout()
        portals_layout.setSpacing(8)

        for key, (short, desc, color) in self.PORTAL_META.items():
            row = self._build_portal_row(key, short, desc, color)
            portals_layout.addLayout(row["layout"])
            self._portal_widgets[key] = row

        root.addLayout(portals_layout)

        # Divider
        div2 = QFrame()
        div2.setFrameShape(QFrame.Shape.HLine)
        div2.setStyleSheet("color: rgba(255,255,255,0.07);")
        root.addWidget(div2)

        # ── Proceed button ───────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._hint_lbl = QLabel(
            "Upload and clean data from all 4 portals to proceed."
        )
        self._hint_lbl.setObjectName("readinessHintLabel")

        self._proceed_btn = QPushButton("Proceed to Data Merge & Pipeline  →")
        self._proceed_btn.setObjectName("readinessProceedBtnLocked")
        self._proceed_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._proceed_btn.setEnabled(False)
        self._proceed_btn.setFixedHeight(38)
        self._proceed_btn.clicked.connect(self._on_proceed)

        btn_row.addWidget(self._hint_lbl, 1)
        btn_row.addWidget(self._proceed_btn)
        root.addLayout(btn_row)

    def _build_portal_row(self, key, short, desc, color) -> dict:
        layout = QHBoxLayout()
        layout.setSpacing(12)

        # Status dot
        dot = QLabel("●")
        dot.setFixedWidth(14)
        dot.setStyleSheet("color: rgba(255,255,255,0.18); font-size: 11px;")

        # Labels
        label_col = QVBoxLayout()
        label_col.setSpacing(1)

        name_lbl = QLabel(f"{short}  —  {desc}")
        name_lbl.setObjectName("readinessPortalName")

        detail_lbl = QLabel("Pending upload")
        detail_lbl.setObjectName("readinessPortalDetail")

        label_col.addWidget(name_lbl)
        label_col.addWidget(detail_lbl)

        # Status badge
        badge = QLabel("Pending")
        badge.setObjectName("readinessBadgePending")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedWidth(80)

        layout.addWidget(dot)
        layout.addLayout(label_col, 1)
        layout.addWidget(badge)

        return {
            "layout":     layout,
            "dot":        dot,
            "detail_lbl": detail_lbl,
            "badge":      badge,
            "color":      color,
        }

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def _refresh(self):
        store     = DataStore.get()
        readiness = store.get_readiness()
        ready     = store.ready_count()
        all_ok    = store.all_portals_ready()

        for key, is_ready in readiness.items():
            w     = self._portal_widgets[key]
            color = w["color"]
            data  = store.get_portal(key)

            if is_ready and data:
                w["dot"].setStyleSheet(
                    f"color: #34d399; font-size: 11px;"
                )
                w["detail_lbl"].setText(
                    f"{data['row_count']:,} rows  ·  cleaned  ·  {data['timestamp']}"
                )
                w["detail_lbl"].setStyleSheet(
                    "color: #34d399; font-size: 11px; background: transparent;"
                )
                w["badge"].setText("✓  Ready")
                w["badge"].setObjectName("readinessBadgeReady")
                w["badge"].setStyleSheet("""
                    background-color: rgba(52,211,153,0.12);
                    border: 1px solid rgba(52,211,153,0.35);
                    border-radius: 10px;
                    color: #34d399;
                    font-size: 11px;
                    font-weight: 600;
                    padding: 3px 8px;
                """)
            else:
                w["dot"].setStyleSheet(
                    "color: rgba(255,255,255,0.18); font-size: 11px;"
                )
                w["detail_lbl"].setText("Pending upload")
                w["detail_lbl"].setStyleSheet(
                    "color: rgba(255,255,255,0.3); font-size: 11px; background: transparent;"
                )
                w["badge"].setText("Pending")
                w["badge"].setStyleSheet("""
                    background-color: rgba(255,255,255,0.04);
                    border: 1px solid rgba(255,255,255,0.10);
                    border-radius: 10px;
                    color: rgba(255,255,255,0.3);
                    font-size: 11px;
                    padding: 3px 8px;
                """)

        # Progress
        self._progress.setValue(ready)
        self._prog_pct.setText(f"{ready} / 4")

        color = "#34d399" if all_ok else "#4f8cff" if ready >= 2 else "#f5b335"
        self._progress.setStyleSheet(f"""
            QProgressBar {{
                background-color: rgba(255,255,255,0.08);
                border-radius: 3px;
                border: none;
            }}
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 3px;
            }}
        """)
        self._prog_pct.setStyleSheet(
            f"color: {color}; font-size: 12px; font-weight: bold; background: transparent;"
        )

        # Summary label
        if all_ok:
            self._summary_lbl.setText(
                "All sources ready. You may now proceed to Data Merge & Pipeline."
            )
            self._summary_lbl.setStyleSheet(
                "color: #34d399; font-size: 12px; background: transparent;"
            )
        else:
            missing = [
                self.PORTAL_META[k][0]
                for k, v in readiness.items()
                if not v
            ]
            self._summary_lbl.setText(
                f"Waiting for: {', '.join(missing)}"
            )
            self._summary_lbl.setStyleSheet(
                "color: rgba(255,255,255,0.4); font-size: 12px; background: transparent;"
            )

        # Proceed button
        self._proceed_btn.setEnabled(all_ok)

        if all_ok:
            self._proceed_btn.setObjectName("readinessProceedBtnReady")
            self._proceed_btn.setStyleSheet("""
                QPushButton {
                    background-color: #34d399;
                    border: none;
                    border-radius: 8px;
                    color: #0d1117;
                    font-size: 12px;
                    font-weight: 700;
                    padding: 0 20px;
                }
                QPushButton:hover {
                    background-color: #2ec48a;
                }
            """)
            self._hint_lbl.setText("All 4 sources uploaded and cleaned.")
            self._hint_lbl.setStyleSheet(
                "color: #34d399; font-size: 12px; background: transparent;"
            )
        else:
            self._proceed_btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(255,255,255,0.04);
                    border: 1px solid rgba(255,255,255,0.10);
                    border-radius: 8px;
                    color: rgba(255,255,255,0.25);
                    font-size: 12px;
                    font-weight: 600;
                    padding: 0 20px;
                }
            """)
            self._hint_lbl.setText(
                "Upload and clean data from all 4 portals to proceed."
            )
            self._hint_lbl.setStyleSheet(
                "color: rgba(255,255,255,0.35); font-size: 12px; background: transparent;"
            )

    # ------------------------------------------------------------------
    # Slot + Callback
    # ------------------------------------------------------------------

    def _on_store_updated(self, key: str):
        self._refresh()

    def _on_proceed(self):
        if self._on_ready_callback:
            self._on_ready_callback()