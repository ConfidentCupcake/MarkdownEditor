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
    
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(0)
        
        # --- Toolbar --- 
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0,0,0,0)
        toolbar.setSpacing(6)
        
        self.status_label = QLabel("● Terminal Ready")
        self.status_label.setStyleSheet("color: #98c379; font-size: 12px;")
        
        # Shell selector dropdown
        self.shell_combo = QComboBox()
        self.shell_combo.setToolTip("Select which shell to use")
        self.shell_combo.setStyleSheet(
        """
        QComboBox {
            background-color: #21c313a; color: #dcdfe4;
            border: 1px solif #3d324d; border-radius: 3px;
            padding: 3px 8px; min-width: 100px;
        }
        QComboBox:hover { border: 1px solid #4b5263; }
        QComboBox::drop-down { border: none; width: 20px; }
        QComboBox QAbstractItemView {
            background-color: #21c313a; color: #dcdfe4;
            selection-background-color: #3d424d;
            border: 1px solid #3d424d;
        }
        """)
        
        # Restart button - kills the current shell and starts a new one
        self.restart_btn = QPushButton("Restart")
        self.restart_btn.setToolTip("Restart the terminal with the selected shell")
        
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setToolTip("Clear the terminal")
        
        toolbar.addWidget(self.status_label)
        toolbar.addWidget(self.shell_combo)
        toolbar.addStretch()
        toolbar.addWidget(self.restart_btn)
        toolbar.addWidget(self.clear_btn)
        layout.addLayout(toolbar)
        
        # --- Terminal output/input are ---
        self.output = QPlainTextEdit()
        self.output.setReadOnly(False)      # User can type directly
        self.output.setFont(QFont("Consolas", 12))
        self.output.setStyleSheet(
        """
        QPlainTextEdit {
            background-color: #1e2127;
            color: #abb2bf;
            border: none;
            padding: 4px;
        }
        """
        )
        layout.addWidget(self.output)
        
        # Track the position where the user's input starts
        self._input_start_pos= 0
    
    def _connect_signals(self):
        self.process.readyReadStandardOutput.connect(self._ready_stdout)
        self.process.readyReadStandardError.connect(self._ready_stdout)
        self.process.finished.connect(self._on_finished)
        self.process.stateChanged.connect(self._on_state_changed)
        self.clear_btn.clicked.connect(self._clear)
        self.restart_btn.clicked.connect(self._restart_shell)
        self.shell_combo.currentIndexChanged.connect(self._on_shell_changed)
        
    def _on_shell_changed(self, index: int):
        """Called when the user selects a different shell from the dropdown."""
        if 0 <= index < len(self._shells):
            display_name, shell_path, args = self._shells[index]
            self._start_shell(shell_path, args)
            
    def _restart_shell(self):
        """Restart the terminal with the currently selected shell."""
        index = self.shell_combo.currentIndex()
        if 0 <= index < len(self._shells):
            display_name, shell_path, args = self._shells[index]
            self._start_shell(shell_path, args)
            
    def _detect_available_shell(self):
        """
        Detect which shells are available on this system.
        Returns a list of (display_name, shell_path, args) tuples.
        """
        shells = []
        
        if sys.platform == "win32":
            # Windows: detect: PowerShell and cmd.exe
            
            # PowerShell 7+ (cross-platform, installed seperately)
            # shutil.which searches the PATH for an executable
            pwsh = shutil.which("pwsh")
            powershell = shutil.which("powershell")
            
            if pwsh:
                shells.append(("PowerShell 7", pwsh, ["-NoExit"]))
            if powershell:
                shells.append(("PowerShell", powershell, ["-NoExit"]))
        
        elif sys.platform == "linux":
            # Linux: detect bash, zsh, and fish
            # Arch Lnux typically has bash; zsh and fish may be installed
            
            bash = shutil.which("bash")
                shells.append(("Bash", bash, ["-i"]))
            
            
        
        
        
        