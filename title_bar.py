from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QStyle, QToolButton, QWidget


class CustomTitleBar(QWidget):
    """Display window controls for a frameless top-level window."""

    def __init__(self, parent: QWidget) -> None:
        """Create the title label and connect each window-control button."""
        super().__init__(parent)
        self.setAutoFillBackground(True)
        self.setBackgroundRole(QPalette.ColorRole.Highlight)
        self.initial_pos = None

        # Supplying self both owns and installs the layout.
        titlebar_layout = QHBoxLayout(self)
        titlebar_layout.setContentsMargins(0, 0, 0, 0)
        titlebar_layout.setSpacing(2)

        self.title = QLabel(self.__class__.__name__, self)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if title := parent.windowTitle():
            self.title.setText(title)
        titlebar_layout.addWidget(self.title)

        self.min_btn = QToolButton(self)
        self.min_btn.setIcon(
            self.style().standardIcon(
                QStyle.StandardPixmap.SP_TitleBarMinButton
            )
        )
        # Connect bound methods. Parentheses would invoke them now.
        self.min_btn.clicked.connect(self.window().showMinimized)

        self.max_btn = QToolButton(self)
        self.max_btn.setIcon(
            self.style().standardIcon(
                QStyle.StandardPixmap.SP_TitleBarMaxButton
            )
        )
        self.max_btn.clicked.connect(self.window().showMaximized)

        self.close_btn = QToolButton(self)
        self.close_btn.setIcon(
            self.style().standardIcon(
                QStyle.StandardPixmap.SP_TitleBarCloseButton
            )
        )
        self.close_btn.clicked.connect(self.window().close)

        self.norm_btn = QToolButton(self)
        self.norm_btn.setIcon(
            self.style().standardIcon(
                QStyle.StandardPixmap.SP_TitleBarNormalButton
            )
        )
        self.norm_btn.clicked.connect(self.window().showNormal)
        self.norm_btn.setVisible(False)

        for button in (
            self.min_btn,
            self.norm_btn,
            self.max_btn,
            self.close_btn,
        ):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setFixedSize(QSize(28, 28))
            titlebar_layout.addWidget(button)