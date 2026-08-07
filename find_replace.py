from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
    QPushButton, QCheckBox, QLabel, QShortcut
)
from PyQt5.Qsci import QsciScintilla


class FindReplaceBar(QWidget):
    """
    A sloating search/replace bar that sits at the top of the editor area.
    
    Signals: 
        find_requested(str, bool, bool, bool) -> text, case_sensitive, whole_word, regex
        replace_requested(str) -> replacement text
        replace_all_requested(str, str, bool, bool, bool) -> find, replace, cs, ww, regex
        closed() -> bar was closed by user
        
    The bar itself doesn't do the searching. It collects the user's input and emits signals. MainWindow connects those signals to the actual
    QScintilla search methods. This keeps the UI seperate from the logic.
    """
    
    # Signals -> define at class level, not __init__
    find_next_requested = pyqtSignal(str, bool, bool, bool) 
    find_prev_requested = pyqtSignal(str, bool, bool, bool)
    replace_requested = pyqtSignal(str, str, bool, bool, bool)
    replace_all_requested = pyqtSignal(str, str, bool, bool, bool)
    closed = pyqtSignal()
    
    def __init__(self, show_replace: bool = False, parent=None):
        """
        show_replace: if True, show the replace fiels (Ctrl+H mode).
                                if False, only show find field (Ctrl+F mode).
        """
        super().__init__(parent)
        self._show_replace = show_replace
        self._init_ui()
        self._connect_signals()
        
    def _init_ui(self):
        # QVBoxLayout stacks things top-to-bottom
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8,6,8,6)
        layout.setSpacing(6)
        
        # --- Find row ---
        find_row = QHBoxLayout()
        find_row.setSpacing(6)

        find_label = QLabel("Find")
        find_label.setFixedWidth(45)
        find_label.setStyleSheet("color: #636d83; font-size: 12px; padding-left: 4px;")

        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("Find...")
        self.find_input.setClearButtonEnabled(True)
        # setClearButtonEnable adds a small X button inside the field
        
        self.find_next_btn = QPushButton("↓")
        self.find_next_btn.setFixedSize(28,28)
        self.find_next_btn.setToolTip("Find next match (Enter)")
        self.find_prev_btn = QPushButton("↑")
        self.find_prev_btn.setFixedSize(28,28)
        self.find_prev_btn.setToolTip("Find previous match (Shift+Enter)")
        
        self.close_btn = QPushButton("X")
        self.close_btn.setFixedSize(28,28)
        self.close_btn.setToolTip("Close (Escape)")
        
        find_row.addWidget(find_label)
        find_row.addWidget(self.find_input)
        find_row.addWidget(self.find_next_btn)
        find_row.addWidget(self.find_prev_btn)
        find_row.addWidget(self.close_btn)
        layout.addLayout(find_row)
        
        # --- Replace row (only shown in replace mode) ---
        self.replace_row_widget = QWidget()
        replace_row = QHBoxLayout(self.replace_row_widget)
        replace_row.setContentsMargins(0, 0, 0, 0)
        replace_row.setSpacing(4)

        replace_label = QLabel("Replace")
        replace_label.setFixedWidth(45)  # same width as "Find" label
        replace_label.setStyleSheet("color: #636d83; font-size: 12px; padding-left: 4px;")
        
        self.replace_input = QLineEdit()
        self.replace_input.setPlaceholderText("Replace with...")
        self.replace_input.setClearButtonEnabled(True)
        
        self.replace_btn = QPushButton("Replace")
        self.replace_btn.setFixedHeight(28)
        self.replace_all_btn = QPushButton("Replace All")
        self.replace_all_btn.setFixedHeight(28)
        self.replace_all_btn.setToolTip("Replace all occurrences")
        
        replace_row.addWidget(replace_label)
        replace_row.addWidget(self.replace_input)
        replace_row.addWidget(self.replace_btn)
        replace_row.addWidget(self.replace_all_btn)
        layout.addWidget(self.replace_row_widget)
        
        # --- Options row ---
        options_row = QHBoxLayout()
        options_row.setSpacing(12)

        self.case_sensitive_cb = QCheckBox("Aa")
        self.case_sensitive_cb.setToolTip("Case sensitive")
        self.case_sensitive_cb.setFixedHeight(26)

        self.whole_word_cb = QCheckBox("W")
        self.whole_word_cb.setToolTip("Whole word")
        self.whole_word_cb.setFixedHeight(26)

        self.regex_cb = QCheckBox(".*")
        self.regex_cb.setToolTip("Regular expression")
        self.regex_cb.setFixedHeight(26)
        
        options_row.addWidget(self.case_sensitive_cb)
        options_row.addWidget(self.whole_word_cb)
        options_row.addWidget(self.regex_cb)
        options_row.addStretch()
        layout.addLayout(options_row)
        
        # Show/hide replace row based on mode
        self.replace_row_widget.setVisible(self._show_replace)

        self.setObjectName("FindReplaceBar")

        # Styling - match your dark theme
        self.setStyleSheet("""
            FindReplaceBar {
                background-color: #21252b;
                border: 1px solid #181a1f;
                border-radius: 6px;
            }

            QLabel {
                background: transparent;
                color: #636d83;
                font-size: 12px;
            }

            QLineEdit {
                background-color: #1b1d23;
                color: #dcdfe4;
                border: 1px solid #3d424d;
                border-radius: 4px;
                padding: 4px 8px;
                selection-background-color: #3d424d;
            }
            QLineEdit:focus {
                border: 1px solid #528bff;
            }

            QPushButton {
                background-color: #2c313a;
                color: #dcdfe4;
                border: 1px solid #3d424d;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #3d424d;
                border: 1px solid #4b5263;
            }
            QPushButton:pressed {
                background-color: #1b1d23;
            }

            QCheckBox {
                background: transparent;
                color: #636d83;
                font-size: 13px;
                font-family: "Consolas", monospace;
                padding: 2px 8px;
                border-radius: 4px;
            }
            QCheckBox:checked {
                color: #61afef;
                background-color: #2c313a;
            }
            QCheckBox:hover {
                color: #dcdfe4;
            }
        """)
        
    def _connect_signals(self):
        # Enter in find input -> find next
        # returnPressed is a built-in QLineEdit signal
        # Docs: https://doc.qt.io/qt-5/qlineedit.html#returnPressed
        self.find_input.returnPressed.connect(self._on_find_next)

        # Shift+Enter → find previous
        shortcut = QShortcut(QKeySequence("Shift+Return"), self.find_input)
        shortcut.activated.connect(self._on_find_prev)

        # Escape → close
        esc_shortcut = QShortcut(QKeySequence("Escape"), self)
        esc_shortcut.activated.connect(self._on_close)
        
        # Button clicks
        self.find_next_btn.clicked.connect(self._on_find_next)
        self.find_prev_btn.clicked.connect(self._on_find_prev)
        self.replace_btn.clicked.connect(self._on_replace)
        self.replace_all_btn.clicked.connect(self._on_replace_all)
        self.close_btn.clicked.connect(self._on_close)
        
    def _get_options(self):
        """Read the current checkbox states and return (cs, ww, regex)."""
        return (
            self.case_sensitive_cb.isChecked(),
            self.whole_word_cb.isChecked(),
            self.regex_cb.isChecked(),
        )
    
    def _on_find_next(self):
        text = self.find_input.text()
        if not text:
            return
        
        cs, ww, regex = self._get_options()
        self.find_next_requested.emit(text, cs, ww, regex)
        
    def _on_find_prev(self):
        text = self.find_input.text()
        if not text:
            return
        
        cs, ww, regex = self._get_options()
        self.find_prev_requested.emit(text, cs, ww, regex)
        
    def _on_replace(self):
        find_text = self.find_input.text()
        replace_text = self.replace_input.text()
        if not find_text:
            return
        
        cs, ww, regex = self._get_options()
        self.replace_requested.emit(find_text, replace_text, cs, ww, regex)
        
    def _on_replace_all(self):
        find_text = self.find_input.text()
        replace_text = self.replace_input.text()
        if not find_text:
            return
        
        cs, ww, regex = self._get_options()
        self.replace_all_requested.emit(find_text, replace_text, cs, ww, regex)
        
    def _on_close(self):
        self.hide()
        self.closed.emit()
        
    def show_find(self):
        """Switch to fin-only mode and focus the input."""
        self._show_replace = False
        self.replace_row_widget.setVisible(False)
        self.find_input.setFocus()
        self.find_input.selectAll()
        self.show()
        
    def show_replace(self):
        """Switch to find+replace mode and focus the input."""
        self._show_replace = True
        self.replace_row_widget.setVisible(True)
        self.find_input.setFocus()
        self.find_input.selectAll()
        self.show()
    
    def set_search_text(self, text: str):
        """Pre-fill the search field with the currently selected text."""
        if text:
            self.find_input.setText(text)
            self.find_input.selectAll()
