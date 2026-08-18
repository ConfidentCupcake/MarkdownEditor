import shutil

# shutil.which("bash") returns:
#   Linux: "usr/bin/bash" or "/bin/bash"
#   Windows: None (bash not on PATH unless Git Bash is installed)
#   macOS: "/bin/bash"

# shutil.which("powershell") returns:
#   Windows: "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
#   Linux: None
#   macOS: None

import sys
import os
from PyQt5.QtCore import Qt, QProcess, QProcessEnvironment, QTimer
from PyQt5.QtGui import QFont, QColor, QTextCursor, QKeyEvent
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPlainTextEdit,
    QPushButton, QLabel, QComboBox
)

class TerminalWidget(QWidget):
    """
    A terminal widget that runs the system shell interacively.
    
    The user types directly into the output area (like a real terminal).
    Characers are sent to the QProcess as they're typed. Output
    from the process is appended to the same area.
    
    Supports a shell selector dropdown to switch between:
        - PowerShell (Windows)
        - Command Prompt / cmd.exe (Windows)
        - Bash (Linux, including Arch Linus)
        - Zsh (macOS / Linux)
        - Fish (Linux)
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self._init_ui()
        self._connect_signals()
        # Don't auto-start - let the user pick a shell or use the default
        self._detect_and_start_default()
