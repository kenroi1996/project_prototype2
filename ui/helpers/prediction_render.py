"""Small shared render helpers for the Prediction Center wizard steps."""

from PyQt6.QtWidgets import QWidget, QLabel, QHBoxLayout, QFrame


def step_label(number: str, title: str) -> QWidget:
    row = QHBoxLayout()
    row.setSpacing(10)
    row.setContentsMargins(0, 0, 0, 0)

    num = QLabel(number)
    num.setObjectName("predStepNumber")
    num.setFixedWidth(28)

    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.VLine)
    sep.setFixedWidth(1)
    sep.setFixedHeight(16)
    sep.setStyleSheet("background: rgba(255,255,255,0.18); border: none;")

    title_lbl = QLabel(title)
    title_lbl.setObjectName("predStepTitle")

    row.addWidget(num)
    row.addWidget(sep)
    row.addWidget(title_lbl)
    row.addStretch()

    host = QWidget()
    host.setLayout(row)
    return host


def input_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("predInputLabel")
    return lbl