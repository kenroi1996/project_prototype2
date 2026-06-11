from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QScrollArea,
)

from PyQt6.QtCore import Qt

from services.data_store import DataStore


# =====================================
# ACTIVITY ROW
# =====================================

class _ActivityRow(QFrame):
    """A single activity entry: accent dot · message · timestamp."""

    def __init__(self, entry: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("activityRow")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)

        # Colored status dot
        dot = QLabel("●")
        dot.setObjectName("activityDot")
        dot.setStyleSheet(f"#activityDot {{ color: {entry.get('color', '#4f8cff')}; font-size: 12px; }}")
        dot.setFixedWidth(14)

        # Icon glyph
        icon = QLabel(entry.get("icon", "•"))
        icon.setObjectName("activityIcon")
        icon.setFixedWidth(20)

        # Message
        message = QLabel(entry.get("message", ""))
        message.setObjectName("activityMessage")
        message.setWordWrap(True)

        # Timestamp
        time = QLabel(entry.get("time", ""))
        time.setObjectName("activityTime")
        time.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(dot)
        layout.addWidget(icon)
        layout.addWidget(message, 1)
        layout.addWidget(time)


# =====================================
# ACTIVITY LOG PANEL
# =====================================

class ActivityLogPanel(QFrame):
    """Modern, compact feed of system-wide activity.

    Listens to the DataStore and refreshes automatically whenever a new
    activity is recorded via ``DataStore.get().add_activity(...)``.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("activityCard")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 18)
        outer.setSpacing(14)

        # ── Header row ─────────────────────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(10)

        title = QLabel("RECENT ACTIVITY")
        title.setObjectName("activityTitle")

        live_dot = QLabel("●")
        live_dot.setObjectName("activityLiveDot")

        live_text = QLabel("Live")
        live_text.setObjectName("activityLiveText")

        header.addWidget(title)
        header.addStretch()
        header.addWidget(live_dot)
        header.addWidget(live_text)
        outer.addLayout(header)

        # ── Scrollable list ────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setObjectName("activityScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFixedHeight(150)

        self._list_host = QWidget()
        self._list_host.setObjectName("activityListHost")
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(6)

        self._scroll.setWidget(self._list_host)
        outer.addWidget(self._scroll)

        # Render current state + subscribe to future updates
        self.refresh()
        DataStore.get().add_listener(self._on_store_updated)

    # ------------------------------------------------------------------

    def _on_store_updated(self, key: str):
        if key in ("activity", "all"):
            self.refresh()

    def _clear_list(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def refresh(self):
        """Rebuild the feed from the DataStore (newest first)."""
        self._clear_list()

        activities = DataStore.get().activities
        if not activities:
            placeholder = QLabel("No activity yet. System events will appear here.")
            placeholder.setObjectName("activityEmpty")
            placeholder.setWordWrap(True)
            self._list_layout.addWidget(placeholder)
            self._list_layout.addStretch()
            return

        # Newest at the top
        for entry in reversed(activities):
            self._list_layout.addWidget(_ActivityRow(entry))

        self._list_layout.addStretch()
