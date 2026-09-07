# Tier 3 Features — Implementation Guide (Updated Sections)

Updated for Code Editor **v1.9.0**. These sections replace Features 16–20 of the original guide.

What changed since the old guide was written:

| Old guide assumed | Current code (v1.9.0) |
|---|---|
| `self.tab_view` was a `QTabWidget` | `self.tab_view` is a `MultiTabView` (QSplitter of tab groups, split view support) |
| `self.tab_view.currentWidget()` / `.count()` / `.widget(i)` | `self.current_editor()`, `tab_view.all_editors()`, `tab_view.group_for_editor(editor)` |
| Settings: new `settings.json` | `QSettings("CodeEditor", "CodeEditor")` already exists (`self.settings`); `settings.json` already stores the interpreter; `ruff_save_mode` already read from QSettings |
| Preview: `setHtml()` per render | Preview renders once into a `#content` div, updates via `runJavaScript()` (`render_preview()`, `_preview_ready`) |
| Editors: single editor class | `MarkdownEditor` and `PythonEditor` with per-tab mode switching (`_convert_current_tab`), Ruff, Jedi, terminal docks |

---

## Feature 16: Settings Dialog

### Concept

A tabbed dialog (Editor / Python / Appearance) that collects the settings your app actually uses today: editor font and tab width, the Python interpreter (already persisted in `settings.json`), the existing `ruff_save_mode` QSettings key, and the syntax theme (`themes/theme.json`).

### Key Methods

