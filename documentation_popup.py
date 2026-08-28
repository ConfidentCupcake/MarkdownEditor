from PyQt5.QtCore import Qt, QPoint
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QFrame, QLabel, QPlainTextEdit, QVBoxLayout, QToolTip

class DocumentationPopup(QFrame):
    """A small scrollable window for displaying Jedi documentation."""
    
    # These constants prevent the documentation popup from becoming
    # to small to read or so large that it covers the editor.
    
    MIN_WIDTH = 360
    MAX_WIDTH = 620
    MIN_HEIGHT = 140
    MAX_HEIGHT = 360
    
    def __init__(self, parent=None):
        # Qt.QToolTip makes this a temporary tooltip-style window.
        # Qt.FramelessWindowHint removes a normal OS title bar/frame
        # Qt.WindowStaysOnTopHint keeps the popup above the editor.
        super().__init__(
            parent,
            Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
        )
        # The object name is used by the stylesheet selector:
        # QFrame#DocumentationPopup ( ... )
        self.setObjectName("DocumentationPopup")
        
        # The popup must never take keyboard focus away from QScintilla.
        # The user should still be able to type while the popup is visible.
        self.setFocusPolicy(Qt.NoFocus)
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setMaximumWidth(self.MAX_WIDTH)
        self.setMinimumHeight(self.MIN_HEIGHT)
        self.setMaximumHeight(self.MAX_HEIGHT)
        
        # QVBoxLayout stacks child widgets vertically:
        # title label at top, documentation editor below it.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)
        
        # The first line of Jedi output is used as a short heading,
        # for example: "Path" or "len (function)".
        self.title_label = QLabel(self)
        
        # Display angle brackets, ampersands and similar characters
        # literally instead of allowing QLabel to interpret them as HTML.
        self.title_label.setTextFormat(Qt.PlainText)
        self.title_label.setWordWrap(True)
        
        # QPlainTextEdit is scrollable and preserves code/docstring spacing.
        self.body_edit = QPlainTextEdit(self)
        self.body_edit.setReadOnly(True)
        
        # Generate documentation does not need an undo history.
        self.body_edit.setUndoRedoEnabled(False)
        
        # A user may select/copy text, but the widget never takes focus.
        self.body_edit.setFocusPolicy(Qt.NoFocus)
        
        # Wrap normal prose at the popup width. 
        # This avoides requiring a horizontal scrollbar for ordinary paragraphs
        self.body_edit.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        
        # This height forces long documentation into the scrollable viewport.
        self.body_edit.setMaximumHeight(300)
        
        # Use a code-oriented font for signatures and documentation examples.
        font = QFont("JetBrains Mono", 10)
        self.title_label.setFont(font)
        self.body_edit.setFont(font)
        
        layout.addWidget(self.title_label)
        layout.addWidget(self.body_edit)
        
        # The colors intentionally match the editor's dark Dracula-style UI
        self.setStyleSheet("""
            QFrame#DocumentationPopup {
                background-color: #1e1f22;
                border: 1px solid #4b5263;
                border-radius: 6px;
            }

            QLabel {
                background: transparent;
                color: #61afef;
                font-weight: bold;
            }

            QPlainTextEdit {
                background-color: #181a1f;
                color: #dcdfe4;
                border: 1px solid #30343d;
                border-radius: 4px;
                padding: 6px;
                selection-background-color: #3d424d;
            }

            QScrollBar:vertical {
                background-color: #1e1f22;
                width: 10px;
                margin: 2px;
            }

            QScrollBar::handle:vertical {
                background-color: #4b5263;
                border-radius: 4px;
                min-height: 24px;
            }

            QScrollBar::handle:vertical:hover {
                background-color: #636d83;
            }
        """)
    
    
    def show_documentation(self, global_pos, info: str):
        """Load Jedi output into the popup and display it near the cursor."""
        
        # splitlines() turns a multi-line string into a list:
        # "Path\n(class)\n\nDocs" -> ["Path", "(class)", "", "Docs"]
        lines = info.splitlines()
        
        # The first line is a compact heading. If Jedi returned an empty string
        # for any reason, use a safe fallback title.
        title = lines[0] if lines else "Documentation"
        
        # Everything after the first line goes into the scrollable body.
        # strip() removes only leading/trailing blank lines, 
        # not internal formatting or indedtation in docstring examples.
        body = "\n".join(lines[1:]).strip()
        
        self.title_label.setText(title)
        self.body_edit.setPlainText(body or "No documentation available")
        
        # Always begin a new documentation result at the top instead of
        # preserving the scrollbar position from the previously hovered item.
        self.body_edit.verticalScrollBar().setValue(0)
        
        # Ask Qt for the natural size of the layout, then clamp it to the 
        # limits above. The body edit becomes scrollable when content is tall.
        self.adjustSize()
        self.resize(
            min(max(self.width(), self.MIN_WIDTH), self.MAX_WIDTH),
            min(max(self.height(), self.MIN_HEIGHT), self.MAX_HEIGHT),
        )
        
        # Do not place the popup directly under the mouse pointer.
        # A small lower-right offset keeps the hovered identifier visible.
        offset= QPoint(16, 20)
        self.move(global_pos + offset)
        
        self.show()
        self.raise_()
    
    def set_documentation_font(self, font: QFont):
        """Use a smaller copy of the editor font in the documentation popup."""
        popup_font = QFont(font)
        
        # Keep hover documentation slightly smaller than source text while never allowing an unreadably tiny font size
        popup_font.setPointSize(max(font.pointSize() - 2, 9))
        
        self.title_label.setFont(popup_font)
        self.body_edit.setFont(popup_font)
        
        
        
        
        
        
    