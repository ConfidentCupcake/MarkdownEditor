# Quick Fix Code Actions — Implementation Guide

This guide adds safe, user-invoked source-code fixes to your Python diagnostics system. It does not use Jedi hover documentation as a fix and does not automatically rewrite code while the user is typing.

---

## Goal

Your current diagnostic flow is:

```text
pyflakes → Diagnostic → squiggly indicator → hover message
```

The quick-fix flow becomes:

```text
pyflakes → Diagnostic → user right-clicks squiggle → CodeAction → editor applies one undoable edit → diagnostics run again
```

A diagnostic reports a problem. A `CodeAction` describes a specific, safe edit that the user chooses to apply.

---

## What can be fixed safely?

| Diagnostic | Offer a fix? | Reason |
|---|---:|---|
| `UnusedImport` on `import os` | Yes | Removing one complete import line is deterministic |
| `UnusedImport` on `import os, sys` | No | Deleting the line could remove a used import |
| `UndefinedName` | No | The intended variable, import, or spelling is unknown |
| `SyntaxError` | No | Multiple possible repairs may exist |
| `UnusedVariable` | Usually no | Removing an assignment can remove side effects |

Start with one safe action only:

```text
UnusedImport → Remove unused import
```

---

## Step 1: Create quick_fixes.py

Create a new project-root file named `quick_fixes.py`.

```python
"""Safe, explicit quick-fix actions for Python diagnostics."""

from dataclasses import dataclass
import re
from themes.diagnostics import Diagnostic


@dataclass(frozen=True)
class CodeAction:
    title: str
    diagnostic_code: str
    line: int
    expected_line_text: str
    replacement: str = ""


_SIMPLE_IMPORT = re.compile(
    r"""\s*(?:import\s+[A-Za-z_]\w*(?:\s+as\s+[A-Za-z_]\w*)?|from\s+[A-Za-z_][\w.]*\s+import\s+[A-Za-z_]\w*(?:\s+as\s+[A-Za-z_]\w*)?)\s*(?:\#.*)?\Z""",
    re.VERBOSE,
)


def actions_for_diagnostic(diagnostic: Diagnostic, source: str) -> list[CodeAction]:
    """Return only source edits proven safe for this diagnostic."""
    if diagnostic.code != "UnusedImport":
        return []

    lines = source.splitlines()
    if diagnostic.line < 0 or diagnostic.line >= len(lines):
        return []

    line_text = lines[diagnostic.line]
    if not _SIMPLE_IMPORT.fullmatch(line_text):
        return []

    return [CodeAction(
        title="Remove unused import",
        diagnostic_code=diagnostic.code,
        line=diagnostic.line,
        expected_line_text=line_text,
    )]
```

### Why `expected_line_text` matters

Diagnostics run asynchronously. By the time a user opens a context menu, the document could have changed. The editor will compare the current line against this saved text before applying an action. If it differs, the action is rejected instead of editing the wrong source line.

---

## Step 2: Import the action types

In `pythoneditor.py`, change:

```python
from PyQt5.QtWidgets import QToolTip
```

To:

```python
from PyQt5.QtWidgets import QMenu, QToolTip
```

Then add this below your `diagnostics` import:

```python
from quick_fixes import CodeAction, actions_for_diagnostic
```

---

## Step 3: Add the right-click menu

Add this method inside `PythonEditor`, near `_format_diagnostic_tooltip()`.

```python
def contextMenuEvent(self, event):
    """Show safe quick fixes when a diagnostic is right-clicked."""
    if not self.is_python_file or self._shutting_down:
        return super().contextMenuEvent(event)

    diagnostic = self._diagnostics_at(event.pos())
    if diagnostic is None:
        return super().contextMenuEvent(event)

    actions = actions_for_diagnostic(diagnostic, self.text())
    if not actions:
        return super().contextMenuEvent(event)

    menu = QMenu(self)
    menu.setTitle("Quick Fix")

    for action in actions:
        item = menu.addAction(action.title)
        item.triggered.connect(
            lambda checked=False, code_action=action:
            self._apply_code_action(code_action)
        )

    menu.addSeparator()
    menu.addAction("No other safe fixes available").setEnabled(False)
    menu.exec_(event.globalPos())
    event.accept()
```

