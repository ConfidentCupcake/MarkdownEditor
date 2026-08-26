import sys

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QKeyEvent, QMouseEvent
from PyQt5.Qsci import QsciScintilla, QsciAPIs
from PyQt5.QtWidgets import QToolTip, QMenu
from pathlib import Path

from custompythonlexer import PyCustomLexer
from autocompleter import AutoCompleter
from definition_finder import DefinitionFinder
from hover_helper import HoverHelper
from ruff_diagnostics_controller import RuffDiagnosticsController
from ruff_service import RuffService
 

class PythonEditor(QsciScintilla):
    goto_definition_requested = pyqtSignal(str, int, int)
    focused = pyqtSignal(object)
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
        self.setAutoCompletionThreshold(2)
        self.setAutoCompletionCaseSensitivity(False)
        self.setAutoCompletionUseSingle(QsciScintilla.AcusNever)

        self.setCaretForegroundColor(QColor("#f31122"))
        self.setCaretLineVisible(True)
        self.setCaretLineBackgroundColor(QColor("#3d424d"))
        self.setCaretWidth(2)

        if self.is_python_file:
            self.ruff_diagnostics = RuffDiagnosticsController(self, sys.executable)

            self.ruff_service = RuffService(sys.executable)
            
            self.hover_helper = HoverHelper()
            self.hover_helper.hover_info_ready.connect(self._on_hover_ready)
            self.hover_helper.hover_info_empty.connect(self._on_hover_empty)
            self._hover_timer = QTimer(self)
            self._hover_timer.setSingleShot(True)
            self._hover_timer.setInterval(500)
            self._hover_timer.timeout.connect(self._trigger_hover)
            self.setMouseTracking(True)

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


    def _trigger_hover(self):
        """Called 500 ms after the mouse stopped moving. Run Jedi."""
        if self._shutting_down:
            return
            
        pos = getattr(self, "_last_mouse_pos", None)
        if pos is None:
            return
            
        # Get the byte position from mouse coordinates
        # SCI_POSITIONFROMPOINT = 2022
        byte_pos = self.SendScintilla(2022, pos.x(), pos.y())
        if byte_pos < 0:
            return
            
        # Convert byte position to line/column manually
        # (SendScintilla 2126 SCI_LINEFROMPOSITION returns 0 in some PyQt5 versions)
        text = self.text()
        if not text.strip():
            return
            
        text_bytes = text.encode("utf-8")
        if byte_pos >= len(text_bytes):
            byte_pos = len(text_bytes) - 1
        
        text_before = text_bytes[:byte_pos].decode('utf-8', errors = 'ignore')
        
        # Count newlines to het the line number (0-based)
        line = text_before.count('\n')
        
        # Find the column: distance from the last newline byte_pos
        last_newline = text_before.rfind('\n')
        if last_newline == -1:
            column = len(text_before)
        else:
            column = len(text_before) - last_newline - 1
            
        file_path = str(self.full_path) if self.full_path else None
        # Jedi uses 1-based line numbers
        self.hover_helper.get_hover(line + 1, column, text, file_path)
        
    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        """Let Ruff display diagnostics hover first, then keep normal QScintillia/Jedi hover behavior available."""
        if self.is_python_file and hasattr(self, "ruff_diagnostics"):
            self.ruff_diagnostics.hover(e.pos())
        
        if not self.is_python_file or self._shutting_down:
            return super().mouseMoveEvent(e)
        
        self._last_mouse_pos = e.pos()
        self._hover_timer.start()
        return super().mouseMoveEvent(e)
        
    def contextMenuEvent(self, event):
        """Let the Ruff controller handle a right-click on a Ruff diagnostic.
        Otherwise, show QScintillia's normal enitor context menu."""
        if self.is_python_file and hasattr(self, "ruff_diagnostics") and self.ruff_diagnostics.context_menu(event):
            event.accept()
            return
        super().contextMenuEvent(event)
    
    def _on_hover_ready(self, info: str):
        """Show the tooltip with the docstring."""
        if self._shutting_down:
            return
        from PyQt5.QtCore import QPoint
        pos = self.mapToGlobal(getattr(self, "_last_mouse_pos", QPoint(0, 0)))
        QToolTip.showText(pos, info, self)
        
    def _on_hover_empty(self):
        """Hide the tooltip if no docstring available."""
        QToolTip.hideText()
    
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
    
    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focused.emit(self)
    
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

    def _on_definition_error(self, err: str):
        """Called when an error occurs during definition lookup."""
        if not self._shutting_down:
            print("Definition error:", err)

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
            self.setModified(False)
        finally:
            self.blockSignals(False)
            self._loading_text = False
        
        if self.is_python_file and hasattr(self, "ruff_diagnostics"):
            self.ruff_diagnostics.initial_check()

    def _ruff_logical_path(self, target_path: Path | None = None) -> Path:
        """Return the path Ruff uses to find project.toml"""
        if target_path is not None:
            return Path(target_path)
        if self.full_path is not None:
            return self.full_path
        return Path.cwd() / "untitled.py"

    def _replace_document_text(self, new_text: str) -> bool:
        """Replace the whole document as one Ctrl+Z undo operation."""
        if new_text == self.text():
            return False
        line, column = self.getCursorPosition()
        self.beginUndoAction()
        try:
            self.selectAll()
            self.replace(new_text)
        finally:
            self.endUndoAction()
        line = min(line, max(self.lines() - 1, 0))
        self.setCursorPosition(line, min(column, self.lineLength(line)))
        return True

    def apply_safe_fixes_with_ruff(self, target_path: Path) -> bool:
        """Apply Ruff safe fixes in memory; never write the real file here."""
        source = self.ruff_service.safe_fix(self.text(), self._ruff_logical_path(target_path))
        return self._replace_document_text(source)

    def format_with_ruff(self, target_path: Path | None = None) -> bool:
        """Format current Python source in memory without saving it."""
        source = self.ruff_service.format(self.text(), self._ruff_logical_path(target_path))
        return self._replace_document_text(source)

    def organize_imports_with_ruff(self, target_path: Path | None = None) -> bool:
        """Apply Ruff I-rule import sorting in memory without saving it."""
        source = self.ruff_service.organize_imports(
            self.text(), self._ruff_logical_path(target_path)
        )
        return self._replace_document_text(source)

    def _cursorPositionChanged(self, line: int, index: int) -> None:
        """Called when the cursor moves. Starts a debounce timer instead
        of immediately running Jedi analysis."""
        if not self.is_python_file or self._loading_text or self._shutting_down:
            return
        # Store the position and restart the timer
        # will fire 300ms after the LAST cursor movement
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
        if hasattr(self, "hover_helper"):
            self.hover_helper.shutdown()
        if hasattr(self, "ruff_diagnostics"):
            self.ruff_diagnostics.shutdown()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)