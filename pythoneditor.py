from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QKeyEvent
from PyQt5.Qsci import QsciScintilla, QsciAPIs
from pathlib import Path

from custompythonlexer import PyCustomLexer
from autocompleter import AutoCompleter
from definition_finder import DefinitionFinder


class PythonEditor(QsciScintilla):
    goto_definition_requested = pyqtSignal(str, int, int)
    def __init__(self, parent=None, path: Path = None, is_python_file: bool = True):
        super(PythonEditor, self).__init__(parent)
        self.path = path
        self.full_path = self.path.absolute() if self.path else None
        self.is_python_file = is_python_file
        self._loading_text = False
        self._shutting_down = False
        self._autocomplete_timer = QTimer(self)
        self._autocomplete_timer.setSingleShot(True)
        self._autocomplete_timer.setInterval(300)
        self._autocomplete_timer.timeout.connect(self._trigger_autocomplete)

        self._pending_line = 0
        self._pending_index = 0
        self.cursorPositionChanged.connect(self._cursorPositionChanged)

        self.setUtf8(True)

        self.window_font = QFont("sans-serif")
        self.window_font.setPointSize(13)
        self.setFont(self.window_font)

        self.setBraceMatching(QsciScintilla.SloppyBraceMatch)
        self.setIndentationGuides(True)
        self.setTabWidth(4)
        self.setIndentationsUseTabs(False)
        self.setAutoIndent(True)
        self.setEolMode(QsciScintilla.EolWindows)
        self.setEolVisibility(False)

        self.setAutoCompletionSource(QsciScintilla.AcsAll)
        self.setAutoCompletionThreshold(3)
        self.setAutoCompletionCaseSensitivity(False)
        self.setAutoCompletionUseSingle(QsciScintilla.AcusNever)

        self.setCaretForegroundColor(QColor("#f31122"))
        self.setCaretLineVisible(True)
        self.setCaretLineBackgroundColor(QColor("#3d424d"))
        self.setCaretWidth(2)

        if self.is_python_file:
            self.py_lexer = PyCustomLexer(self)
            self.py_lexer.setDefaultFont(self.window_font)

            self._api = QsciAPIs(self.py_lexer)
            self.auto_completer = AutoCompleter(str(self.full_path) if self.full_path else None, self._api)
            self.auto_completer.completions_ready.connect(self._apply_completions)
            self.auto_completer.error.connect(self._handle_completion_error)

            self.setLexer(self.py_lexer)
        else:
            self.setPaper(QColor("#1e1f22"))
            self.setColor(QColor("#abb2bf"))

        self.definition_finder = DefinitionFinder()
        self.definition_finder.definition_found.connect(self._on_definition_found)
        self.definition_finder.definition_not_found.connect(self._on_definition_not_found)
        self.definition_finder.error.connect(self._on_definition_error)

        self.setMarginType(0, QsciScintilla.NumberMargin)
        self.setMarginWidth(0, "0000")
        self.setMarginsForegroundColor(QColor('#ff888888'))
        self.setMarginsBackgroundColor(QColor('#1e1f22'))
        self.setMarginsFont(self.window_font)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setWrapMode(QsciScintilla.WrapNone)

    def goto_definition(self):
        """Trigger the definition search at the current cursor position."""
        if not self.is_python_file or self._shutting_down:
            return

        pos = self.getCursorPosition()
        line, index = pos[0] + 1, pos[1]
        text = self.text()
        if not text.strip():
            return

        file_path = str(self.full_path) if self.full_path else None
        self.definition_finder.find(line, index, text, file_path)

    def _on_definition_found(self, module_path: str, line: int, column: int):
        """Called when Jedi finds a definition location."""
        if self._shutting_down:
            return
        # Emit a signal that MainWindow can connect to. We need MainWindow to open the file (it might be a different file)
        # and se the cursor position. For same-file navigation, we can do it directly
        if self.full_path and str(self.full_path) == module_path:
            # Definition is in the same file
            self.setCursorPosition(line - 1, column)
            self.ensureLineVisible(line - 1)
            self.setFocus()
        else:
            # Definition is in a different file - MainWindow needs to open it.
            # We emit a signal that MainWindow connects to (You need to add this signal to the class)
            self.goto_definition_requested.emit(module_path, line - 1, column)

    def _on_definition_not_found(self):
        """Called when no definition is found."""
        if not self._shutting_down:
            pass

    def _on_definition_error(self):
        """Called when an error occurs during definition lookup."""
        if not self._shutting_down:
            pass

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_F12:
            self.goto_definition()
            return
        if e.modifiers() == Qt.KeyboardModifier.ControlModifier and e.key() == Qt.Key.Key_Space:
            if self.is_python_file:
                pos = self.getCursorPosition()
                self.auto_completer.get_completions(pos[0]+1, pos[1], self.text())
                self.autoCompleteFromAPIs()
                return
        if e.modifiers() == Qt.KeyboardModifier.ControlModifier and e.key() == Qt.Key.Key_X: #Cut Shortcut
            if not self.hasSelectedText():
                line, index = self.getCursorPosition()
                self.setSelection(line, 0, line, self.lineLength(line))
                self.cut()
                return

        return super().keyPressEvent(e)

    def setTextSafely(self, text: str):
        self._loading_text = True
        try:
            self.blockSignals(True)
            self.setText(text)
        finally:
            self.blockSignals(False)
            self._loading_text = False

    def _cursorPositionChanged(self, line: int, index: int) -> None:
        """Called when the cursor moves. Starts a debounce timer instead
        of immediately running Jedi analysis."""
        if not self.is_python_file or self._loading_text or self._shutting_down:
            return
        # Store the position and restart the timer
        # The timer will fire 300ms after the LAST cursor movement
        # If the user keeps moving the cursor, the timer keeps resetting
        self._pending_line = line
        self._pending_index = index
        self._autocomplete_timer.start()

    def _trigger_autocomplete(self):
        """Called 300ms after the cursor stopped moving. Now run Jedi."""
        if self._shutting_down or self._loading_text:
            return
        text = self.text()
        if not text.strip():
            return
        self.auto_completer.get_completions(
            self._pending_line + 1, self._pending_index, text
        )

    def _apply_completions(self, names):
        if self._shutting_down:
            return
        self._api.clear()
        for name in names:
            self._api.add(name)
        self._api.prepare()

    def _handle_completion_error(self, err: str):
        if not self._shutting_down:
            print("Autocomplete error:", err)

    def shutdown(self):
        self._shutting_down = True
        self._loading_text = True
        if hasattr(self, "auto_completer"):
            self.auto_completer.requestInterruption()
            try:
                self.auto_completer.completions_ready.disconnect(self._apply_completions)
            except TypeError:
                pass
            try:
                self.auto_completer.error.disconnect(self._handle_completion_error)
            except TypeError:
                pass
            if self.auto_completer.isRunning():
                self.auto_completer.wait()
        if hasattr(self, "definition_finder"):
            self.definition_finder.shutdown()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)