The existing `_diagnostics_at(event.pos())` method is reused. It already verifies that the mouse is over one of your diagnostic indicators and resolves the corresponding cached diagnostic.

---

## Step 4: Apply the action safely

Add this method directly below `contextMenuEvent()`.

```python
def _apply_code_action(self, action: CodeAction):
    """Apply one verified quick-fix edit as a single Undo operation."""
    if self._shutting_down or action.line < 0 or action.line >= self.lines():
        return

    current_line = self.text(action.line).rstrip("\r\n")
    if current_line != action.expected_line_text:
        return

    self.beginUndoAction()
    try:
        if action.line < self.lines() - 1:
            self.setSelection(action.line, 0, action.line + 1, 0)
        else:
            self.setSelection(action.line, 0, action.line, self.lineLength(action.line))
        self.replace(action.replacement)
    finally:
        self.endUndoAction()

    self._schedule_diagnostics()
```

### What this method guarantees

- The action can only modify the line it was created for.
- The action is refused when the line changed after diagnostics ran.
- The complete removal is one Undo operation.
- QScintilla emits `textChanged`, so dirty state and diagnostic scheduling continue normally.
- The explicit `_schedule_diagnostics()` call makes the intended re-check behavior clear.

---

## Test the first quick fix

Use this Python source:

```python
import os

print("Hello")
```

After the diagnostics timer runs:

1. `import os` receives an `UnusedImport` warning.
2. Right-click directly on the yellow squiggle.
3. Choose **Remove unused import**.
4. The editor becomes:

```python
print("Hello")
```

5. Press Ctrl+Z once.
6. Confirm that `import os` returns.
7. Wait for diagnostics and confirm that the warning returns too.

The quick fix must not appear for these examples:

```python
import os, sys
from os import path, getenv
print(undefined_name)
```

Those cases are intentionally unsupported by this first action provider.

---

## Why UndefinedName has no generic fix

For this diagnostic:

```python
print(user_nmae)
```

The editor cannot safely decide whether you meant:

```python
user_name
username
user.name
from project.users import user_nmae
```

A guessed replacement could silently break correct code. Show the diagnostic message and documentation instead; only offer a fix when a deterministic transformation exists.

---

## Long-term option: Ruff

Pyflakes reports diagnostics but does not expose a general quick-fix edit model. Ruff is a better long-term lint backend when you want many editor-grade code actions.

Ruff supports diagnostics with fixes and editor code actions, including quick fixes for fixable diagnostics, ignore actions, fix-all actions, and import organization.

Do not run this against an unsaved editor file:

```bash
ruff check --fix file.py
```

That edits the disk file and can conflict with unsaved editor text. Instead, request diagnostics and fix metadata, convert fix edits into `CodeAction` objects, let the user choose, and apply selected edits in QScintilla memory.

---

## Safety rules

| Rule | Reason |
|---|---|
| Never apply an action automatically | The user must approve source edits |
| Check the current source before applying | Async diagnostics can become stale |
| Group edits as one undo action | Ctrl+Z must restore the prior source predictably |
| Never use the on-disk file as a temporary edit target | Open tabs may contain unsaved text |
| Start with deterministic actions only | Incorrect rewrites are worse than a warning |

---

## Documentation

| Topic | Link |
|---|---|
| Ruff editor code actions | https://docs.astral.sh/ruff/editors/features/ |
| Ruff lint fixes | https://docs.astral.sh/ruff/linter/ |
| Scintilla documentation | https://www.scintilla.org/ScintillaDoc.html |
| QScintilla API | https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html |
