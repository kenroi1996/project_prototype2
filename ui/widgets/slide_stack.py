"""
Horizontal slide-carousel container — lightweight equivalent of
QStackedWidget with an animated transition between pages.

Uses the same QPropertyAnimation-on-geometry technique as
StudentProfileDrawer's slide-in panel
(ui/components/student_profile_drawer.py), just horizontal and scoped to a
fixed-size host instead of a full-window overlay. Qt clips child widgets to
their parent's rect by default, so pages positioned outside the host's
bounds during the slide are simply not painted — no manual clipping needed.

Extracted from ui/pages/prediction_page.py — no logic changes, only
relocation.
"""

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import (
    QPropertyAnimation, QEasingCurve, QRect, QParallelAnimationGroup,
)


class _SlideStack(QWidget):
    """Holds N pages side-by-side; slide_to(index) animates between them."""

    ANIM_DURATION = 320

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pages: list[QWidget] = []
        self._current_index = -1
        self._anim_group: QParallelAnimationGroup | None = None

    def add_page(self, widget: QWidget) -> int:
        widget.setParent(self)
        widget.hide()
        self._pages.append(widget)
        if self._current_index == -1:
            self._current_index = 0
            widget.setGeometry(self.rect())
            widget.show()
        return len(self._pages) - 1

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if 0 <= self._current_index < len(self._pages):
            self._pages[self._current_index].setGeometry(self.rect())

    def current_index(self) -> int:
        return self._current_index

    def slide_to(self, index: int):
        if index == self._current_index or not (0 <= index < len(self._pages)):
            return
        if self._anim_group and self._anim_group.state() == QParallelAnimationGroup.State.Running:
            self._anim_group.stop()

        direction = 1 if index > self._current_index else -1
        outgoing  = self._pages[self._current_index]
        incoming  = self._pages[index]
        w, h      = self.width(), self.height()

        incoming.setGeometry(direction * w, 0, w, h)
        incoming.show()
        incoming.raise_()

        anim_out = QPropertyAnimation(outgoing, b"geometry")
        anim_out.setDuration(self.ANIM_DURATION)
        anim_out.setStartValue(outgoing.geometry())
        anim_out.setEndValue(QRect(-direction * w, 0, w, h))
        anim_out.setEasingCurve(QEasingCurve.Type.InOutCubic)

        anim_in = QPropertyAnimation(incoming, b"geometry")
        anim_in.setDuration(self.ANIM_DURATION)
        anim_in.setStartValue(incoming.geometry())
        anim_in.setEndValue(QRect(0, 0, w, h))
        anim_in.setEasingCurve(QEasingCurve.Type.InOutCubic)

        self._anim_group = QParallelAnimationGroup()
        self._anim_group.addAnimation(anim_out)
        self._anim_group.addAnimation(anim_in)

        prev_index = self._current_index

        def _on_finished():
            self._pages[prev_index].hide()
            self._current_index = index

        self._anim_group.finished.connect(_on_finished)
        self._anim_group.start()