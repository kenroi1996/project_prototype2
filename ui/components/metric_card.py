from PyQt6.QtWidgets import (
    QFrame, QLabel, QVBoxLayout, QHBoxLayout, QWidget,
)
from PyQt6.QtCore import Qt


class MetricCard(QFrame):

    def __init__(
        self,
        title:   str,
        value:   str,
        status:  str = "",
        remarks: str = "",
        accent:  str = "#4f8cff",
    ):
        super().__init__()
        self.setObjectName("metricCard")
        self._accent = accent

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top accent stripe ──────────────────────────────────────────
        stripe = QFrame()
        stripe.setFixedHeight(3)
        stripe.setStyleSheet(
            f"background:{accent}; border-radius:2px 2px 0 0; border:none;")
        root.addWidget(stripe)

        # ── Card body ──────────────────────────────────────────────────
        body = QWidget()
        body.setStyleSheet("background:transparent;")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(6)

        self._title_lbl = QLabel(title)
        self._title_lbl.setObjectName("metricTitle")
        self._title_lbl.setStyleSheet(
            f"color:{accent}; font-size:11px; font-weight:700; "
            "letter-spacing:0.6px; background:transparent; "
            "text-transform:uppercase;"
        )
        layout.addWidget(self._title_lbl)

        # Subtle divider under title
        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet(
            f"background:rgba(255,255,255,0.07); border:none;")
        layout.addWidget(div)
        layout.addSpacing(4)

        self._value_lbl = QLabel(value)
        self._value_lbl.setObjectName("metricValue")
        layout.addWidget(self._value_lbl)

        if status:
            self._status_lbl = QLabel(status)
            self._status_lbl.setObjectName("metricStatus")
            layout.addWidget(self._status_lbl)
        else:
            self._status_lbl = None

        if remarks:
            self._remarks_lbl = QLabel(remarks)
            self._remarks_lbl.setObjectName("metricRemarks")
            layout.addWidget(self._remarks_lbl)
        else:
            self._remarks_lbl = None

        root.addWidget(body, 1)

    # ── Live update ────────────────────────────────────────────────────

    def update_values(
        self,
        value:   str = None,
        status:  str = None,
        remarks: str = None,
    ):
        """Update displayed values without rebuilding the widget."""
        if value is not None:
            self._value_lbl.setText(value)
        if status is not None and self._status_lbl is not None:
            self._status_lbl.setText(status)
        if remarks is not None and self._remarks_lbl is not None:
            self._remarks_lbl.setText(remarks)