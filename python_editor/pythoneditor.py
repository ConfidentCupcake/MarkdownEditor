import sys
import re

from pathlib import Path

from PyQt5.Qsci import QsciAPIs, QsciScintilla
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QKeyEvent, QMouseEvent
from PyQt5.QtWidgets import QToolTip

from code_inteligence.autocompleter import AutoCompleter
from python_editor.custompythonlexer import PyCustomLexer
from code_inteligence.definition_finder import DefinitionFinder
from code_inteligence.documentation_popup import DocumentationPopup
from code_inteligence.hover_helper import HoverHelper
from code_inteligence.signature_helper import SignatureHelper
from code_inteligence.indentation_helper import indentation_for_new_line

from ruff_implementation.ruff_diagnostics_view import RuffDiagnosticView
from ruff_implementation.ruff_lsp_controller import RuffLspController

SCI_AUTOCACTIVE = 2102

# A Unicode Python identifier or dotted identifier chain at the end of the
# text immediately before an opening parenthesis. ``[^\W\d]`` means a Unicode
# word character except digits, so digits remain legal after the first char.
CALLABLE_CHAIN_RE = re.compile(
    r"(?:[^\W\d]\w*)(?:\.(?:[^\W\d]\w*))*$",
    re.UNICODE,
)

class PythonEditor(QsciScintilla):
    goto_definition_requested = pyqtSignal(str, int, int)
    focused = pyqtSignal(object)
    shutdown_complete = pyqtSignal(object)

    def __init__(self, parent=None, path: Path = None, is_python_file: bool = True, ruff_lsp_client = None):
        super().__init__(parent)
        
        # Store the absolute logical file path. Ruff LSP uses it to form the stable
        # document UEI sent in didOpen/didChanged notifications.
        
        self.path = path
        self.full_path = self.path.absolute() if self.path else None
        self.is_python_file = is_python_file
        self.ruff_lsp = None
        self._manual_completion_generation = None

        self._ruff_hover_active = False
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

        # NOTE: no editor-level font anymore. The theme (via the lexer)
        # owns family, size, colors and paper — one source of truth.
        # The lexer is created below; helpers on it (editor_font(),
        # editor_color()) supply the margins/popup/caret values.

        self.setBraceMatching(QsciScintilla.SloppyBraceMatch)
        self.setIndentationGuides(True)
        self.setTabWidth(4)
        self.setIndentationsUseTabs(False)
        self.setAutoIndent(True)
        self.setEolMode(
            QsciScintilla.EolUnix if sys.platform != "win32" else QsciScintilla.EolWindows
        )
        self.setEolVisibility(False)

        self.setAutoCompletionSource(QsciScintilla.AcsAll)
        self.setAutoCompletionThreshold(2)
        self.setAutoCompletionCaseSensitivity(False)
        self.setAutoCompletionUseSingle(QsciScintilla.AcusNever)

        # Caret styling defaults; replaced by theme values once the lexer
        # exists (see _apply_theme_editor_style below + _apply_editor_settings
        # in main.py after a lexer swap). Kept as safe fallbacks so the
        # widget is never unstyled before a theme loads.
        self.setCaretForegroundColor(QColor("#f31122"))
        self.setCaretLineVisible(True)
        self.setCaretLineBackgroundColor(QColor("#3d424d"))
        self.setCaretWidth(2)

        if self.is_python_file:
            if ruff_lsp_client is not None:
                self.ruff_lsp = RuffLspController(editor=self, client=ruff_lsp_client, parent=self)


            self._ruff_hover_timer = QTimer(self)
            self._ruff_hover_timer.setSingleShot(True)
            self._ruff_hover_timer.setInterval(150)
            self._ruff_hover_timer.timeout.connect(self._trigger_ruff_hover)

            self.signature_helper = SignatureHelper()
            self.signature_helper.signature_ready.connect(self._on_signature_ready)
            self.signature_helper.signature_empty.connect(self._on_signature_empty)

            self.hover_helper = HoverHelper()
            self.hover_helper.hover_info_ready.connect(self._on_hover_ready)
            self.hover_helper.hover_info_empty.connect(self._on_hover_empty)

            # DocumentationPopup ows only the UI. HoverHelper still performs
            # Jedi analysis is its worker thread and emits plain text results.
            # Create one popup for this editor tab. Passing self as parent means
            # Qt cleans it up automatically when this PythonEditor is destroyed.
            # The lexer loads the theme itself — font, colors and paper all
            # come from themes/theme.json (or the theme chosen in Settings).
            # Created FIRST so everything below can read its theme helpers.
            self.py_lexer = PyCustomLexer(self)

            self.documentation_popup = DocumentationPopup(self)
            # Popup font follows the theme's global editor font (the old
            # self.window_font is gone — the lexer owns fonts now).
            self.documentation_popup.set_documentation_font(
                self.py_lexer.editor_font())

            self._hover_timer = QTimer(self)
            self._hover_timer.setSingleShot(True)
            self._hover_timer.setInterval(500)
            self._hover_timer.timeout.connect(self._trigger_hover)
            self.setMouseTracking(True)

            self._apply_theme_editor_style()

            self._api = QsciAPIs(self.py_lexer)
            self.auto_completer = AutoCompleter(
                str(self.full_path) if self.full_path else None, self._api
            )
            self.auto_completer.completions_ready.connect(self._apply_completions)
            self.auto_completer.error.connect(self._handle_completion_error)

            self.setLexer(self.py_lexer)
        else:
            # No lexer in this mode yet (tab not converted): neutral defaults.
            # Once a lexer attaches, _apply_theme_editor_style takes over.
            self.setPaper(QColor("#1e1f22"))
            self.setColor(QColor("#abb2bf"))

        self.definition_finder = DefinitionFinder()
        self.definition_finder.definition_found.connect(self._on_definition_found)
        self.definition_finder.definition_not_found.connect(self._on_definition_not_found)
        self.definition_finder.error.connect(self._on_definition_error)

        # Margin 0: source-code line numbers.
        self.setMarginType(0, QsciScintilla.NumberMargin)
        self.setMarginWidth(0, "0000")

        # Margin 1: Ruff error, warning, information and quick-fix symbols.
        self.setMarginType(1, QsciScintilla.SymbolMargin)
        self.setMarginWidth(1, "000")

        ruff_marker_mask = (
            (1 << RuffDiagnosticView.ERROR_MARKER)
            | (1 << RuffDiagnosticView.WARNING_MARKER)
            | (1 << RuffDiagnosticView.INFO_MARKER)
            | (1 << RuffDiagnosticView.FIX_MARKER)
        )
        self.setMarginMarkerMask(1, ruff_marker_mask)

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setWrapMode(QsciScintilla.WrapNone)

        # Final theme application (font, margins, caret, paper) — AFTER all
        # margin setup so nothing overrides it. The old hardcoded
        # setMarginsForegroundColor/BackgroundColor/setMarginsFont lines
        # that used to live here are replaced by this one theme-driven call.
        if getattr(self, "py_lexer", None) is not None:
            self._apply_theme_editor_style()

    def _apply_theme_editor_style(self):
        """
        Push the theme's editor-section styling onto this widget.

        Called once at the end of __init__ (margin setup) and again by
        main.py's _apply_editor_settings() after a lexer swap (a fresh
        lexer resets Scintilla's style table, which wipes margin colors —
        the gutter-turns-white bug, now handled in exactly one place).

        Reads everything through the lexer so the theme stays the single
        source of truth: global editor font (family+size), paper, caret,
        caret-line highlight, and the line-number margin colors.
        """
        lexer = self.py_lexer
        f = lexer.editor_font()
        self.setFont(f)                    # STYLE_DEFAULT; lexer styles inherit it
        self.setMarginsFont(f)
        self.setPaper(lexer.defaultPaper())
        self.setCaretForegroundColor(
            lexer.editor_color("caret-color", "#f31122"))
        self.setCaretLineBackgroundColor(
            lexer.editor_color("caret-line-background", "#3d424d"))
        self.setMarginsForegroundColor(
            lexer.editor_color("margin-foreground", "#ff888888"))
        self.setMarginsBackgroundColor(
            lexer.editor_color("margin-background", "#1e1f22"))
        if hasattr(self, "documentation_popup") and self.documentation_popup:
            self.documentation_popup.set_documentation_font(f)

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

        text_before = text_bytes[:byte_pos].decode("utf-8", errors="ignore")

        # Count newlines to het the line number (0-based)
        line = text_before.count("\n")

        # Find the column: distance from the last newline byte_pos
        last_newline = text_before.rfind("\n")
        if last_newline == -1:
            column = len(text_before)
        else:
            column = len(text_before) - last_newline - 1

        file_path = str(self.full_path) if self.full_path else None
        # Jedi uses 1-based line numbers

        if getattr(self, "_ruff_hover_active", False):
            return
        self.hover_helper.get_hover(line + 1, column, text, file_path)

    def _trigger_ruff_hover(self):
        """Show Ruff diagnostic tooltip after 150ms of no mouse movement."""
        if self._shutting_down:
            return
        pos = getattr(self, "_last_mouse_pos", None)
        if pos is None:
            self._ruff_hover_active = False
            return

        if self.is_python_file and self.ruff_lsp is not None:
            # True means the current mouse location lies on a Ruff diagnostic line.
            self._ruff_hover_active = self.ruff_lsp.hover(pos)
            
            # Do not leave general documentation covering syntax/lint message.
            if self._ruff_hover_active and hasattr(self, "documentation_popup"):
                self.documentation_popup.hide()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        """Debounce both Ruff and Jedi hover tooltips."""
        if not self.is_python_file or self._shutting_down:
            return super().mouseMoveEvent(e)

        self._last_mouse_pos = e.pos()
        # Hide the old result immediately. Jedi runs asynchronously, so this prevents stale documentation
        # from appearing to belong to a new symbol while a fresh Jedi request is waiting for the hover debounce timer.
        if hasattr(self, "documentation_popup"):
            self.documentation_popup.hide()  # The previous symbol's documentation must not remain visible after the user moved to a different editor location.
        self.hover_helper.invalidate()

        if hasattr(self, "_ruff_hover_timer"):
            self._ruff_hover_timer.start()

        self._hover_timer.start()
        return super().mouseMoveEvent(e)

    def contextMenuEvent(self, event):
        """Let the Ruff controller handle a right-click on a Ruff diagnostic.
        Otherwise, show QScintillia's normal enitor context menu."""
        if self.is_python_file and self.ruff_lsp is not None and self.ruff_lsp.context_menu(event):
            event.accept()
            return
        super().contextMenuEvent(event)

    def _on_hover_ready(self, generation: int, info: str):
        """Show the tooltip with the docstring."""
        if self._shutting_down or generation != self.hover_helper.generation or not info:
            return

        # The popup only exists in Python Editor mode. hasattr() also makes this method safe
        # during shutdown or unusual partial initialization.
        if not hasattr(self, "documentation_popup"):
            return

        # _last_mouse_pos is set by mouseMoveEvent. If no mouse event has occurred,
        # there is no meaningful screen position for this popup.
        mouse_pos = getattr(self, "_last_mouse_pos", None)

        if mouse_pos is None:
            return

        # mapToGlobal converts editor-local mouse coordinates into screen
        # coordinates. Popup window use global/screen position for move()
        global_pos = self.mapToGlobal(mouse_pos)
        self.documentation_popup.show_documentation(global_pos, info)

    def _on_hover_empty(self, generation: int):
        """Hide Jedi documenation when no symbol is under the mouse."""
        if generation == self.hover_helper.generation and hasattr(self, "documentation_popup"):
            self.documentation_popup.hide()

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

    def _on_definition_found(self, generation: int, module_path: str, line: int, column: int):
        """Called when Jedi finds a definition location."""
        if self._shutting_down or generation != self.definition_finder.generation:
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

    def _on_definition_not_found(self, generation: int):
        """Called when no definition is found."""
        if generation == self.definition_finder.generation and not self._shutting_down:
            pass

    def _on_definition_error(self, generation: int, err: str):
        """Called when an error occurs during definition lookup."""
        if generation == self.definition_finder.generation and not self._shutting_down:
            print("Definition error:", err)
    
    def _handle_python_return(self) -> None:
        """Insert a newline and apply the exact indentation for the new line."""

        # Capture text only to the left of the cursor. When Enter splits a line,
        # this is the only text that determines the new line's indentation.
        old_line, old_index = self.getCursorPosition()
        left_of_caret = self.text(old_line)[:old_index]

        # The helper returns:
        # - dedented indent for a terminal statement (return/raise/break/continue/pass)
        # - current indent + one level for a suite-opening colon
        # - current indent for an ordinary statement
        desired_indent = indentation_for_new_line(
            left_of_caret,
            use_tabs=self.indentationsUseTabs(),
            width=self.tabWidth(),
        )

        self.beginUndoAction()
        try:
            # SCI_NEWLINE = 2329. Inserts a native Scintilla newline using the
            # configured EOL mode. This is the ONLY newline insertion in this
            # method — do not call it a second time.
            #
            # NOTE: with setAutoIndent(True), Scintilla ALSO copies the previous
            # line's indentation onto the new line inside SCI_NEWLINE.
            self.SendScintilla(2329)

            new_line, _new_index = self.getCursorPosition()

            # Replace whatever indentation Scintilla's auto-indent produced
            # with our computed indent, atomically, via
            # SCI_SETLINEINDENTATION (= 2126). The old approach
            # (setSelection + replace("")) is a silent NO-OP in this PyQt5
            # QScintilla binding — an empty replacement string leaves the
            # selection untouched, so the auto-indent stayed in place and
            # insertAt() stacked a second indent on top of it (bug: pressing
            # Enter after a continuation line added 16 spaces instead of 8).
            indent_columns = 0
            for ch in desired_indent:
                indent_columns += self.tabWidth() if ch == "\t" else 1
            self.SendScintilla(2126, new_line, indent_columns)

            self.setCursorPosition(new_line, len(desired_indent))
        finally:
            self.endUndoAction()

    @staticmethod
    def _has_callable_before_parenthesis(text: str) -> bool:
        """Return true when ``text`` ends in a valid callable name chain.

        The callable may follow indentation, assignment, an operator, or a
        nested opening parenthesis. It must not start in the middle of another
        identifier/number/dotted token.
        """
        match = CALLABLE_CHAIN_RE.search(text.rstrip())
        if match is None:
            return False
        if match.start() == 0:
            return True

        previous = text[match.start() - 1]
        return not (previous.isalnum() or previous in "_.")

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle editor shortcuts, indentation, and signature scheduling."""
        if event.key() == Qt.Key.Key_F12:
            self.goto_definition()
            return

        if (
                event.modifiers() == Qt.KeyboardModifier.ControlModifier
                and event.key() == Qt.Key.Key_Space
                and self.is_python_file
        ):
            line, column = self.getCursorPosition()
            self._manual_completion_generation = self.auto_completer.get_completions(
                line + 1,
                column,
                self.text(),
            )
            return

        if (
                event.modifiers() == Qt.KeyboardModifier.ControlModifier
                and event.key() == Qt.Key.Key_X
                and not self.hasSelectedText()
        ):
            line, _column = self.getCursorPosition()
            self.setSelection(line, 0, line, self.lineLength(line))
            self.cut()
            return

        if (
                event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and self.SendScintilla(SCI_AUTOCACTIVE)
        ):
            return super().keyPressEvent(event)

        if (
                self.is_python_file
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() == Qt.KeyboardModifier.NoModifier
                and not self.hasSelectedText()
        ):
            self._handle_python_return()
            return

        if event.text() == "(" and self.is_python_file and not self._shutting_down:
            line, column = self.getCursorPosition()
            before = self.text(line)[:column]
            if self._has_callable_before_parenthesis(before):
                # Let the base handler insert '(' first. The delayed lookup then
                # sees the cursor/source state Jedi expects.
                QTimer.singleShot(50, self._trigger_signature_help)

        return super().keyPressEvent(event)

    def _trigger_signature_help(self):
        """Run Jedi signature lookup at the current cursor position."""
        if self._shutting_down or self._loading_text:
            return

        line, index = self.getCursorPosition()
        text = self.text()
        if not text.strip():
            return

        file_path = str(self.full_path) if self.full_path else None
        self.signature_helper.get_signatures(line + 1, index, text, file_path)

    def _on_signature_ready(self, generation: int, signature: str):
        """Show the signature popup as a tooltip near the cursor."""
        if self._shutting_down or generation != self.signature_helper.generation:
            return

        # BUGFIX: self.cursorPos() does not exist in the PyQt5 QScintilla
        # binding -> AttributeError (hard crash inside a Qt slot) every time
        # a signature became ready. Query the caret's pixel position through
        # SendScintilla instead:
        #   2008 = SCI_GETCURRENTPOS (byte offset of the caret)
        #   2164 = SCI_POINTXFROMPOSITION, 2165 = SCI_POINTYFROMPOSITION
        from PyQt5.QtCore import QPoint

        caret = self.SendScintilla(2008)
        point = self.mapToGlobal(QPoint(
            self.SendScintilla(2164, 0, caret),
            self.SendScintilla(2165, 0, caret) - 20,  # slightly above the caret line
        ))
        QToolTip.showText(point, signature, self)

    def _on_signature_empty(self, generation: int):
        """No signature available -> hide tooltip."""
        if generation == self.signature_helper.generation:
            QToolTip.hideText()

    def setTextSafely(self, text: str):
        self._loading_text = True
        try:
            self.blockSignals(True)
            self.setText(text)
            self.setModified(False)
        finally:
            self.blockSignals(False)
            self._loading_text = False
            
        # Because textChanged was blocked, manually synchronize the real loaded file text with Ruff.
        # Without this, Ruff keeps the empty didOpen buffer.
        if self.is_python_file and self.ruff_lsp is not None:
            self.ruff_lsp.sync_from_editor()


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
        for helper_name in ("auto_completer", "signature_helper", "definition_finder"):
            helper = getattr(self, helper_name, None)
            if helper is not None:
                helper.invalidate()
        self._autocomplete_timer.start()

    def _trigger_autocomplete(self):
        """Called 300ms after the cursor stopped moving. Now run Jedi."""
        if self._shutting_down or self._loading_text:
            return
        text = self.text()
        if not text.strip():
            return
        self.auto_completer.get_completions(self._pending_line + 1, self._pending_index, text)

    def _apply_completions(self, generation, names):
        if self._shutting_down or generation != self.auto_completer.generation:
            return
        self._api.clear()
        for name in names:
            self._api.add(name)
        self._api.prepare()
        if generation == self._manual_completion_generation:
            self._manual_completion_generation = None
            self.autoCompleteFromAPIs()

    def _handle_completion_error(self, err: str):
        if not self._shutting_down:
            print("Autocomplete error:", err)

    def _analysis_workers(self):
        """Return every per-editor QThread that must outlive the tab."""
        names = (
            "auto_completer",
            "definition_finder",
            "hover_helper",
            "signature_helper",
        )
        return [getattr(self, name) for name in names if hasattr(self, name)]

    def shutdown(self):
        """Stop producing results and emit when all workers naturally finish."""
        if self._shutting_down:
            return
        self._shutting_down = True
        self._loading_text = True
        self._autocomplete_timer.stop()
        for timer_name in ("_hover_timer", "_ruff_hover_timer"):
            timer = getattr(self, timer_name, None)
            if timer is not None:
                timer.stop()

        if self.ruff_lsp is not None:
            self.ruff_lsp.shutdown()

        workers = self._analysis_workers()
        for worker in workers:
            worker.shutdown()
            try:
                worker.finished.connect(self._check_shutdown_complete)
            except TypeError:
                pass
        self._check_shutdown_complete()

    def _check_shutdown_complete(self):
        """Signals exactly when no owned worker is still executing."""
        if self._shutting_down and not any(worker.isRunning() for worker in self._analysis_workers()):
            self.shutdown_complete.emit(self)

    def closeEvent(self, event):
        self.shutdown()
        event.accept()
