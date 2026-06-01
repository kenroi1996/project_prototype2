from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QVBoxLayout
)


class MetricCard(QFrame):

    def __init__(
        self,
        title,
        value,
        status="",
        remarks=""
    ):
        super().__init__()
        self.setObjectName("metricCard")

        layout = QVBoxLayout(self)

        self._title_lbl = QLabel(title)
        self._title_lbl.setObjectName("metricTitle")

        self._value_lbl = QLabel(value)
        self._value_lbl.setObjectName("metricValue")

        layout.addWidget(self._title_lbl)
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

    # ------------------------------------------------------------------
    # Live update — called after predictions run
    # ------------------------------------------------------------------

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