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
from PyQt5.QtCore import Qt, QProcess, QProcessEnvironment, QEvent
from PyQt5.QtGui import QFont, QTextCursor, QKeySequence
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPlainTextEdit,
    QApplication, QPushButton, QLabel, QComboBox
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
        self._populate_shell_combo_method()
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
            background-color: #2c313a; color: #dcdfe4;
            border: 1px solid #3d324d; border-radius: 3px;
            padding: 3px 8px; min-width: 100px;
        }
        QComboBox:hover { border: 1px solid #4b5263; }
        QComboBox::drop-down { border: none; width: 20px; }
        QComboBox QAbstractItemView {
            background-color: #2c313a; color: #dcdfe4;
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

        # Intercept key presses on the output area
        self.output.installEventFilter(self)
        
        # Track the position where the user's input starts
        self._input_start_pos= 0
    
    def _connect_signals(self):
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
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
            
    def _detect_available_shells(self):
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

            # cmd.exe - always available on Windows
            cmd = os.environ.get("COMSPEC", "cmd.exe")
            shells.append(("Command Prompt", cmd, ["/K"]))
        
        elif sys.platform == "linux":
            # Linux: detect bash, zsh, and fish
            # Arch Lnux typically has bash; zsh and fish may be installed
            
            bash = shutil.which("bash")
            if bash:
                shells.append(("Bash", bash, ["-i"]))
            
            zsh = shutil.which("zsh")
            if zsh:
                shells.append(("Zsh", zsh, ["-i"]))
                
            fish = shutil.which("fish")
            if fish:
                shells.append(("Fish", fish, ["-i"]))
            
        elif sys.platform == "darwin":
            # macOS: zsh is default, bash also available
            
            zsh = shutil.which("zsh")
            if zsh:
                shells.append(("Zsh", zsh, ["-i"]))
                
            bash = shutil.which("bash")
            if bash:
                shells.append(("Bash", bash, ["-i"]))
        
        return shells
        
    def _populate_shell_combo_method(self):
        """
        Fill the shell dropdown with detected shells.
        
        Also, this method stores the detected shells in self._shells and adds each display name to the QComboBox.
        The QComboBox.addItem(text) method adds an item to the dropdown. QComboBox.addItem docs
        """
        self._shells = self._detect_available_shells()
        for display_name, shell_path, arg in self._shells:
            self.shell_combo.addItem(display_name)
            
    def _detect_and_start_default(self):
        """
        Start the first available shell (or the system default).
        
        On startup, the widget starts the first detected shell. On Windows, this would be PowerShell 7 (if installed) or Windows PowerShell. 
        On Arch Linux, this would be Bash.
        """
        if self._shells:
            # Start the first detected shell
            display_name, shell_path, args = self._shells[0]
            self._start_shell(shell_path, args)
        else:
            self._append_text("No shell detected on this system.\n")
    
    def _start_shell(self, shell_path: str, args: list = None):
        """Start a shell process with the given path and arguments."""
        if args is None:
            args = []
            
        # Stop existing shell process
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()
            self.process.waitForFinished(2000)
            
        # Build the environment for the shell
        env = QProcessEnvironment.systemEnvironment()
        
        # On Linux, ensure TERM is set so the shell knows it's a terminal
        # Without TERM, some shells don't show a prompt
        if sys.platform != "win32":
            if not env.value("TERM"):
                env.insert("TERM", "xterm-256color")
                
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(os.path.expanduser("~"))
        
        # Clear the output area for the new shell session
        self.output.clear()
        self._input_start_pos = 0
            
        self.process.start(shell_path, args)

    def _read_stdout(self):
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._append_text(data)

    def _read_stderr(self):
        data = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        self._append_text(data)
        
        
    def _append_text(self, text: str):
        """Append text from the process to the output area."""
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()

        # Update the input start position
        self._input_start_pos = cursor.position()

    def eventFilter(self, obj, event):
        """Intercept key presses on the output area and send them to the shell."""
        if obj != self.output or event.type() != QEvent.KeyPress:
            return super().eventFilter(obj, event)

        if self.process.state() != QProcess.Running:
            return True # block all input when process isn't running

        cursor = self.output.textCursor()

        if event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return):
            cursor.movePosition(QTextCursor.End)
            end_pos = cursor.position()

            # Get all text from input_start to end
            cursor.setPosition(self._input_start_pos)
            cursor.setPosition(end_pos, QTextCursor.KeepAnchor)
            line = cursor.selectedText()

            # Insert a newline in the display
            cursor.movePosition(QTextCursor.End)
            cursor.insertText("\n")
            self.output.setTextCursor(cursor)

            # Send the line + newline to the process
            self.process.write((line + "\n").encode("utf-8"))

            # Update input start position
            self._input_start_pos = cursor.position()
            return True # consume the event

        if event.matches(QKeySequence.Paste):
            cursor.movePosition(QTextCursor.End)
            cursor.insertText(QApplication.clipboard().text().replace("\r\n", "\n"))
            self.output.setTextCursor(cursor)
            return True

        # Backspace -- only allow if cursor is past the input start position
        if event.key() == Qt.Key.Key_Backspace:
            if cursor.hasSelection():
                if cursor.selectionStart() < self._input_start_pos:
                    return True
                cursor.removeSelectedText()
            elif cursor.position() > self._input_start_pos:
                cursor.deletePreviousChar()
            self.output.setTextCursor(cursor)
            return True # consume event

        if event.key() == Qt.Key.Key_Delete:
            if cursor.hasSelection() and cursor.selectionStart() < self._input_start_pos:
                return True
            if cursor.position() < self._input_start_pos:
                return True
            cursor.deleteChar()
            self.output.setTextCursor(cursor)
            return True

        # Regular character -- insert it at cursor position
        if event.text():
            if cursor.hasSelection() and cursor.selectionStart() < self._input_start_pos:
                cursor.clearSelection()
            cursor.movePosition(QTextCursor.End)
            cursor.insertText(event.text())
            self.output.setTextCursor(cursor)
            return True # consume the Event

        # Let other keys (arrow keys, etc. pass through QPlainTextEdit
        return False


    def _on_finished(self, exit_code, exit_status):
        self._append_text(f"\n[Process exited with code {exit_code}]\n")
        self.status_label.setText("● Terminal Stopped")
        self.status_label.setStyleSheet("color: #e06c75; font-size: 12px;")

    def _on_state_changed(self, state):
        if state == QProcess.Running:
            self.status_label.setText("● Terminal Running")
            self.status_label.setStyleSheet("color: #98c379; font-size: 12px;")
        else:
            self.status_label.setText("● Terminal Stopped")
            self.status_label.setStyleSheet("color: #e06c75; font-size: 12px;")

    def _clear(self):
        self.output.clear()
        self._input_start_pos = 0

    def stop(self):
        """Kill the shell process."""
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()
            self.process.waitForFinished(2000)