| Method | Description | Docs |
|---|---|---|
| `QTabWidget` | Dialog pages, one visible at a time | [Link](https://doc.qt.io/qt-5/qtabwidget.html) |
| `QFontComboBox` | Font family picker | [Link](https://doc.qt.io/qt-5/qfontcombobox.html) |
| `QSettings.value(key, default)` | Read a persisted setting (already used for `ruff_save_mode`) | [Link](https://doc.qt.io/qt-5/qsettings.html#value) |
| `QsciScintilla.setWrapMode()` | Word-wrap on/off | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |
| `QsciLexerCustom.setFont()` | Re-apply the font to a lexer after it changes | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciLexer.html) |

### Step 1: Create `settings_dialog.py`

```python
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QSpinBox, QCheckBox, QPushButton,
    QFontComboBox, QComboBox, QFileDialog, QFormLayout,
)


class SettingsDialog(QDialog):
    """Tabbed settings dialog. Reads/writes the same keys the app
    already uses:
      - settings.json  -> "interpreter"
      - QSettings      -> "ruff_save_mode"
      - new keys       -> font_family, font_size, tab_width, word_wrap,
                          theme, line_numbers, highlight_line
    """

    def __init__(self, current_settings: dict, parent=None):
        super().__init__(parent)
        self.settings = current_settings.copy()
        self.setWindowTitle("Settings")
        self.setMinimumSize(520, 420)

        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._create_editor_tab(), "Editor")
        self.tabs.addTab(self._create_python_tab(), "Python")
        self.tabs.addTab(self._create_appearance_tab(), "Appearance")
        layout.addWidget(self.tabs)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(save_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    # --- Editor tab -------------------------------------------------- #
    def _create_editor_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)

        self.font_combo = QFontComboBox()
        self.font_combo.setCurrentFont(
            QFont(self.settings.get("font_family", "sans-serif"))
        )
        layout.addRow("Font family:", self.font_combo)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 32)
        self.font_size_spin.setValue(self.settings.get("font_size", 13))
        layout.addRow("Font size:", self.font_size_spin)

        self.tab_width_spin = QSpinBox()
        self.tab_width_spin.setRange(1, 8)
        # MarkdownEditor defaults to 2, PythonEditor to 4 — use 4 as the
        # shared setting and let Markdown keep 2 if you prefer per-mode.
        self.tab_width_spin.setValue(self.settings.get("tab_width", 4))
        layout.addRow("Tab width:", self.tab_width_spin)

        self.word_wrap_cb = QCheckBox("Enable word wrap")
        self.word_wrap_cb.setChecked(self.settings.get("word_wrap", False))
        layout.addRow("", self.word_wrap_cb)

        return tab

    # --- Python tab -------------------------------------------------- #
    def _create_python_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)

        interp_row = QHBoxLayout()
        self.interpreter_input = QLineEdit()
        # Pre-fill with the interpreter the runner is currently using,
        # falling back to the settings.json value.
        self.interpreter_input.setText(self.settings.get("interpreter", ""))
        interp_row.addWidget(self.interpreter_input)

        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_interpreter)
        interp_row.addWidget(browse_btn)
        layout.addRow("Python interpreter:", interp_row)

        # Maps onto the existing QSettings "ruff_save_mode" key that
        # _run_ruff_before_save() already checks ("safe_format" or "off").
        self.ruff_combo = QComboBox()
        self.ruff_combo.addItem("Off (never run Ruff on save)", "off")
        self.ruff_combo.addItem("Safe fixes + format on save", "safe_format")
        current_mode = self.settings.get("ruff_save_mode", "safe_format")
        index = self.ruff_combo.findData(current_mode)
        if index >= 0:
            self.ruff_combo.setCurrentIndex(index)
        layout.addRow("Ruff on save:", self.ruff_combo)

        return tab

    # --- Appearance tab ---------------------------------------------- #
    def _create_appearance_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)

        self.theme_combo = QComboBox()
        themes_dir = Path(__file__).parent / "themes"
        if themes_dir.is_dir():
            for theme_file in sorted(themes_dir.glob("*.json")):
                # userData stores the file name; MarkdownCustomLexer and
                # PyCustomLexer both load themes/theme.json by default, so
                # a theme switch recreates the lexers (see Step 3).
                self.theme_combo.addItem(theme_file.stem, theme_file.name)
        current_theme = self.settings.get("theme", "theme.json")
        index = self.theme_combo.findData(current_theme)
        if index >= 0:
            self.theme_combo.setCurrentIndex(index)
        layout.addRow("Theme:", self.theme_combo)

        self.line_numbers_cb = QCheckBox("Show line numbers")
        self.line_numbers_cb.setChecked(self.settings.get("line_numbers", True))
        layout.addRow("", self.line_numbers_cb)

        self.highlight_line_cb = QCheckBox("Highlight current line")
        self.highlight_line_cb.setChecked(self.settings.get("highlight_line", True))
        layout.addRow("", self.highlight_line_cb)

        return tab

    def _browse_interpreter(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Python Interpreter", "",
            "Python Executable (python.exe);;All Files (*)",
        )
        if path:
            self.interpreter_input.setText(path)

    def get_settings(self) -> dict:
        return {
            "font_family": self.font_combo.currentFont().family(),
            "font_size": self.font_size_spin.value(),
            "tab_width": self.tab_width_spin.value(),
            "word_wrap": self.word_wrap_cb.isChecked(),
            "interpreter": self.interpreter_input.text().strip(),
            "ruff_save_mode": self.ruff_combo.currentData(),
            "theme": self.theme_combo.currentData(),
            "line_numbers": self.line_numbers_cb.isChecked(),
            "highlight_line": self.highlight_line_cb.isChecked(),
        }
```

### Step 2: Settings management in MainWindow

You already have `self.settings = QSettings("CodeEditor", "CodeEditor")` and a `settings.json` for the interpreter. Consolidate the load/save so the dialog sees one dictionary:

```python
    def _load_settings(self) -> dict:
        """Merge settings.json (interpreter) and QSettings into one dict."""
        import json

        settings_path = Path(__file__).parent / "settings.json"
        data = {}
        if settings_path.exists():
            try:
                data = json.loads(settings_path.read_text())
            except json.JSONDecodeError:
                pass

        return {
            "interpreter": data.get("interpreter", self.python_runner.interpreter),
            "ruff_save_mode": self.settings.value("ruff_save_mode", "safe_format", type=str),
            "font_family": self.settings.value("font_family", "sans-serif", type=str),
            "font_size": self.settings.value("font_size", 13, type=int),
            "tab_width": self.settings.value("tab_width", 4, type=int),
            "word_wrap": self.settings.value("word_wrap", False, type=bool),
            "theme": self.settings.value("theme", "theme.json", type=str),
            "line_numbers": self.settings.value("line_numbers", True, type=bool),
            "highlight_line": self.settings.value("highlight_line", True, type=bool),
        }

    def _save_settings(self, new_settings: dict):
        """Persist settings back to settings.json (interpreter) + QSettings."""
        import json

        settings_path = Path(__file__).parent / "settings.json"
        data = {}
        if settings_path.exists():
            try:
                data = json.loads(settings_path.read_text())
            except json.JSONDecodeError:
                pass
        data["interpreter"] = new_settings["interpreter"]
        settings_path.write_text(json.dumps(data, indent=2))

        for key in ("ruff_save_mode", "font_family", "font_size", "tab_width",
                    "word_wrap", "theme", "line_numbers", "highlight_line"):
            self.settings.setValue(key, new_settings[key])

    def open_settings(self):
        from settings_dialog import SettingsDialog

        dialog = SettingsDialog(self._load_settings(), self)
        if dialog.exec_() == QDialog.Accepted:
            new_settings = dialog.get_settings()
            self._save_settings(new_settings)
            self._apply_settings(new_settings)
            self.statusBar().showMessage("Settings saved", 2000)
```

Note: `ruff_save_mode` is already read in `__init__` (`self.ruff_save_mode = self.settings.value(...)`), so after saving, also update `self.ruff_save_mode = new_settings["ruff_save_mode"]` inside `open_settings`.

### Step 3: Apply settings to all open editors

`MultiTabView.all_editors()` gives you every editor across all split groups — no index math needed:

```python
    def _apply_settings(self, settings: dict):


    from PyQt5.Qsci import QsciScintilla
from markdown_editor.markdowneditor import MarkdownEditor
from python_editor.pythoneditor import PythonEditor
from markdown_editor.markdowncustomlexer import MarkdownCustomLexer
from python_editor.custompythonlexer import PyCustomLexer

font = QFont(settings["font_family"])
font.setPointSize(settings["font_size"])
wrap = (QsciScintilla.WrapWord if settings["word_wrap"]
        else QsciScintilla.WrapNone)

for editor in self.tab_view.all_editors():
    editor.window_font = QFont(font)  # both editor classes use this attr
    editor.setFont(font)
    editor.setMarginsFont(font)
    editor.setTabWidth(settings["tab_width"])
    editor.setWrapMode(wrap)
    editor.setCaretLineVisible(settings["highlight_line"])

    if settings["line_numbers"]:
        editor.setMarginWidth(0, "0000")
    else:
        editor.setMarginWidth(0, 0)

    # Recreate the lexer so a theme switch takes effect immediately.
    # Both lexers read themes/theme.json in __init__.
    if isinstance(editor, MarkdownEditor):
        editor.md_lexer = MarkdownCustomLexer(editor)
        editor.md_lexer.setFont(font)
        editor.setLexer(editor.md_lexer)
    elif isinstance(editor, PythonEditor):
        editor.py_lexer = PyCustomLexer(editor)
        editor.py_lexer.setDefaultFont(font)
        editor.setLexer(editor.py_lexer)

if settings["interpreter"]:
    self.python_runner.set_interpreter(settings["interpreter"])
    self.statusBar().showMessage(
        f"Interpreter: {self.python_runner.interpreter}", 3000)
```

### Step 4: Menu action (in `set_up_menu`)

```python
        settings_action = view_menu.addAction("Settings")
        settings_action.setShortcut("Ctrl+,")
        settings_action.setShortcutContext(Qt.ApplicationShortcut)
        settings_action.triggered.connect(self.open_settings)
```

### How the Settings Dialog Works

```
Ctrl+,  →  open_settings()
    ↓
SettingsDialog created from _load_settings()
(settings.json + QSettings merged into one dict)
    ↓
User picks Consolas 14, tab width 2, Ruff "off"
    ↓
Save → dialog.exec_() returns Accepted
    ↓
_save_settings(): interpreter → settings.json, rest → QSettings
    ↓
_apply_settings(): every editor from tab_view.all_editors()
gets font, tab width, wrap, margins, and a fresh lexer
```

---

## Feature 17: Remember Open Tabs on Restart

### Concept

On close, save which files were open, which editor mode each tab used (Python vs Markdown — they are different classes now), and which split group each tab lived in. On startup, reopen everything.

### Key Methods

| Method | Description | Docs |
|---|---|---|
| `QSettings.setValue(key, value)` | Persist a value | [Link](https://doc.qt.io/qt-5/qsettings.html#setValue) |
| `MultiTabView.all_editors()` | Every editor across all split groups | your `multi_tab_view.py` |
| `MultiTabView.group_for_editor(editor)` | The QTabWidget group holding an editor | your `multi_tab_view.py` |
| `MainWindow.closeEvent(event)` | Already exists — save the session first | [Link](https://doc.qt.io/qt-5/qwidget.html#closeEvent) |

### Step 1: Save the session in `closeEvent`

Your current `closeEvent` already iterates `self.tab_view.all_editors()` and prompts for unsaved files. Insert the session save **before** that loop, while all paths are still available:

```python
    def closeEvent(self, event):
        # Save the session FIRST — close_editor() below may remove
        # editors (and their paths) one by one.
        self._save_session()

        for editor in list(self.tab_view.all_editors()):
            if not self.close_editor(editor):
                event.ignore()
                return
        if hasattr(self, "terminal"):
            self.terminal.stop()
        if hasattr(self, "python_runner") and self.python_runner:
            self.python_runner.stop()
        if hasattr(self, "settings"):
            self.settings.setValue("recent_files", self.recent_files)

        event.accept()

    def _save_session(self):
        """Store open tabs, their modes, split groups and the active tab."""
        import json

        if not self.settings.value("restore_tabs", True, type=bool):
            return

        groups = list(self.tab_view.groups())
        tabs = []
        for editor in self.tab_view.all_editors():
            path = getattr(editor, "path", None)
            if path is None:          # untitled editors are not restorable
                continue
            group = self.tab_view.group_for_editor(editor)
            tabs.append({
                "path": str(path),
                # Editor class encodes the mode — remember it per tab
                "python": isinstance(editor, PythonEditor),
                "group": groups.index(group) if group in groups else 0,
            })

        active = self.current_editor()
        session = {
            "tabs": tabs,
            "active": str(getattr(active, "path", None))
                      if getattr(active, "path", None) is not None else None,
            "python_mode": self.python_editor_active,
        }
        # QSettings can't store nested dicts reliably — store JSON text
        self.settings.setValue("session", json.dumps(session))
```

### Step 2: Restore on startup

In `__init__`, after `self.init_ui()`:

```python
        self.init_ui()
        self._restore_session()
```

```python
    def _restore_session(self):
        """Reopen the tabs (and split layout) from the last session."""
        import json

        if not self.settings.value("restore_tabs", True, type=bool):
            return

        raw = self.settings.value("session", "", type=str)
        if not raw:
            return

        try:
            session = json.loads(raw)
        except json.JSONDecodeError:
            return

        tabs = session.get("tabs", [])
        if not tabs:
            return

        # Create enough tab groups to restore the split layout.
        # _create_group() is technically private; optionally rename it to
        # create_group() in multi_tab_view.py and update this call.
        max_group = max(entry["group"] for entry in tabs)
        while len(self.tab_view.groups()) <= max_group:
            self.tab_view._create_group()
        groups = list(self.tab_view.groups())

        active_editor = None
        for entry in tabs:
            path = Path(entry["path"])
            if not path.is_file():
                continue

            # set_new_tab() chooses the editor class via
            # self.python_editor_active — set it per tab so each file
            # reopens in the mode it was last edited with.
            self.python_editor_active = entry["python"]
            editor = self.set_new_tab(path, target_group=groups[entry["group"]])
            if editor is not None and session.get("active") == str(path):
                active_editor = editor

        # Restore the global mode for NEW files opened afterwards
        self.python_editor_active = session.get("python_mode", False)

        if active_editor is not None:
            self.tab_view.focus_editor(active_editor)

        self.statusBar().showMessage(
            f"Restored {len(tabs)} tab(s) from last session", 3000)
```

`set_new_tab()` already handles everything else: binary-file rejection, duplicate detection via `find_editor_by_path`, recent-files bookkeeping, and dirty-state wiring through `_connect_editor()`.

### Step 3: Settings toggle

Add a checkbox to the Settings dialog's Editor tab (Feature 16):

```python
        self.restore_tabs_cb = QCheckBox("Restore open tabs on startup")
        self.restore_tabs_cb.setChecked(self.settings.get("restore_tabs", True))
        layout.addRow("", self.restore_tabs_cb)
```

…and return `"restore_tabs": self.restore_tabs_cb.isChecked()` from `get_settings()`. In `_load_settings`/`_save_settings`, treat it like the other QSettings keys.

### How It Works

```
App closing
    ↓
closeEvent → _save_session()
    ↓
Walk tab_view.all_editors():
  {path: "C:/proj/main.py", python: true,  group: 0}
  {path: "C:/proj/notes.md", python: false, group: 1}
    ↓
QSettings["session"] = json.dumps(...)   (one string)

App restarting
    ↓
__init__ → init_ui() → _restore_session()
    ↓
Create split groups until len(groups) > max saved group index
    ↓
For each entry: set python_editor_active → set_new_tab(path, target_group)
    ↓
focus_editor(active) restores the focused tab
```

---

## Feature 18: Export to PDF

### Concept

Export the Markdown preview to PDF. The preview pipeline changed: `render_preview()` injects HTML into the `#content` div via `runJavaScript()`, which is asynchronous — the PDF must be printed **after** the JavaScript has run, not immediately.

### Key Methods

| Method | Description | Docs |
|---|---|---|
| `QWebEnginePage.printToPdf(path, layout)` | Print the page to a PDF (async) | [Link](https://doc.qt.io/qt-5/qwebenginepage.html#printToPdf) |
| `QWebEnginePage.pdfGenerated(bool)` | Emitted when printing finishes | [Link](https://doc.qt.io/qt-5/qwebenginepage.html#pdfGenerated) |
| `QPageLayout` / `QPageSize` | Page size and margins for the PDF | [Link](https://doc.qt.io/qt-5/qpagelayout.html) |
| `QWebEnginePage.runJavaScript(js, callback)` | Run JS with a completion callback | [Link](https://doc.qt.io/qt-5/qwebenginepage.html#runJavaScript) |

### Step 1: Connect the `pdfGenerated` signal

In `init_ui()`, after `self.preview.loadFinished.connect(...)`:

```python
        self.preview.page().pdfGenerated.connect(self._on_pdf_generated)
```

```python
    def _on_pdf_generated(self, ok: bool):
        if ok:
            self.statusBar().showMessage("PDF exported", 3000)
        else:
            QMessageBox.warning(self, "Export to PDF", "PDF export failed.")
```

### Step 2: The export method

```python
    def export_pdf(self):
        """Export the Markdown preview to a PDF file."""
        editor = self.current_editor()
        if not isinstance(editor, MarkdownEditor):
            self.statusBar().showMessage("PDF export requires a Markdown file", 3000)
            return

        path = getattr(editor, "path", None)
        suggested = path.stem + ".pdf" if path else "document.pdf"

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export to PDF", suggested, "PDF Files (*.pdf)"
        )
        if not file_path:
            return
        if not file_path.lower().endswith(".pdf"):
            file_path += ".pdf"

        if not self._preview_ready:
            self.statusBar().showMessage("Preview not ready yet", 3000)
            return

        # Re-render the preview from the CURRENT text, then print in the
        # JS callback — runJavaScript is asynchronous, and printToPdf()
        # would otherwise capture the stale page.
        self.md.reset()
        body = self.md.convert(editor.text())
        payload = json.dumps(body)
        js = f'document.getElementById("content").innerHTML = {payload};'

        from PyQt5.QtGui import QPageLayout, QPageSize, QMarginsF

        page_layout = QPageLayout(
            QPageSize(QPageSize.A4),
            QPageLayout.Portrait,
            QMarginsF(15, 15, 15, 15),   # millimetres
            QPageLayout.Millimeter,
        )

        def _print():
            self.preview.page().printToPdf(file_path, page_layout)

        self.preview.page().runJavaScript(js, _print)
```

### Step 3: Menu action (in `set_up_menu`, File menu)

```python
        export_pdf_action = file_menu.addAction("Export to PDF")
        export_pdf_action.setShortcut("Ctrl+E")
        export_pdf_action.setShortcutContext(Qt.ApplicationShortcut)
        export_pdf_action.triggered.connect(self.export_pdf)
```

### How It Works

```
Ctrl+E in a Markdown tab
    ↓
export_pdf(): current_editor() is a MarkdownEditor
    ↓
Save dialog suggests "<filename>.pdf"
    ↓
md.convert(current text) → HTML payload
    ↓
runJavaScript(payload, callback=_print)   ← waits for the DOM update
    ↓
callback: printToPdf(file_path, A4 layout)
    ↓
QWebEngine renders off-screen and writes the PDF
    ↓
pdfGenerated(True) → status bar: "PDF exported"
```

---

## Feature 19: Git Integration

### Concept

Color-code files in the `FileManager` tree by Git status (modified / staged / untracked / deleted). The current `FileManager` uses a plain `QFileSystemModel` stored in `self.model` — we swap in a Git-aware subclass and refresh status on folder open, on save, and periodically.

### Key Methods

| Method | Description | Docs |
|---|---|---|
| `subprocess.run(["git", ...], cwd=...)` | Run git in a subprocess | [Link](https://docs.python.org/3/library/subprocess.html#subprocess.run) |
| `git status --porcelain` | Machine-readable status codes | [Link](https://git-scm.com/docs/git-status#_porcelain_format) |
| `QFileSystemModel.data(index, role)` | Override to inject colors | [Link](https://doc.qt.io/qt-5/qfilesystemmodel.html#data) |
| `QThread` | Keep git off the GUI thread (same pattern as `AutoCompleter`, `DefinitionFinder`) | [Link](https://doc.qt.io/qt-5/qthread.html) |

### Step 1: Create `git_integration.py`

Two upgrades over the old version: it resolves the **repo root** first (so it works when you open a subfolder of a repo), and it parses **rename** entries (`R  old -> new`).

```python
import subprocess
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal


class GitStatusChecker(QThread):
    """Background thread running `git status --porcelain`.

    Porcelain format: "XY filename" where X = staged, Y = working tree.
      " M" modified    "M " staged-modified   "A " staged-new
      "??" untracked   "D " / " D" deleted     "R " renamed (staged)
    Docs: https://git-scm.com/docs/git-status#_porcelain_format
    """

    status_ready = pyqtSignal(dict)   # {absolute_path: "XY"}

    def __init__(self):
        super().__init__(None)
        self.repo_path = ""
        self._shutting_down = False

    def check(self, repo_path: str):
        if self.isRunning() or not repo_path:
            return
        self.repo_path = repo_path
        self.start()

    def shutdown(self):
        self._shutting_down = True
        self.requestInterruption()
        if self.isRunning():
            self.wait(2000)

    def _git(self, *args):
        """Run a git command in repo_path. Returns stdout or None."""
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        if result.returncode != 0:
            return None
        return result.stdout

    def run(self):
        if self._shutting_down:
            return
        try:
            # Not a repo (or git missing) → empty status
            toplevel = self._git("rev-parse", "--show-toplevel")
            if toplevel is None:
                self.status_ready.emit({})
                return
            repo_root = Path(toplevel.strip())

            output = self._git("status", "--porcelain")
            if output is None:
                self.status_ready.emit({})
                return

            statuses = {}
            for line in output.splitlines():
                if len(line) < 4:
                    continue
                code = line[:2]
                filepath = line[3:].strip('"')
                if " -> " in filepath:            # rename: keep the new path
                    filepath = filepath.split(" -> ", 1)[1]
                # Porcelain paths are relative to the REPO root, which may
                # be a parent of the folder opened in the FileManager.
                statuses[str((repo_root / filepath).resolve())] = code

            self.status_ready.emit(statuses)

        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            self.status_ready.emit({})

        except Exception:
            self.status_ready.emit({})
```

### Step 2: Git-aware file model

In `file_manager.py`, add the subclass and swap the model. `FileManager.__init__` currently creates `self.model = QFileSystemModel()` — replace that one line:

```python
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QFileSystemModel


class GitAwareFileSystemModel(QFileSystemModel):
    """QFileSystemModel that colors filenames by git status."""

    # Colors tuned to the editor's CustomDark palette
    GIT_COLORS = {
        " M": "#e5c07b",   # modified (not staged) — yellow
        "M ": "#98c379",   # modified (staged)     — green
        "A ": "#98c379",   # added (staged)        — green
        "R ": "#98c379",   # renamed (staged)      — green
        "??": "#56b6c2",   # untracked             — cyan
        "D ": "#e06c75",   # deleted (staged)      — red
        " D": "#e06c75",   # deleted (not staged)  — red
    }

    def __init__(self, file_manager):
        super().__init__()
        self.file_manager = file_manager

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.ForegroundRole:
            color = self.file_manager.get_git_color(self.filePath(index))
            if color:
                return QColor(color)
        return super().data(index, role)
```

In `FileManager.__init__`:

```python
        # BEFORE:
        # self.model: QFileSystemModel = QFileSystemModel()

        # AFTER:
        self.model = GitAwareFileSystemModel(self)
```

### Step 3: Wire the checker into FileManager

Add to `FileManager.__init__` (after `self.model` is set):

```python
        from git_integration import GitStatusChecker

        self.git_checker = GitStatusChecker()
        self.git_checker.status_ready.connect(self._on_git_status)
        self.git_statuses = {}

    def check_git_status(self):
        """Start a git status check for the current root path."""
        self.git_checker.check(self.model.rootPath())

    def _on_git_status(self, statuses: dict):
        self.git_statuses = statuses
        self.viewport().update()   # repaint with new colors

    def get_git_color(self, filepath: str):
        from PyQt5.QtCore import Qt  # (already imported at module level)

        status = self.git_statuses.get(filepath)
        if status:
            return self.GIT_COLORS.get(status) if hasattr(self, "GIT_COLORS") \
                else GitAwareFileSystemModel.GIT_COLORS.get(status)
        return None
```

(Cleaner: move the color dict lookup entirely into the model and let `get_git_color` just return `self.git_statuses.get(filepath)` — shown this way to keep the model dumb.)

### Step 4: Trigger refreshes in MainWindow

Four trigger points that fit the current code:

```python
    # 1. When a folder is opened — end of open_folder():
    def open_folder(self):
        # ... existing dialog + setRootPath code ...
        self.file_manager.model.setRootPath(new_folder)
        self.file_manager.setRootIndex(self.file_manager.model.index(new_folder))
        self.statusBar().showMessage(f"Opened {new_folder}", 2000)
        self.file_manager.check_git_status()          # ← ADD

    # 2. After a successful save — end of mark_editor_clean():
    def mark_editor_clean(self, editor):
        # ... existing code ...
        if getattr(editor, "path", None) is not None:
            self.file_manager.check_git_status()      # ← ADD

    # 3. Periodic auto-refresh — in init_ui(), after set_up_body():
        self._git_refresh_timer = QTimer(self)
        self._git_refresh_timer.setInterval(15_000)   # every 15 s
        self._git_refresh_timer.timeout.connect(
            self.file_manager.check_git_status)
        self._git_refresh_timer.start()

    # 4. Manual refresh — in set_up_menu(), View menu:
        git_refresh_action = view_menu.addAction("Refresh Git Status")
        git_refresh_action.setShortcut("Ctrl+Shift+G")
        git_refresh_action.setShortcutContext(Qt.ApplicationShortcut)
        git_refresh_action.triggered.connect(
            self.file_manager.check_git_status)
```

Also shut the checker down in `closeEvent`, next to `self.terminal.stop()`:

```python
        if hasattr(self.file_manager, "git_checker"):
            self.file_manager.git_checker.shutdown()
```

### How Git Status Colors Work

| Status | Color | Meaning |
|---|---|---|
| ` M` | Yellow (#e5c07b) | Modified, not staged |
| `M ` / `A ` / `R ` | Green (#98c379) | Staged: modified / added / renamed |
| `??` | Cyan (#56b6c2) | Untracked |
| `D ` / ` D` | Red (#e06c75) | Deleted (staged / not staged) |

```
Open folder → check_git_status()
    ↓
GitStatusChecker thread: git rev-parse --show-toplevel
    ↓
git status --porcelain → " M main.py" | "?? new_file.py" | "R  a.py -> b.py"
    ↓
status_ready({abs_path: " M", ...})  (paths joined with the REPO root)
    ↓
FileManager.git_statuses updated, viewport repaints
    ↓
GitAwareFileSystemModel.data(index, ForegroundRole) returns
QColor("#e5c07b") for modified files
```

---

## Feature 20: Command Palette

### Concept

Ctrl+Shift+L opens a fuzzy-searchable popup listing every command the editor has today — including the new ones since the old guide (Ruff actions, split view, terminal, mode switching, Settings, PDF export, Git refresh). Arrow keys navigate, Enter executes, Esc closes.

Ctrl+Shift+P is taken (Mode → Python Editor), so the palette keeps **Ctrl+Shift+L**.

### Key Methods

| Method | Description | Docs |
|---|---|---|
| `QLineEdit` + `QListWidget` | Search input + results list | [Link](https://doc.qt.io/qt-5/qlineedit.html) |
| `QObject.eventFilter()` | Intercept Up/Down/Esc from the input field | [Link](https://doc.qt.io/qt-5/qobject.html#eventFilter) |
| `QShortcut` | Global shortcut registration | [Link](https://doc.qt.io/qt-5/qshortcut.html) |

### Step 1: Create `command_palette.py`

Two upgrades over the old version: keyboard navigation (Up/Down/Esc) and fuzzy subsequence matching instead of plain substring search.

```python
from PyQt5.QtCore import Qt, pyqtSignal, QEvent
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
)


class CommandPalette(QWidget):
    """Popup command palette (Ctrl+Shift+L).

    Knows nothing about commands — just filters the list it is given and
    emits command_selected(name). MainWindow maps names to methods.
    """

    command_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self._commands = []
        self._filtered = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Type a command...")
        self.input.installEventFilter(self)   # Up/Down/Esc navigation
        self.input.setStyleSheet("""
            QLineEdit {
                background-color: #1e1f22;
                color: #dcdfe4;
                border: 1px solid #3d424d;
                border-radius: 0px;
                padding: 8px 12px;
                font-size: 14px;
            }
            QLineEdit:focus { border: 1px solid #528bff; }
        """)
        layout.addWidget(self.input)

        self.list = QListWidget()
        self.list.setMinimumWidth(440)
        self.list.setMaximumHeight(320)
        self.list.setStyleSheet("""
            QListWidget {
                background-color: #1e1f22;
                color: #dcdfe4;
                border: none;
                padding: 4px;
            }
            QListWidget::item { padding: 6px 12px; }
            QListWidget::item:selected { background-color: #2b2d30; }
        """)
        layout.addWidget(self.list)

        self.input.textChanged.connect(self._filter_commands)
        self.input.returnPressed.connect(self._execute_selected)
        self.list.itemClicked.connect(self._on_item_clicked)

    # --- public API -------------------------------------------------- #
    def set_commands(self, commands):
        """commands: list of (name, shortcut_or_description) tuples."""
        self._commands = commands

    def show_palette(self):
        self.input.clear()
        self._populate(self._commands)
        self.show()
        self.input.setFocus(Qt.PopupFocusReason)

    # --- fuzzy filtering --------------------------------------------- #
    @staticmethod
    def _fuzzy_score(query: str, text: str):
        """Subsequence match. Lower score = better match. -1 = no match."""
        q, t = query.lower(), text.lower()
        if not q:
            return 0
        score, pos = 0, 0
        for ch in q:
            found = t.find(ch, pos)
            if found == -1:
                return -1
            score += found - pos   # consecutive chars score best
            pos = found + 1
        return score

    def _filter_commands(self, text: str):
        scored = []
        for name, desc in self._commands:
            s = self._fuzzy_score(text, name)
            if s >= 0:
                scored.append((s, name, desc))
        scored.sort(key=lambda item: item[0])
        self._populate([(name, desc) for _, name, desc in scored])

    def _populate(self, commands):
        self._filtered = commands
        self.list.clear()
        for name, desc in commands:
            item = QListWidgetItem(f"{name}    ({desc})" if desc else name)
            item.setData(Qt.UserRole, name)
            self.list.addItem(item)
        if self.list.count() > 0:
            self.list.setCurrentRow(0)

    # --- execution ---------------------------------------------------- #
    def _execute_selected(self):
        item = self.list.currentItem()
        if item is not None:
            self.command_selected.emit(item.data(Qt.UserRole))
        self.hide()

    def _on_item_clicked(self, item):
        self.command_selected.emit(item.data(Qt.UserRole))
        self.hide()

    # --- keyboard navigation ------------------------------------------ #
    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == QEvent.KeyPress:
            key = event.key()
            row = self.list.currentRow()
            if key == Qt.Key_Down and row < self.list.count() - 1:
                self.list.setCurrentRow(row + 1)
                return True
            if key == Qt.Key_Up and row > 0:
                self.list.setCurrentRow(row - 1)
                return True
            if key == Qt.Key_Escape:
                self.hide()
                return True
        return super().eventFilter(obj, event)
```

### Step 2: Register in MainWindow

In `init_ui()`, after `set_up_body()`:

```python
        from command_palette import CommandPalette

        self.command_palette = CommandPalette(self)
        self._build_command_list()
        self.command_palette.command_selected.connect(self._execute_command)
```

### Step 3: The command list (current v1.9.0 command set)

```python
    def _build_command_list(self):
        self._commands = [
            # File
            ("File: New", "Ctrl+N"),
            ("File: Open File", "Ctrl+O"),
            ("File: Open Folder", "Ctrl+K"),
            ("File: Save", "Ctrl+S"),
            ("File: Save As", "Ctrl+Shift+S"),
            ("File: Save All", "Ctrl+Shift+A"),
            ("File: Close All", "Ctrl+Shift+W"),
            ("File: Export to PDF", "Ctrl+E"),
            # Edit
            ("Edit: Undo", "Ctrl+Z"),
            ("Edit: Redo", "Ctrl+Shift+Z"),
            ("Edit: Find", "Ctrl+F"),
            ("Edit: Replace", "Ctrl+H"),
            ("Edit: Go to Line", "Ctrl+G"),
            ("Edit: Toggle Comment", "Ctrl+1"),
            ("Edit: Go to Definition", "F12"),
            ("Edit: Format Document (Ruff)", "Ctrl+Alt+L"),
            ("Edit: Organize Imports (Ruff)", "Ctrl+Alt+O"),
            ("Edit: Apply Safe Ruff Fixes", "Ctrl+Alt+F"),
            # Mode
            ("Mode: Python Editor", "Ctrl+Shift+P"),
            ("Mode: Markdown Editor", "Ctrl+Shift+M"),
            # Run
            ("Run: Run File", "F5"),
            ("Run: Run with Arguments", "Shift+F5"),
            ("Run: Run Selection", "Ctrl+Return"),
            ("Run: Stop", "F6"),
            ("Run: Choose Interpreter", ""),
            # View
            ("View: Toggle Sidebar", "Ctrl+B"),
            ("View: Toggle Preview", "Ctrl+J"),
            ("View: Toggle Console", "Ctrl+Shift+-"),
            ("View: Toggle Terminal", "Ctrl+Shift+T"),
            ("View: Split Right", "Ctrl+Alt+Right"),
            ("View: Unsplit", "Ctrl+Alt+Left"),
            ("View: Fullscreen", "F11"),
            ("View: Settings", "Ctrl+,"),
            ("View: Refresh Git Status", "Ctrl+Shift+G"),
        ]
        self.command_palette.set_commands(self._commands)
```

### Step 4: Execute commands

```python
    def _execute_command(self, command_name: str):
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
            "Edit: Go to Definition": self._trigger_goto_definition,
            "Edit: Format Document (Ruff)": self.format_current_document,
            "Edit: Organize Imports (Ruff)": self.organize_current_imports,
            "Edit: Apply Safe Ruff Fixes": self.apply_safe_ruff_fixes,
            "Mode: Python Editor": self.change_editor_python,
            "Mode: Markdown Editor": self.change_editor_markdown,
            "Run: Run File": self.run_current_file,
            "Run: Run with Arguments": self.run_with_arguments,
            "Run: Run Selection": self.run_selection,
            "Run: Stop": self.python_runner.stop,
            "Run: Choose Interpreter": self.choose_interpreter,
            "View: Toggle Sidebar": self.toggle_sidebar,
            "View: Toggle Preview": self.toggle_preview,
            "View: Toggle Console": self.toggle_console,
            "View: Toggle Terminal": self._toggle_terminal,
            "View: Split Right": self.split_current_editor_right,
            "View: Unsplit": self.unsplit_active_group,
            "View: Fullscreen": self._show_full_screen,
            "View: Settings": self.open_settings,
            "View: Refresh Git Status": self.file_manager.check_git_status,
        }
        method = command_map.get(command_name)
        if method:
            method()
```

**Important fix required:** `_close_all_tabs()` still contains the old pre-MultiTabView code (`self.tab_view.count()` / `self.tab_view.widget(i)` — `MultiTabView` has neither). Replace its body before wiring it into the palette:

```python
    def _close_all_tabs(self):
        """Close every open tab (MultiTabView version)."""
        for editor in list(self.tab_view.all_editors()):
            if not self.close_editor(editor):
                return
```

(The same applies to `close_tab`, `_close_other_tabs`, and `_show_tab_context_menu`, which also use the old `QTabWidget` API — they need the same rewrite if you use them.)

### Step 5: The shortcut (in `set_up_menu`)

```python
        palette_shortcut = QShortcut(QKeySequence("Ctrl+Shift+L"), self)
        palette_shortcut.activated.connect(self._show_command_palette)
```

```python
    def _show_command_palette(self):
        """Show the palette at the top-center of the window."""
        rect = self.geometry()
        x = rect.center().x() - 220   # 220 = half the palette width
        y = rect.top() + 60
        self.command_palette.move(x, y)
        self.command_palette.show_palette()
```

### How the Command Palette Works

```
Ctrl+Shift+L
    ↓
_show_command_palette() positions + shows the popup
    ↓
Palette lists all 33 commands (name + shortcut)
    ↓
User types "ruff"
    ↓
_fuzzy_score filters + ranks:
    "Edit: Format Document (Ruff)"     ← best (consecutive match)
    "Edit: Organize Imports (Ruff)"
    "Edit: Apply Safe Ruff Fixes"
    ↓
Up/Down to navigate (via the event filter on the input field)
    ↓
Enter → command_selected("Edit: Format Document (Ruff)")
    ↓
_execute_command() → self.format_current_document()
    ↓
Palette hides
```

---

## Quick Reference: New Menu Actions (updated)

| Menu | Action | Shortcut | Method |
|---|---|---|---|
| View | Settings | Ctrl+, | `self.open_settings()` |
| File | Export to PDF | Ctrl+E | `self.export_pdf()` |
| View | Refresh Git Status | Ctrl+Shift+G | `self.file_manager.check_git_status()` |
| (global) | Command Palette | Ctrl+Shift+L | `self._show_command_palette()` |

## Quick Reference: New Files (updated)

| File | Purpose |
|---|---|
| `settings_dialog.py` | Tabbed settings dialog (Editor / Python / Appearance) |
| `git_integration.py` | GitStatusChecker QThread (repo-root aware, rename aware) |
| `command_palette.py` | Fuzzy-searchable popup with keyboard navigation |

## Quick Reference: New Methods in MainWindow (updated)

| Method | Purpose |
|---|---|
| `_load_settings()` / `_save_settings()` | Merge settings.json + QSettings |
| `open_settings()` / `_apply_settings()` | Dialog + apply to `tab_view.all_editors()` |
| `_save_session()` / `_restore_session()` | Tab + split-group + mode session persistence |
| `export_pdf()` / `_on_pdf_generated()` | Async PDF export via `runJavaScript` callback |
| `_build_command_list()` / `_execute_command()` | Command palette wiring (33 commands) |
| `_show_command_palette()` | Position + show the palette |

## Quick Reference: New Signals (updated)

| Signal | Emitted By | Purpose |
|---|---|---|
| `command_selected(str)` | CommandPalette | User picked a command |
| `status_ready(dict)` | GitStatusChecker | Git status (repo-root absolute paths) |
| `pdfGenerated(bool)` | QWebEnginePage (built-in) | PDF export finished |
