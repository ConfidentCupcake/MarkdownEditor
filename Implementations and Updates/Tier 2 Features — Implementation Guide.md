# Tier 2 Features — Implementation Guide

This document covers the implementation of 7 features for the Code Editor, with code examples, explanations, and documentation references.

---

## Table of Contents

1. [Go to Line (Ctrl+G)](#feature-7-go-to-line-ctrlg)
2. [Toggle Comment (Ctrl+/)](#feature-8-toggle-comment-ctrl)
3. [Recent Files](#feature-9-recent-files)
4. [Save All / Close All](#feature-10-save-all--close-all)
5. [Word Count in Status Bar (Markdown)](#feature-11-word-count-in-status-bar-markdown)
6. [Run with Arguments](#feature-12-run-with-arguments)
7. [ANSI Color in Console](#feature-13-ansi-color-in-console)

---

## Documentation Reference

| What | Link |
|---|---|
| QScintilla methods | [Riverbank QScintilla](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |
| QInputDialog | [Qt QInputDialog docs](https://doc.qt.io/qt-5/qinputdialog.html) |
| QSettings | [Qt QSettings docs](https://doc.qt.io/qt-5/qsettings.html) |
| QMenu | [Qt QMenu docs](https://doc.qt.io/qt-5/qmenu.html) |
| QProcess | [Qt QProcess docs](https://doc.qt.io/qt-5/qprocess.html) |
| ANSI escape codes | [Wikipedia ANSI escape codes](https://en.wikipedia.org/wiki/ANSI_escape_code) |
| Python re module | [Python re docs](https://docs.python.org/3/library/re.html) |
| Python shlex module | [Python shlex docs](https://docs.python.org/3/library/shlex.html) |
| Python str.split | [Python str.split docs](https://docs.python.org/3/library/stdtypes.html#str.split) |
| Python sys.argv | [Python sys.argv docs](https://docs.python.org/3/library/sys.html#sys.argv) |
| Python os.path.expanduser | [Python os.path docs](https://docs.python.org/3/library/os.path.html#os.path.expanduser) |
| Python webbrowser | [Python webbrowser docs](https://docs.python.org/3/library/webbrowser.html) |
| QLabel | [Qt QLabel docs](https://doc.qt.io/qt-5/qlabel.html#text-prop) |
| QStatusBar | [Qt QStatusBar docs](https://doc.qt.io/qt-5/qstatusbar.html) |
| QAction | [Qt QAction docs](https://doc.qt.io/qt-5/qaction.html) |
| Scintilla raw messages | [Scintilla C++ docs](https://www.scintilla.org/ScintillaDoc.html) |

---

## Feature 7: Go to Line (Ctrl+G)

### Concept

A small dialog asks the user for a line number. When they click OK, the editor jumps to that line and places the cursor there.

### Key Methods

| Method | Description | Docs |
|---|---|---|
| `QInputDialog.getInt(parent, title, label, value, min, max)` | Shows a small dialog with a spin box. Returns `(value, ok)`. | [Link](https://doc.qt.io/qt-5/qinputdialog.html#getInt) |
| `editor.setCursorPosition(line, 0)` | Moves the cursor to the given line and column. | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |
| `editor.ensureLineVisible(line)` | Scrolls the editor so the given line is visible. Without this, the cursor moves but the view doesn't scroll. | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |
| `editor.lines()` | Returns the total number of lines in the document. | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |

### Step 1: Add the Menu Action

In `set_up_menu`, add to the Edit menu after the Find/Replace actions:

```python
        edit_menu.addSeparator()

        goto_action = edit_menu.addAction("Go to Line")
        goto_action.setShortcut("Ctrl+G")
        goto_action.setShortcutContext(Qt.ApplicationShortcut)
        goto_action.triggered.connect(self.goto_line)
```

### Step 2: Write the Method

Add this method to `MainWindow`:

```python
    def goto_line(self):
        """Open a dialog to jump to a specific line number."""
        editor = self.tab_view.currentWidget()
        if editor is None:
            return

        max_lines = editor.lines()
        # editor.lines() returns the total number of lines in the document.
        # We use this as the maximum value for the spin box so the user
        # can't enter a line number that doesn't exist.

        # QInputDialog.getInt shows a small dialog with a spin box.
        # Parameters: parent, title, label, default value, minimum, maximum
        # Returns: (value, ok) where ok is True if the user clicked OK
        # Docs: https://doc.qt.io/qt-5/qinputdialog.html#getInt
        line_num, ok = QInputDialog.getInt(
            self,
            "Go to Line",
            f"Line number (1-{max_lines}):",
            1,              # default value shown in the spin box
            1,              # minimum value
            max_lines,      # maximum value
        )

        if not ok:
            # User clicked Cancel or pressed Escape
            return

        # QScintilla uses 0-based line numbers internally.
        # The user typed a 1-based number, so we subtract 1.
        target_line = line_num - 1

        # setCursorPosition moves the text cursor to (line, column)
        # Parameters are (line, index) where both are 0-based
        editor.setCursorPosition(target_line, 0)

        # ensureLineVisible scrolls the editor so the target line is
        # in the visible area. Without this, the cursor moves but the
        # view stays where it was.
        editor.ensureLineVisible(target_line)

        # Give the editor focus so the user can start typing immediately
        editor.setFocus()
```

### Flow Diagram

```
User presses Ctrl+G
    ↓
goto_line() is called
    ↓
QInputDialog.getInt() shows a dialog with a spin box
    ↓
User types "42" and clicks OK
    ↓
ok = True, line_num = 42
    ↓
target_line = 42 - 1 = 41  (convert to 0-based)
    ↓
editor.setCursorPosition(41, 0)  — cursor moves to line 42
    ↓
editor.ensureLineVisible(41)  — view scrolls to show line 42
    ↓
editor.setFocus()  — editor receives keyboard focus
```

---

## Feature 8: Toggle Comment (Ctrl+/)

### Concept

When the user presses Ctrl+/, the current line (or all selected lines) get a `#` prefix added. Press Ctrl+/ again and the `#` is removed.

### Key Methods

| Method | Description | Docs |
|---|---|---|
| `editor.getCursorPosition()` | Returns `(line, index)` tuple | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |
| `editor.text(line)` | Returns the text of a specific line including newline | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |
| `editor.setSelection(lineFrom, indexFrom, lineTo, indexTo)` | Selects a range of text | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |
| `editor.replace(text)` | Replaces the current selection with new text | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |
| `editor.insertAt(text, line, index)` | Inserts text at a specific position | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |
| `editor.lineLength(line)` | Returns the length of a specific line | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |
| `editor.getSelection()` | Returns `(lineFrom, indexFrom, lineTo, indexTo)` | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |

### Step 1: Add the Menu Action

In `set_up_menu`, add to the Edit menu:

```python
        edit_menu.addSeparator()

        toggle_comment_action = edit_menu.addAction("Toggle Comment")
        toggle_comment_action.setShortcut("Ctrl+/")
        toggle_comment_action.setShortcutContext(Qt.ApplicationShortcut)
        toggle_comment_action.triggered.connect(self.toggle_comment)
```

### Step 2: Write the Main Method

Two cases to handle:
1. **No selection** — comment/uncomment the single line the cursor is on
2. **Selection exists** — comment/uncomment all lines in the selection

```python
    def toggle_comment(self):
        """Toggle # comment on the current line or selected lines."""
        editor = self.tab_view.currentWidget()
        if editor is None:
            return

        # Only toggle comments in Python mode — Markdown uses <!-- -->
        # You can adapt this later for different comment styles
        if not isinstance(editor, PythonEditor):
            return

        if editor.hasSelectedText():
            self._toggle_comment_selection(editor)
        else:
            self._toggle_comment_single_line(editor)
```

### Step 3: Single Line Toggle

```python
    def _toggle_comment_single_line(self, editor):
        """Toggle comment on the line where the cursor currently is."""
        line, index = editor.getCursorPosition()

        # Read the text of the current line
        # editor.text(line) returns the text of line `line` INCLUDING the newline
        line_text = editor.text(line)

        # Check if the line already starts with # (ignoring leading whitespace)
        stripped = line_text.lstrip()
        if stripped.startswith("#"):
            # Uncomment: remove the first # we find
            hash_pos = line_text.index("#")

            # Check if there's a space after # (common formatting: "# code")
            if hash_pos + 1 < len(line_text) and line_text[hash_pos + 1] == " ":
                # Remove "# " (hash + space)
                editor.setSelection(line, hash_pos, line, hash_pos + 2)
            else:
                # Remove just "#"
                editor.setSelection(line, hash_pos, line, hash_pos + 1)
            editor.replace("")
        else:
            # Comment: insert "# " at the beginning of the line
            # insertAt(text, line, index) inserts text at the given position
            editor.insertAt("# ", line, 0)

        # Move cursor back to a sensible position
        editor.setCursorPosition(line, 0)
        editor.ensureLineVisible(line)
```

### Step 4: Multi-Line Selection Toggle

```python
    def _toggle_comment_selection(self, editor):
        """Toggle comments on all lines in the current selection."""
        # Get the selection boundaries
        # getSelection returns (lineFrom, indexFrom, lineTo, indexTo)
        line_from, index_from, line_to, index_to = editor.getSelection()

        # Ensure line_from <= line_to (selection can be made bottom-to-top)
        if line_from > line_to:
            line_from, line_to = line_to, line_from

        # Check if the FIRST selected line is already commented
        # This determines whether we're commenting or uncommenting
        first_line_text = editor.text(line_from)
        is_commented = first_line_text.lstrip().startswith("#")

        # We need to modify lines from bottom to top when INSERTING text
        # because inserting at line N shifts all lines below it.
        # If we go top-to-bottom, line numbers would be wrong after the first insert.
        # When REMOVING text, we also go bottom-to-top to keep line numbers stable.

        if is_commented:
            # Uncomment all selected lines
            for line in range(line_to, line_from - 1, -1):
                line_text = editor.text(line)
                stripped = line_text.lstrip()
                if stripped.startswith("#"):
                    hash_pos = line_text.index("#")
                    if hash_pos + 1 < len(line_text) and line_text[hash_pos + 1] == " ":
                        editor.setSelection(line, hash_pos, line, hash_pos + 2)
                    else:
                        editor.setSelection(line, hash_pos, line, hash_pos + 1)
                    editor.replace("")
        else:
            # Comment all selected lines
            # Go from BOTTOM to TOP so inserting "# " doesn't shift line numbers
            for line in range(line_to, line_from - 1, -1):
                editor.insertAt("# ", line, 0)

        # Restore the selection to cover all modified lines
        editor.setSelection(line_from, 0, line_to, editor.lineLength(line_to))
```

### Why Iterate Bottom-to-Top When Inserting

```
Lines:        Insert "# " at line 0 first:
  0: print()    →  0: # print()     Line 1 is still at index 1
  1: x = 1         1: x = 1         (correct)
  2: y = 2         2: y = 2

Now insert at line 1:  But wait — inserting text at line 0 shifted
everything down by 0 lines (insertAt doesn't add newlines, it inserts
at a position within a line). So line indices are still correct.

Actually, insertAt("# ", line, 0) inserts at the START of the line
without adding a newline. So line numbers DON'T shift. You CAN go
top-to-bottom. But going bottom-to-top is still safer because if
your logic changes later (e.g. adding full-line inserts), it won't
break.
```

### How to Test

1. Open a Python file
2. Put cursor on a line, press Ctrl+/ — line gets `# ` prefix
3. Press Ctrl+/ again — `# ` is removed
4. Select 3 lines, press Ctrl+/ — all 3 get `# ` prefix
5. Press Ctrl+/ again — all 3 are uncommented

---

## Feature 9: Recent Files

### Concept

Store the last 10 opened file paths. Show them in a "File > Open Recent" submenu. Clicking one opens it.

### Key Methods

| Method | Description | Docs |
|---|---|---|
| `QSettings` | Qt's built-in persistent settings storage. Saves to registry on Windows, `.config` on Linux. | [Link](https://doc.qt.io/qt-5/qsettings.html) |
| `QMenu.addMenu("title")` | Creates a submenu inside an existing menu. | [Link](https://doc.qt.io/qt-5/qmenu.html) |
| `menu.addAction(text)` | Adds a clickable item to a menu. | [Link](https://doc.qt.io/qt-5/qmenu.html) |
| `menu.clear()` | Removes all actions from a menu. | [Link](https://doc.qt.io/qt-5/qmenu.html) |
| `QAction.triggered.connect(callback)` | Connects a menu item click to a function. | [Link](https://doc.qt.io/qt-5/qaction.html) |
| `QAction.setData(data)` | Stores arbitrary data on a menu item. | [Link](https://doc.qt.io/qt-5/qaction.html#setData) |
| `QAction.data()` | Retrieves the stored data. | [Link](https://doc.qt.io/qt-5/qaction.html#data) |

### Step 1: Initialize QSettings and the Recent Files List

In `MainWindow.__init__`, add:

```python
        # QSettings stores data persistently across app restarts.
        # Parameters: organization name, application name
        # On Windows: saved to HKEY_CURRENT_USER\Software\CodeEditor\CodeEditor
        # On Linux: saved to ~/.config/CodeEditor/CodeEditor.conf
        # Docs: https://doc.qt.io/qt-5/qsettings.html
        self.settings = QSettings("CodeEditor", "CodeEditor")
        # Load the saved recent files list, default to empty list
        self.recent_files = self.settings.value("recent_files", [], type=list)
        # value() returns the stored value or the default (second arg)
        # type=list tells Qt to interpret the stored data as a Python list
```

Since you already have `from PyQt5.QtCore import *`, `QSettings` is already available.

### Step 2: Create the "Open Recent" Submenu

In `set_up_menu`, after the "Open File" action:

```python
        open_file = file_menu.addAction("Open File")
        open_file.setShortcut("Ctrl+O")
        open_file.setShortcutContext(Qt.ApplicationShortcut)
        open_file.triggered.connect(self.open_file)

        # Open Recent submenu
        # addMenu returns a QMenu object. We store it as an instance
        # variable so we can rebuild it when the recent files list changes.
        self.recent_menu = file_menu.addMenu("Open Recent")
        self._update_recent_menu()

        open_folder = file_menu.addAction("Open Folder")
        # ... rest of menu setup
```

### Step 3: Build the Submenu Dynamically

```python
    def _update_recent_menu(self):
        """Rebuild the 'Open Recent' submenu from the recent_files list."""
        # clear() removes all existing menu items so we can rebuild
        self.recent_menu.clear()

        if not self.recent_files:
            # Show a disabled placeholder when the list is empty
            empty_action = self.recent_menu.addAction("(No recent files)")
            empty_action.setEnabled(False)
            return

        # Add each file path as a menu item
        for path in self.recent_files:
            # Display just the filename, not the full path — cleaner
            # Path(path).name extracts just the filename
            action = self.recent_menu.addAction(Path(path).name)
            # setData stores the full path on the action so we can
            # retrieve it when the user clicks
            action.setData(path)
            # triggered is a signal that fires when the user clicks the item
            # We use a lambda to capture the path and pass it to open_recent_file
            # The default argument path=path captures the CURRENT value of path
            # Without it, all lambdas would use the last value of path
            action.triggered.connect(lambda checked, p=path: self.open_recent_file(p))

    def open_recent_file(self, path: str):
        """Open a file from the recent files list."""
        file_path = Path(path)
        if not file_path.exists():
            # File was deleted since it was added to recent files
            QMessageBox.information(self, "File Not Found",
                                    f"The file '{file_path.name}' no longer exists.")
            self.recent_files.remove(path)
            self._save_recent_files()
            self._update_recent_menu()
            return

        self.set_new_tab(file_path)
```

### Step 4: Add Files to the Recent List When Opening

```python
    def _add_to_recent_files(self, path: str):
        """Add a file path to the recent files list (max 10)."""
        # Convert to string in case it's a Path object
        path = str(path)

        # Remove if already in list (to avoid duplicates)
        if path in self.recent_files:
            self.recent_files.remove(path)

        # Insert at the beginning (most recent first)
        self.recent_files.insert(0, path)

        # Keep only the 10 most recent
        self.recent_files = self.recent_files[:10]

        # Save to QSettings
        self._save_recent_files()

        # Rebuild the submenu
        self._update_recent_menu()

    def _save_recent_files(self):
        """Persist the recent files list to QSettings."""
        self.settings.setValue("recent_files", self.recent_files)
        # setValue stores the value. QSettings serializes Python lists
        # automatically. The data is written to disk — it persists
        # across app restarts.
        # Docs: https://doc.qt.io/qt-5/qsettings.html#setValue
```

### Step 5: Call It When Opening Files

In `set_new_tab`, after the file is successfully loaded:

```python
            self.tab_view.addTab(self.editor, path.name)
            self.current_file = path
            # Add to recent files
            self._add_to_recent_files(str(path))
```

Also in `open_file`, after `self.set_new_tab(f)`:

```python
        f = Path(new_file)
        self.set_new_tab(f)
        self._add_to_recent_files(str(f))
```

### How QSettings Works

```
App opens file "C:\projects\main.py"
    ↓
_add_to_recent_files("C:\projects\main.py")
    ↓
List: ["C:\projects\main.py"]
    ↓
_save_recent_files()
    ↓
settings.setValue("recent_files", ["C:\projects\main.py"])
    ↓
QSettings writes to disk (registry on Windows, .conf on Linux)

App restarts
    ↓
__init__ loads: self.recent_files = settings.value("recent_files", [], type=list)
    ↓
List is restored: ["C:\projects\main.py"]
    ↓
_update_recent_menu() builds the submenu
```

---

## Feature 10: Save All / Close All

### Concept

- **Save All** — Iterates over every open tab and saves each one that has a file path
- **Close All** — Closes every tab (with unsaved changes warning)

### Step 1: Add Menu Actions

In `set_up_menu`, add after the Save As action:

```python
        save_as = file_menu.addAction("Save As")
        save_as.setShortcut("Ctrl+Shift+S")
        save_as.setShortcutContext(Qt.ApplicationShortcut)
        save_as.triggered.connect(self.save_as)

        save_all_action = file_menu.addAction("Save All")
        save_all_action.setShortcut("Ctrl+Shift+A")
        save_all_action.setShortcutContext(Qt.ApplicationShortcut)
        save_all_action.triggered.connect(self.save_all)

        close_all_action = file_menu.addAction("Close All")
        close_all_action.setShortcut("Ctrl+Shift+W")
        close_all_action.setShortcutContext(Qt.ApplicationShortcut)
        close_all_action.triggered.connect(self._close_all_tabs)
```

### Step 2: Write the save_all Method

```python
    def save_all(self):
        """Save all open tabs that have a file path."""
        saved_count = 0

        # Save the currently active tab index so we can restore it
        current_index = self.tab_view.currentIndex()

        for i in range(self.tab_view.count()):
            # Set each tab as the current widget temporarily
            # so save_file() operates on it
            self.tab_view.setCurrentIndex(i)

            editor = self.tab_view.widget(i)
            if editor is None:
                continue

            path = getattr(editor, "path", None)
            if path is not None:
                # This tab has a file path — save it
                # We call the save logic directly instead of self.save_file()
                # to avoid status bar spam from each individual save
                path.write_bytes(editor.text().replace("\r\n", "\n").encode("utf-8"))

                # Remove dirty indicator
                self._dirty_tabs.discard(i)
                title = self.tab_view.tabText(i)
                if title.startswith("● "):
                    self.tab_view.setTabText(i, title[2:])

                saved_count += 1

        # Restore the originally active tab
        self.tab_view.setCurrentIndex(current_index)

        self.statusBar().showMessage(f"Saved {saved_count} file(s)", 3000)
```

### Why Not Just Call self.save_file() in a Loop

`self.save_file()` uses `self.tab_view.currentWidget()` and `self.tab_view.currentIndex()` internally. If we call it in a loop, we need to switch tabs each time so `currentWidget()` returns the right editor. We also avoid calling `save_file()` for tabs without a path (untitled files) — `save_file` would pop up a "Save As" dialog for each one, which is annoying.

Instead, we directly write the file and handle the dirty flag ourselves. This gives us full control and avoids dialog spam.

### Step 3: Close All Already Works

The `_close_all_tabs` method (from the Tier 1 tab context menu) already handles closing all tabs. It calls `close_tab(i)` for each tab, which shows the unsaved changes dialog if needed. The menu action just triggers it.

---

## Feature 11: Word Count in Status Bar (Markdown)

### Concept

When editing a Markdown file, show "Words: 42 | Chars: 208" in the status bar. Update in real time as the user types.

### Key Methods

| Method | Description | Docs |
|---|---|---|
| `editor.text()` | Returns all text in the document as a string | [Link](https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html) |
| `str.split()` | Splits by whitespace and returns a list of words | [Link](https://docs.python.org/3/library/stdtypes.html#str.split) |
| `len(str)` | Returns the character count | [Link](https://docs.python.org/3/library/functions.html#len) |
| `QLabel.setText(str)` | Updates the label's display text | [Link](https://doc.qt.io/qt-5/qlabel.html#text-prop) |
| `self.statusBar().addPermanentWidget(widget)` | Adds a widget that stays visible on the right side | [Link](https://doc.qt.io/qt-5/qstatusbar.html#addPermanentWidget) |

### Step 1: Add a Permanent Word Count Label

In `init_ui`, after the cursor position label:

```python
        self.cursor_pos_label = QLabel("Ln 1, Col 1")
        self.cursor_pos_label.setStyleSheet("color: #888; padding: 0 10px;")
        self.statusBar().addPermanentWidget(self.cursor_pos_label)

        # Word count label — shows "Words: X | Chars: Y"
        self.word_count_label = QLabel("Words: 0 | Chars: 0")
        self.word_count_label.setStyleSheet("color: #888; padding: 0 10px;")
        self.statusBar().addPermanentWidget(self.word_count_label)
```

### Step 2: Write the Update Method

```python
    def update_word_count(self):
        """Update the word/character count in the status bar."""
        editor = self.tab_view.currentWidget()

        # Only show word count for Markdown files
        if editor is None or not isinstance(editor, MarkdownEditor):
            self.word_count_label.setText("")
            return

        text = editor.text()

        # Count words: split the text by whitespace and count the pieces
        # split() with no arguments splits on ANY whitespace (spaces, tabs,
        # newlines) and removes empty strings automatically.
        # "hello world\n" → ["hello", "world"] → 2 words
        # "" → [] → 0 words
        # "   " → [] → 0 words
        # Docs: https://docs.python.org/3/library/stdtypes.html#str.split
        words = len(text.split())

        # Count characters: len(text) includes whitespace and newlines
        chars = len(text)

        self.word_count_label.setText(f"Words: {words} | Chars: {chars}")
```

### Step 3: Call It on Text Changes and Tab Switches

In `set_new_tab`, after connecting `textChanged`:

```python
        self.editor.textChanged.connect(self._on_editor_text_changed)
        self.editor.textChanged.connect(self.update_word_count)  # ← ADD THIS
```

In `new_file`:

```python
        editor.textChanged.connect(self._on_editor_text_changed)
        editor.textChanged.connect(self.update_word_count)  # ← ADD THIS
```

In `on_tab_changed`, at the end of the method:

```python
        editor = self.tab_view.currentWidget()
        if editor is not None:
            line, index = editor.getCursorPosition()
            self.update_cursor_position(line, index)
            self.update_word_count()  # ← ADD THIS
```

In `_convert_current_tab`, after creating `new_editor`:

```python
        new_editor.textChanged.connect(self._on_editor_text_changed)
        new_editor.textChanged.connect(self.update_word_count)  # ← ADD THIS
```

### How It Works

```
User types in a Markdown file
    ↓
editor.textChanged signal fires
    ↓
update_word_count() is called
    ↓
editor.text() returns "Hello world this is markdown"
    ↓
text.split() returns ["Hello", "world", "this", "is", "markdown"]
    ↓
len() = 5 words
    ↓
len(text) = 30 characters
    ↓
word_count_label.setText("Words: 5 | Chars: 30")
    ↓
Label updates in status bar
```

When the user switches to a Python tab, `on_tab_changed` calls `update_word_count()`, which checks `isinstance(editor, MarkdownEditor)` — it's False, so the label is set to empty string, hiding the word count for non-Markdown files.

---

## Feature 12: Run with Arguments

### Concept

When the user presses Shift+F5, a dialog appears where they can type command-line arguments. The script is then run with those arguments passed to it.

### Key Methods

| Method | Description | Docs |
|---|---|---|
| `QInputDialog.getText(parent, title, label, echo, text)` | Shows a dialog with a text input. Returns `(text, ok)`. | [Link](https://doc.qt.io/qt-5/qinputdialog.html#getText) |
| `QProcess.start(program, args_list)` | Starts a process with the program and a list of arguments. | [Link](https://doc.qt.io/qt-5/qprocess.html#start) |
| `shlex.split(text)` | Splits a command-line string into a list of arguments, respecting quotes. | [Link](https://docs.python.org/3/library/shlex.html#shlex.split) |

### Step 1: Add `run_file_with_args` to PythonRunner

In `python_runner.py`, add this method:

```python
    def run_file_with_args(self, path: Path, args: str, cwd: Path = None):
        """
        Run a .py file with command-line arguments.

        `args` is a string like "--verbose --output result.txt"
        It gets split into a list: ["--verbose", "--output", "result.txt"]
        The final command is: python -u script.py --verbose --output result.txt
        """
        import shlex

        if self.is_running():
            self.stop()

        env = self._build_env(cwd or path.parent)

        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(cwd or path.parent))

        # Build the argument list: ["-u", "script.py", "--verbose", "--output", "result.txt"]
        # shlex.split parses the args string the same way a shell would:
        # '--output "my file.txt"' → ["--output", "my file.txt"]
        # (preserves quoted strings with spaces)
        # Docs: https://docs.python.org/3/library/shlex.html#shlex.split
        arg_list = ["-u", str(path)] + shlex.split(args)

        self.process.start(self.interpreter, arg_list)
```

### Step 2: Add the Menu Action

In `set_up_menu`, add to the Run menu after "Run File":

```python
        run_file_action = run_menu.addAction("Run File")
        run_file_action.setShortcut("F5")
        run_file_action.setShortcutContext(Qt.ApplicationShortcut)
        run_file_action.triggered.connect(self.run_current_file)

        run_with_args_action = run_menu.addAction("Run with Arguments")
        run_with_args_action.setShortcut("Shift+F5")
        run_with_args_action.setShortcutContext(Qt.ApplicationShortcut)
        run_with_args_action.triggered.connect(self.run_with_arguments)
```

### Step 3: Write the Method in MainWindow

```python
    def run_with_arguments(self):
        """Save the current file, ask for arguments, then run it."""
        editor = self.tab_view.currentWidget()
        if editor is None:
            return

        path = getattr(editor, "path", None)
        if path is None:
            self.save_as()
            path = getattr(editor, "path", None)
            if path is None:
                return
        else:
            self.save_file()

        # QInputDialog.getText shows a dialog with a single text input.
        # Parameters: parent, title, label, echo mode, default text
        # Returns: (text, ok) where ok is True if user clicked OK
        # Docs: https://doc.qt.io/qt-5/qinputdialog.html#getText
        args, ok = QInputDialog.getText(
            self,
            "Run with Arguments",
            "Command-line arguments:",
            QLineEdit.Normal,         # show text normally (not password mode)
            "",                       # default empty
        )

        if not ok:
            # User cancelled
            return

        # Show the console
        self.console_dock.show()

        # Run the file with the arguments
        self.python_runner.run_file_with_args(Path(path), args, cwd=Path(path).parent)
```

### How the Arguments Flow

```
User presses Shift+F5
    ↓
run_with_arguments() is called
    ↓
File is saved
    ↓
QInputDialog shows: "Command-line arguments: [________]"
    ↓
User types: --verbose --count 5 --output "result file.txt"
    ↓
ok = True, args = '--verbose --count 5 --output "result file.txt"'
    ↓
python_runner.run_file_with_args(path, args)
    ↓
shlex.split(args) → ["--verbose", "--count", "5", "--output", "result file.txt"]
    ↓
arg_list = ["-u", "main.py", "--verbose", "--count", "5", "--output", "result file.txt"]
    ↓
QProcess.start("python.exe", arg_list)
    ↓
Subprocess runs: python -u main.py --verbose --count 5 --output "result file.txt"
```

In the user's script, `sys.argv` will be:

```python
sys.argv = ["main.py", "--verbose", "--count", "5", "--output", "result file.txt"]
```

Python sys.argv docs: https://docs.python.org/3/library/sys.html#sys.argv

---

## Feature 13: ANSI Color in Console

### Concept

When a Python script uses ANSI escape codes (e.g. `print("\033[31mRed text\033[0m")`), the console should display colored text instead of showing the raw escape codes.

### How ANSI Escape Codes Work

ANSI codes start with `\033[` (escape character + bracket) followed by a number and a letter:

| Code | Color | Example |
|---|---|---|
| `\033[30m` | Black | `\033[30mBlack\033[0m` |
| `\033[31m` | Red | `\033[31mRed\033[0m` |
| `\033[32m` | Green | `\033[32mGreen\033[0m` |
| `\033[33m` | Yellow | `\033[33mYellow\033[0m` |
| `\033[34m` | Blue | `\033[34mBlue\033[0m` |
| `\033[35m` | Magenta | `\033[35mMagenta\033[0m` |
| `\033[36m` | Cyan | `\033[36mCyan\033[0m` |
| `\033[0m` | Reset | Returns to default color |

Full reference: [Wikipedia ANSI escape code - Colors](https://en.wikipedia.org/wiki/ANSI_escape_code#Colors)

### Step 1: Add the ANSI Color Map

In `console_widget.py`, add this class attribute to `ConsoleWidget`:

```python
    # Map of ANSI color codes to hex colors
    # This dictionary maps the number inside \033[Nm to a hex color string
    # that we can pass to QColor()
    ANSI_COLORS = {
        "30": "#abb2bf",   # black (use light gray for visibility)
        "31": "#e06c75",   # red
        "32": "#98c379",   # green
        "33": "#e5c07b",   # yellow
        "34": "#61afef",   # blue
        "35": "#c678dd",   # magenta
        "36": "#56b6c2",   # cyan
        "37": "#dcdfe4",   # white
        "0":  "#abb2bf",   # reset → default color
    }
```

### Step 2: Write the ANSI Parser Method

```python
    def _append_ansi(self, text: str, default_color: str):
        """
        Parse ANSI escape codes in text and append colored segments.

        This method scans the text for \033[Nm sequences (where N is a
        color code number). Text between codes is colored according to
        the code. \033[0m resets to the default color.

        Example input: "Hello \033[31mRed World\033[0m Done"
        Output: "Hello " in gray, "Red World" in red, " Done" in gray
        """
        import re

        # This regex matches ANSI escape codes:
        # \033\[  — the escape sequence prefix (ESC + [)
        # (\d+)   — one or more digits (the color code)
        # m       — the letter m (which means "set display attribute")
        # Docs: https://docs.python.org/3/library/re.html#re.Pattern
        ansi_pattern = re.compile(r'\033\[(\d+)m')

        current_color = default_color
        pos = 0  # current position in the text string

        # finditer returns an iterator of Match objects, one for each
        # ANSI code found in the text. Each Match has:
        #   .start() — position where the match begins
        #   .end()   — position after the match
        #   .group(1) — the captured digits (the color code)
        # Docs: https://docs.python.org/3/library/re.html#re.Pattern.finditer
        for match in ansi_pattern.finditer(text):
            # Text before the escape code (plain text to display)
            before = text[pos:match.start()]
            if before:
                self._append(before, current_color)

            # Extract the color code number from the regex match
            code = match.group(1)

            # Look up the color in our dictionary
            # If the code isn't in our map, keep the current color
            if code in self.ANSI_COLORS:
                current_color = self.ANSI_COLORS[code]

            # Move position past the escape code
            pos = match.end()

        # Append any remaining text after the last escape code
        remaining = text[pos:]
        if remaining:
            self._append(remaining, current_color)
```

### Step 3: Replace the stdout/stderr Handlers

In `_connect_signals`, change the signal connections:

```python
    def _connect_signals(self):
        # Use the ANSI parser for stdout and stderr
        self.runner.output_ready.connect(
            lambda text: self._append_ansi(text, "#abb2bf")
        )
        self.runner.error_ready.connect(
            lambda text: self._append_ansi(text, "#e06c75")
        )

        self.runner.process_finished.connect(self._on_finished)
        self.runner.state_changed.connect(self._on_state_changed)

        # Button clicks
        self.stop_btn.clicked.connect(self.runner.stop)
        self.clear_btn.clicked.connect(self.output.clear)

        # Input line → runner stdin
        self.input_line.returnPressed.connect(self._submit_input)
```

### Step 4: Test It

Create a Python script with this content and run it:

```python
print("Normal text")
print("\033[31mRed text\033[0m")
print("\033[32mGreen text\033[0m")
print("\033[33mYellow text\033[0m")
print("\033[34mBlue text\033[0m")
print("\033[35mMagenta text\033[0m")
print("\033[36mCyan text\033[0m")
print("Back to normal")
```

You should see each line in its corresponding color in the console.

### How the Parser Works

```
Input: "Hello \033[31mRed World\033[0m Done"

Regex finds: match1 at position 7, code="31"
             match2 at position 22, code="0"

Iteration 1: match = match1
  before = text[0:7] = "Hello " → append in gray
  code = "31" → current_color = "#e06c75" (red)
  pos = 14 (after the escape code)

Iteration 2: match = match2
  before = text[14:22] = "Red World" → append in red
  code = "0" → current_color = "#abb2bf" (reset to gray)
  pos = 27 (after the escape code)

After loop:
  remaining = text[27:] = " Done" → append in gray

Result: "Hello " (gray) + "Red World" (red) + " Done" (gray)
```

### Adding Bold and Other Attributes (Optional, Later)

The full ANSI spec also supports:
- `\033[1m` — bold
- `\033[4m` — underline
- `\033[1;31m` — bold + red (combined with semicolons)

To support these, extend the regex to `r'\033\[(\d+(?:;\d+)*)m'` and split the captured group on `;` to get multiple attributes. This is more complex — start with basic colors and add this later.

---

## Quick Reference: All New Menu Actions

| Menu | Action | Shortcut | Method |
|---|---|---|---|
| Edit | Go to Line | Ctrl+G | `self.goto_line()` |
| Edit | Toggle Comment | Ctrl+/ | `self.toggle_comment()` |
| File | Save All | Ctrl+Shift+A | `self.save_all()` |
| File | Close All | Ctrl+Shift+W | `self._close_all_tabs()` |
| File | Open Recent | (submenu) | `self.open_recent_file(path)` |
| Run | Run with Arguments | Shift+F5 | `self.run_with_arguments()` |

## Quick Reference: New Status Bar Widgets

| Widget | Position | Purpose |
|---|---|---|
| `self.cursor_pos_label` | Permanent (right) | Ln/Col display |
| `self.word_count_label` | Permanent (right) | Words/Chars (Markdown only) |

## Quick Reference: New Methods in PythonRunner

| Method | Purpose |
|---|---|
| `run_file_with_args(path, args, cwd)` | Run a script with command-line arguments |

## Quick Reference: New Methods in ConsoleWidget

| Method | Purpose |
|---|---|
| `_append_ansi(text, default_color)` | Parse ANSI escape codes and append colored text |

## Quick Reference: New Methods in MainWindow

| Method | Purpose |
|---|---|
| `goto_line()` | Jump to a specific line number |
| `toggle_comment()` | Toggle # comments on current line or selection |
| `_toggle_comment_single_line(editor)` | Toggle comment on a single line |
| `_toggle_comment_selection(editor)` | Toggle comment on multiple selected lines |
| `_add_to_recent_files(path)` | Add a file to the recent files list |
| `_save_recent_files()` | Persist recent files to QSettings |
| `_update_recent_menu()` | Rebuild the Open Recent submenu |
| `open_recent_file(path)` | Open a file from the recent files list |
| `save_all()` | Save all open tabs |
| `update_word_count()` | Update word/char count in status bar |
| `run_with_arguments()` | Run current file with command-line arguments |
