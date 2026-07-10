"""Small help / tooltip helpers for the desktop UI."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget


def tip(widget: QWidget, text: str) -> None:
    """Attach a hover tooltip."""
    if widget is not None and text:
        widget.setToolTip(text)


def help_banner(text: str, parent: QWidget | None = None) -> QLabel:
    """Muted instructional strip under a section title."""
    lbl = QLabel(text, parent)
    lbl.setWordWrap(True)
    lbl.setStyleSheet("color: #8a93a0; font-size: 11px; padding: 2px 0 6px 0;")
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lbl
