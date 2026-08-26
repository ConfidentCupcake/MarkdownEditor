# Auto Indentation — Implementation Guide

This guide explains how to design and implement a new context-aware Python auto-indentation feature for the current QScintilla editor. It does **not** use `indentation_helper.py-tested.py` as a project dependency. Instead, it explains the design and creates a clean, self-contained `indentation_helper.py` module from first principles.

The feature is limited deliberately to Python editing. It does not change Markdown behavior, multi-tab behavior, saving, the file manager, previews, or split panes.

---

## Table of Contents

1. [Goal](#goal)
2. [Why a Helper Module](#why-a-helper-module)
3. [Python Tokenization](#python-tokenization)
4. [Architecture](#architecture)
5. [Files Changed](#files-changed)
6. [Step 1: Create the Helper](#step-1-create-the-helper)
7. [Step 2: Understand the Helper](#step-2-understand-the-helper)
8. [Step 3: Configure PythonEditor](#step-3-configure-pythoneditor)
9. [Step 4: Handle Enter](#step-4-handle-enter)
10. [Runtime Flow](#runtime-flow)
11. [Test Matrix](#test-matrix)
12. [Troubleshooting](#troubleshooting)
13. [Reference Documentation](#reference-documentation)

---

## Goal

When the user presses plain Enter after a Python statement that opens a suite, the editor should create a new line with one extra indentation level.

```python
if user.is_admin:
    |
```

The same applies to Python compound statements such as:

```python
if condition:
elif condition:
else:
for item in items:
while running:
try:
except ValueError:
finally:
with open(path) as file:
def build(value):
class Editor:
match command:
case "open":
async def fetch_data():
```

The feature must not treat every colon as a suite opener. These must not receive an extra indentation level:

```python
settings = {"theme": "dark"}
text = "Status: ready"
items[1:4]
callback = lambda value: value + 1
```

---

## Why a Helper Module

`PythonEditor` is responsible for keyboard events and document changes. It should not also contain a large amount of language-analysis logic.

Separate the responsibilities:

```text
indentation_helper.py
    Determines whether a colon opens a Python suite.
    Computes indentation text.
    Has no Qt imports or UI side effects.

pythoneditor.py
    Receives an Enter key event.
    Reads text before the cursor.
    Calls the helper.
    Inserts a newline and computed indentation.
```

This separation makes the indentation rules independently testable:

```python
assert should_indent_after_colon("if enabled:") is True
assert should_indent_after_colon("text = 'Status:'") is False
```

It also prevents `main.py`, `MultiTabView`, and `FileManager` from becoming involved in per-keystroke Python syntax decisions.

---

## Python Tokenization

### The problem with `endswith(":")`

A naive implementation might be:

```python
if current_line.rstrip().endswith(":"):
    add_indent()
```

That implementation is incorrect because a colon occurs in many Python constructs:

```python
record = {"name": "Ada"}      # dictionary key/value separator
message = "Error:"             # string content
items[1:4]                      # slice separator
transform = lambda x: x * 2     # lambda separator
value: int                      # type annotation
```

A correct editor feature needs to understand lexical context: whether `:` is an operator in executable Python syntax, part of a string, inside brackets, or the final delimiter of a compound statement.

### What `tokenize` provides

Python's standard-library `tokenize` module is a lexical scanner. It converts source text into token objects such as:

```text
NAME       → if, def, user
OP         → :, (, ), [, ], {, }
STRING     → "Status:"
COMMENT    → # explanation
NEWLINE    → line ending
```

For this line:

```python
def build(value: str) -> dict:
```

the important tokens are conceptually:

```text
NAME('def')
NAME('build')
OP('(')
NAME('value')
OP(':')
NAME('str')
OP(')')
OP('->')
NAME('dict')
OP(':')
```

The colon after `value` occurs while parentheses are open, so it is an annotation. The final colon occurs at top level after `def`, so it opens the function suite.

### Why `io.StringIO` is used

`tokenize.generate_tokens()` expects a callable that behaves like a text file's `readline()` method. `io.StringIO(source).readline` turns an in-memory string into exactly that interface without writing a temporary file.

```python
stream = io.StringIO("if enabled:")
tokens = tokenize.generate_tokens(stream.readline)
```

---

## Architecture

```text
User presses Enter in PythonEditor
    ↓
PythonEditor.keyPressEvent(event)
    ↓
Verify: plain Enter, Python editor, no selection
    ↓
Read current line from its start to the cursor
    ↓
should_indent_after_colon(text_before_cursor)
    ↓
False → delegate Enter to QScintilla normally
True  → compute_new_line_indent(...)
    ↓
Insert "\n" + indentation text
    ↓
Move cursor after inserted indentation
```

The current editor already has `setAutoIndent(True)`. The custom handler only takes ownership of confirmed suite-opening colons. It delegates every other case to QScintilla, preserving ordinary Enter behavior.

---

## Files Changed

| File | Change | Reason |
|---|---|---|
| `indentation_helper.py` | Create a new module | Pure token-aware indentation logic |
| `pythoneditor.py` | Import helper functions | Makes logic available to PythonEditor |
| `pythoneditor.py` | Store indentation preferences | Keeps QScintilla and custom insertion consistent |
| `pythoneditor.py` | Extend `keyPressEvent()` | Intercepts only safe, plain Enter cases |

Do not change these files for the initial feature:

```text
main.py
markdowneditor.py
file_manager.py
multi_tab_view.py
find_replace.py
```

---

## Step 1: Create the Helper

Create a new file in the project root:

```text
indentation_helper.py
```

Place it beside `pythoneditor.py` and `main.py`.

### Add this complete implementation

```python
"""Pure helpers for context-aware Python suite indentation."""

from __future__ import annotations

import io
import tokenize


# Words that can begin a Python compound statement whose final ':' opens a suite.
COMPOUND_KEYWORDS = frozenset({
    "if",
    "elif",
    "else",
    "for",
    "while",
    "try",
    "except",
    "finally",
    "with",
    "class",
    "def",
    "match",
    "case",
    "async",
})


# Token types that do not contribute to the syntactic decision.
IGNORED_TOKEN_TYPES = frozenset({
    tokenize.NEWLINE,
    tokenize.NL,
    tokenize.INDENT,
    tokenize.DEDENT,
    tokenize.ENDMARKER,
    tokenize.COMMENT,
})


def should_indent_after_colon(text_before_cursor: str) -> bool:
    """Return whether Enter should add a Python suite indentation level.

    `text_before_cursor` must contain text from the beginning of the current
    physical line through the current cursor position. The function returns
    True only if the final meaningful token is a top-level ':' preceded by a
    recognized Python compound keyword.

    If the user is currently typing malformed or incomplete syntax that cannot
    be tokenized reliably, return False. A false negative is safer than adding
    indentation in an unrelated expression.
    """

    if not text_before_cursor or not text_before_cursor.strip():
        return False

    try:
        tokens = list(
            tokenize.generate_tokens(
                io.StringIO(text_before_cursor).readline
            )
        )
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return False

    meaningful = [
        token
        for token in tokens
        if token.type not in IGNORED_TOKEN_TYPES
    ]

    if not meaningful:
        return False

    final_token = meaningful[-1]

    # The final meaningful item must be the colon being evaluated.
    if not (
        final_token.type == tokenize.OP
        and final_token.string == ":"
    ):
        return False

    bracket_depth = 0
    found_compound_keyword = False

    # Analyse tokens before the final ':' only.
    for token in meaningful[:-1]:
        if token.type == tokenize.NAME:
            if (
                token.string in COMPOUND_KEYWORDS
                and bracket_depth == 0
            ):
                found_compound_keyword = True
            continue

        if token.type != tokenize.OP:
            continue

        if token.string in "([{":
            bracket_depth += 1
        elif token.string in ")]}":
            bracket_depth = max(0, bracket_depth - 1)
        elif token.string == ":" and bracket_depth == 0:
            # A prior top-level colon begins another logical clause.
            # Do not reuse a keyword observed before it.
            found_compound_keyword = False

    return bracket_depth == 0 and found_compound_keyword


def get_indent_string(
    use_tabs: bool = False,
    width: int = 4,
) -> str:
    """Return exactly one indentation level."""

    if use_tabs:
        return "\t"

    return " " * width


def compute_new_line_indent(
    text_before_cursor: str,
    use_tabs: bool = False,
    width: int = 4,
) -> str:
    """Return the indentation that belongs on the new line.

    The current leading whitespace is preserved. One extra indentation level
    is added only when `text_before_cursor` ends in a verified suite-opening
    colon.
    """

    first_non_whitespace = text_before_cursor.lstrip()
    current_indent_length = (
        len(text_before_cursor) - len(first_non_whitespace)
    )
    current_indent = text_before_cursor[:current_indent_length]

    if should_indent_after_colon(text_before_cursor):
        return current_indent + get_indent_string(use_tabs, width)

    return current_indent
```

### Design choices

| Choice | Reason |
|---|---|
| `frozenset` for keyword/token groups | Immutable membership sets communicate that these rules are constants |
| `generate_tokens()` | Accepts Unicode strings, which fits QScintilla text APIs |
| `StringIO(...).readline` | Supplies the file-like callback required by `generate_tokens()` |
| Return `False` on tokenization failure | Safe behavior while the user is typing incomplete syntax |
| Track `bracket_depth` | Reject dictionary, annotation, slice, and lambda-related colons inside brackets |
| Keep UI code out of module | Enables unit testing without starting Qt |

---

## Step 2: Understand the Helper

### `should_indent_after_colon()`

This is the decision function.

```python
should_indent_after_colon("if enabled:")
# True
```

It performs five checks:

1. The input is not empty or whitespace only.
2. Python can tokenize the current text.
3. The final meaningful token is `:`.
4. That colon is at bracket depth zero.
5. A compound keyword appeared at the same top-level depth before it.

#### Example: a valid block

```python
if enabled:
```

Relevant tokens:

```text
NAME('if')
NAME('enabled')
OP(':')
```

- `if` belongs to `COMPOUND_KEYWORDS`.
- No bracket is open.
- The final token is `:`.
- Result: `True`.

#### Example: a dictionary

```python
settings = {"theme": "dark"}
```

Relevant structural tokens:

```text
OP('{')
STRING('"theme"')
OP(':')
STRING('"dark"')
OP('}')
```

- The line does not end in a final colon token.
- The colon was inside braces.
- No compound keyword is present.
- Result: `False`.

#### Example: function annotation plus suite colon

```python
def build(value: str) -> dict:
```

- `def` is found at bracket depth zero.
- The annotation colon is inside `(` and `)`, so bracket depth is greater than zero.
- The final colon after `dict` is at bracket depth zero.
- Result: `True`.

### `get_indent_string()`

This function centralizes one indentation level:

```python
get_indent_string(use_tabs=False, width=4)
# "    "

get_indent_string(use_tabs=True, width=4)
# "\t"
```

It lets the editor support either style later without changing the syntax logic.

### `compute_new_line_indent()`

This function preserves the leading indentation on the current line, then adds one level when the decision function returns `True`.

```python
compute_new_line_indent("    if enabled:")
# "        "
```

The function receives text **before the cursor**, not necessarily the whole line. This is essential if the user presses Enter in the middle of a line.

---

## Step 3: Configure PythonEditor

### Add imports

Open `pythoneditor.py`. Below existing project imports, add:

```python
from indentation_helper import (
    should_indent_after_colon,
    compute_new_line_indent,
)
```

Example import region:

```python
from custompythonlexer import PyCustomLexer
from autocompleter import AutoCompleter
from definition_finder import DefinitionFinder
from indentation_helper import (
    should_indent_after_colon,
    compute_new_line_indent,
)
```

### Store indentation settings

Location: `PythonEditor.__init__()`.

Find the existing configuration:

```python
self.setTabWidth(4)
self.setIndentationsUseTabs(False)
self.setAutoIndent(True)
```

Replace it with:

```python
self._indent_width = 4
self._indent_with_tabs = False

self.setTabWidth(self._indent_width)
self.setIndentationsUseTabs(self._indent_with_tabs)
self.setAutoIndent(True)
```

The custom helper must use the same indentation configuration as QScintilla.

---

## Step 4: Handle Enter

### Location

File: `pythoneditor.py`  
Class: `PythonEditor`  
Method: `keyPressEvent()`

Keep the existing F12, Ctrl+Space, and Ctrl+X branches.

Add the following code after the Ctrl+X branch and before the final fallback:

```python
return super().keyPressEvent(e)
```

### Add this block

```python
        # Context-aware suite indentation for plain Enter only.
        if (
            self.is_python_file
            and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and e.modifiers() == Qt.NoModifier
            and not self.hasSelectedText()
        ):
            line, column = self.getCursorPosition()

            # text(line) returns the complete line. Slice at the cursor so
            # only text to the left participates in the syntax decision.
            text_before_cursor = self.text(line)[:column]

            if should_indent_after_colon(text_before_cursor):
                new_indent = compute_new_line_indent(
                    text_before_cursor,
                    use_tabs=self._indent_with_tabs,
                    width=self._indent_width,
                )

                # Returning afterwards prevents QScintilla's normal Enter
                # processing from applying a second indentation level.
                self.insert("\n" + new_indent)
                self.setCursorPosition(
                    line + 1,
                    len(new_indent),
                )
                return
```

### Complete keyPressEvent reference

```python
    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_F12:
            self.goto_definition()
            return

        if (
            e.modifiers() == Qt.KeyboardModifier.ControlModifier
            and e.key() == Qt.Key.Key_Space
        ):
            if self.is_python_file:
                line, column = self.getCursorPosition()
                self.auto_completer.get_completions(
                    line + 1,
                    column,
                    self.text(),
                )
                self.autoCompleteFromAPIs()
                return

        if (
            e.modifiers() == Qt.KeyboardModifier.ControlModifier
            and e.key() == Qt.Key.Key_X
        ):
            if not self.hasSelectedText():
                line, _ = self.getCursorPosition()
                self.setSelection(
                    line,
                    0,
                    line,
                    self.lineLength(line),
                )
                self.cut()
                return

        if (
            self.is_python_file
            and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and e.modifiers() == Qt.NoModifier
            and not self.hasSelectedText()
        ):
            line, column = self.getCursorPosition()
            text_before_cursor = self.text(line)[:column]

            if should_indent_after_colon(text_before_cursor):
                new_indent = compute_new_line_indent(
                    text_before_cursor,
                    use_tabs=self._indent_with_tabs,
                    width=self._indent_width,
                )
                self.insert("\n" + new_indent)
                self.setCursorPosition(line + 1, len(new_indent))
                return

        return super().keyPressEvent(e)
```

### Why plain Enter only?

The condition uses:

```python
e.modifiers() == Qt.NoModifier
```

This means the custom behavior does not replace Shift+Enter, Ctrl+Enter, Alt+Enter, or Meta+Enter behavior. Those events pass through to QScintilla or future editor shortcuts normally.

### Why skip selected text?

When text is selected, Enter normally replaces that selection. The custom code does not attempt to reproduce selection replacement rules. It delegates this case to QScintilla:

```python
not self.hasSelectedText()
```

### Why return after insertion?

The existing editor enables:

```python
self.setAutoIndent(True)
```

Calling `super().keyPressEvent(e)` after inserting your own newline and indentation could run QScintilla's automatic indentation too. Returning prevents possible double indentation.

---

## Runtime Flow

Given this source line and caret location:

```python
    if account.is_active:|
```

The runtime sequence is:

```text
1. keyPressEvent receives Key_Return.
2. The event has no modifiers and no selection.
3. getCursorPosition returns the current line and column.
4. text(line)[:column] produces "    if account.is_active:".
5. should_indent_after_colon tokenizes this text.
6. It sees NAME('if') at top level and final OP(':') at top level.
7. compute_new_line_indent preserves four leading spaces and adds four more.
8. PythonEditor inserts "\n        ".
9. The cursor moves to the end of the eight spaces.
10. The method returns; no second auto-indent operation runs.
```

Result:

```python
    if account.is_active:
        |
```

---

## Test Matrix

Restart the editor after creating the new module and changing `pythoneditor.py`.

### Positive cases

| Input before Enter | Expected indentation on next line |
|---|---|
| `if enabled:` | 4 spaces |
| `for item in items:` | 4 spaces |
| `while running:` | 4 spaces |
| `def build(value):` | 4 spaces |
| `class Editor:` | 4 spaces |
| `try:` | 4 spaces |
| `except ValueError:` | 4 spaces |
| `with open(path) as file:` | 4 spaces |
| `match command:` | 4 spaces |
| `case "open":` | 4 spaces |
| `    if enabled:` | 8 spaces |
| `def build(value: str) -> dict:` | 4 spaces |
| `if enabled:  # explanation` | 4 spaces |

### Negative cases

| Input before Enter | Expected custom result |
|---|---|
| `text = "Status:"` | No extra suite indent |
| `data = {"enabled": True}` | No extra suite indent |
| `items[1:4]` | No extra suite indent |
| `func = lambda value: value + 1` | No extra suite indent |
| `value: int` | No extra suite indent |
| Unterminated string or bracket expression | No custom indentation; delegate safely |

### Multi-tab and split-pane tests

1. Open two Python files.
2. Use **View → Split Right**.
3. Place one Python tab in each group.
4. Type `if condition:` in each pane and press Enter.
5. Confirm both editors indent correctly.
6. Move a tab between groups and repeat.

This works without MultiTabView-specific code because the behavior belongs to each `PythonEditor` instance.

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'indentation_helper'`

Check all of the following:

```text
- The file is named exactly indentation_helper.py.
- It is beside pythoneditor.py.
- It does not have a hidden .txt extension.
- The application was fully restarted.
```

### Enter produces eight spaces

The custom handler probably inserts indentation and then allows QScintilla's normal Enter handling to run.

Ensure this code ends with `return`:

```python
self.insert("\n" + new_indent)
self.setCursorPosition(line + 1, len(new_indent))
return
```

Do not call `super().keyPressEvent(e)` inside the successful custom branch.

### No extra indentation after `if ...:`

Confirm that:

1. The block was added to `PythonEditor.keyPressEvent()`, not `MainWindow`.
2. The helper imports are present.
3. The file is being edited in a `PythonEditor` instance.
4. The user pressed plain Enter, not Shift+Enter or Ctrl+Enter.
5. The cursor was placed after the suite-opening colon.

### Markdown also receives Python indentation

Do not copy the key handler into `MarkdownEditor`. The logic belongs only in `PythonEditor`.

### Use tabs instead of spaces

Change these settings in `PythonEditor.__init__()`:

```python
self._indent_width = 4
self._indent_with_tabs = True
```

The helper then returns `"\t"` for each indentation level.

---

## Reference Documentation

| Topic | Documentation |
|---|---|
| Python `tokenize` | https://docs.python.org/3/library/tokenize.html |
| `tokenize.generate_tokens()` | https://docs.python.org/3/library/tokenize.html#tokenize.generate_tokens |
| Python `io.StringIO` | https://docs.python.org/3/library/io.html#io.StringIO |
| QScintilla API | https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html |
| Qt key events | https://doc.qt.io/qt-5/qkeyevent.html |

The Python documentation states that `tokenize.generate_tokens()` accepts a `readline` callable that returns Unicode strings, which is why this guide passes `io.StringIO(text).readline`. The module returns token objects with fields including token type and token string; this is what allows the helper to distinguish an operator colon from text inside a string or comment.

---

## Future Enhancements

After the basic implementation is stable, possible additions include:

- Smart dedentation for `elif`, `else`, `except`, `finally`, and `case`.
- Automatic dedentation after certain flow-control statements.
- User-configurable tab width and tabs-versus-spaces settings.
- A command to convert indentation across an entire document.
- Unit tests using `unittest` or `pytest` for every helper case.

The recommended first version is intentionally conservative: it only adds one indentation level after a verified suite-opening colon and otherwise leaves QScintilla's established behavior untouched.
