from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QColor, QKeyEvent
from PyQt5.Qsci import QsciScintilla, QsciAPIs
from pathlib import Path

from custompythonlexer import PyCustomLexer
from autocompleter import AutoCompleter


class PythonEditor(QsciScintilla):
    def __init__(self, parent=None, path: Path = None, is_python_file: bool = True):
        super(PythonEditor, self).__init__(parent)
        self.path = path
        self.full_path = self.path.absolute() if self.path else None
        self.is_python_file = is_python_file
        self._loading_text = False
        self._shutting_down = False

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
        self.setAutoCompletionThreshold(1)
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
            self.setPaper(QColor("#282c34"))
            self.setColor(QColor("#abb2bf"))

        self.setMarginType(0, QsciScintilla.NumberMargin)
        self.setMarginWidth(0, "0000")
        self.setMarginsForegroundColor(QColor('#ff888888'))
        self.setMarginsBackgroundColor(QColor('#282c34'))
        self.setMarginsFont(self.window_font)

    def keyPressEvent(self, e: QKeyEvent) -> None:
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
        if not self.is_python_file or self._loading_text or self._shutting_down:
            return
        text = self.text()
        if not text.strip():
            return
        self.auto_completer.get_completions(line + 1, index, text)

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

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)