from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
)

from PyQt6.QtCore import (
    Qt,
    QTimer,
    QPropertyAnimation,
    QEasingCurve,
)

from PyQt6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPen,
    QBrush,
)


# =====================================
# SPINNER WIDGET
# =====================================

class _SpinnerWidget(QWidget):
    """Animated arc spinner drawn with QPainter."""

    def __init__(self, size=48, color="#4f8cff", parent=None):
        super().__init__(parent)

        self._angle = 0
        self._color = QColor(color)
        self._size = size

        self.setFixedSize(size, size)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._rotate)
        self._timer.start(16)  # ~60fps

    def _rotate(self):
        self._angle = (self._angle + 6) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        margin = 6
        rect = self.rect().adjusted(margin, margin, -margin, -margin)

        # Background track
        track_pen = QPen(QColor(255, 255, 255, 30))
        track_pen.setWidth(4)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        painter.drawEllipse(rect)

        # Spinning arc
        arc_pen = QPen(self._color)
        arc_pen.setWidth(4)
        arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc_pen)
        painter.drawArc(rect, -self._angle * 16, -120 * 16)

    def stop(self):
        self._timer.stop()

    def start(self):
        self._timer.start(16)


# =====================================
# LOADING OVERLAY
# =====================================

class LoadingOverlay(QWidget):
    """
    A full-widget overlay that dims the dashboard and shows a
    centered spinner + status message while a prediction runs.

    Usage:
        self.overlay = LoadingOverlay(self)
        self.overlay.show()   # show before prediction starts
        self.overlay.hide()   # hide when prediction finishes
        self.overlay.set_message("Loading model...")  # update text mid-run
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        # Sit on top of everything
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._build_ui()
        self.hide()

        # Keep overlay sized to parent
        if parent:
            parent.installEventFilter(self)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Semi-transparent dark backdrop
        self.setStyleSheet("""
            LoadingOverlay {
                background-color: rgba(10, 14, 26, 0.72);
            }
        """)

        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Frosted card
        card = QFrame()
        card.setObjectName("overlayCard")
        card.setFixedWidth(260)
        card.setStyleSheet("""
            #overlayCard {
                background-color: #161b2e;
                border: 1px solid rgba(79, 140, 255, 0.25);
                border-radius: 16px;
            }
        """)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(32, 36, 32, 36)
        card_layout.setSpacing(20)
        card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Spinner
        spinner_row = QHBoxLayout()
        spinner_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._spinner = _SpinnerWidget(size=48, color="#4f8cff")
        spinner_row.addWidget(self._spinner)
        card_layout.addLayout(spinner_row)

        # Primary message
        self._label = QLabel("Running Prediction...")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("""
            color: #e8eaf0;
            font-size: 14px;
            font-weight: 600;
            background: transparent;
        """)
        card_layout.addWidget(self._label)

        # Secondary sub-message (animated dots)
        self._sub_label = QLabel("Analyzing student records")
        self._sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub_label.setWordWrap(True)
        self._sub_label.setStyleSheet("""
            color: #6b7a99;
            font-size: 12px;
            background: transparent;
        """)
        card_layout.addWidget(self._sub_label)

        outer.addWidget(card)

        # Animated dot-cycling for sub_label
        self._dot_timer = QTimer(self)
        self._dot_timer.timeout.connect(self._cycle_dots)
        self._dot_messages = [
            "Analyzing student records",
            "Analyzing student records.",
            "Analyzing student records..",
            "Analyzing student records...",
        ]
        self._dot_index = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_message(self, message: str, sub_message: str = ""):
        """Update the primary and optional secondary message."""
        self._label.setText(message)
        if sub_message:
            self._sub_label.setText(sub_message)
            self._dot_messages = [
                sub_message,
                sub_message + ".",
                sub_message + "..",
                sub_message + "...",
            ]

    def show(self):
        self._resize_to_parent()
        self._spinner.start()
        self._dot_timer.start(500)
        super().show()
        self.raise_()

    def hide(self):
        self._spinner.stop()
        self._dot_timer.stop()
        super().hide()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cycle_dots(self):
        self._dot_index = (self._dot_index + 1) % len(self._dot_messages)
        self._sub_label.setText(self._dot_messages[self._dot_index])

    def _resize_to_parent(self):
        if self.parent():
            self.resize(self.parent().size())

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if obj is self.parent() and event.type() == QEvent.Type.Resize:
            self._resize_to_parent()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)