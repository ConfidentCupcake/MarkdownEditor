"""
cozy/overlays.py -> full-window paint effect. C17 ships ScanlineOverlay.

One 1-px line every 3 px at ~11% black: reads as CRT without hurting
readability. Same transparent-for-mouse pattern as ParicleOverlay.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter, QColor
from PyQt5.QtWidgets import QWidget

class ScanlineOverlay(QWidget):
    """CRT scanlines over the whole window. HIdden until enabled."""
    def __init__(self, parent):
        super().__init__(parent=parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()
        
    def painterEvent(self, _):
        painter = QPainter(self)
        line = QColor(0, 0, 0, 28)
        for y in range(0, self.height(), 3):
            painter.fillRect(0, y, self.width(), 1, line)
        painter.end()
        
    def resizeEvent(self, e):
        self.setGeometry(self.parentWidget().rect())
        super().resizeEvent(e)
    