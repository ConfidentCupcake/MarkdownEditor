from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QTextCursor, QTextCharFormat
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPlainTextEdit,
    QLineEdit, QPushButton, QLabel
)

from python_runner import PythonRunner

# QWidget = Base class for all UI widgets. Your console is a composite widget (contains multiple child widgets)
# QVBoxLayout / QHBoxLayout = Layout managers that stack widgets vertically/horizontally.
# QPlainTextEdit = A text editor optimized for plain text. Better than QTextEdit for console output becuase it doesn't do rich text formattinf overhead.
# QLineEdit = Single-line text input
# QPuchButton = Clickable Button
# QTextCharFormat = Describes font/color for a range of text
# QTextCursor = Lets you move around a text document and insert formatted text.

class ConsoleWidget(QWidget):
    input_submitted = pyqtSignal(str) # emitted when user presses Enter in input line.
    def __init__(self, runner: PythonRunner, parent=None):
        super().__init__(parent)
        # The console takes a reference to the PythonRunner. It needs to connect to the runner's signals and call its methods
        self.runner = runner
        self._init_ui()
        self._connect_signals()
        
        # The three part layout
    def _init_ui(self):
        layout = QVBoxLayout(self) #Stacks things top-to-bottom
        layout.setContentsMargins(0, 0, 0, 0) # removes the default padding so the console fills its fock area edge-to-edge
        layout.setSpacing(0) # removes gaps between elements
        
        # --- Toolbar ---
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(6, 4, 6, 4)
        toolbar.setSpacing(6)
        
        self.status_label = QLabel("● Idle")
        self.status_label.setStyleSheet("color: #888; font-size: 12px;")
        
        self.run_btn = QPushButton("Run File")
        self.run_btn.setToolTip("Run the current Python file (F5)")
        
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setToolTip("Kill the running process (F6)")
        
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setToolTip("Clear the console output")
        
        toolbar.addWidget(self.status_label)
        toolbar.addStretch() # pushes buttons to the right
        toolbar.addWidget(self.run_btn)
        toolbar.addWidget(self.stop_btn)
        toolbar.addWidget(self.clear_btn)
        layout.addLayout(toolbar)
        
        # --- Output area ---
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True) # prevents User from writing into the output area.
        self.output.setFont(QFont("Consolas", 11)) # uses monospace fonts so the output alines nicely
        # The SyleSheet sets the dark background, with light text.
        self.output.setStyleSheet("""
            QPlainTextEdit {
                background-color: #1e2127;
                color: #abb2bf;
                border: none;
                padding: 4px;
            }
        """)
        layout.addWidget(self.output)
        
        # --- Input line ---
        self.input_line = QLineEdit()
        self.input_line.setPlaceholderText("Type input for stdin and press Enter...") # shows gray hint text when the field is empty
        self.input_line.setFont(QFont("Consolas", 11))
        layout.addWidget(self.input_line)
        
    def _connect_signals(self):
        # Runner -> Console display
        self.runner.output_ready.connect(lambda text: self._append(text, "#abb2bf")) # passes a color along with a text
        self.runner.error_ready.connect(lambda text: self._append(text, "#e06c75"))
        self.runner.process_finished.connect(self._on_finished)
        self.runner.state_changed.connect(self._on_state_changed)
        
        # Button clicks
        self.stop_btn.clicked.connect(self.runner.stop)
        self.clear_btn.clicked.connect(self.output.clear)
        
        # Input line -> runner stdin
        self.input_line.returnPressed.connect(self._submit_input) # a built-in QLineEdit signal that fires when user presses Enter in the input field
    
    def _submit_input(self):
        """Handle Enter key in input line"""
        text = self.input_line.text()
        self.input_line.clear()
        self._append(text + "\n", "#61afef") # show what the user typed in blue
        self.runner.send_input(text)
        # When the user types "42" and presses Enter:
        # 1. text() returns "42"
        # 2. clear() empties the input field
        # 3. _append("42\n", "#61afef") echoes it to the output area in blue, so the user sees what they typed
        # 4. runner.send_input("42") writes "42\n" to the subprocess's stdin, which unblocks input() in the running script
    
    def _append(self, text: str, color: str):
        cursor = self.output.textCursor() # Gets a QTextCursor pointing to the current cursor position in the output area.
        cursor.movePosition(QTextCursor.End) # Moves the cursor tp the end of the document. You always append new text at the bottom.
        
        fmt = QTextCharFormat() # Creates a new format object. This describes how text should look (font, color, weight, etc.)
        fmt.setForeground(QColor(color)) # Sets the text Color
        cursor.setCharFormat(fmt) # Applies the format to the cursor. Any text inserted after this point will use this color.
        cursor.insertText(text) # Inserts the text at the cursor position with the current format.
        
        self.output.setTextCursor(cursor) # Updates the QPlainTextEdit's cursor to your modified cursor. This is needed because the textCursor() returns a copy, not a reference. You have to set it back.
        self.output.ensureCursorVisible() # Scrolls the output area so the cursor (which is at the end) is visible. Without this, new output would appear below the visible area and you'd have to manually scroll.
        
    def _on_finished(self, exit_code: int):
        if exit_code == 0:
            self._append(f"\n[Process finished with exit code {exit_code}]\n", "#98c379")
        else:
            self._append(f"\n[Process finished with exit code {exit_code}]\n", "#e06c75")
    
    def _on_state_changed(self, state: str):
        if state == "running":
            self.status_label.setText("● Running")
            self.status_label.setStyleSheet("color: #e06c75; font-size: 12px;")
            
        else:
            self.status_label.setText("● Idle")
            self.status_label.setStyleSheet("color: #888; font-size: 12px;")
