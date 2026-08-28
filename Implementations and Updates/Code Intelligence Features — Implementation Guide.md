# Code Intelligence Features — Implementation Guide

This document covers the implementation of 9 code intelligence features for the Code Editor. Each section includes code examples, explanations, and documentation references.

---

## Table of Contents

1. [Go to Definition (F12)](#1-go-to-definition-f12)
2. [Hover Tooltips](#2-hover-tooltips)
3. [Function Signature Help](#3-function-signature-help)
4. [Find All References (Shift+F12)](#4-find-all-references-shiftf12)
5. [Code Outline / Symbol Tree](#5-code-outline--symbol-tree)
6. [Real-time Error Highlighting](#6-real-time-error-highlighting)
7. [Code Formatting](#7-code-formatting)
8. [Import Sorting](#8-import-sorting)
9. [Type Hint Display](#9-type-hint-display)

---

## Documentation Reference

| What | Link |
|---|---|
| Jedi API | [jedi.readthedocs.io](https://jedi.readthedocs.io/en/latest/docs/api.html) |
| QScintilla methods | [Riverbank QScintilla](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |
| QToolTip | [Qt QToolTip docs](https://doc.qt.io/qt-5/qtooltip.html) |
| QThread | [Qt QThread docs](https://doc.qt.io/qt-5/qthread.html) |
| QProcess | [Qt QProcess docs](https://doc.qt.io/qt-5/qprocess.html) |
| Python ast module | [Python ast docs](https://docs.python.org/3/library/ast.html) |
| pyflakes | [pyflakes docs](https://github.com/PyCQA/pyflakes) |
| autopep8 | [autopep8 docs](https://pypi.org/project/autopep8/) |
| black | [black docs](https://black.readthedocs.io/) |
| isort | [isort docs](https://pycqa.github.io/isort/) |
| Scintilla indicators | [Scintilla Indicator docs](https://www.scintilla.org/ScintillaDoc.html#Indicators) |
| Scintilla markers | [Scintilla Marker docs](https://www.scintilla.org/ScintillaDoc.html#Markers) |

---

## 1. Go to Definition (F12)

### Concept

When the user presses F12 (or Ctrl+Clicks a symbol), the editor jumps to where that function, class, or variable is defined. Jedi analyzes the code at the cursor position and returns the definition location (file path, line, column).

### Key Jedi Methods

| Method | Description | Docs |
|---|---|---|
| `jedi.Script(code, path)` | Creates a Jedi analysis object | [Link](https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.Script) |
| `script.goto(line, column, follow_imports=True)` | Returns definition locations | [Link](https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.Script.goto) |
| `definition.module_path` | File path where the symbol is defined | [Link](https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.api.classes.Name) |
| `definition.line` | Line number (1-based) | [Link](https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.api.classes.Name) |
| `definition.column` | Column number (0-based) | [Link](https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.api.classes.Name) |

### Key QScintilla Methods

| Method | Description | Docs |
|---|---|---|
| `editor.getCursorPosition()` | Returns `(line, index)` tuple, 0-based | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |
| `editor.setCursorPosition(line, index)` | Moves cursor to position | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |
| `editor.ensureLineVisible(line)` | Scrolls to make line visible | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |

### Step 1: Create `definition_finder.py`

Jedi analysis takes a few hundred milliseconds. Running it on the main thread would freeze the GUI. This QThread runs the analysis in the background.

```python
from PyQt5.QtCore import QThread, pyqtSignal
from jedi import Script


class DefinitionFinder(QThread):
    """
    Background thread that finds where a symbol is defined using Jedi.

    Signals:
        definition_found(str, int, int) — module_path, line, column
        definition_not_found() — no definition found
        error(str) — an error occurred

    The thread is reusable — call find() again for each new lookup.
    If a previous search is still running, the new request is ignored.
    """
    definition_found = pyqtSignal(str, int, int)
    definition_not_found = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__(None)
        self.code = ""
        self.file_path = None
        self.line = 1
        self.column = 0
        self._shutting_down = False

    def find(self, line: int, column: int, code: str, file_path: str = None):
        """
        Start the definition search.

        Parameters:
            line: 1-based line number (Jedi uses 1-based lines)
            column: 0-based column number
            code: The full source code of the file
            file_path: The file's path (helps Jedi resolve relative imports)
        """
        if self.isRunning():
            return
        self.line = line
        self.column = column
        self.code = code
        self.file_path = file_path
        self.start()

    def run(self):
        try:
            # Jedi Script takes the code string and optional file path.
            # The path helps Jedi resolve relative imports — if the file
            # does `from utils import helper`, Jedi needs to know where
            # the file is to find utils.py.
            script = Script(code=self.code, path=self.file_path)

            # goto() returns a list of Name objects representing definitions.
            # follow_imports=True means: if the symbol is imported from
            # another module, follow the import chain to the actual definition.
            # For example, if you F12 on `os.path.join`, Jedi follows:
            #   import os → os module → os.path module → join function
            definitions = script.goto(
                line=self.line,
                column=self.column,
                follow_imports=True
            )

            if self._shutting_down:
                return

            if definitions:
                definition = definitions[0]
                module_path = definition.module_path
                line = definition.line
                column = definition.column

                if module_path is None:
                    # Built-in or compiled module (like `print`, `len`)
                    # module_path is None because there's no source file.
                    self.definition_not_found.emit()
                else:
                    self.definition_found.emit(
                        str(module_path), line, column
                    )
            else:
                self.definition_not_found.emit()

        except Exception as err:
            if not self._shutting_down:
                self.error.emit(str(err))

    def shutdown(self):
        """Stop the thread cleanly. Called when the editor is closing."""
        self._shutting_down = True
        self.requestInterruption()
        if self.isRunning():
            self.wait()
```

### Step 2: Add to PythonEditor

In `pythoneditor.py`:

```python
from definition_finder import DefinitionFinder

class PythonEditor(QsciScintilla):
    # Signal: emitted when the user wants to go to a definition in another file
    # MainWindow connects this to open the file and jump to the position
    goto_definition_requested = pyqtSignal(str, int, int)  # path, line, column

    def __init__(self, parent=None, path: Path = None, is_python_file: bool = True):
        # ... existing init code ...

        if self.is_python_file:
            # ... existing lexer and autocompleter setup ...

            # Definition finder
            self.definition_finder = DefinitionFinder()
            self.definition_finder.definition_found.connect(self._on_definition_found)
            self.definition_finder.definition_not_found.connect(self._on_definition_not_found)
            self.definition_finder.error.connect(self._on_definition_error)
```

Add the handler methods:

```python
    def goto_definition(self):
        """Trigger the definition search at the current cursor position."""
        if not self.is_python_file or self._shutting_down:
            return

        # getCursorPosition returns (line, index), both 0-based
        # Jedi uses 1-based line numbers, so we add 1
        line, index = self.getCursorPosition()
        text = self.text()
        if not text.strip():
            return

        file_path = str(self.full_path) if self.full_path else None
        # Pass 1-based line to Jedi
        self.definition_finder.find(line + 1, index, text, file_path)

    def _on_definition_found(self, module_path: str, line: int, column: int):
        """Called when Jedi finds the definition location."""
        if self._shutting_down:
            return

        if self.full_path and str(self.full_path) == module_path:
            # Definition is in the same file — jump directly
            # Convert 1-based line to 0-based for QScintilla
            self.setCursorPosition(line - 1, column)
            self.ensureLineVisible(line - 1)
            self.setFocus()
        else:
            # Definition is in a different file — ask MainWindow to open it
            self.goto_definition_requested.emit(module_path, line - 1, column)

    def _on_definition_not_found(self):
        """Called when no definition is found (e.g. built-in symbols)."""
        if not self._shutting_down:
            pass  # MainWindow can show a status message

    def _on_definition_error(self, err: str):
        """Called when an error occurs during definition lookup."""
        if not self._shutting_down:
            print("Definition error:", err)
```

### Step 3: Handle F12 in keyPressEvent

```python
    def keyPressEvent(self, e: QKeyEvent) -> None:
        # F12 — Go to definition
        if e.key() == Qt.Key.Key_F12:
            self.goto_definition()
            return
        # ... existing handlers ...
        return super().keyPressEvent(e)
```

### Step 4: Connect in MainWindow

In `set_new_tab` (and `new_file`), after creating the editor:

```python
        if isinstance(self.editor, PythonEditor):
            self.editor.goto_definition_requested.connect(self._open_file_at_position)
```

Add the handler:

```python
    def _open_file_at_position(self, file_path: str, line: int, column: int):
        """Open a file and jump to a specific line/column."""
        self.set_new_tab(Path(file_path))
        editor = self.tab_view.currentWidget()
        if editor is not None:
            editor.setCursorPosition(line, column)
            editor.ensureLineVisible(line)
            editor.setFocus()
```

### Step 5: Add menu action

```python
        goto_def_action = edit_menu.addAction("Go to Definition")
        goto_def_action.setShortcut("F12")
        goto_def_action.setShortcutContext(Qt.ApplicationShortcut)
        goto_def_action.triggered.connect(self._trigger_goto_definition)

    def _trigger_goto_definition(self):
        editor = self.tab_view.currentWidget()
        if isinstance(editor, PythonEditor):
            editor.goto_definition()
```

### Step 6: Shutdown

In `PythonEditor.shutdown`:

```python
    def shutdown(self):
        self._shutting_down = True
        self._loading_text = True
        if hasattr(self, "auto_completer"):
            self.auto_completer.shutdown()
        if hasattr(self, "definition_finder"):
            self.definition_finder.shutdown()
```

### How the Flow Works

```
User presses F12 (cursor is on "join" in "os.path.join()")
    ↓
PythonEditor.goto_definition()
    ↓
DefinitionFinder.find(line=2, column=10, code=..., file_path="test.py")
    ↓
Background thread: Jedi.Script(code).goto(line=2, column=10, follow_imports=True)
    ↓
Jedi returns: Name(name="join", module_path="/usr/lib/python3/os/path.py",
                   line=70, column=4)
    ↓
definition_found signal: ("/usr/lib/python3/os/path.py", 70, 4)
    ↓
If same file: editor.setCursorPosition(69, 4)  (0-based)
If different file: goto_definition_requested → MainWindow opens the file
```

---

## 2. Hover Tooltips

### Concept

When the user hovers the mouse over a symbol (function, class, variable) for a moment, a tooltip appears showing the symbol's docstring and type information.

### Key Methods

| Method | Description | Docs |
|---|---|---|
| `jedi.Script(code, path)` | Creates a Jedi analysis object | [Link](https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.Script) |
| `script.help(line, column)` | Returns docstring and help info | [Link](https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.Script.help) |
| `QToolTip.showText(pos, text, widget)` | Shows a tooltip at a position | [Link](https://doc.qt.io/qt-5/qtooltip.html#showText) |
| `editor.mouseMoveEvent(e)` | Override to detect hover | [Link](https://doc.qt.io/qt-5/qwidget.html#mouseMoveEvent) |
| `editor.getCursorPosition()` | Get cursor position | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |
| `editor.charAt(pos)` | Get character at position | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |
| `editor.positionFromPoint(point)` | Convert mouse position to byte position | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |

### Step 1: Create `hover_helper.py`

```python
from PyQt5.QtCore import QThread, pyqtSignal, QTimer
from jedi import Script


class HoverHelper(QThread):
    """
    Background thread that retrieves hover information (docstrings) for
    the symbol at a given position using Jedi.

    Signals:
        hover_info_ready(str) — the docstring text to display
        hover_info_empty() — no docstring available
    """
    hover_info_ready = pyqtSignal(str)
    hover_info_empty = pyqtSignal()

    def __init__(self):
        super().__init__(None)
        self.code = ""
        self.file_path = None
        self.line = 1
        self.column = 0
        self._shutting_down = False

    def get_hover(self, line: int, column: int, code: str, file_path: str = None):
        """Start the hover lookup. Jedi uses 1-based line numbers."""
        if self.isRunning():
            return
        self.line = line
        self.column = column
        self.code = code
        self.file_path = file_path
        self.start()

    def run(self):
        try:
            script = Script(code=self.code, path=self.file_path)

            # help() returns a list of Name objects with docstring info.
            # For a function like `def foo(): """Does stuff."""`,
            # help() returns a Name with .docstring_raw() = "Does stuff."
            # Docs: https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.Script.help
            help_results = script.help(line=self.line, column=self.column)

            if self._shutting_down:
                return

            if help_results:
                # Build the tooltip text from the first result
                name = help_results[0]
                parts = []

                # name.name is the symbol name (e.g. "print", "MyClass")
                if name.name:
                    parts.append(name.name)

                # name.type is "function", "class", "module", "instance", etc.
                if name.type:
                    parts.append(f"({name.type})")

                # name.docstring_raw() returns the raw docstring
                # without indentation cleanup
                docstring = name.docstring_raw()
                if docstring:
                    parts.append("\n" + docstring)

                if len(parts) > 1:
                    self.hover_info_ready.emit("\n".join(parts))
                else:
                    self.hover_info_empty.emit()
            else:
                self.hover_info_empty.emit()

        except Exception:
            if not self._shutting_down:
                self.hover_info_empty.emit()

    def shutdown(self):
        self._shutting_down = True
        self.requestInterruption()
        if self.isRunning():
            self.wait()
```

### Step 2: Add hover support to PythonEditor

```python
from hover_helper import HoverHelper
from PyQt5.QtGui import QToolTip, QMouseEvent
from PyQt5.QtCore import QTimer

class PythonEditor(QsciScintilla):
    def __init__(self, parent=None, path: Path = None, is_python_file: bool = True):
        # ... existing init code ...

        if self.is_python_file:
            # Hover helper
            self.hover_helper = HoverHelper()
            self.hover_helper.hover_info_ready.connect(self._on_hover_ready)
            self.hover_helper.hover_info_empty.connect(self._on_hover_empty)

            # Hover debounce timer — only show tooltip after mouse is still
            # for 500ms. Without this, Jedi would run on every mouse pixel
            # movement, which is extremely slow.
            self._hover_timer = QTimer(self)
            self._hover_timer.setSingleShot(True)
            self._hover_timer.setInterval(500)
            self._hover_timer.timeout.connect(self._trigger_hover)

            # Enable mouse tracking so mouseMoveEvent fires on every movement
            # Without this, mouseMoveEvent only fires when a button is held
            self.setMouseTracking(True)
```

### Step 3: Override mouseMoveEvent

```python
    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        """Detect when the mouse hovers over a symbol."""
        if not self.is_python_file or self._shutting_down:
            return super().mouseMoveEvent(e)

        # Store the mouse position for the timer callback
        self._last_mouse_pos = e.pos()

        # Restart the debounce timer — if the mouse stays still for 500ms,
        # the timer fires and we run Jedi
        self._hover_timer.start()

        return super().mouseMoveEvent(e)

    def _trigger_hover(self):
        """Called 500ms after the mouse stopped moving. Run Jedi."""
        if self._shutting_down:
            return

        pos = getattr(self, "_last_mouse_pos", None)
        if pos is None:
            return

        # positionFromPoint converts a pixel position to a byte position
        # in the document. This is how we know which symbol the mouse
        # is hovering over.
        byte_pos = self.positionFromPoint(pos)
        if byte_pos < 0:
            return

        # Convert byte position to line/column for Jedi
        # SendScintilla(2127, pos) = SCI_LINEFROMPOSITION → line number
        # SendScintilla(2128, pos) = SCI_POSITIONTOCOLUMN → column
        line = self.SendScintilla(2126, byte_pos)  # line from position
        # For column, we need: position in line → column index
        # SCI_POSITIONFROMLINE = 2167 gets the start of the line
        line_start = self.SendScintilla(2167, line)
        column = byte_pos - line_start

        text = self.text()
        if not text.strip():
            return

        file_path = str(self.full_path) if self.full_path else None
        # Jedi uses 1-based lines
        self.hover_helper.get_hover(line + 1, column, text, file_path)

    def _on_hover_ready(self, info: str):
        """Show the tooltip with the docstring."""
        if self._shutting_down:
            return
        # QToolTip.showText shows a tooltip at the given global position
        # globalPos() converts the widget-relative position to screen position
        # Docs: https://doc.qt.io/qt-5/qtooltip.html#showText
        from PyQt5.QtCore import QPoint
        from PyQt5.QtWidgets import QApplication
        # Position the tooltip near the cursor
        cursor_pos = QApplication.mouseButtons()  # just to use QApplication
        pos = self.mapToGlobal(getattr(self, "_last_mouse_pos", QPoint(0, 0)))
        QToolTip.showText(pos, info, self)

    def _on_hover_empty(self):
        """Hide the tooltip if no docstring available."""
        QToolTip.hideText()
```

### Step 3: Add to shutdown

```python
    def shutdown(self):
        self._shutting_down = True
        self._loading_text = True
        if hasattr(self, "auto_completer"):
            self.auto_completer.shutdown()
        if hasattr(self, "definition_finder"):
            self.definition_finder.shutdown()
        if hasattr(self, "hover_helper"):
            self.hover_helper.shutdown()
```

### How Hover Works

```
User moves mouse over the word "print"
    ↓
mouseMoveEvent fires
    ↓
_hover_timer.start() — 500ms countdown begins
    ↓
Mouse stays still for 500ms
    ↓
_hover_timer.timeout fires
    ↓
_trigger_hover()
    ↓
positionFromPoint(mouse_pos) → byte position 42
    ↓
Convert to line=2, column=10
    ↓
HoverHelper.get_hover(line=3, column=10, code=..., file_path=...)
    ↓
Background thread: Jedi.Script(code).help(line=3, column=10)
    ↓
Jedi returns: Name(name="print", type="function", docstring="print(...)")
    ↓
hover_info_ready signal emits "print (function)\nprint(...)"
    ↓
QToolTip.showText() displays the tooltip
```

---

## 3. Function Signature Help

### Concept

When the user types `(` after a function name, a popup shows the function's parameter list. This helps the user remember what arguments a function expects.

### Key Jedi Methods

| Method | Description | Docs |
|---|---|---|
| `script.get_signatures(line, column)` | Returns signature objects | [Link](https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.Script.get_signatures) |
| `signature.params` | List of parameters | [Link](https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.api.classes.Signature) |
| `param.name` | Parameter name | [Link](https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.api.classes.ParamName) |

### Step 1: Create `signature_helper.py`

```python
from PyQt5.QtCore import QThread, pyqtSignal
from jedi import Script


class SignatureHelper(QThread):
    """
    Background thread that retrieves function signature information.

    Signals:
        signature_ready(str) — formatted signature string to display
        signature_empty() — no signature available
    """
    signature_ready = pyqtSignal(str)
    signature_empty = pyqtSignal()

    def __init__(self):
        super().__init__(None)
        self.code = ""
        self.file_path = None
        self.line = 1
        self.column = 0
        self._shutting_down = False

    def get_signatures(self, line: int, column: int, code: str, file_path: str = None):
        """Start signature lookup. Jedi uses 1-based lines."""
        if self.isRunning():
            return
        self.line = line
        self.column = column
        self.code = code
        self.file_path = file_path
        self.start()

    def run(self):
        try:
            script = Script(code=self.code, path=self.file_path)

            # get_signatures returns a list of Signature objects.
            # Each signature represents a possible function overload.
            # For most functions there's only one signature.
            # Docs: https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.Script.get_signatures
            signatures = script.get_signatures(line=self.line, column=self.column)

            if self._shutting_down:
                return

            if signatures:
                sig = signatures[0]

                # sig.name is the function name (e.g. "my_function")
                # sig.params is a list of ParamName objects
                # Each param has: .name (string), .kind (POSITIONAL_ONLY, etc.)

                params = []
                for param in sig.params:
                    # Build the parameter string
                    # Some params have default values detected by Jedi
                    p_str = param.name
                    if hasattr(param, 'infer_default') and param.infer_default():
                        defaults = param.infer_default()
                        if defaults:
                            p_str += f"={defaults[0].name}"
                    params.append(p_str)

                # Format: "function_name(param1, param2, param3)"
                signature_text = f"{sig.name}({', '.join(params)})"

                # If there's a docstring, add it
                docstring = sig.docstring_raw()
                if docstring:
                    signature_text += f"\n{docstring}"

                self.signature_ready.emit(signature_text)
            else:
                self.signature_empty.emit()

        except Exception:
            if not self._shutting_down:
                self.signature_empty.emit()

    def shutdown(self):
        self._shutting_down = True
        self.requestInterruption()
        if self.isRunning():
            self.wait()
```

### Step 2: Add to PythonEditor

```python
from signature_helper import SignatureHelper
from PyQt5.QtWidgets import QLabel
from PyQt5.QtGui import QToolTip

class PythonEditor(QsciScintilla):
    def __init__(self, parent=None, path: Path = None, is_python_file: bool = True):
        # ... existing init code ...

        if self.is_python_file:
            # Signature helper
            self.signature_helper = SignatureHelper()
            self.signature_helper.signature_ready.connect(self._on_signature_ready)
            self.signature_helper.signature_empty.connect(self._on_signature_empty)
```

### Step 3: Detect `(` in keyPressEvent

```python
    def keyPressEvent(self, e: QKeyEvent) -> None:
        # ... existing F12, Ctrl+Space, Ctrl+X handling ...

        # When the user types '(' after a word, trigger signature help
        if e.text() == "(" and self.is_python_file and not self._shutting_down:
            # Check if the character before '(' is a word character
            # (meaning the user typed "function_name(")
            line, index = self.getCursorPosition()
            if index > 0:
                # Get the text on the current line before the cursor
                line_text = self.text(line)
                before = line_text[:index].rstrip()
                if before and before[-1].isidentifier():
                    # The user typed "something(" — trigger signature help
                    QTimer.singleShot(50, self._trigger_signature_help)
                    # QTimer.singleShot(50, ...) delays by 50ms so the
                    # '(' character is actually inserted before we query
                    # Jedi. Without this delay, the cursor position
                    # hasn't updated yet.

        return super().keyPressEvent(e)

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

    def _on_signature_ready(self, signature: str):
        """Show the signature popup as a tooltip near the cursor."""
        if self._shutting_down:
            return

        # Get the cursor's screen position for the tooltip
        # SendScintilla(2025, line) = SCI_VISIBLEFROMDOCWRAP — not what we need
        # Instead, use the point from the cursor position
        from PyQt5.QtCore import QPoint
        cursor_pos = self.cursorPos()  # This returns a QPoint in some QScintilla versions
        # Alternative: use mapToGlobal with the editor's geometry
        point = self.mapToGlobal(QPoint(50, 50))  # approximate position
        QToolTip.showText(point, signature, self)

    def _on_signature_empty(self):
        """No signature available — hide tooltip."""
        QToolTip.hideText()
```

### How Signature Help Works

```
User types: my_function(
    ↓
keyPressEvent detects '('
    ↓
50ms delay (lets '(' be inserted)
    ↓
_trigger_signature_help()
    ↓
SignatureHelper.get_signatures(line=5, column=15, code=..., file_path=...)
    ↓
Background thread: Jedi.Script(code).get_signatures(line=5, column=15)
    ↓
Jedi returns: Signature(name="my_function", params=["x", "y", "z=10"])
    ↓
signature_ready signal: "my_function(x, y, z=10)"
    ↓
QToolTip.showText() displays it near the cursor
```

---

## 4. Find All References (Shift+F12)

### Concept

Find every place in the codebase where a symbol is used. Show the results in a list panel. Clicking a result opens the file and jumps to the line.

### Key Jedi Methods

| Method | Description | Docs |
|---|---|---|
| `script.get_references_all(line, column)` | Returns all usage locations | [Link](https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.Script.get_references_all) |
| `ref.module_path` | File path where the reference is | [Link](https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.api.classes.Name) |
| `ref.line` | Line number (1-based) | [Link](https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.api.classes.Name) |
| `ref.column` | Column number | [Link](https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.api.classes.Name) |

### Step 1: Create `references_finder.py`

```python
from PyQt5.QtCore import QThread, pyqtSignal
from jedi import Script


class ReferencesFinder(QThread):
    """
    Background thread that finds all references to a symbol using Jedi.

    Signals:
        references_found(list) — list of (file_path, line, column, line_text) tuples
        references_empty() — no references found
    """
    references_found = pyqtSignal(list)
    references_empty = pyqtSignal()

    def __init__(self):
        super().__init__(None)
        self.code = ""
        self.file_path = None
        self.line = 1
        self.column = 0
        self._shutting_down = False

    def find_references(self, line: int, column: int, code: str, file_path: str = None):
        """Start the reference search. Jedi uses 1-based lines."""
        if self.isRunning():
            return
        self.line = line
        self.column = column
        self.code = code
        self.file_path = file_path
        self.start()

    def run(self):
        try:
            script = Script(code=self.code, path=self.file_path)

            # get_references_all returns a list of Name objects.
            # Each Name represents a usage of the symbol — the definition
            # itself, imports, and all call sites.
            # Docs: https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.Script.get_references_all
            references = script.get_references_all(line=self.line, column=self.column)

            if self._shutting_down:
                return

            if references:
                results = []
                for ref in references:
                    module_path = str(ref.module_path) if ref.module_path else ""
                    line_num = ref.line
                    column = ref.column

                    # Try to read the line text for display
                    line_text = ""
                    if module_path:
                        try:
                            with open(module_path, "r", encoding="utf-8") as f:
                                lines = f.readlines()
                                if 0 < line_num <= len(lines):
                                    line_text = lines[line_num - 1].strip()
                        except (IOError, OSError):
                            pass
                    elif self.file_path:
                        # Same file — use the code we already have
                        code_lines = self.code.split("\n")
                        if 0 < line_num <= len(code_lines):
                            line_text = code_lines[line_num - 1].strip()

                    results.append((module_path, line_num, column, line_text))

                self.references_found.emit(results)
            else:
                self.references_empty.emit()

        except Exception as err:
            if not self._shutting_down:
                self.references_empty.emit()

    def shutdown(self):
        self._shutting_down = True
        self.requestInterruption()
        if self.isRunning():
            self.wait()
```

### Step 2: Create the references panel UI

```python
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QTreeWidget, QTreeWidgetItem


class ReferencesTree(QTreeWidget):
    """
    A tree widget showing all references to a symbol.
    Clicking a reference opens the file and jumps to the line.
    """

    # Signal: (file_path, line, column) — MainWindow opens the file
    reference_clicked = pyqtSignal(str, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setStyleSheet("""
            QTreeWidget {
                background-color: #21252b;
                color: #dcdfe4;
                border: none;
                padding: 4px;
            }
            QTreeWidget::item { padding: 2px 0px; }
            QTreeWidget::item:selected { background-color: #2c313a; }
        """)
        self.itemClicked.connect(self._on_item_clicked)

    def show_references(self, references: list):
        """
        Populate the tree with references.

        references: list of (file_path, line, column, line_text) tuples
        """
        self.clear()

        for file_path, line, column, line_text in references:
            # Display: filename:line  |  line_text
            from pathlib import Path
            display_name = Path(file_path).name if file_path else "current file"
            item = QTreeWidgetItem(self)
            item.setText(0, f"{display_name}:{line}")
            # Store the full path and position as data
            item.setData(0, Qt.UserRole, (file_path, line - 1, column))
            # Second line shows the actual code
            child = QTreeWidgetItem(item)
            child.setText(0, f"  {line_text}")
            child.setForeground(0, self._color("#777777"))
            # Also store data on the child
            child.setData(0, Qt.UserRole, (file_path, line - 1, column))

        self.expandAll()

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int):
        """When the user clicks a reference, emit the signal."""
        data = item.data(0, Qt.UserRole)
        if data is not None:
            file_path, line, col = data
            self.reference_clicked.emit(file_path, line, col)

    def _color(self, hex_color: str):
        from PyQt5.QtGui import QColor
        return QColor(hex_color)
```

### Step 3: Add to PythonEditor and MainWindow

In `pythoneditor.py`:

```python
from references_finder import ReferencesFinder

class PythonEditor(QsciScintilla):
    # Signal: emit when references are found (MainWindow shows the panel)
    references_found = pyqtSignal(list)

    def __init__(self, parent=None, path: Path = None, is_python_file: bool = True):
        # ... existing init code ...

        if self.is_python_file:
            self.references_finder = ReferencesFinder()
            self.references_finder.references_found.connect(self._on_references_found)
            self.references_finder.references_empty.connect(self._on_references_empty)

    def find_all_references(self):
        """Trigger the reference search at the current cursor position."""
        if not self.is_python_file or self._shutting_down:
            return

        line, index = self.getCursorPosition()
        text = self.text()
        if not text.strip():
            return

        file_path = str(self.full_path) if self.full_path else None
        self.references_finder.find_references(line + 1, index, text, file_path)

    def _on_references_found(self, references: list):
        """Forward references to MainWindow via signal."""
        if not self._shutting_down:
            self.references_found.emit(references)

    def _on_references_empty(self):
        """No references found."""
        if not self._shutting_down:
            self.references_found.emit([])
```

In `main.py`, add the references panel and connect:

```python
    def set_up_body(self):
        # ... existing setup ...

        # References panel (docked, hidden by default)
        from references_finder import ReferencesTree
        self.references_tree = ReferencesTree()
        self.references_tree.reference_clicked.connect(self._open_file_at_position)

        self.references_dock = QDockWidget("References", self)
        self.references_dock.setWidget(self.references_tree)
        self.references_dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.references_dock)
        self.references_dock.hide()
```

In `set_new_tab` (and `new_file`), connect the signal:

```python
        if isinstance(self.editor, PythonEditor):
            self.editor.goto_definition_requested.connect(self._open_file_at_position)
            self.editor.references_found.connect(self._show_references)

    def _show_references(self, references: list):
        """Show the references panel with results."""
        if not references:
            self.statusBar().showMessage("No references found", 3000)
            return
        self.references_tree.show_references(references)
        self.references_dock.show()
        self.statusBar().showMessage(f"Found {len(references)} references", 3000)
```

### Step 4: Add keyboard shortcut

In `set_up_menu`:

```python
        find_refs_action = edit_menu.addAction("Find All References")
        find_refs_action.setShortcut("Shift+F12")
        find_refs_action.setShortcutContext(Qt.ApplicationShortcut)
        find_refs_action.triggered.connect(self._trigger_find_references)

    def _trigger_find_references(self):
        editor = self.tab_view.currentWidget()
        if isinstance(editor, PythonEditor):
            editor.find_all_references()
```

### How It Works

```
User presses Shift+F12 (cursor on "my_function")
    ↓
PythonEditor.find_all_references()
    ↓
ReferencesFinder.find_references(line=5, column=10, code=..., file_path=...)
    ↓
Background thread: Jedi.Script(code).get_references_all(line=5, column=10)
    ↓
Jedi returns: [
    Name(module_path="main.py", line=5, column=4),    ← definition
    Name(module_path="main.py", line=20, column=8),  ← call site
    Name(module_path="utils.py", line=15, column=12), ← import
]
    ↓
references_found signal: [("main.py", 5, 4, "def my_function()"), ...]
    ↓
_show_references() populates the tree
    ↓
User clicks "main.py:20" → _open_file_at_position opens the file at line 20
```

---

## 5. Code Outline / Symbol Tree

### Concept

Parse the current Python file for `class` and `def` declarations using the `ast` module. Show them in a tree panel. Click a symbol to jump to it.

This feature is already implemented in your editor (Feature 15 from the Tier 3 guide). The implementation uses `ast.parse()` to walk the Abstract Syntax Tree and extract class/function names with their line numbers.

### Key Methods

| Method | Description | Docs |
|---|---|---|
| `ast.parse(source)` | Parses Python source into an AST | [Link](https://docs.python.org/3/library/ast.html#ast.parse) |
| `ast.ClassDef` | AST node for a class definition | [Link](https://docs.python.org/3/library/ast.html#ast.ClassDef) |
| `ast.FunctionDef` | AST node for a function definition | [Link](https://docs.python.org/3/library/ast.html#ast.FunctionDef) |
| `node.name` | The name of the class or function | [Link](https://docs.python.org/3/library/ast.html#ast.ClassDef) |
| `node.lineno` | Line number (1-based) | [Link](https://docs.python.org/3/library/ast.html#ast.ClassDef) |
| `node.body` | List of child nodes inside the class/function | [Link](https://docs.python.org/3/library/ast.html#ast.ClassDef) |

The implementation is in your `code_outline.py` file. See the existing `CodeOutlineTree` class for the full implementation.

---

## 6. Real-time Error Highlighting

### Concept

Run `pyflakes` on the file in a background thread. When errors are found, underline them with red squiggly lines using Scintilla's indicator system. Show a marker in the margin.

### Key Scintilla Methods

| Method | Description | Docs |
|---|---|---|
| `SendScintilla(2500)` | SCI_INDICSETSTYLE — set indicator style | [Link](https://www.scintilla.org/ScintillaDoc.html#SCI_INDICSETSTYLE) |
| `SendScintilla(2502, indicator, color)` | SCI_INDICSETFORE — set indicator color | [Link](https://www.scintilla.org/ScintillaDoc.html#SCI_INDICSETFORE) |
| `SendScintilla(2508, start, end)` | SCI_INDICATORFILLRANGE — underline a range | [Link](https://www.scintilla.org/ScintillaDoc.html#SCI_INDICATORFILLRANGE) |
| `SendScintilla(2510, start, end)` | SCI_INDICATORCLEARRANGE — clear underlines | [Link](https://www.scintilla.org/ScintillaDoc.html#SCI_INDICATORCLEARRANGE) |
| `SendScintilla(2049, line, marker)` | SCI_MARKERADD — add margin marker | [Link](https://www.scintilla.org/ScintillaDoc.html#SCI_MARKERADD) |
| `SendScintilla(2046, line)` | SCI_MARKERDELETE — remove margin marker | [Link](https://www.scintilla.org/ScintillaDoc.html#SCI_MARKERDELETE) |

### Indicator styles

| Value | Style | Visual |
|---|---|---|
| 0 | INDIC_SQUIGGLE | Red wavy underline (classic spell-check style) |
| 1 | INDIC_TT | Dotted underline |
| 2 | INDIC_PLAIN | Plain underline |
| 5 | INDIC_SQUIGGLEPIXMAP | Pixmap squiggle (higher quality) |
| 8 | INDIC_FULLBOX | Full box highlight |
| 9 | INDIC_TEXTFORE | Text foreground color change |

[Scintilla indicator styles docs](https://www.scintilla.org/ScintillaDoc.html#Indicators)

### Step 1: Create `error_checker.py`

```python
import io
import sys
from PyQt5.QtCore import QThread, pyqtSignal


class ErrorChecker(QThread):
    """
    Background thread that runs pyflakes on the current file content.

    pyflakes is a linting tool that finds common Python errors:
    - Undefined names
    - Unused imports
    - Redefined functions
    - Syntax errors

    Signals:
        errors_found(list) — list of (line, column, message) tuples
        errors_cleared() — no errors found
    """
    errors_found = pyqtSignal(list)
    errors_cleared = pyqtSignal()

    def __init__(self):
        super().__init__(None)
        self.code = ""
        self._shutting_down = False

    def check(self, code: str):
        """Start the error check."""
        if self.isRunning():
            return
        self.code = code
        self.start()

    def run(self):
        try:
            # Import pyflakes modules
            # pyflakes is a pure Python linter that doesn't need to
            # run a subprocess — it can analyze code directly.
            # Docs: https://github.com/PyCQA/pyflakes
            from pyflakes.api import check
            from pyflakes.reporter import Reporter

            # Create a custom reporter that captures errors
            # instead of printing to stderr
            errors = []

            class CustomReporter(Reporter):
                """Captures pyflakes output instead of printing to stderr."""
                def __init__(self):
                    self.output = io.StringIO()
                    self.errors = io.StringIO()
                    super().__init__(self.output, self.errors)

                def unexpectedError(self, filename, msg):
                    errors.append((0, 0, f"Syntax error: {msg}"))

                def syntaxError(self, filename, msg, lineno, offset, text):
                    # pyflakes passes: filename, message, line number,
                    # column offset, and the text of the error line
                    col = offset or 0
                    errors.append((lineno - 1, col, msg))

                def flake(self, message):
                    # message is a pyflakes.messages.Message object
                    # .lineno is the line number (1-based)
                    # .col is the column (0-based)
                    # .message % message is the formatted error text
                    line = getattr(message, 'lineno', 0) - 1
                    col = getattr(message, 'col', 0)
                    msg_text = str(message.message % message)
                    errors.append((line, col, msg_text))

            reporter = CustomReporter()

            # check() runs pyflakes on the code string
            # It calls the reporter methods for each error found
            # Returns the number of errors
            check(self.code, '<editor>', reporter)

            if self._shutting_down:
                return

            if errors:
                self.errors_found.emit(errors)
            else:
                self.errors_cleared.emit()

        except ImportError:
            # pyflakes not installed — skip error checking
            self.errors_cleared.emit()
        except Exception:
            if not self._shutting_down:
                self.errors_cleared.emit()

    def shutdown(self):
        self._shutting_down = True
        self.requestInterruption()
        if self.isRunning():
            self.wait()
```

### Step 2: Add error indicators to PythonEditor

```python
from error_checker import ErrorChecker

class PythonEditor(QsciScintilla):
    # Indicator ID for errors (Scintilla supports 0-31)
    INDICATOR_ERROR = 8  # use indicator 8

    def __init__(self, parent=None, path: Path = None, is_python_file: bool = True):
        # ... existing init code ...

        if self.is_python_file:
            # Set up error indicator styling
            # SCI_INDICSETSTYLE = 2500
            # INDIC_SQUIGGLE = 0 (red wavy underline)
            self.SendScintilla(2500, self.INDICATOR_ERROR, 0)

            # SCI_INDICSETFORE = 2502 — set the color
            # Color is in Scintilla's BGR format: 0x00rrggbb
            # For red (#e06c75): 0x00756ce0
            from PyQt5.QtGui import QColor
            red = QColor("#e06c75")
            # Convert RGB to Scintilla color format (BGR)
            sc_color = (red.blue() << 16) | (red.green() << 8) | red.red()
            self.SendScintilla(2502, self.INDICATOR_ERROR, sc_color)

            # Error checker
            self.error_checker = ErrorChecker()
            self.error_checker.errors_found.connect(self._on_errors_found)
            self.error_checker.errors_cleared.connect(self._on_errors_cleared)

            # Debounce timer — only check after user stops typing for 1 second
            self._error_debounce = QTimer(self)
            self._error_debounce.setSingleShot(True)
            self._error_debounce.setInterval(1000)
            self._error_debounce.timeout.connect(self._check_errors)

            # Connect textChanged to debounce
            self.textChanged.connect(self._error_debounce.start)

            # Store marker handles for cleanup
            self._error_markers = []  # list of (line, marker_handle) tuples
```

### Step 3: Error display methods

```python
    def _check_errors(self):
        """Run pyflakes on the current content."""
        if self._shutting_down or self._loading_text:
            return
        self.error_checker.check(self.text())

    def _on_errors_found(self, errors: list):
        """Clear old indicators and add new ones."""
        if self._shutting_down:
            return

        # Clear all previous error indicators
        # SCI_INDICATORCLEARRANGE = 2510
        text_len = len(self.text().encode('utf-8'))
        self.SendScintilla(2510, 0, text_len)

        # Clear old margin markers
        for line, handle in self._error_markers:
            # SCI_MARKERDELETE = 2046
            self.SendScintilla(2046, line, 0)
        self._error_markers.clear()

        for line, col, message in errors:
            if line < 0:
                continue

            # Get the line text to determine the range to underline
            try:
                line_text = self.text(line)
                line_len = len(line_text.encode('utf-8'))
            except IndexError:
                continue

            # Calculate the byte position for the start of the line
            # SCI_POSITIONFROMLINE = 2167
            line_start = self.SendScintilla(2167, line)

            # Underline the entire line
            # SCI_INDICATORFILLRANGE = 2508
            self.SendScintilla(2508, line_start, line_len, self.INDICATOR_ERROR)

            # Add a marker in the margin
            # First set the marker symbol
            # SCI_MARKERDEFINE = 2040
            # SC_MARK_CIRCLE = 0 (small circle in the margin)
            self.SendScintilla(2040, 0, 0)

            # SCI_MARKERSETFORE = 2041 — marker foreground color
            self.SendScintilla(2041, 0, sc_color)

            # SCI_MARKERSETBACK = 2042 — marker background color
            self.SendScintilla(2042, 0, sc_color)

            # SCI_MARKERADD = 2049 — add marker to the margin
            handle = self.SendScintilla(2049, line, 0)
            self._error_markers.append((line, handle))

    def _on_errors_cleared(self):
        """Clear all error indicators."""
        if self._shutting_down:
            return

        text_len = len(self.text().encode('utf-8'))
        self.SendScintilla(2510, 0, text_len)

        for line, handle in self._error_markers:
            self.SendScintilla(2046, line, 0)
        self._error_markers.clear()
```

### Step 4: Install pyflakes

```bash
pip install pyflakes
```

### Step 5: Add to shutdown

```python
    def shutdown(self):
        self._shutting_down = True
        self._loading_text = True
        if hasattr(self, "auto_completer"):
            self.auto_completer.shutdown()
        if hasattr(self, "definition_finder"):
            self.definition_finder.shutdown()
        if hasattr(self, "hover_helper"):
            self.hover_helper.shutdown()
        if hasattr(self, "error_checker"):
            self.error_checker.shutdown()
```

### How Error Highlighting Works

```
User types code with an error (e.g. undefined variable)
    ↓
textChanged signal fires
    ↓
_error_debounce.start() — 1000ms countdown
    ↓
User stops typing for 1 second
    ↓
_error_debounce.timeout fires
    ↓
_check_errors()
    ↓
ErrorChecker.check(code)
    ↓
Background thread: pyflakes checks the code
    ↓
pyflakes finds: undefined name 'xyz' at line 5, column 8
    ↓
errors_found signal: [(5, 8, "undefined name 'xyz'")]
    ↓
_on_errors_found()
    ↓
Clear old indicators with SCI_INDICATORCLEARRANGE
    ↓
Add red squiggly underline with SCI_INDICATORFILLRANGE
    ↓
Add margin marker with SCI_MARKERADD
    ↓
User sees red wavy line under the error + a marker in the margin
```

---

## 7. Code Formatting

### Concept

Run `autopep8` or `black` on the current file via `QProcess`. Replace the editor content with the formatted output. This reindents code, adds/removes blank lines, and fixes other style issues automatically.

### Key Methods

| Method | Description | Docs |
|---|---|---|
| `QProcess.start(program, args)` | Starts a subprocess | [Link](https://doc.qt.io/qt-5/qprocess.html#start) |
| `QProcess.readAllStandardOutput()` | Reads stdout | [Link](https://doc.qt.io/qt-5/qprocess.html#readAllStandardOutput) |
| `editor.setText(text)` | Replaces all editor content | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |
| `editor.getCursorPosition()` | Save cursor position before formatting | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |
| `editor.setCursorPosition(line, index)` | Restore cursor after formatting | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |

### Step 1: Add to PythonRunner

In `python_runner.py`:

```python
    def format_code(self, code: str, formatter: str = "autopep8"):
        """
        Run a code formatter on the given code string.

        The code is written to a temporary file, formatted by the
        external tool (autopep8 or black), and the formatted output
        is captured from stdout.

        Parameters:
            code: The Python source code to format
            formatter: "autopep8" or "black"
        """
        import tempfile, os

        if self.is_running():
            self.stop()

        # Write the code to a temporary file
        # The formatter needs a file path to operate on
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.py', delete=False, encoding='utf-8'
        ) as f:
            f.write(code)
            temp_path = f.name

        # Build the command arguments
        if formatter == "autopep8":
            # autopep8 outputs the formatted code to stdout
            # --aggressive applies more aggressive fixes
            # Docs: https://pypi.org/project/autopep8/
            args = ["--aggressive", "--aggressive", temp_path]
        elif formatter == "black":
            # black outputs the formatted code to stdout with - output
            # Docs: https://black.readthedocs.io/
            args = ["-", temp_path]
        else:
            os.unlink(temp_path)
            return

        env = QProcessEnvironment.systemEnvironment()
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(os.path.dirname(temp_path))

        # We need to capture the output, so connect a one-shot handler
        self._format_temp_path = temp_path
        self._format_output = ""
        self._is_formatting = True

        # Disconnect normal stdout handler temporarily
        # and connect our format-specific handler
        self.process.readyReadStandardOutput.disconnect()
        self.process.readyReadStandardOutput.connect(self._read_format_output)
        self.process.finished.connect(self._on_format_finished)

        self.process.start(formatter, args)

    def _read_format_output(self):
        """Capture formatter output."""
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._format_output += data

    def _on_format_finished(self, exit_code, exit_status):
        """Called when the formatter finishes. Emit the result."""
        import os
        if hasattr(self, '_format_temp_path') and os.path.exists(self._format_temp_path):
            os.unlink(self._format_temp_path)

        if getattr(self, '_is_formatting', False):
            self._is_formatting = False
            # Reconnect normal stdout handler
            self.process.readyReadStandardOutput.disconnect()
            self.process.readyReadStandardOutput.connect(self._read_stdout)
            # Emit the formatted code
            if self._format_output:
                self.output_ready.emit(self._format_output)
```

### Step 2: Add menu actions in MainWindow

```python
        # In set_up_menu, add to the Edit menu:
        format_menu = edit_menu.addMenu("Format Code")

        autopep8_action = format_menu.addAction("Format with autopep8")
        autopep8_action.setShortcut("Ctrl+Shift+F")
        autopep8_action.setShortcutContext(Qt.ApplicationShortcut)
        autopep8_action.triggered.connect(lambda: self._format_code("autopep8"))

        black_action = format_menu.addAction("Format with Black")
        black_action.setShortcut("Ctrl+Alt+F")
        black_action.setShortcutContext(Qt.ApplicationShortcut)
        black_action.triggered.connect(lambda: self._format_code("black"))

    def _format_code(self, formatter: str):
        """Format the current file's code."""
        editor = self.tab_view.currentWidget()
        if not isinstance(editor, PythonEditor):
            self.statusBar().showMessage("Formatting requires a Python file", 3000)
            return

        code = editor.text()

        # Connect a one-shot handler to receive the formatted output
        def on_formatted(formatted_code):
            self.python_runner.output_ready.disconnect(on_formatted)
            if formatted_code.strip():
                # Save cursor position
                line, index = editor.getCursorPosition()
                # Replace the editor content
                editor.setText(formatted_code)
                # Try to restore cursor position (may be different after formatting)
                editor.setCursorPosition(min(line, editor.lines() - 1), index)
                self.statusBar().showMessage(f"Formatted with {formatter}", 3000)

        self.python_runner.output_ready.connect(on_formatted)
        self.python_runner.format_code(code, formatter)
```

### Step 3: Install formatters

```bash
pip install autopep8 black
```

### How Code Formatting Works

```
User presses Ctrl+Shift+F (autopep8)
    ↓
_format_code("autopep8")
    ↓
Save cursor position: (line=42, index=10)
    ↓
python_runner.format_code(code, "autopep8")
    ↓
Write code to temp file: /tmp/tmpXXXX.py
    ↓
QProcess.start("autopep8", ["--aggressive", "--aggressive", "/tmp/tmpXXXX.py"])
    ↓
autopep8 formats the file, outputs to stdout
    ↓
_read_format_output() captures the formatted code
    ↓
_on_format_finished() fires
    ↓
output_ready.emit(formatted_code)
    ↓
on_formatted() handler receives it
    ↓
editor.setText(formatted_code) — replaces content
    ↓
editor.setCursorPosition(42, 10) — restores cursor
    ↓
Delete temp file
```

---

## 8. Import Sorting

### Concept

Run `isort` on the current file content via `QProcess`. `isort` sorts Python import statements alphabetically and groups them into standard library, third-party, and local imports. Replace the editor content with the sorted result.

### Key Methods

| Method | Description | Docs |
|---|---|---|
| `subprocess.run(["isort", "--stdout", "-"], input=code)` | Run isort on stdin, get output from stdout | [isort CLI docs](https://pycqa.github.io/isort/docs/configuration/options.html) |
| `editor.setText(text)` | Replace editor content | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |

### Step 1: Add import sorting method to MainWindow

```python
    def sort_imports(self):
        """Sort imports in the current Python file using isort."""
        import subprocess

        editor = self.tab_view.currentWidget()
        if not isinstance(editor, PythonEditor):
            self.statusBar().showMessage("Import sorting requires a Python file", 3000)
            return

        code = editor.text()

        try:
            # Run isort on the code via stdin/stdout
            # --stdout tells isort to output to stdout instead of modifying a file
            # - tells isort to read from stdin
            # The code is passed as input via subprocess
            # Docs: https://pycqa.github.io/isort/
            result = subprocess.run(
                ["isort", "--stdout", "-"],
                input=code,
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0 and result.stdout.strip():
                # Save cursor position
                line, index = editor.getCursorPosition()

                # Replace editor content with sorted imports
                editor.setText(result.stdout)

                # Restore cursor position
                editor.setCursorPosition(min(line, editor.lines() - 1), index)
                self.statusBar().showMessage("Imports sorted", 3000)
            else:
                self.statusBar().showMessage("Import sorting failed", 3000)

        except FileNotFoundError:
            # isort not installed
            self.statusBar().showMessage("isort not installed. Run: pip install isort", 5000)
        except subprocess.TimeoutExpired:
            self.statusBar().showMessage("Import sorting timed out", 3000)
```

### Step 2: Add menu action

```python
        sort_imports_action = edit_menu.addAction("Sort Imports")
        sort_imports_action.setShortcut("Ctrl+Shift+I")
        sort_imports_action.setShortcutContext(Qt.ApplicationShortcut)
        sort_imports_action.triggered.connect(self.sort_imports)
```

### Step 3: Install isort

```bash
pip install isort
```

### How Import Sorting Works

```
Before:
    import sys
    import os
    from PyQt5.QtWidgets import QMainWindow
    import json
    from pathlib import Path

User presses Ctrl+Shift+I
    ↓
sort_imports()
    ↓
subprocess.run(["isort", "--stdout", "-"], input=code)
    ↓
isort reads the code, sorts imports into groups:
  1. Standard library (os, sys, json, pathlib)
  2. Third-party (PyQt5)
  3. Local

After:
    import json
    import os
    import sys
    from pathlib import Path

    from PyQt5.QtWidgets import QMainWindow

    ↓
editor.setText(result.stdout)
    ↓
Imports are now alphabetically sorted and grouped
```

---

## 9. Type Hint Display

### Concept

When the user hovers over a variable or function, Jedi infers its type and shows it in a tooltip. For example, hovering over `result` shows `result: str` if Jedi can determine the type.

### Key Jedi Methods

| Method | Description | Docs |
|---|---|---|
| `script.infer(line, column)` | Returns inferred type information | [Link](https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.Script.infer) |
| `name.name` | The type name (e.g. "str", "int", "MyClass") | [Link](https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.api.classes.Name) |
| `name.type` | The kind of name ("class", "function", "instance") | [Link](https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.api.classes.Name) |

### Step 1: Create `type_inferencer.py`

```python
from PyQt5.QtCore import QThread, pyqtSignal
from jedi import Script


class TypeInferencer(QThread):
    """
    Background thread that infers the type of a symbol using Jedi.

    Signals:
        type_inferred(str) — the inferred type as a string
        type_unknown() — couldn't infer the type
    """
    type_inferred = pyqtSignal(str)
    type_unknown = pyqtSignal()

    def __init__(self):
        super().__init__(None)
        self.code = ""
        self.file_path = None
        self.line = 1
        self.column = 0
        self._shutting_down = False

    def infer_type(self, line: int, column: int, code: str, file_path: str = None):
        """Start type inference. Jedi uses 1-based lines."""
        if self.isRunning():
            return
        self.line = line
        self.column = column
        self.code = code
        self.file_path = file_path
        self.start()

    def run(self):
        try:
            script = Script(code=self.code, path=self.file_path)

            # infer() returns a list of Name objects representing the
            # inferred type(s). For most variables there's one result,
            # but for union types or overloaded functions there may be more.
            # Docs: https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.Script.infer
            inferred = script.infer(line=self.line, column=self.column)

            if self._shutting_down:
                return

            if inferred:
                # Collect unique type names
                type_names = set()
                for name in inferred:
                    # name.name is the type name (e.g. "str", "int", "list")
                    # name.type is the kind: "class", "function", "instance"
                    if name.name:
                        if name.type == "class":
                            type_names.add(name.name)
                        elif name.type == "instance":
                            # For instances, try to get the class name
                            type_names.add(name.name)
                        else:
                            type_names.add(f"{name.name} ({name.type})")

                if type_names:
                    # Join multiple types with | (like Python's union syntax)
                    type_str = " | ".join(sorted(type_names))
                    self.type_inferred.emit(type_str)
                else:
                    self.type_unknown.emit()
            else:
                self.type_unknown.emit()

        except Exception:
            if not self._shutting_down:
                self.type_unknown.emit()

    def shutdown(self):
        self._shutting_down = True
        self.requestInterruption()
        if self.isRunning():
            self.wait()
```

### Step 2: Integrate with hover (extends Feature 2)

Instead of a separate tooltip, the type info can be added to the existing hover tooltip from Feature 2. Modify `HoverHelper` to also run type inference:

```python
class HoverHelper(QThread):
    hover_info_ready = pyqtSignal(str)
    hover_info_empty = pyqtSignal()

    def run(self):
        try:
            script = Script(code=self.code, path=self.file_path)

            # Get help info (docstring)
            help_results = script.help(line=self.line, column=self.column)

            # Get type info
            inferred = script.infer(line=self.line, column=self.column)

            if self._shutting_down:
                return

            parts = []

            # Add type information first
            if inferred:
                type_names = set()
                for name in inferred:
                    if name.name:
                        type_names.add(name.name)
                if type_names:
                    parts.append(" | ".join(sorted(type_names)))

            # Add symbol name and type kind
            if help_results:
                name = help_results[0]
                if name.name and name.type:
                    parts.append(f"{name.name} ({name.type})")
                elif name.name:
                    parts.append(name.name)

                docstring = name.docstring_raw()
                if docstring:
                    parts.append("\n" + docstring)

            if len(parts) > 1 or (len(parts) == 1 and parts[0]):
                self.hover_info_ready.emit("\n".join(parts))
            else:
                self.hover_info_empty.emit()

        except Exception:
            if not self._shutting_down:
                self.hover_info_empty.emit()
```

### How Type Inference Works

```
User hovers over the variable "result" (which was assigned: result = "hello")
    ↓
HoverHelper runs Jedi.Script(code).help(line, column)
                  AND Jedi.Script(code).infer(line, column)
    ↓
help() returns: Name(name="result", type="instance")
infer() returns: [Name(name="str", type="class")]
    ↓
Combined tooltip: "str\nresult (instance)"
    ↓
QToolTip.showText() displays: "str" on the first line, "result (instance)" below
```

### Example tooltip outputs

| Code | Hover tooltip |
|---|---|
| `x = 42` | `int` |
| `name = "hello"` | `str` |
| `items = [1, 2, 3]` | `list` |
| `def foo(): pass` | `foo (function)\n` |
| `class Bar: pass` | `Bar (class)` |
| `from os import path` | `module` |

---

## Quick Reference: All New Files

| File | Purpose |
|---|---|
| `definition_finder.py` | Background QThread for Jedi definition lookup (F12) |
| `hover_helper.py` | Background QThread for Jedi hover tooltips + type inference |
| `signature_helper.py` | Background QThread for function signature help |
| `references_finder.py` | Background QThread for find all references (Shift+F12) |
| `references_tree.py` (or panel in references_finder) | QTreeWidget showing reference results |
| `error_checker.py` | Background QThread for pyflakes error checking |
| `type_inferencer.py` | Background QThread for Jedi type inference (merged into hover_helper) |

## Quick Reference: All New Menu Actions

| Menu | Action | Shortcut | Method |
|---|---|---|---|
| Edit | Go to Definition | F12 | `editor.goto_definition()` |
| Edit | Find All References | Shift+F12 | `editor.find_all_references()` |
| Edit | Format with autopep8 | Ctrl+Shift+F | `self._format_code("autopep8")` |
| Edit | Format with Black | Ctrl+Alt+F | `self._format_code("black")` |
| Edit | Sort Imports | Ctrl+Shift+I | `self.sort_imports()` |

## Quick Reference: New Signals on PythonEditor

| Signal | Purpose |
|---|---|
| `goto_definition_requested(str, int, int)` | Request opening a file at a definition |
| `references_found(list)` | Forward reference results to MainWindow |

## Quick Reference: New Background Threads on PythonEditor

| Thread | Purpose | Trigger |
|---|---|---|
| `DefinitionFinder` | F12 go to definition | F12 key press |
| `HoverHelper` | Hover tooltips + type inference | Mouse hover (500ms debounce) |
| `SignatureHelper` | Function parameter hints | Typing `(` |
| `ReferencesFinder` | Find all references | Shift+F12 |
| `ErrorChecker` | Real-time error highlighting | textChanged (1000ms debounce) |

## Quick Reference: pip Packages Required

| Package | Feature | Install |
|---|---|---|
| `jedi` | Go to def, hover, signatures, references, type inference | Already installed |
| `pyflakes` | Real-time error highlighting | `pip install pyflakes` |
| `autopep8` | Code formatting | `pip install autopep8` |
| `black` | Code formatting (alternative) | `pip install black` |
| `isort` | Import sorting | `pip install isort` |

## Quick Reference: Scintilla Messages Used

| Message | Value | Purpose |
|---|---|---|
| `SCI_LINEFROMPOSITION` | 2126 | Convert byte position to line number |
| `SCI_POSITIONFROMLINE` | 2167 | Get byte position of line start |
| `SCI_INDICSETSTYLE` | 2500 | Set indicator (underline) style |
| `SCI_INDICSETFORE` | 2502 | Set indicator color |
| `SCI_INDICATORFILLRANGE` | 2508 | Underline a byte range |
| `SCI_INDICATORCLEARRANGE` | 2510 | Clear underlines in a range |
| `SCI_MARKERDEFINE` | 2040 | Set margin marker symbol |
| `SCI_MARKERSETFORE` | 2041 | Set marker foreground color |
| `SCI_MARKERSETBACK` | 2042 | Set marker background color |
| `SCI_MARKERADD` | 2049 | Add marker to a line |
| `SCI_MARKERDELETE` | 2046 | Remove marker from a line |
