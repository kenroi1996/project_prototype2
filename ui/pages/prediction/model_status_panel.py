"""
Model status panel — top strip on the Prediction Center showing whether a
trained model is active, and its headline metrics.

Fully self-sufficient: reads DataStore().trained_model itself, so callers
just need to call refresh() whenever the "trained_model" DataStore key
changes (or on page showEvent, to catch anything that happened while the
page was hidden).
"""

from PyQt6.QtWidgets import QFrame, QLabel, QHBoxLayout, QVBoxLayout
from PyQt6.QtCore import Qt

from services.data_store import DataStore


class ModelStatusPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("predModelStatusPanel")
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(0)

        left = QHBoxLayout()
        left.setSpacing(12)

        self._dot = QLabel("●")
        self._dot.setObjectName("predStatusDot")

        status_col = QVBoxLayout()
        status_col.setSpacing(3)
        self._title = QLabel("No Model Trained")
        self._title.setObjectName("predStatusTitle")
        self._sub = QLabel(
            "Train a model from the Model Training page before running prediction."
        )
        self._sub.setObjectName("predStatusSub")
        self._sub.setWordWrap(True)
        status_col.addWidget(self._title)
        status_col.addWidget(self._sub)

        left.addWidget(self._dot)
        left.addLayout(status_col, 1)
        layout.addLayout(left, 2)

        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setFixedWidth(1)
        div.setStyleSheet("background: rgba(255,255,255,0.08); border: none;")
        layout.addSpacing(20)
        layout.addWidget(div)
        layout.addSpacing(20)

        self._chips_layout = QHBoxLayout()
        self._chips_layout.setSpacing(12)
        layout.addLayout(self._chips_layout, 3)

    def refresh(self):
        store = DataStore.get()
        model = store.trained_model

        while self._chips_layout.count():
            item = self._chips_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not model or not store.model_ready:
            self._dot.setStyleSheet(
                "color: #ff5b5b; font-size: 11px; background: transparent;"
            )
            self._title.setText("No Model Trained")
            self._sub.setText(
                "Train a model from the Model Training page before running prediction."
            )
            self._title.setStyleSheet(
                "color: rgba(255,255,255,0.75); font-size: 14px; "
                "font-weight: 700; background: transparent;"
            )
            self._chips_layout.addWidget(
                self._make_chip("Prediction Unavailable", "—", "#ff5b5b")
            )
            self._chips_layout.addStretch()
            return

        self._dot.setStyleSheet(
            "color: #34d399; font-size: 11px; background: transparent;"
        )
        self._title.setText("✓  Model Active - Ready for Prediction")
        self._title.setStyleSheet(
            "color: #34d399; font-size: 14px; font-weight: 700; background: transparent;"
        )

        meta      = model.get("metadata", {}) or {}
        model_id  = model.get("model_id", "rf")
        recall    = meta.get("recall")
        f1        = meta.get("f1_score")
        precision = meta.get("precision")
        pr_auc    = meta.get("pr_auc")
        threshold = (model.get("decision_threshold")
                     or meta.get("decision_threshold")
                     or meta.get("threshold"))
        train_sz  = meta.get("train_size")
        timestamp = meta.get("timestamp")

        ts_str = ""
        if timestamp:
            try:
                from datetime import datetime as _dt
                ts = _dt.fromisoformat(str(timestamp))
                ts_str = f"  ·  Trained {ts.strftime('%b %d, %Y  %H:%M')}"
            except Exception:
                ts_str = f"  ·  {timestamp}"
        self._sub.setText(f"Random Forest  ·  ID: {model_id}{ts_str}")
        self._sub.setStyleSheet(
            "color: rgba(255,255,255,0.45); font-size: 12px; background: transparent;"
        )

        def _fmt_pct(v):
            return f"{v*100:.1f}%" if v is not None else "—"
        def _fmt_f(v, decimals=3):
            return f"{v:.{decimals}f}" if v is not None else "—"

        chips = [
            ("Recall",    _fmt_pct(recall),    "#34d399"),
            ("Precision", _fmt_pct(precision), "#4f8cff"),
            ("F1 Score",  _fmt_f(f1),          "#a78bfa"),
            ("PR-AUC",    _fmt_f(pr_auc),      "#f5b335"),
            ("Threshold", _fmt_f(threshold, 2),"#8b949e"),
        ]
        if train_sz:
            chips.append(("Train Size", f"{int(train_sz):,}", "#8b949e"))

        for label, value, color in chips:
            self._chips_layout.addWidget(self._make_chip(label, value, color))
        self._chips_layout.addStretch()

    @staticmethod
    def _make_chip(label: str, value: str, color: str) -> QFrame:
        chip = QFrame()
        chip.setObjectName("predMetricChip")
        col = QVBoxLayout(chip)
        col.setContentsMargins(14, 10, 14, 10)
        col.setSpacing(3)

        val_lbl = QLabel(value)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        val_lbl.setStyleSheet(
            f"color: {color}; font-size: 15px; font-weight: 800; background: transparent;"
        )
        key_lbl = QLabel(label)
        key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        key_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.40); font-size: 10px; "
            "font-weight: 600; letter-spacing: 0.5px; background: transparent;"
        )
        col.addWidget(val_lbl)
        col.addWidget(key_lbl)
        return chip