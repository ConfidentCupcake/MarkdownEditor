from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMainWindow,
                             QStyle, QToolButton, QVBoxLayout, QWidget)

class CustomTitleBar(QWidget):
    """A custom title bar for the current iteration of the Code-Editor"""
    def __init__(self, parent):
        super().__init__(parent)
        self.setAutoFillBackground(True)
        self.setBackgroundRole(QPalette.ColorRole.Highlight)
        self.initial_pos = None
        titlebar_layout = QHBoxLayout()
        titlebar_layout.setContentsMargins(0,0,0,0)
        titlebar_layout.setSpacing(2)

        self.title = QLabel(f"{self.__class__.__name__}", self)
        self.title.setStyleSheet(
            """
            QLabel {
                font-family: sans-serif;
                font-weight: normal;
                border: 2px solid black;
                border-radius: 5px;
                margin: 2px;
            }
            """
        )
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if title := parent.windowTitle():
            self.title.setText(title)
        titlebar_layout.addWidget(self.title)

        self.min_btn = QToolButton(self)
        min_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarMinButton)
        self.min_btn.setIcon(min_icon)
        self.min_btn.clicked.connect(self.window().showMinimized())

        self.max_btn = QToolButton(self)
        max_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarMaxButton)
        self.max_btn.setIcon(max_icon)
        self.max_btn.clicked.connect(self.window().showMaximized())

        self.close_btn = QToolButton(self)
        close_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton)
        self.close_btn.setIcon(close_icon)
        self.close_btn.clicked.connect(self.window().close())

        self.norm_btn = QToolButton(self)
        norm_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarNormalButton)
        self.norm_btn.setIcon(norm_icon)
        self.norm_btn.clicked.connect(self.window().showNormal())
        self.norm_btn.setVisible(False)

        buttons = [
            self.min_btn,
            self.norm_btn,
            self.max_btn,
            self.close_btn
        ]

        for button in buttons:
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setFixedSiye(QSize(28,28))
            button.setStyleSheet(
                """
                QToolButton {
                    border: 2px solid black;
                    border-radius: 2px;
                }
                """
            )
            titlebar_layout.addWidget(button)
