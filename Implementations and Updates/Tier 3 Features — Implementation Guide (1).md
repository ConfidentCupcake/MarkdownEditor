# Tier 3 Features — Implementation Guide

This document covers the implementation of 7 advanced features for the Code Editor, plus the pip package manager integration. Each section includes code examples, explanations, and documentation references.

---

## Table of Contents

1. [Go to Definition (F12) with Jedi](#feature-14-go-to-definition-f12-with-jedi)
2. [Code Outline Panel](#feature-15-code-outline-panel)
3. [Settings Dialog](#feature-16-settings-dialog)
4. [Remember Open Tabs on Restart](#feature-17-remember-open-tabs-on-restart)
5. [Export to PDF](#feature-18-export-to-pdf)
6. [Git Integration](#feature-19-git-integration)
7. [Command Palette](#feature-20-command-palette)
8. [Pip Package Manager (pip console)](#feature-21-pip-package-manager-pip-console)

---

## Documentation Reference

| What | Link |
|---|---|
| QScintilla methods | [Riverbank QScintilla](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |
| Jedi API | [Jedi documentation](https://jedi.readthedocs.io/en/latest/docs/api.html) |
| QDockWidget | [Qt QDockWidget docs](https://doc.qt.io/qt-5/qdockwidget.html) |
| QTreeWidget | [Qt QTreeWidget docs](https://doc.qt.io/qt-5/qtreewidget.html) |
| QDialog | [Qt QDialog docs](https://doc.qt.io/qt-5/qdialog.html) |
| QTabWidget (for dialog tabs) | [Qt QTabWidget docs](https://doc.qt.io/qt-5/qtabwidget.html) |
| QSettings | [Qt QSettings docs](https://doc.qt.io/qt-5/qsettings.html) |
| QWebEnginePage | [Qt QWebEnginePage docs](https://doc.qt.io/qt-5/qwebenginepage.html) |
| QProcess | [Qt QProcess docs](https://doc.qt.io/qt-5/qprocess.html) |
| QShortcut | [Qt QShortcut docs](https://doc.qt.io/qt-5/qshortcut.html) |
| QInputDialog | [Qt QInputDialog docs](https://doc.qt.io/qt-5/qinputdialog.html) |
| Python ast module | [Python ast docs](https://docs.python.org/3/library/ast.html) |
| Python re module | [Python re docs](https://docs.python.org/3/library/re.html) |
| Python shlex module | [Python shlex docs](https://docs.python.org/3/library/shlex.html) |
| Python os.path | [Python os.path docs](https://docs.python.org/3/library/os.path.html) |

---

## Feature 14: Go to Definition (F12) with Jedi

### Concept

When the user presses F12 (or Ctrl+Clicks a symbol), the editor jumps to where that function, class, or variable is defined. Jedi already powers your autocomplete — this extends it to navigation.

### Key Methods

| Method | Description | Docs |
|---|---|---|
| `jedi.Script(code, path)` | Creates a Jedi Script object for analysis | [Link](https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.Script) |
| `script.goto(line, column)` | Returns definition locations | [Link](https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.Script.goto) |
| `editor.getCursorPosition()` | Returns `(line, index)` of the cursor | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |
| `editor.getCursorPosition()` | Returns `(line, index)` tuple | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |
| `editor.setCursorPosition(line, index)` | Moves cursor to a position | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |
| `editor.ensureLineVisible(line)` | Scrolls to make a line visible | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |

### How Jedi Definitions Work

Jedi returns `Name` objects that contain the definition location:

```python
from jedi import Script

script = Script(code="import os\nos.path.join()", path="test.py")
# Get definitions at line 2, column 10 (the "join" part)
defs = script.goto(line=2, column=10)

for definition in defs:
    print(definition.name)        # "join"
    print(definition.module_path) # "/usr/lib/python3.x/os.py"
    print(definition.line)       # 70 (the line where join is defined)
    print(definition.column)     # 4 (the column where join is defined)
```

[Jedi goto docs](https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.Script.goto)

### Step 1: Add a Background Thread for Definition Lookup

Jedi analysis can take a few hundred milliseconds. Running it on the main thread would freeze the GUI. You already have the `AutoCompleter` QThread pattern — this follows the same approach.

Create `definition_finder.py`:

```python
from PyQt5.QtCore import QThread, pyqtSignal
from jedi import Script


class DefinitionFinder(QThread):
    """
    Background thread that finds where a symbol is defined using Jedi.
    Runs in a separate thread so the GUI doesn't freeze during analysis.
    """
    definition_found = pyqtSignal(str, int, int)  # module_path, line, column
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
        """Start the search. Jedi uses 1-based line numbers."""
        if self.isRunning():
            return
        self.line = line
        self.column = column
        self.code = code
        self.file_path = file_path
        self.start()

    def run(self):
        try:
            # Jedi Script takes the code string and optional file path
            # The path helps Jedi resolve relative imports
            script = Script(code=self.code, path=self.file_path)

            # goto returns a list of Name objects representing definitions
            # If the cursor is on a built-in like "print", the definition
            # might be in a compiled module (module_path is None)
            definitions = script.goto(
                line=self.line,
                column=self.column,
                # follow_imports=True means: if the symbol is imported
                # from another module, follow the import to the actual
                # definition location
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
                    # Built-in or compiled module — can't open the file
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
        self._shutting_down = True
        self.requestInterruption()
        if self.isRunning():
            self.wait()
```

### Step 2: Add the Definition Finder to PythonEditor

In `pythoneditor.py`, modify `__init__` to create a `DefinitionFinder`:

```python
from code_inteligence.definition_finder import DefinitionFinder


class PythonEditor(QsciScintilla):
    def __init__(self, parent=None, path: Path = None, is_python_file: bool = True):
        # ... existing init code ...

        if self.is_python_file:
            # ... existing lexer and autocompleter setup ...

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

        pos = self.getCursorPosition()
        line, index = pos[0] + 1, pos[1]  # Jedi uses 1-based lines
        text = self.text()
        if not text.strip():
            return

        file_path = str(self.full_path) if self.full_path else None
        self.definition_finder.find(line, index, text, file_path)

    def _on_definition_found(self, module_path: str, line: int, column: int):
        """Called when Jedi finds a definition location."""
        if self._shutting_down:
            return

        # Emit a signal that MainWindow can connect to
        # We need MainWindow to open the file (it might be a different file)
        # and set the cursor position
        # For same-file navigation, we can do it directly:
        if self.full_path and str(self.full_path) == module_path:
            # Definition is in the same file
            self.setCursorPosition(line - 1, column)  # convert to 0-based
            self.ensureLineVisible(line - 1)
            self.setFocus()
        else:
            # Definition is in a different file — MainWindow needs to open it
            # We emit a signal that MainWindow connects to
            # (You need to add this signal to the class)
            self.goto_definition_requested.emit(module_path, line - 1, column)

    def _on_definition_not_found(self):
        """Called when no definition is found."""
        if not self._shutting_down:
            # Could show a tooltip or status bar message
            pass

    def _on_definition_error(self, err: str):
        """Called when an error occurs during definition lookup."""
        if not self._shutting_down:
            print("Definition error:", err)
```

Add the signal at the class level:

```python
class PythonEditor(QsciScintilla):
    # Signal emitted when the user wants to go to a definition in another file
    # MainWindow connects this to open the file and jump to the position
    goto_definition_requested = pyqtSignal(str, int, int)  # path, line, column
```

Add `pyqtSignal` to imports if not already there:

```python
from PyQt5.QtCore import Qt, pyqtSignal
```

### Step 3: Handle F12 and Ctrl+Click

In `PythonEditor.keyPressEvent`, add F12 handling:

```python
    def keyPressEvent(self, e: QKeyEvent) -> None:
        # F12 — Go to definition
        if e.key() == Qt.Key.Key_F12:
            self.goto_definition()
            return

        # ... existing Ctrl+Space and Ctrl+X handling ...

        return super().keyPressEvent(e)
```

### Step 4: Connect in MainWindow

In `set_new_tab` (and `new_file`), after creating the editor, connect the signal:

```python
        self.editor.goto_definition_requested.connect(self._open_file_at_position)
```

Add the handler to `MainWindow`:

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

### Step 5: Add Menu Action

In `set_up_menu`:

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

### Step 6: Shutdown the DefinitionFinder

In `PythonEditor.shutdown`, add:

```python
    def shutdown(self):
        self._shutting_down = True
        self._loading_text = True
        if hasattr(self, "auto_completer"):
            # ... existing autocompleter shutdown ...
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
Background thread runs Jedi: Script(code).goto(line=2, column=10)
    ↓
Jedi returns: Name(name="join", module_path="/usr/lib/python3.x/os.py",
                   line=70, column=4)
    ↓
definition_found signal: ("/usr/lib/python3.x/os.py", 70, 4)
    ↓
PythonEditor._on_definition_found()
    ↓
If same file: editor.setCursorPosition(69, 4)  (0-based)
If different file: goto_definition_requested signal → MainWindow opens it
```

---

## Feature 15: Code Outline Panel

### Concept

Parse the current Python file for `class` and `def` declarations. Show them in a tree panel on the side. Click a symbol to jump to it.

### Key Methods

| Method | Description | Docs |
|---|---|---|
| `ast.parse(source)` | Parses Python source code into an AST | [Link](https://docs.python.org/3/library/ast.html#ast.parse) |
| `ast.walk(tree)` | Iterates over all nodes in the AST | [Link](https://docs.python.org/3/library/ast.html#ast.walk) |
| `QTreeWidget` | A tree view widget for showing hierarchical data | [Link](https://doc.qt.io/qt-5/qtreewidget.html) |
| `QTreeWidgetItem` | An item in a QTreeWidget | [Link](https://doc.qt.io/qt-5/qtreewidgetitem.html) |
| `item.setData(column, role, data)` | Stores data on a tree item | [Link](https://doc.qt.io/qt-5/qtreewidgetitem.html#setData) |

### Step 1: Create `code_outline.py`

```python
import ast
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QTreeWidget, QTreeWidgetItem


class CodeOutlineTree(QTreeWidget):
    """
    A tree widget that shows the structure of a Python file.
    Displays classes and functions in a collapsible tree.
    Clicking an item jumps to that line in the editor.
    """

    # Signal emitted when the user clicks a symbol
    # Parameters: line number (0-based), column number (0-based)
    symbol_clicked = pyqtSignal(int, int)

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
            QTreeWidget::item {
                padding: 2px 0px;
            }
            QTreeWidget::item:selected {
                background-color: #2c313a;
            }
        """)
        # itemClicked signal is built into QTreeWidget
        # It emits (item, column) when the user clicks an item
        # Docs: https://doc.qt.io/qt-5/qtreewidget.html#itemClicked
        self.itemClicked.connect(self._on_item_clicked)

    def update_outline(self, code: str):
        """Parse Python code and rebuild the tree."""
        self.clear()

        try:
            # ast.parse parses Python source code into an Abstract Syntax Tree
            # SyntaxError is raised if the code has syntax errors
            # Docs: https://docs.python.org/3/library/ast.html#ast.parse
            tree = ast.parse(code)
        except SyntaxError:
            # Code has syntax errors — can't parse it
            return

        # Walk the top-level nodes of the module body
        # We only look at top-level classes and functions, then
        # their direct children (methods and nested functions)
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                # Class definition
                # node.name is the class name (e.g. "MyClass")
                # node.lineno is the line number (1-based)
                # node.col_offset is the column (usually 0 for top-level)
                class_item = QTreeWidgetItem(self)
                class_item.setText(0, f"class {node.name}")
                class_item.setData(0, Qt.UserRole, (node.lineno - 1, node.col_offset))
                # Use a different color for classes
                class_item.setForeground(0, self._color("#e5c07b"))

                # Add methods as children
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_item = QTreeWidgetItem(class_item)
                        method_item.setText(0, f"def {child.name}()")
                        method_item.setData(0, Qt.UserRole, (child.lineno - 1, child.col_offset))
                        method_item.setForeground(0, self._color("#61afef"))

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Top-level function definition
                func_item = QTreeWidgetItem(self)
                func_item.setText(0, f"def {node.name}()")
                func_item.setData(0, Qt.UserRole, (node.lineno - 1, node.col_offset))
                func_item.setForeground(0, self._color("#61afef"))

        # Expand all class nodes by default
        self.expandAll()

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int):
        """When the user clicks a symbol, emit the line/column."""
        # Retrieve the stored line/column data
        # Qt.UserRole is a custom role for storing arbitrary data
        # Docs: https://doc.qt.io/qt-5/qtreewidgetitem.html#data
        data = item.data(0, Qt.UserRole)
        if data is not None:
            line, col = data
            self.symbol_clicked.emit(line, col)

    def _color(self, hex_color: str):
        from PyQt5.QtGui import QColor
        return QColor(hex_color)
```

### Step 2: Add the Outline Panel to the Sidebar

In `main.py`, in `set_up_body`, create the outline panel and add it as a third sidebar view:

```python
        from code_outline import CodeOutlineTree

        # Code outline panel
        self.outline_tree = CodeOutlineTree()
        self.outline_tree.symbol_clicked.connect(self._goto_symbol)

        # Add to the side panel as a third view
        self.outline_frame = self.get_frame()
        outline_layout = QVBoxLayout()
        outline_layout.setContentsMargins(0, 0, 0, 0)
        outline_layout.setSpacing(0)

        outline_label = QLabel("Outline")
        outline_label.setStyleSheet("color: #636d83; padding: 4px 8px; font-size: 12px;")
        outline_layout.addWidget(outline_label)
        outline_layout.addWidget(self.outline_tree)
        self.outline_frame.setLayout(outline_layout)

        # Add to the stacked widget
        self.side_panel.addWidget(self.outline_frame)
```

Add the handler:

```python
    def _goto_symbol(self, line: int, column: int):
        """Jump to a symbol in the current editor."""
        editor = self.tab_view.currentWidget()
        if editor is not None:
            editor.setCursorPosition(line, column)
            editor.ensureLineVisible(line)
            editor.setFocus()
```

### Step 3: Add a Sidebar Icon for the Outline

In `set_up_body`, add a third icon:

```python
        # Store icon paths for active/inactive switching
        outline_label = self.get_sidebar_label("icons/code.png", "outline")
        self.sidebar_labels["outline"] = outline_label
        side_bar_layout.addWidget(outline_label)
```

Update `show_hide_tab` to include the outline panel:

```python
    def show_hide_tab(self, e, type_):
        panels = {
            "folder": self.file_manager_frame,
            "search": self.search_frame,
            "outline": self.outline_frame,  # ← ADD THIS
        }

        icon_map = {
            "folder": ("icons/folder.png", "icons/folder-active.png"),
            "search": ("icons/search.png", "icons/search-active.png"),
            "outline": ("icons/code.png", "icons/code-active.png"),  # ← ADD THIS
        }
        # ... rest of the method stays the same ...
```

### Step 4: Update the Outline When the Editor Changes

In `set_new_tab`, after loading the file:

```python
        # Update the code outline
        if isinstance(self.editor, PythonEditor):
            self.outline_tree.update_outline(text)
```

In `on_tab_changed`, at the end:

```python
        editor = self.tab_view.currentWidget()
        if isinstance(editor, PythonEditor):
            self.outline_tree.update_outline(editor.text())
        else:
            self.outline_tree.clear()
```

Connect `textChanged` with a debounce timer to update the outline as the user types:

```python
        # In __init__:
        self._outline_debounce = QTimer(self)
        self._outline_debounce.setSingleShot(True)
        self._outline_debounce.setInterval(500)  # 500ms delay after typing stops
        self._outline_debounce.timeout.connect(self._update_outline)

        # In set_new_tab, after connecting textChanged:
        self.editor.textChanged.connect(self._outline_debounce.start)

    def _update_outline(self):
        editor = self.tab_view.currentWidget()
        if isinstance(editor, PythonEditor):
            self.outline_tree.update_outline(editor.text())
```

### How the AST Parsing Works

```
Python source code:
    class MyClass:
        def __init__(self):
            pass

        def do_something(self):
            pass

    def main():
        pass

ast.parse(code) → AST tree:
    Module(body=[
        ClassDef(name="MyClass", lineno=1, body=[
            FunctionDef(name="__init__", lineno=2),
            FunctionDef(name="do_something", lineno=5),
        ]),
        FunctionDef(name="main", lineno=8),
    ])

Tree widget output:
    ▼ class MyClass
        def __init__()
        def do_something()
    def main()
```

---

## Feature 16: Settings Dialog

### Concept

A proper dialog with tabs for Editor, Python, Markdown, and Appearance settings. Everything stored in `settings.json`.

### Step 1: Create `settings_dialog.py`

```python
import json
from pathlib import Path
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QSpinBox, QCheckBox, QPushButton,
    QFontComboBox, QComboBox, QFileDialog, QGroupBox, QFormLayout
)
from PyQt5.QtGui import QFont


class SettingsDialog(QDialog):
    """
    A tabbed settings dialog for configuring the editor.
    All settings are stored in settings.json and persist across restarts.
    """

    def __init__(self, current_settings: dict, parent=None):
        super().__init__(parent)
        self.settings = current_settings.copy()
        self._init_ui()

    def _init_ui(self):
        self.setWindowTitle("Settings")
        self.setFixedSize(500, 400)

        layout = QVBoxLayout(self)

        # QTabWidget holds multiple pages, only one visible at a time
        # Docs: https://doc.qt.io/qt-5/qtabwidget.html
        self.tabs = QTabWidget()
        self.tabs.addTab(self._create_editor_tab(), "Editor")
        self.tabs.addTab(self._create_python_tab(), "Python")
        self.tabs.addTab(self._create_appearance_tab(), "Appearance")
        layout.addWidget(self.tabs)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(save_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        layout.addLayout(btn_row)

    def _create_editor_tab(self) -> QWidget:
        """Tab for editor-related settings (font, tab width, etc.)."""
        tab = QWidget()
        layout = QFormLayout(tab)

        # QFontComboBox lets the user pick a font family
        # Docs: https://doc.qt.io/qt-5/qfontcombobox.html
        self.font_combo = QFontComboBox()
        self.font_combo.setCurrentFont(QFont(self.settings.get("font_family", "sans-serif")))
        layout.addRow("Font family:", self.font_combo)

        # QSpinBox for numeric input with up/down buttons
        # Docs: https://doc.qt.io/qt-5/qspinbox.html
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 32)
        self.font_size_spin.setValue(self.settings.get("font_size", 13))
        layout.addRow("Font size:", self.font_size_spin)

        self.tab_width_spin = QSpinBox()
        self.tab_width_spin.setRange(1, 8)
        self.tab_width_spin.setValue(self.settings.get("tab_width", 4))
        layout.addRow("Tab width:", self.tab_width_spin)

        # Word wrap checkbox
        self.word_wrap_cb = QCheckBox("Enable word wrap")
        self.word_wrap_cb.setChecked(self.settings.get("word_wrap", False))
        layout.addRow("", self.word_wrap_cb)

        # Auto-save checkbox
        self.auto_save_cb = QCheckBox("Auto-save on focus loss")
        self.auto_save_cb.setChecked(self.settings.get("auto_save", False))
        layout.addRow("", self.auto_save_cb)

        return tab

    def _create_python_tab(self) -> QWidget:
        """Tab for Python interpreter settings."""
        tab = QWidget()
        layout = QFormLayout(tab)

        # Interpreter path with browse button
        interp_row = QHBoxLayout()
        self.interpreter_input = QLineEdit()
        self.interpreter_input.setText(self.settings.get("interpreter", ""))
        interp_row.addWidget(self.interpreter_input)

        browse_btn = QPushButton("Browse...")
        # QFileDialog.getOpenFileName opens a file picker
        # We use it to let the user find python.exe
        browse_btn.clicked.connect(self._browse_interpreter)
        interp_row.addWidget(browse_btn)

        layout.addRow("Python interpreter:", interp_row)

        # Auto-run on save checkbox
        self.auto_run_cb = QCheckBox("Run on save (F5 after Ctrl+S)")
        self.auto_run_cb.setChecked(self.settings.get("auto_run", False))
        layout.addRow("", self.auto_run_cb)

        return tab

    def _create_appearance_tab(self) -> QWidget:
        """Tab for theme and visual settings."""
        tab = QWidget()
        layout = QFormLayout(tab)

        # Theme selection dropdown
        # QComboBox is a dropdown selector
        # Docs: https://doc.qt.io/qt-5/qcombobox.html
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Default Dark", "themes/theme.json")
        # Add more themes here as you create them
        current_theme = self.settings.get("theme", "themes/theme.json")
        index = self.theme_combo.findData(current_theme)
        if index >= 0:
            self.theme_combo.setCurrentIndex(index)
        layout.addRow("Theme:", self.theme_combo)

        # Show line numbers
        self.line_numbers_cb = QCheckBox("Show line numbers")
        self.line_numbers_cb.setChecked(self.settings.get("line_numbers", True))
        layout.addRow("", self.line_numbers_cb)

        # Highlight current line
        self.highlight_line_cb = QCheckBox("Highlight current line")
        self.highlight_line_cb.setChecked(self.settings.get("highlight_line", True))
        layout.addRow("", self.highlight_line_cb)

        return tab

    def _browse_interpreter(self):
        """Open a file dialog to pick python.exe."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Python Interpreter", "",
            "Python Executable (python.exe);;All Files (*)"
        )
        if path:
            self.interpreter_input.setText(path)

    def get_settings(self) -> dict:
        """Return the current settings as a dictionary."""
        return {
            "font_family": self.font_combo.currentFont().family(),
            "font_size": self.font_size_spin.value(),
            "tab_width": self.tab_width_spin.value(),
            "word_wrap": self.word_wrap_cb.isChecked(),
            "auto_save": self.auto_save_cb.isChecked(),
            "interpreter": self.interpreter_input.text(),
            "auto_run": self.auto_run_cb.isChecked(),
            "theme": self.theme_combo.currentData(),
            "line_numbers": self.line_numbers_cb.isChecked(),
            "highlight_line": self.highlight_line_cb.isChecked(),
        }
```

### Step 2: Add Settings Management to MainWindow

```python
    def _load_settings(self) -> dict:
        """Load settings from settings.json."""
        settings_path = Path(__file__).parent / "settings.json"
        if settings_path.exists():
            try:
                return json.loads(settings_path.read_text())
            except json.JSONDecodeError:
                pass
        return {}

    def _save_settings(self, settings: dict):
        """Save settings to settings.json."""
        settings_path = Path(__file__).parent / "settings.json"
        settings_path.write_text(json.dumps(settings, indent=2))

    def open_settings(self):
        """Open the settings dialog."""
        current = self._load_settings()
        dialog = SettingsDialog(current, self)

        # exec_() shows the dialog and blocks until the user clicks
        # Save (accept → returns QDialog.Accepted) or Cancel (reject → returns QDialog.Rejected)
        # Docs: https://doc.qt.io/qt-5/qdialog.html#exec
        if dialog.exec_() == QDialog.Accepted:
            new_settings = dialog.get_settings()
            self._save_settings(new_settings)
            self._apply_settings(new_settings)
            self.statusBar().showMessage("Settings saved", 2000)

    def _apply_settings(self, settings: dict):
        """Apply settings to the editor."""
        # Font
        font_family = settings.get("font_family", "sans-serif")
        font_size = settings.get("font_size", 13)
        tab_width = settings.get("tab_width", 4)

        # Apply to all open editors
        for i in range(self.tab_view.count()):
            editor = self.tab_view.widget(i)
            if editor is not None:
                font = QFont(font_family)
                font.setPointSize(font_size)
                editor.setFont(font)
                editor.setTabWidth(tab_width)
                editor.setWrapMode(
                    QsciScintilla.WrapWord if settings.get("word_wrap", False)
                    else QsciScintilla.WrapNone
                )
                editor.setMarginType(0, QsciScintilla.NumberMargin)
                editor.setMarginWidth(0, "0000")
                editor.setCaretLineVisible(settings.get("highlight_line", True))

        # Interpreter
        if settings.get("interpreter"):
            self.python_runner.set_interpreter(settings["interpreter"])
```

### Step 3: Add the Menu Action

In `set_up_menu`:

```python
        settings_action = view_menu.addAction("Settings")
        settings_action.setShortcut("Ctrl+,")
        settings_action.setShortcutContext(Qt.ApplicationShortcut)
        settings_action.triggered.connect(self.open_settings)
```

### How the Settings Dialog Works

```
User presses Ctrl+,
    ↓
open_settings() called
    ↓
SettingsDialog created with current settings from settings.json
    ↓
Dialog shows tabs: Editor | Python | Appearance
    ↓
User changes font to "Consolas", tab width to 2
    ↓
User clicks "Save"
    ↓
dialog.exec_() returns QDialog.Accepted
    ↓
dialog.get_settings() returns the new settings dict
    ↓
_save_settings() writes to settings.json
    ↓
_apply_settings() applies changes to all open editors
    ↓
App restarts → _load_settings() reads settings.json → settings restored
```

---

## Feature 17: Remember Open Tabs on Restart

### Concept

When the user closes the app, save the list of open file paths. On the next launch, reopen those files automatically.

### Key Methods

| Method | Description | Docs |
|---|---|---|
| `QSettings.setValue(key, value)` | Stores a value persistently | [Link](https://doc.qt.io/qt-5/qsettings.html#setValue) |
| `QSettings.value(key, default)` | Retrieves a stored value | [Link](https://doc.qt.io/qt-5/qsettings.html#value) |
| `QMainWindow.closeEvent(event)` | Called when the window is closing | [Link](https://doc.qt.io/qt-5/qwidget.html#closeEvent) |

### Step 1: Save Open Tabs on Close

Override `closeEvent` in `MainWindow`:

```python
    def closeEvent(self, event):
        """
        Called when the user closes the main window.
        Save the list of open file paths so they can be restored on restart.
        """
        # Collect the paths of all open tabs that have a file path
        open_paths = []
        for i in range(self.tab_view.count()):
            editor = self.tab_view.widget(i)
            path = getattr(editor, "path", None)
            if path is not None and Path(path).exists():
                open_paths.append(str(path))

        # Save to QSettings (persists across restarts)
        # Docs: https://doc.qt.io/qt-5/qsettings.html#setValue
        self.settings.setValue("open_tabs", open_paths)

        # Save the index of the active tab
        self.settings.setValue("active_tab", self.tab_view.currentIndex())

        # Shut down all Python editors (stop background threads)
        for i in range(self.tab_view.count()):
            editor = self.tab_view.widget(i)
            if isinstance(editor, PythonEditor):
                editor.shutdown()

        event.accept()
```

### Step 2: Restore Tabs on Startup

In `__init__`, after `self.init_ui()`:

```python
        # Restore previously open tabs
        self._restore_tabs()
```

```python
    def _restore_tabs(self):
        """Reopen files that were open when the app was last closed."""
        # QSettings.value returns the stored value or the default
        # We specify type=list so Qt knows to interpret it as a list
        open_paths = self.settings.value("open_tabs", [], type=list)

        if not open_paths:
            return

        for path_str in open_paths:
            path = Path(path_str)
            if path.exists():
                self.set_new_tab(path)

        # Restore the active tab
        active_index = self.settings.value("active_tab", 0, type=int)
        if 0 <= active_index < self.tab_view.count():
            self.tab_view.setCurrentIndex(active_index)
```

### How It Works

```
App closing
    ↓
closeEvent triggered
    ↓
Iterate over all tabs, collect file paths
    ↓
settings.setValue("open_tabs", ["C:/main.py", "C:/utils.py"])
    ↓
App exits

App restarting
    ↓
__init__ runs
    ↓
init_ui() creates the window
    ↓
_restore_tabs() called
    ↓
settings.value("open_tabs") returns ["C:/main.py", "C:/utils.py"]
    ↓
set_new_tab() called for each path
    ↓
settings.value("active_tab") returns 0
    ↓
tab_view.setCurrentIndex(0) restores the active tab
```

---

## Feature 18: Export to PDF

### Concept

Export the rendered Markdown preview to a PDF file. QWebEngineView has a built-in method for this.

### Key Methods

| Method | Description | Docs |
|---|---|---|
| `QWebEnginePage.printToPdf(filePath)` | Renders the web page to a PDF file | [Link](https://doc.qt.io/qt-5/qwebenginepage.html#printToPdf) |
| `QFileDialog.getSaveFileName()` | Opens a "Save As" dialog | [Link](https://doc.qt.io/qt-5/qfiledialog.html#getSaveFileName) |

### Step 1: Add the Menu Action

In `set_up_menu`, add to the File menu:

```python
        export_pdf_action = file_menu.addAction("Export to PDF")
        export_pdf_action.setShortcut("Ctrl+E")
        export_pdf_action.setShortcutContext(Qt.ApplicationShortcut)
        export_pdf_action.triggered.connect(self.export_pdf)
```

### Step 2: Write the Method

```python
    def export_pdf(self):
        """Export the Markdown preview to a PDF file."""
        editor = self.tab_view.currentWidget()
        if not isinstance(editor, MarkdownEditor):
            self.statusBar().showMessage("PDF export requires a Markdown file", 3000)
            return

        # Ask the user where to save the PDF
        # QFileDialog.getSaveFileName returns (path, filter)
        # Docs: https://doc.qt.io/qt-5/qfiledialog.html#getSaveFileName
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export to PDF", "", "PDF Files (*.pdf)"
        )

        if not file_path:
            return  # User cancelled

        # Ensure the extension is .pdf
        if not file_path.lower().endswith(".pdf"):
            file_path += ".pdf"

        # Make sure the preview is up to date
        self.render_preview()

        # QWebEnginePage.printToPdf renders the current web page
        # content to a PDF file. It's asynchronous — the PDF is
        # generated in the background and the file is written when done.
        # Docs: https://doc.qt.io/qt-5/qwebenginepage.html#printToPdf
        self.preview.page().printToPdf(file_path)

        self.statusBar().showMessage(f"Exported to {file_path}", 3000)
```

### How It Works

```
User presses Ctrl+E in a Markdown file
    ↓
export_pdf() called
    ↓
"Save As" dialog appears
    ↓
User picks "document.pdf"
    ↓
render_preview() updates the preview with the latest markdown
    ↓
preview.page().printToPdf("document.pdf")
    ↓
QWebEngine renders the HTML to PDF in the background
    ↓
PDF file written to disk
```

---

## Feature 19: Git Integration

### Concept

Show which files are modified, added, or untracked by Git. Color-code them in the file manager tree.

### Key Methods

| Method | Description | Docs |
|---|---|---|
| `subprocess.run(["git", "status", "--porcelain"], cwd=path)` | Runs git status in a subprocess | [Python subprocess docs](https://docs.python.org/3/library/subprocess.html#subprocess.run) |
| `QFileSystemModel.data(index, role)` | Returns data for a tree item — override to customize colors | [Link](https://doc.qt.io/qt-5/qfilesystemmodel.html#data) |

### Step 1: Create a Git Status Checker

Create `git_integration.py`:

```python
import subprocess
from pathlib import Path
from PyQt5.QtCore import QThread, pyqtSignal


class GitStatusChecker(QThread):
    """
    Background thread that runs `git status --porcelain` and parses the output.

    The --porcelain flag produces machine-readable output:
    Each line starts with a 2-character status code followed by the file path.

    Status codes:
      " M" — modified (not staged)
      "M " — modified (staged)
      "A " — added (staged)
      "??" — untracked
      "D " — deleted (staged)
      " D" — deleted (not staged)

    Docs: https://git-scm.com/docs/git-status#_porcelain_format
    """

    status_ready = pyqtSignal(dict)  # {filepath: status_code}

    def __init__(self):
        super().__init__(None)
        self.repo_path = ""
        self._shutting_down = False

    def check(self, repo_path: str):
        if self.isRunning():
            return
        self.repo_path = repo_path
        self.start()

    def run(self):
        if self._shutting_down:
            return

        try:
            # subprocess.run executes a command and waits for it to finish
            # capture_output=True captures stdout and stderr
            # text=True returns strings instead of bytes
            # cwd sets the working directory to the repo root
            # Docs: https://docs.python.org/3/library/subprocess.html#subprocess.run
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=5  # 5 second timeout
            )

            if result.returncode != 0:
                # Not a git repo or git not installed
                self.status_ready.emit({})
                return

            statuses = {}
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                # Porcelain format: "XY filename"
                # X = staged status, Y = working tree status
                status_code = line[:2]
                # File path starts at character 3
                # Strip quotes (git wraps paths with spaces in quotes)
                filepath = line[3:].strip('"')
                # Make the path absolute relative to the repo root
                full_path = str(Path(self.repo_path) / filepath)
                statuses[full_path] = status_code

            self.status_ready.emit(statuses)

        except (subprocess.TimeoutExpired, FileNotFoundError):
            # Git not installed or not a git repo
            self.status_ready.emit({})
        except Exception:
            self.status_ready.emit({})
```

### Step 2: Use Git Status in the File Manager

In `file_manager.py`, add the git status checker and color-code files:

```python
from git_integration import GitStatusChecker

class FileManager(QTreeView):
    def __init__(self, tab_view, get_editor, _swap_editor, set_new_tab=None, main_window=None, is_python=False):
        # ... existing init code ...

        # Git status checker
        self.git_checker = GitStatusChecker()
        self.git_checker.status_ready.connect(self._on_git_status)
        self.git_statuses = {}  # {filepath: status_code}

        # Color mapping for git statuses
        self.git_colors = {
            " M": "#e5c07b",  # modified — yellow
            "M ": "#98c379",  # staged — green
            "A ": "#98c379",  # added — green
            "??": "#56b6c2",  # untracked — cyan
            "D ": "#e06c75",  # deleted — red
            " D": "#e06c75",  # deleted — red
        }

        # Check git status when root path changes
        # We'll trigger this from MainWindow when a folder is opened

    def check_git_status(self):
        """Start a git status check for the current root path."""
        root = self.model.rootPath()
        self.git_checker.check(root)

    def _on_git_status(self, statuses: dict):
        """Called when the git status check completes."""
        self.git_statuses = statuses
        # Force a repaint of the tree view to show the new colors
        # viewport() returns the visible area of the scroll view
        # update() triggers a repaint
        self.viewport().update()

    def get_git_color(self, filepath: str):
        """Return the color for a file based on its git status, or None."""
        status = self.git_statuses.get(filepath)
        if status and status in self.git_colors:
            return self.git_colors[status]
        return None
```

### Step 3: Custom Delegate for Colored Filenames

Override the file model's data method to apply git colors:

```python
class GitAwareFileSystemModel(QFileSystemModel):
    """
    A QFileSystemModel that color-codes filenames based on git status.
    """

    def __init__(self, file_manager):
        super().__init__()
        self.file_manager = file_manager

    def data(self, index, role=Qt.DisplayRole):
        """
        Override data() to inject git colors.

        QFileSystemModel.data() is called by the tree view to get
        the display text, icon, color, etc. for each item.
        We intercept Qt.ForegroundRole to return a git-based color.

        Docs: https://doc.qt.io/qt-5/qfilesystemmodel.html#data
        """
        if role == Qt.ForegroundRole:
            # This role asks for the text color
            filepath = self.filePath(index)
            color = self.file_manager.get_git_color(filepath)
            if color:
                from PyQt5.QtGui import QColor
                return QColor(color)

        # For all other roles, use the default behavior
        return super().data(index, role)
```

In `FileManager.__init__`, replace the model:

```python
        # BEFORE:
        self.model = QFileSystemModel()

        # AFTER:
        self.model = GitAwareFileSystemModel(self)
```

### Step 4: Trigger Git Status on Folder Open

In `MainWindow.open_folder`, after setting the root path:

```python
    def open_folder(self):
        # ... existing code ...
        self.file_manager.model.setRootPath(new_folder)
        self.file_manager.setRootIndex(self.file_manager.model.index(new_folder))
        self.statusBar().showMessage(f"Opened {new_folder}", 2000)

        # Check git status for the new folder
        self.file_manager.check_git_status()
```

### Step 5: Add a "Refresh Git Status" Menu Action

```python
        git_refresh_action = view_menu.addAction("Refresh Git Status")
        git_refresh_action.setShortcut("Ctrl+Shift+G")
        git_refresh_action.setShortcutContext(Qt.ApplicationShortcut)
        git_refresh_action.triggered.connect(self.file_manager.check_git_status)
```

### How Git Status Colors Work

| Status | Color | Meaning |
|---|---|---|
| ` M` | Yellow (#e5c07b) | Modified, not staged |
| `M ` | Green (#98c379) | Modified, staged |
| `A ` | Green (#98c379) | Added (new file, staged) |
| `??` | Cyan (#56b6c2) | Untracked (new file, not added) |
| `D ` | Red (#e06c75) | Deleted, staged |
| ` D` | Red (#e06c75) | Deleted, not staged |

---

## Feature 20: Command Palette

### Concept

A popup that appears when the user presses Ctrl+Shift+P. It shows a fuzzy-searchable list of all available commands. The user types to filter, presses Enter to execute.

### Key Methods

| Method | Description | Docs |
|---|---|---|
| `QLineEdit` | Input field for typing the search query | [Link](https://doc.qt.io/qt-5/qlineedit.html) |
| `QListWidget` | List of matching commands | [Link](https://doc.qt.io/qt-5/qlistwidget.html) |
| `QShortcut` | Registers a keyboard shortcut | [Link](https://doc.qt.io/qt-5/qshortcut.html) |

### Step 1: Create `command_palette.py`

```python
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem
)


class CommandPalette(QWidget):
    """
    A popup command palette (like VS Code's Ctrl+Shift+P).
    Shows a searchable list of commands. User types to filter,
    presses Enter to execute.

    The palette doesn't know what commands do — it just collects
    user input and emits a signal. MainWindow connects that signal
    to the actual command execution.
    """

    command_selected = pyqtSignal(str)  # emits the command name

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        # Qt.Popup makes this a popup window (closes when you click outside)
        # Qt.FramelessWindowHint removes the window border
        self._commands = []
        self._init_ui()
        self._connect_signals()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Search input
        self.input = QLineEdit()
        self.input.setPlaceholderText("Type a command...")
        self.input.setStyleSheet("""
            QLineEdit {
                background-color: #1b1d23;
                color: #dcdfe4;
                border: 1px solid #3d424d;
                border-radius: 0px;
                padding: 8px 12px;
                font-size: 14px;
            }
            QLineEdit:focus {
                border: 1px solid #528bff;
            }
        """)
        layout.addWidget(self.input)

        # Results list
        self.list = QListWidget()
        self.list.setStyleSheet("""
            QListWidget {
                background-color: #21252b;
                color: #dcdfe4;
                border: none;
                padding: 4px;
            }
            QListWidget::item {
                padding: 6px 12px;
            }
            QListWidget::item:selected {
                background-color: #2c313a;
            }
        """)
        # Set a reasonable size
        self.list.setMinimumWidth(400)
        self.list.setMaximumHeight(300)
        layout.addWidget(self.list)

    def _connect_signals(self):
        # textChanged fires every time the user types a character
        self.input.textChanged.connect(self._filter_commands)
        # returnPressed fires when Enter is pressed
        self.input.returnPressed.connect(self._execute_selected)
        # itemClicked fires when the user clicks a list item
        self.list.itemClicked.connect(self._on_item_clicked)

    def set_commands(self, commands: list):
        """
        Set the list of available commands.
        commands should be a list of (name, description) tuples.
        Example: [("File: New", "Create a new file"), ("File: Open", "Open a file")]
        """
        self._commands = commands

    def show_palette(self):
        """Show the palette and populate it with all commands."""
        self.input.clear()
        self._populate_list(self._commands)
        self.input.setFocus()
        self.show()

    def _filter_commands(self, text: str):
        """Filter the command list based on the search text."""
        text_lower = text.lower()
        filtered = [
            (name, desc) for name, desc in self._commands
            if text_lower in name.lower()
        ]
        self._populate_list(filtered)

    def _populate_list(self, commands: list):
        """Fill the list widget with commands."""
        self.list.clear()
        for name, desc in commands:
            item = QListWidgetItem(name)
            # Store the command name as data for retrieval when clicked
            item.setData(Qt.UserRole, name)
            # Set tooltip to the description
            item.setToolTip(desc)
            self.list.addItem(item)

        # Select the first item so Enter works immediately
        if self.list.count() > 0:
            self.list.setCurrentRow(0)

    def _execute_selected(self):
        """Execute the currently selected command."""
        item = self.list.currentItem()
        if item is not None:
            command = item.data(Qt.UserRole)
            self.command_selected.emit(command)
        self.hide()

    def _on_item_clicked(self, item: QListWidgetItem):
        """Handle click on a list item."""
        command = item.data(Qt.UserRole)
        self.command_selected.emit(command)
        self.hide()
```

### Step 2: Add the Command Palette to MainWindow

In `init_ui`:

```python
        # Command palette
        self.command_palette = CommandPalette(self)
        self._build_command_list()
        self.command_palette.command_selected.connect(self._execute_command)
```

### Step 3: Build the Command List

```python
    def _build_command_list(self):
        """Define all commands available in the palette."""
        self._commands = [
            ("File: New", "Create a new file"),
            ("File: Open File", "Open an existing file"),
            ("File: Open Folder", "Open a folder in the sidebar"),
            ("File: Save", "Save the current file"),
            ("File: Save As", "Save the current file with a new name"),
            ("File: Save All", "Save all open files"),
            ("File: Close All", "Close all open tabs"),
            ("File: Export to PDF", "Export Markdown to PDF"),
            ("Edit: Undo", "Undo last edit"),
            ("Edit: Redo", "Redo last undone edit"),
            ("Edit: Find", "Find in current file"),
            ("Edit: Replace", "Find and replace in current file"),
            ("Edit: Go to Line", "Jump to a specific line number"),
            ("Edit: Toggle Comment", "Comment or uncomment selected lines"),
            ("Edit: Select All", "Select all text"),
            ("Edit: Delete Line", "Delete the current line"),
            ("Mode: Python Editor", "Switch to Python editing mode"),
            ("Mode: Markdown Editor", "Switch to Markdown editing mode"),
            ("Run: Run File", "Run the current Python file"),
            ("Run: Run with Arguments", "Run with command-line arguments"),
            ("Run: Stop", "Stop the running process"),
            ("Run: Choose Interpreter", "Select which Python to use"),
            ("View: Toggle Sidebar", "Show or hide the sidebar"),
            ("View: Toggle Preview", "Show or hide the Markdown preview"),
            ("View: Toggle Console", "Show or hide the Python console"),
            ("View: Settings", "Open the settings dialog"),
        ]
        self.command_palette.set_commands(self._commands)
```

### Step 4: Execute Commands by Name

```python
    def _execute_command(self, command_name: str):
        """Execute a command by its name (from the command palette)."""
        # Map command names to methods
        command_map = {
            "File: New": self.new_file,
            "File: Open File": self.open_file,
            "File: Open Folder": self.open_folder,
            "File: Save": self.save_file,
            "File: Save As": self.save_as,
            "File: Save All": self.save_all,
            "File: Close All": self._close_all_tabs,
            "File: Export to PDF": self.export_pdf,
            "Edit: Undo": self.undo,
            "Edit: Redo": self.redo,
            "Edit: Find": self.show_find_bar,
            "Edit: Replace": self.show_replace_bar,
            "Edit: Go to Line": self.goto_line,
            "Edit: Toggle Comment": self.toggle_comment,
            "Edit: Select All": self.select_all,
            "Edit: Delete Line": self.delete_line,
            "Mode: Python Editor": self.change_editor_python,
            "Mode: Markdown Editor": self.change_editor_markdown,
            "Run: Run File": self.run_current_file,
            "Run: Run with Arguments": self.run_with_arguments,
            "Run: Stop": self.python_runner.stop,
            "Run: Choose Interpreter": self.choose_interpreter,
            "View: Toggle Sidebar": self.toggle_sidebar,
            "View: Toggle Preview": self.toggle_preview,
            "View: Toggle Console": self.toggle_console,
            "View: Settings": self.open_settings,
        }

        method = command_map.get(command_name)
        if method:
            method()
```

### Step 5: Add the Shortcut

In `set_up_menu`:

```python
        # Command palette shortcut (not in a menu — just a shortcut)
        palette_shortcut = QShortcut(QKeySequence("Ctrl+Shift+P"), self)
        palette_shortcut.activated.connect(self._show_command_palette)
```

Wait — Ctrl+Shift+P is already used for "Switch to Python Editor". Use a different shortcut:

```python
        # Use Ctrl+Shift+A... no, that's Save All.
        # Use Ctrl+P (common in VS Code for file search, but we'll use it for commands)
        palette_shortcut = QShortcut(QKeySequence("Ctrl+Shift+L"), self)
        palette_shortcut.activated.connect(self._show_command_palette)

    def _show_command_palette(self):
        """Show the command palette popup."""
        # Position it at the top-center of the window
        rect = self.geometry()
        x = rect.center().x() - 200  # 200 = half the palette width
        y = rect.top() + 60
        self.command_palette.move(x, y)
        self.command_palette.show_palette()
```

### How the Command Palette Works

```
User presses Ctrl+Shift+L
    ↓
_show_command_palette() positions and shows the popup
    ↓
Palette shows all 26 commands in a list
    ↓
User types "save"
    ↓
_filter_commands filters to: "File: Save", "File: Save As", "File: Save All"
    ↓
User presses Enter (or clicks an item)
    ↓
command_selected signal emits "File: Save"
    ↓
_execute_command("File: Save") looks up the method in command_map
    ↓
self.save_file() is called
    ↓
Palette hides
```

---

## Feature 21: Pip Package Manager (pip console)

### Concept

A pip input field at the bottom of the console where the user can type `install requests`, `uninstall numpy`, `list`, etc. Commands are executed using the selected Python interpreter via `python -m pip`.

### Key Methods

| Method | Description | Docs |
|---|---|---|
| `QProcess.start(program, args)` | Starts a subprocess | [Link](https://doc.qt.io/qt-5/qprocess.html#start) |
| `QProcessEnvironment.systemEnvironment()` | Gets the current environment | [Link](https://doc.qt.io/qt-5/qprocessenvironment.html#systemEnvironment) |
| `str.split()` | Splits a string into a list | [Link](https://docs.python.org/3/library/stdtypes.html#str.split) |
| `QLineEdit.returnPressed` | Signal fired when Enter is pressed | [Link](https://doc.qt.io/qt-5/qlineedit.html#returnPressed) |
| `QMessageBox.question()` | Shows a Yes/No confirmation dialog | [Link](https://doc.qt.io/qt-5/qmessagebox.html#question) |
| `os.path.expanduser("~")` | Gets the user's home directory | [Link](https://docs.python.org/3/library/os.path.html#os.path.expanduser) |

### Step 1: Add `run_pip` to PythonRunner

In `python_runner.py`, add this method:

```python
    def run_pip(self, args: str):
        """
        Run a pip command using the selected interpreter.

        Examples:
            args = "install requests"     → python -m pip install requests
            args = "uninstall numpy"      → python -m pip uninstall numpy
            args = "list"                 → python -m pip list

        We use 'python -m pip' instead of calling pip.exe directly
        because pip.exe might not be on the PATH, but 'python -m pip'
        always works as long as the interpreter has pip installed.

        Docs: https://docs.python.org/3/installing/index.html#how-do-i-install-pip
        """
        if self.is_running():
            self.stop()

        import os
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")

        self.process.setProcessEnvironment(env)
        # Set working directory to the user's home folder
        # (pip doesn't need a specific working directory)
        self.process.setWorkingDirectory(os.path.expanduser("~"))

        # -m pip runs pip as a Python module
        # This is the recommended way to invoke pip
        # Docs: https://docs.python.org/3/using/cmdline.html#cmdoption-m
        #
        # args.split() converts "install requests numpy" into
        # ["install", "requests", "numpy"]
        # QProcess.start takes a list of arguments
        self.process.start(self.interpreter, ["-m", "pip"] + args.split())
```

### Why Use `python -m pip` Instead of `pip`

| Approach | Problem |
|---|---|
| `pip install requests` | `pip.exe` might not be on the system PATH |
| `pip3 install requests` | Same issue, plus pip3 might not exist |
| `python -m pip install requests` | Always works — runs pip as a module using the selected Python |

The [Python documentation](https://docs.python.org/3/installing/index.html#how-do-i-install-pip) explicitly recommends `python -m pip` as the most reliable way to invoke pip.

### Step 2: Add the Pip Input to ConsoleWidget

In `console_widget.py`, in `_init_ui`, add a pip input row below the existing stdin input:

```python
        # --- Input line (existing, for stdin) ---
        self.input_line = QLineEdit()
        self.input_line.setPlaceholderText("Type input for stdin and press Enter...")
        self.input_line.setFont(QFont("Consolas", 11))
        layout.addWidget(self.input_line)

        # --- Pip command line (NEW) ---
        pip_row = QHBoxLayout()
        pip_row.setContentsMargins(6, 2, 6, 4)
        pip_row.setSpacing(4)

        pip_label = QLabel("pip")
        pip_label.setStyleSheet(
            "color: #98c379; font-family: Consolas; font-size: 11px; font-weight: bold;"
        )

        self.pip_input = QLineEdit()
        self.pip_input.setPlaceholderText("install requests  (or: uninstall, list, freeze...)")
        self.pip_input.setFont(QFont("Consolas", 11))
        self.pip_input.setStyleSheet("""
            QLineEdit {
                background-color: #1b1d23;
                color: #dcdfe4;
                border: 1px solid #3d424d;
                border-radius: 3px;
                padding: 3px 8px;
            }
            QLineEdit:focus {
                border: 1px solid #98c379;
            }
        """)

        pip_row.addWidget(pip_label)
        pip_row.addWidget(self.pip_input)
        layout.addLayout(pip_row)
```

### Step 3: Connect the Pip Input Signal

In `_connect_signals`, add:

```python
        # Pip input → run pip command
        self.pip_input.returnPressed.connect(self._submit_pip)
```

### Step 4: Write the Pip Command Handler

```python
    def _submit_pip(self):
        """
        Handle pip commands typed in the pip input field.

        The user types a pip subcommand (without the "pip" prefix):
          - "install requests"     → installs the requests package
          - "uninstall numpy"      → removes numpy
          - "list"                 → shows all installed packages
          - "freeze"               → shows installed packages in requirements format
          - "show requests"        → shows info about a package
        """
        text = self.pip_input.text().strip()
        if not text:
            return

        self.pip_input.clear()

        # Show the command in the console output (green, like a terminal prompt)
        self._append(f"$ pip {text}\n", "#98c379")

        # Check for potentially dangerous commands
        if text.lower().startswith("uninstall"):
            # Ask for confirmation before uninstalling
            # QMessageBox.question shows a Yes/No dialog
            # Docs: https://doc.qt.io/qt-5/qmessagebox.html#question
            reply = QMessageBox.question(
                self, "Confirm Uninstall",
                f"Run: pip {text}?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply == QMessageBox.No:
                self._append("Cancelled.\n", "#e06c75")
                return

        # For uninstall, add --yes flag so pip doesn't hang waiting for input
        # Without -y, pip prompts "Proceed (Y/n)?" which blocks the process
        if text.lower().startswith("uninstall") and "-y" not in text and "--yes" not in text:
            text = text + " -y"

        # Run the pip command via the PythonRunner
        # The runner will emit output_ready/error_ready signals as pip produces output
        self.runner.run_pip(text)
```

### Step 5: Add QMessageBox to Imports

In `console_widget.py`, update the imports:

```python
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPlainTextEdit,
    QLineEdit, QPushButton, QLabel, QMessageBox  # ← ADD QMessageBox
)
```

### Step 6: How the Console Looks

```
┌─────────────────────────────────────────────────────────┐
│  ● Idle                              [Run] [Stop] [Clear] │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  $ pip install requests                                  │  ← green (what you typed)
│  Collecting requests                                    │  ← gray (pip stdout)
│    Downloading requests-2.31.0-py3-none-any.whl (62 kB) │
│    Downloading charset_normalizer-3.1.0-py3-none-...     │
│    Downloading urllib3-2.0.3-py3-none-any.whl (123 kB)   │
│    Downloading idna-3.4-py3-none-any.whl (60 kB)        │
│    Downloading certifi-2023.5.7-py3-none-any.whl (60 kB)│
│  Installing collected packages: urllib3, idna, ...      │
│  Successfully installed requests-2.31.0                 │
│                                                          │
├─────────────────────────────────────────────────────────┤
│  Type input for stdin and press Enter...                │  ← existing stdin input
├─────────────────────────────────────────────────────────┤
│  pip │ install requests                                  │  ← NEW pip input
└─────────────────────────────────────────────────────────┘
```

### Supported Pip Commands

| What you type | What runs | Result |
|---|---|---|
| `install requests` | `python -m pip install requests` | Installs requests |
| `install requests numpy flask` | `python -m pip install requests numpy flask` | Installs multiple packages |
| `uninstall numpy` | `python -m pip uninstall numpy -y` | Removes numpy (auto-confirms) |
| `list` | `python -m pip list` | Shows all installed packages |
| `freeze` | `python -m pip freeze` | Shows installed packages in requirements format |
| `install -r requirements.txt` | `python -m pip install -r requirements.txt` | Installs from a requirements file |
| `show requests` | `python -m pip show requests` | Shows info about a package |
| `--version` | `python -m pip --version` | Shows pip version |
| `upgrade pip` | `python -m pip install --upgrade pip` | Upgrades pip itself |

### How the Pip Command Flow Works

```
User types "install requests" in the pip input field
    ↓
User presses Enter
    ↓
_submit_pip() called
    ↓
text = "install requests"
    ↓
Console shows: "$ pip install requests" (in green)
    ↓
Not an uninstall → skip confirmation
    ↓
runner.run_pip("install requests")
    ↓
PythonRunner builds the command:
    interpreter = "C:/Python311/python.exe"
    args = ["-m", "pip", "install", "requests"]
    ↓
QProcess.start("C:/Python311/python.exe", ["-m", "pip", "install", "requests"])
    ↓
Subprocess runs: python -m pip install requests
    ↓
pip outputs: "Collecting requests..."
    ↓
QProcess.readyReadStandardOutput fires
    ↓
PythonRunner._read_stdout() reads the data
    ↓
output_ready signal emits "Collecting requests..."
    ↓
ConsoleWidget._append_ansi() shows it in gray
    ↓
pip finishes: "Successfully installed requests-2.31.0"
    ↓
QProcess.finished fires → process_finished signal → "[Process finished]" shown
```

---

## Quick Reference: All New Menu Actions

| Menu | Action | Shortcut | Method |
|---|---|---|---|
| Edit | Go to Definition | F12 | `self._trigger_goto_definition()` |
| View | Refresh Git Status | Ctrl+Shift+G | `self.file_manager.check_git_status()` |
| View | Settings | Ctrl+, | `self.open_settings()` |
| File | Export to PDF | Ctrl+E | `self.export_pdf()` |
| (global) | Command Palette | Ctrl+Shift+L | `self._show_command_palette()` |

## Quick Reference: New Files

| File | Purpose |
|---|---|
| `definition_finder.py` | Background QThread for Jedi definition lookup |
| `code_outline.py` | QTreeWidget showing class/function structure |
| `settings_dialog.py` | QDialog with tabs for editor settings |
| `git_integration.py` | Background QThread for git status checking |
| `command_palette.py` | Popup command palette widget |

## Quick Reference: New Methods in PythonRunner

| Method | Purpose |
|---|---|
| `run_file_with_args(path, args, cwd)` | Run a script with command-line arguments |
| `run_pip(args)` | Run a pip command using the selected interpreter |

## Quick Reference: New Methods in ConsoleWidget

| Method | Purpose |
|---|---|
| `_append_ansi(text, default_color)` | Parse ANSI escape codes and append colored text |
| `_submit_pip()` | Handle pip commands from the pip input field |

## Quick Reference: New Methods in MainWindow

| Method | Purpose |
|---|---|
| `_trigger_goto_definition()` | Trigger Jedi definition lookup at cursor |
| `_open_file_at_position(path, line, col)` | Open a file and jump to a position |
| `_goto_symbol(line, col)` | Jump to a symbol from the outline tree |
| `_update_outline()` | Update the code outline panel |
| `_load_settings()` | Load settings from settings.json |
| `_save_settings(settings)` | Save settings to settings.json |
| `open_settings()` | Open the settings dialog |
| `_apply_settings(settings)` | Apply settings to all open editors |
| `_restore_tabs()` | Reopen tabs from last session |
| `export_pdf()` | Export Markdown preview to PDF |
| `_build_command_list()` | Define commands for the palette |
| `_execute_command(name)` | Execute a command by name |
| `_show_command_palette()` | Show the command palette popup |

## Quick Reference: New Signals

| Signal | Emitted By | Purpose |
|---|---|---|
| `goto_definition_requested(str, int, int)` | PythonEditor | Request opening a file at a definition |
| `definition_found(str, int, int)` | DefinitionFinder | Definition location found |
| `definition_not_found()` | DefinitionFinder | No definition found |
| `symbol_clicked(int, int)` | CodeOutlineTree | User clicked a symbol in the outline |
| `command_selected(str)` | CommandPalette | User selected a command |
| `status_ready(dict)` | GitStatusChecker | Git status check complete |
