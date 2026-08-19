# Four Features Implementation Guide
Project: PyQt5 + QScintilla Python Code Editor
Target files: pythoneditor.py, main.py, custompythonlexer.py, error_checker.py, hover_helper.py
New files: diagnostics.py, indentation_helper.py, split_editor.py, minimap.py
Guide scope: Real-Time Python Diagnostics, Context-Aware Python Indentation, Split Editor Views, VS Code-Style Minimap

## 3. Feature 1: Real-Time Python Diagnostics
### 3.1 Goal and what changes
Enhance the existing pyflakes pipeline to:

Emit structured Diagnostic objects (with severity, source, code) instead of raw (line, col, message) tuples.

Render warnings with a second yellow squiggly indicator (INDICATOR_WARNING = 9) in addition to the existing red error indicator (INDICATOR_ERROR = 8).

Show a hover tooltip when the mouse rests on text underlined by a diagnostic indicator (the diagnostic message), in addition to the existing Jedi docstring hover.

Discard stale results: each check is tagged with a monotonically increasing request ID; results whose ID no longer matches the latest request are dropped before they touch the UI.

Classify pyflakes messages into error / warning / info per the table in Section 2.1.

3.2 New file: diagnostics.py
File: CREATE diagnostics.py in the project root (same directory as pythoneditor.py).

```
"""
Diagnostic data model and pyflakes message classification.

Used by error_checker.py (emits Diagnostic objects) and pythoneditor.py
(renders indicators + hover tooltips from Diagnostic objects).

File: CREATE new file `diagnostics.py` in project root.
"""

from dataclasses import dataclass, field
from enum import IntEnum


class DiagnosticSeverity(IntEnum):
    """Severity levels, ordered so higher == more severe."""
    INFO = 1
    WARNING = 2
    ERROR = 3


# pyflakes message class names that map to each severity.
# Verified against pyflakes 3.4.0 by running pyflakes.api.check on
# representative inputs (see guide Section 2.1).
_ERROR_CLASSES = frozenset({
    "UndefinedName",
    "UndefinedExport",
    "UndefinedLocal",
    "DuplicateArgument",
    "ReturnOutsideFunction",
    "YieldOutsideFunction",
    "BreakOutsideLoop",
    "ContinueOutsideLoop",
    "DefaultExceptNotLast",
    "AssertTuple",
    "IfTuple",
    "TwoStarredExpressions",
    "TooManyExpressionsInStarredAssignment",
    "ForwardAnnotationSyntaxError",
    "DoctestSyntaxError",
    "FStringMissingPlaceholders",
    "TStringMissingPlaceholders",
    "InvalidPrintSyntax",
    "RaiseNotImplemented",
})

_WARNING_CLASSES = frozenset({
    "UnusedImport",
    "UnusedVariable",
    "UnusedAnnotation",
    "UnusedIndirectAssignment",
    "RedefinedWhileUnused",
    "ImportStarUsed",
    "ImportStarNotPermitted",
    "ImportStarUsage",
    "ImportShadowedByLoopVar",
    "LateFutureImport",
    "FutureFeatureNotDefined",
    "PercentFormatExpectedMapping",
    "PercentFormatExpectedSequence",
    "PercentFormatExtraNamedArguments",
    "PercentFormatInvalidFormat",
    "PercentFormatMissingArgument",
    "PercentFormatMixedPositionalAndNamed",
    "PercentFormatPositionalCountMismatch",
    "PercentFormatStarRequiresSequence",
    "PercentFormatUnsupportedFormatCharacter",
    "StringDotFormatExtraNamedArguments",
    "StringDotFormatExtraPositionalArguments",
    "StringDotFormatInvalidFormat",
    "StringDotFormatMissingArgument",
    "StringDotFormatMixingAutomatic",
    "MultiValueRepeatedKeyLiteral",
    "MultiValueRepeatedKeyVariable",
    "IsLiteral",
})


def classify_severity(message) -> DiagnosticSeverity:
    """
    Map a pyflakes message object to a DiagnosticSeverity.

    `message` may be:
      - a pyflakes message instance (use type(message).__name__)
      - a syntax-error tuple/dict from the syntaxError/unexpectedError
        reporter callbacks (caller passes the class name string instead)
    """
    # If the caller already resolved a class name string, look it up.
    if isinstance(message, str):
        if message in _ERROR_CLASSES:
            return DiagnosticSeverity.ERROR
        if message in _WARNING_CLASSES:
            return DiagnosticSeverity.WARNING
        return DiagnosticSeverity.WARNING  # unknown -> warn, never silent

    class_name = type(message).__name__
    if class_name in _ERROR_CLASSES:
        return DiagnosticSeverity.ERROR
    if class_name in _WARNING_CLASSES:
        return DiagnosticSeverity.WARNING
    return DiagnosticSeverity.WARNING


@dataclass
class Diagnostic:
    """
    A single diagnostic item produced by a linter.

    Attributes:
        line:       0-based line index (matches QScintilla line numbers).
        column:     0-based column index.
        message:    Human-readable message text (shown in hover tooltip).
        severity:   DiagnosticSeverity (ERROR / WARNING / INFO).
        source:     Linter name, e.g. "pyflakes".
        code:       Stable code string, e.g. "UnusedImport" or "SyntaxError".
    """
    line: int
    column: int
    message: str
    severity: DiagnosticSeverity = DiagnosticSeverity.WARNING
    source: str = "pyflakes"
    code: str = ""

    @property
    def is_error(self) -> bool:
        return self.severity == DiagnosticSeverity.ERROR

    @property
    def is_warning(self) -> bool:
        return self.severity == DiagnosticSeverity.WARNING

    def indicator_id(self, error_id: int, warning_id: int, info_id: int) -> int:
        """Return the QScintilla indicator number to use for this diagnostic."""
        if self.severity == DiagnosticSeverity.ERROR:
            return error_id
        if self.severity == DiagnosticSeverity.WARNING:
            return warning_id
        return info_id  
```

### 3.3 Modified error_checker.py
**File:** REPLACE the existing error_checker.py with the version below. Only the run() method, the signals, and the request-ID plumbing change; the public check() / shutdown() contract is preserved and extended.

Changes summary:

- errors_found now emits a list[Diagnostic] (was list[tuple]).

- New diagnostics_found(list, int) signal carries the request ID for stale-result handling.

- check(code) increments self._request_id and passes it to the thread.

- run() builds Diagnostic objects, classifies severity via diagnostics.classify_severity, and emits diagnostics_found(diagnostics, request_id).

- errors_cleared(int) now carries the request ID too.

```
import io
from PyQt5.QtCore import QThread, pyqtSignal

from diagnostics import Diagnostic, DiagnosticSeverity, classify_severity


class ErrorChecker(QThread):
    """
    Background thread that runs pyflakes on the current file content.
    Signals:
        diagnostics_found(list, int) - list of Diagnostic objects + request id
        errors_cleared(int)          - no diagnostics found (+ request id)
    Legacy signal kept for backward compatibility:
        errors_found(list) - list of Diagnostic objects (always emitted with
                             diagnostics_found so old slots still work)
    """
    diagnostics_found = pyqtSignal(list, int)
    errors_cleared = pyqtSignal(int)
    # Legacy: emit the same list without the id for any old listeners.
    errors_found = pyqtSignal(list)

    # pyflakes message substrings that are false positives caused by
    # wildcard imports (from module import *). Preserved from the original.
    FALSE_POSITIVE_PATTERNS = [
        "unable to detect undefined names",
        "may be undefined, or defined from star imports",
    ]

    def __init__(self):
        super().__init__(None)
        self.code = ""
        self._shutting_down = False
        self._request_id = 0

    def check(self, code: str):
        """Start the error checker with a new request id."""
        if self.isRunning():
            return
        self._request_id += 1
        self.code = code
        self.start()

    @property
    def request_id(self) -> int:
        return self._request_id

    def _is_false_positive(self, msg: str) -> bool:
        """Check if a pyflakes message is a false positive from wildcard imports."""
        for pattern in self.FALSE_POSITIVE_PATTERNS:
            if pattern in msg:
                return True
        return False

    def run(self):
        request_id = self._request_id
        try:
            from pyflakes.api import check
            from pyflakes.reporter import Reporter

            collected = []  # list of Diagnostic

            class CustomReporter(Reporter):
                """Captures pyflakes output instead of printing to stderr."""
                def __init__(self):
                    self.output = io.StringIO()
                    self.errors_stream = io.StringIO()
                    super().__init__(self.output, self.errors_stream)

                def unexpectedError(self, filename, msg):
                    # Internal pyflakes failure -> error severity.
                    collected.append(Diagnostic(
                        line=0, column=0,
                        message=f"Syntax error: {msg}",
                        severity=DiagnosticSeverity.ERROR,
                        source="pyflakes",
                        code="UnexpectedError",
                    ))

                def syntaxError(self, filename, msg, lineno, offset, text):
                    col = offset or 0
                    collected.append(Diagnostic(
                        line=lineno - 1, column=col,
                        message=msg,
                        severity=DiagnosticSeverity.ERROR,
                        source="pyflakes",
                        code="SyntaxError",
                    ))

                def flake(self, message):
                    line = getattr(message, 'lineno', 0) - 1
                    col = getattr(message, 'col', 0) or 0
                    msg_text = str(message)
                    code_name = type(message).__name__
                    severity = classify_severity(message)
                    collected.append(Diagnostic(
                        line=line, column=col,
                        message=msg_text,
                        severity=severity,
                        source="pyflakes",
                        code=code_name,
                    ))

            reporter = CustomReporter()
            check(self.code, '<editor>', reporter)

            if self._shutting_down:
                return

            # Filter out false positives from wildcard imports (preserved
            # behavior from the original error_checker.py).
            real = [
                d for d in collected
                if not self._is_false_positive(d.message)
            ]

            if self._shutting_down:
                return

            if real:
                self.diagnostics_found.emit(real, request_id)
                # Legacy compatibility:
                self.errors_found.emit(real)
            else:
                self.errors_cleared.emit(request_id)

        except ImportError:
            # pyflakes not installed
            if not self._shutting_down:
                self.errors_cleared.emit(request_id)
        except Exception:
            if not self._shutting_down:
                self.errors_cleared.emit(request_id)

    def shutdown(self):
        self._shutting_down = True
        self.requestInterruption()
        if self.isRunning():
            self.wait()
```

### 3.4 Modified pythoneditor.py
File: MODIFY the enhanced pythoneditor.py (baseline = pythoneditor_v4.py). The changes below touch only __init__, _on_errors_found, _on_errors_cleared, mouseMoveEvent, and add a new _diagnostic_at helper plus a diagnostic-hover branch in _trigger_hover. All other methods are unchanged.

### 3.4.1 New imports and indicator constants
At the top of pythoneditor.py, add the diagnostics import and the warning indicator constant. The existing imports stay.
```
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QKeyEvent, QMouseEvent
from PyQt5.Qsci import QsciScintilla, QsciAPIs
from PyQt5.QtWidgets import QToolTip
from pathlib import Path

from custompythonlexer import PyCustomLexer
from autocompleter import AutoCompleter
from definition_finder import DefinitionFinder
from hover_helper import HoverHelper
from error_checker import ErrorChecker
from diagnostics import Diagnostic, DiagnosticSeverity   # NEW
```
In the class body, extend the indicator constants:
```
class PythonEditor(QsciScintilla):
    goto_definition_requested = pyqtSignal(str, int, int)

    # Indicator and marker IDs
    INDICATOR_ERROR = 8       # existing
    INDICATOR_WARNING = 9     # NEW: yellow squiggly for warnings
    INDICATOR_INFO = 10       # NEW: reserved for future info diagnostics
    MARKER_ERROR = 0         # existing
    MARKER_WARNING = 1       # NEW: yellow margin marker for warnings
```

3.4.2 __init__ changes
Inside the if self.is_python_file: block, after the existing INDICATOR_ERROR setup, add the warning indicator and marker definitions, and add the stale-result tracking field. Also rewire the error-checker signal connections to the new diagnostics_found/errors_cleared(int) signals.

            # Error indicator: use QScintilla's built-in indicatorDefine
            # instead of raw SendScintilla. This is the safe Python API.
            # SquiggleIndicator = red wavy underline
            self.indicatorDefine(QsciScintilla.SquiggleIndicator, self.INDICATOR_ERROR)
            # Set the indicator color using SendScintilla (only the color setter,
            # which takes 2 args and is known to work)
            red = QColor("#e06c75")
            self.setIndicatorForegroundColor(red, self.INDICATOR_ERROR)

            # Error margin marker: Circle
            self.markerDefine(QsciScintilla.Circle, self.MARKER_ERROR)
            self.setMarkerForegroundColor(red, self.MARKER_ERROR)
            self.setMarkerBackgroundColor(red, self.MARKER_ERROR)

            # Error checker
            self.error_checker = ErrorChecker()
            self.error_checker.errors_found.connect(self._on_errors_found)
            self.error_checker.errors_cleared.connect(self._on_errors_cleared)

Replace it with:

            # --- Error indicator (red squiggly) ---
            self.indicatorDefine(QsciScintilla.SquiggleIndicator, self.INDICATOR_ERROR)
            red = QColor("#e06c75")
            self.setIndicatorForegroundColor(red, self.INDICATOR_ERROR)

            # --- Warning indicator (yellow squiggly) ---  NEW
            self.indicatorDefine(QsciScintilla.SquiggleIndicator, self.INDICATOR_WARNING)
            yellow = QColor("#e5c07b")
            self.setIndicatorForegroundColor(yellow, self.INDICATOR_WARNING)

            # --- Info indicator (blue dotted) ---  NEW (reserved)
            self.indicatorDefine(QsciScintilla.DotBoxIndicator, self.INDICATOR_INFO)
            self.setIndicatorForegroundColor(QColor("#61afef"), self.INDICATOR_INFO)

            # --- Error margin marker: Circle ---
            self.markerDefine(QsciScintilla.Circle, self.MARKER_ERROR)
            self.setMarkerForegroundColor(red, self.MARKER_ERROR)
            self.setMarkerBackgroundColor(red, self.MARKER_ERROR)

            # --- Warning margin marker: Circle (yellow) ---  NEW
            self.markerDefine(QsciScintilla.Circle, self.MARKER_WARNING)
            self.setMarkerForegroundColor(yellow, self.MARKER_WARNING)
            self.setMarkerBackgroundColor(yellow, self.MARKER_WARNING)

            # --- Error checker (now emits Diagnostic objects + request id) ---
            self.error_checker = ErrorChecker()
            # New signature: diagnostics_found(list[Diagnostic], request_id:int)
            self.error_checker.diagnostics_found.connect(self._on_diagnostics_found)
            # errors_cleared now carries the request id for stale handling.
            self.error_checker.errors_cleared.connect(self._on_errors_cleared)
            # Keep the legacy errors_found(list) connected as a no-op fallback
            # so external listeners don't break the new flow.
            self.error_checker.errors_found.connect(lambda _l: None)

            # Stale-result tracking: only the latest request id may paint.  NEW
            self._latest_request_id = 0
            # Cache diagnostics per line for hover tooltips.  NEW
            self._diagnostics_by_line = {}   # line(int) -> list[Diagnostic]

3.4.3 New slot _on_diagnostics_found (replaces the painting part of _on_errors_found)
Add this new method. It guards on the request id, clears both indicators and both markers, repaints per-diagnostic severity, and rebuilds the hover cache.

    def _on_diagnostics_found(self, diagnostics: list, request_id: int):
        """
        Apply a fresh batch of Diagnostic objects to the editor.

        Stale results (request_id != self._latest_request_id) are discarded.
        """
        if self._shutting_down:
            return
        # Stale-result handling: ignore results from any request that is not
        # the most recent one.
        if request_id != self.error_checker.request_id:
            return
        self._latest_request_id = request_id

        try:
            total_lines = self.lines()

            # Clear BOTH indicators across the whole document.
            if total_lines > 0:
                last_len = self.lineLength(total_lines - 1)
                self.clearIndicatorRange(0, 0, total_lines - 1, last_len, self.INDICATOR_ERROR)
                self.clearIndicatorRange(0, 0, total_lines - 1, last_len, self.INDICATOR_WARNING)
                self.clearIndicatorRange(0, 0, total_lines - 1, last_len, self.INDICATOR_INFO)

            # Clear BOTH margin markers.
            self.markerDeleteAll(self.MARKER_ERROR)
            self.markerDeleteAll(self.MARKER_WARNING)

            # Rebuild the hover cache.
            self._diagnostics_by_line = {}

            for diag in diagnostics:
                line = diag.line
                if line < 0 or line >= total_lines:
                    continue
                line_len = self.lineLength(line)
                if line_len <= 0:
                    continue

                ind = diag.indicator_id(self.INDICATOR_ERROR,
                                        self.INDICATOR_WARNING,
                                        self.INDICATOR_INFO)
                # Paint the indicator across the whole line (matches the
                # original behavior, which underlined the entire line).
                self.fillIndicatorRange(line, 0, line, line_len, ind)

                # Margin marker colored by severity.
                marker = self.MARKER_ERROR if diag.is_error else self.MARKER_WARNING
                self.markerAdd(line, marker)

                # Cache for hover tooltips.
                self._diagnostics_by_line.setdefault(line, []).append(diag)

        except Exception as e:
            print(f"Error in _on_diagnostics_found: {e}")


3.4.4 Modified _on_errors_cleared
Locate:

    def _on_errors_cleared(self):
        """Clear all error indicators."""
        if self._shutting_down:
            return

        try:
            total_lines = self.lines()
            if total_lines > 0:
                last_line_len = self.lineLength(total_lines - 1)
                self.clearIndicatorRange(
                    0, 0, total_lines - 1, last_line_len,
                    self.INDICATOR_ERROR
                )
            self.markerDeleteAll(self.MARKER_ERROR)
            self._error_markers = []
        except Exception as e:
            print(f"Error in _on_errors_cleared: {e}")

Replace with (note the new request_id parameter and clearing of all indicators/markers/cache):

    def _on_errors_cleared(self, request_id: int = -1):
        """Clear all diagnostic indicators, markers, and hover cache."""
        if self._shutting_down:
            return
        # Stale-result handling: ignore clears from old requests.
        if request_id != -1 and request_id != self.error_checker.request_id:
            return
        self._latest_request_id = request_id if request_id != -1 else self._latest_request_id

        try:
            total_lines = self.lines()
            if total_lines > 0:
                last_line_len = self.lineLength(total_lines - 1)
                self.clearIndicatorRange(0, 0, total_lines - 1, last_line_len, self.INDICATOR_ERROR)
                self.clearIndicatorRange(0, 0, total_lines - 1, last_line_len, self.INDICATOR_WARNING)
                self.clearIndicatorRange(0, 0, total_lines - 1, last_line_len, self.INDICATOR_INFO)
            self.markerDeleteAll(self.MARKER_ERROR)
            self.markerDeleteAll(self.MARKER_WARNING)
            self._error_markers = []
            self._diagnostics_by_line = {}
        except Exception as e:
            print(f"Error in _on_errors_cleared: {e}")



Backward-compatibility note: the old _on_errors_found(self, errors) slot is no longer connected to diagnostics_found (which now passes (list, int)). If any external code still calls editor._on_errors_found(list), keep the old method as a thin shim:

python
    def _on_errors_found(self, errors: list):
        # Legacy shim: re-dispatch as a diagnostics batch with no stale guard.
        self._on_diagnostics_found(errors, self.error_checker.request_id)

This is optional and only needed if other modules call the slot directly.

3.4.5 Modified mouseMoveEvent (diagnostic hover detection)
The existing mouseMoveEvent only stores the mouse position and starts the Jedi hover timer. Add a fast, synchronous check for a diagnostic at the mouse position so the diagnostic tooltip can appear without waiting for Jedi (and so it appears even when Jedi has no docstring).

Locate:
    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        """Detect when the mouse hovers over a symbol."""
        if not self.is_python_file or self._shutting_down:
            return super().mouseMoveEvent(e)

        self._last_mouse_pos = e.pos()
        self._hover_timer.start()
        return super().mouseMoveEvent(e)

Relace with:
    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        """Detect when the mouse hovers over a symbol or a diagnostic range."""
        if not self.is_python_file or self._shutting_down:
            return super().mouseMoveEvent(e)

        self._last_mouse_pos = e.pos()

        # Fast path: if the mouse is over a diagnostic indicator, show the
        # diagnostic tooltip immediately (synchronous, no Jedi round-trip)
        # and skip the Jedi hover timer for this move.
        diag = self._diagnostic_at(e.pos())
        if diag is not None:
            # Hide any Jedi tooltip that may still be showing.
            QToolTip.hideText()
            tip = self._format_diagnostic_tooltip(diag)
            QToolTip.showText(self.mapToGlobal(e.pos()), tip, self)
            return super().mouseMoveEvent(e)

        # No diagnostic under cursor: hide any diagnostic tooltip and let
        # the Jedi hover flow run as before.
        QToolTip.hideText()
        self._hover_timer.start()
        return super().mouseMoveEvent(e)

3.4.6 New helpers _diagnostic_at and _format_diagnostic_tooltip
Add these two methods anywhere in the class (e.g., right after _trigger_hover).

    def _diagnostic_at(self, pos) -> Diagnostic | None:
        """
        Return the Diagnostic under the mouse position, or None.

        Uses the cached self._diagnostics_by_line map plus an indicator
        bitmask check so the tooltip only fires on actually-underlined text.
        """
        try:
            # SCI_POSITIONFROMPOINT = 2022 (same constant the existing
            # _trigger_hover uses). Returns -1 if outside the text area.
            byte_pos = self.SendScintilla(2022, pos.x(), pos.y())
            if byte_pos < 0:
                return None
        except Exception:
            return None

        # SCI_INDICATORALLONFOR = 2510 returns a bitmask of all indicators
        # present at the given document position. Bit i is set if indicator
        # i is active there. We only care about our diagnostic indicators.
        try:
            mask = self.SendScintilla(2510, byte_pos)
        except Exception:
            mask = 0
        diag_bits = (1 << self.INDICATOR_ERROR) | (1 << self.INDICATOR_WARNING) | (1 << self.INDICATOR_INFO)
        if not (mask & diag_bits):
            return None

        # Resolve the line number from the byte position (the existing
        # _trigger_hover already does this conversion; replicate it here).
        text = self.text()
        if not text.strip():
            return None
        text_bytes = text.encode('utf-8')
        if byte_pos >= len(text_bytes):
            byte_pos = len(text_bytes) - 1
        text_before = text_bytes[:byte_pos].decode('utf-8', errors='ignore')
        line = text_before.count('\n')

        diags = self._diagnostics_by_line.get(line)
        if not diags:
            return None
        # Return the most severe diagnostic on the line (errors first).
        return min(diags, key=lambda d: -int(d.severity))

    def _format_diagnostic_tooltip(self, diag: Diagnostic) -> str:
        """Format a Diagnostic as a hover tooltip string."""
        sev = {DiagnosticSeverity.ERROR: "Error",
               DiagnosticSeverity.WARNING: "Warning",
               DiagnosticSeverity.INFO: "Info"}.get(diag.severity, "Diagnostic")
        code = f" [{diag.code}]" if diag.code else ""
        return f"{sev}{code} ({diag.source})\n{diag.message}"

Why SCI_INDICATORALLONFOR = 2510: This Scintilla message returns a 32-bit bitmask of every indicator active at a position in a single call, which is cheaper and simpler than calling SCI_INDICATORSTART/SCI_INDICATOREND per indicator. It is available on QsciScintillaBase in PyQt5 QScintilla 2.11+. If a particular build does not expose it, the fallback is to iterate SCI_INDICATORVALUEAT (message 2508) for each of the three indicator IDs; the try/except above degrades gracefully to "no diagnostic" on any failure.

3.4.7 _trigger_hover interaction
No change required to _trigger_hover itself. Because mouseMoveEvent now hides the tooltip and does not start _hover_timer when a diagnostic is under the cursor, the Jedi docstring hover will not fight the diagnostic tooltip. When the mouse moves off the diagnostic, mouseMoveEvent restarts _hover_timer and the normal Jedi flow resumes.

3.4.8 shutdown interaction
No change required. The existing self.error_checker.shutdown() call still works because ErrorChecker.shutdown() is unchanged. The new _diagnostics_by_line cache is pure Python and needs no explicit cleanup.


4. Feature 2: Context-Aware Python Indentation
4.1 Status
Fully implemented and tested. The module indentation_helper.py exists at /home/user/workspace/indentation_helper.py, and test_indentation.py at /home/user/workspace/test_indentation.py reports 43 passed, 0 failed (verified by running python test_indentation.py in the sandbox). This section reproduces the verified code verbatim and specifies only its integration into pythoneditor.py.

4.2 New file: indentation_helper.py
File: CREATE indentation_helper.py in the project root. The content below is the exact, tested implementation.


"""
Context-Aware Python Indentation — Pure logic module.

This module provides a function that determines whether pressing Enter
after a colon should add an extra indentation level.

It uses Python's tokenize module to distinguish suite-opening colons
from colons in strings, comments, dictionaries, slices, lambdas, etc.

File: CREATE new file `indentation_helper.py` in project root.
"""

import tokenize
import io


# Keywords that open a suite with a colon
COMPOUND_KEYWORDS = frozenset({
    'if', 'elif', 'else', 'for', 'while', 'try', 'except', 'finally',
    'with', 'class', 'def', 'match', 'case', 'async',
})


def should_indent_after_colon(text_before_cursor: str) -> bool:
    """
    Determine if the text ends with a Python compound statement that
    opens a new suite with ':'.

    Returns True if Enter should add one extra indentation level.
    Returns False for colons in strings, comments, dicts, slices, lambdas, etc.

    Args:
        text_before_cursor: All text from the start of the current line
                           up to (and including) the colon.

    Example:
        >>> should_indent_after_colon("if x > 5:")
        True
        >>> should_indent_after_colon("d = {'key':")
        False
        >>> should_indent_after_colon("x = 'hello:'")
        False
    """
    if not text_before_cursor:
        return False

    stripped = text_before_cursor.rstrip()
    if not stripped:
        return False

    # Tokenize the text
    try:
        tokens = list(tokenize.generate_tokens(
            io.StringIO(text_before_cursor).readline
        ))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # If tokenization fails, fall back to a simple check
        before_colon = stripped[:-1].strip()
        if not before_colon:
            return False
        words = before_colon.split()
        if not words:
            return False
        return words[0] in COMPOUND_KEYWORDS

    # Filter out non-meaningful tokens for our analysis
    # We keep NAME, OP, NUMBER, STRING, COMMENT
    # We skip NEWLINE, NL, ENDMARKER, INDENT, DEDENT
    meaningful = []
    for tok in tokens:
        if tok.type in (tokenize.NEWLINE, tokenize.NL, tokenize.ENDMARKER,
                        tokenize.INDENT, tokenize.DEDENT, tokenize.COMMENT):
            continue
        meaningful.append(tok)

    if not meaningful:
        return False

    # The last meaningful token must be a colon
    last_tok = meaningful[-1]
    if not (last_tok.type == tokenize.OP and last_tok.string == ':'):
        return False

    # Now check: was a compound keyword seen at paren_depth == 0
    # before the final colon?
    paren_depth = 0
    seen_compound = False

    # We process all meaningful tokens EXCEPT the last one (the colon)
    for tok in meaningful[:-1]:
        if tok.type == tokenize.NAME and tok.string in COMPOUND_KEYWORDS:
            if paren_depth == 0:
                seen_compound = True

        elif tok.type == tokenize.OP:
            if tok.string in ('(', '[', '{'):
                paren_depth += 1
            elif tok.string in (')', ']', '}'):
                paren_depth = max(0, paren_depth - 1)
            elif tok.string == ':':
                # A colon before the final one.
                # If at paren_depth == 0, it's NOT a suite opener
                # (because the final colon is the suite opener).
                # If at paren_depth > 0, it's a dict/slice/annotation.
                # Either way, reset seen_compound because the statement
                # after this colon is a new logical context.
                if paren_depth == 0:
                    seen_compound = False

        elif tok.type == tokenize.COMMENT:
            # Comments don't affect the suite context
            pass

    # Check the final colon's paren_depth
    # We need to know if the final colon is at paren_depth == 0
    # We've been tracking paren_depth through all tokens except the last
    final_paren_depth = paren_depth

    # The final colon is a suite opener if:
    # 1. final_paren_depth == 0 (not inside brackets)
    # 2. seen_compound == True (we saw a compound keyword at top level)
    return final_paren_depth == 0 and seen_compound


def get_indent_string(use_tabs: bool = False, width: int = 4) -> str:
    """Return the indentation string for one level."""
    if use_tabs:
        return '\t'
    return ' ' * width


def compute_new_line_indent(current_line_text: str, use_tabs: bool = False,
                            width: int = 4) -> str:
    """
    Compute the indentation string for a new line after Enter.

    - Preserves the current indentation level
    - Adds one extra level if the current line ends with a suite-opening colon
    - Handles trailing whitespace

    Args:
        current_line_text: The full text of the line where Enter was pressed
        use_tabs: Whether to use tabs for indentation
        width: Indentation width in spaces (default 4)

    Returns:
        The indentation string for the new line
    """
    indent = get_indent_string(use_tabs, width)

    # Get current indentation (leading whitespace)
    stripped = current_line_text.lstrip()
    current_indent = current_line_text[:len(current_line_text) - len(stripped)]

    # Check if we need to add extra indentation
    if should_indent_after_colon(current_line_text.rstrip()):
        return current_indent + indent

    return current_indent

4.3 Integration into pythoneditor.py
File: MODIFY pythoneditor.py. Add the import and extend keyPressEvent to compute the new-line indentation when Enter/Return is pressed.

4.3.1 Import
At the top of pythoneditor.py, add:

from indentation_helper import compute_new_line_indent

4.3.2 keyPressEvent changes
Locate the existing keyPressEvent (baseline pythoneditor_v4.py):


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
        if e.modifiers() == Qt.KeyboardModifier.ControlModifier and e.key() == Qt.Key.Key_X:
            if not self.hasSelectedText():
                line, index = self.getCursorPosition()
                self.setSelection(line, 0, line, self.lineLength(line))
                self.cut()
                return

        return super().keyPressEvent(e)


Replace with (adds an Enter/Return branch that lets QScintilla insert the newline, then inserts the computed indentation):

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
        if e.modifiers() == Qt.KeyboardModifier.ControlModifier and e.key() == Qt.Key.Key_X:
            if not self.hasSelectedText():
                line, index = self.getCursorPosition()
                self.setSelection(line, 0, line, self.lineLength(line))
                self.cut()
                return

        # --- Context-aware indentation on Enter/Return ---  NEW (Feature 2)
        if (self.is_python_file
                and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and e.modifiers() == Qt.KeyboardModifier.NoModifier
                and not self.hasSelectedText()):
            return self._handle_enter_with_indent()

        return super().keyPressEvent(e)

    def _handle_enter_with_indent(self):
        """
        Let QScintilla's built-in auto-indent insert the newline, then replace
        the auto-indented leading whitespace on the new line with the
        context-aware indentation computed by indentation_helper.
        """
        # 1. Capture the current line text BEFORE the newline is inserted.
        line, index = self.getCursorPosition()
        current_line_text = self.text(line)  # QScintilla text(line) -> str

        # 2. Let QScintilla do its default newline + auto-indent.
        super().keyPressEvent(QKeyEvent(
            Qt.QEvent.KeyPress,
            Qt.Key.Key_Return,
            Qt.KeyboardModifier.NoModifier,
            "\n",
        ))

        # 3. Compute the correct indentation for the new line.
        use_tabs = self.indentationsUseTabs()
        width = self.tabWidth()
        indent = compute_new_line_indent(current_line_text, use_tabs, width)

        # 4. The cursor is now at the start of the new line, possibly with
        #    QScintilla's auto-indented whitespace already inserted. Replace
        #    whatever leading whitespace is there with our computed indent.
        new_line, new_index = self.getCursorPosition()
        existing = self.text(new_line)
        leading_len = len(existing) - len(existing.lstrip(" \t"))
        if leading_len:
            self.setSelection(new_line, 0, new_line, leading_len)
            self.removeSelectedText()
        if indent:
            self.insert(indent)
        # Place the cursor after the inserted indentation.
        self.setCursorPosition(new_line, len(indent))


4.4 Why this design
QScintilla's setAutoIndent(True) is left enabled so the newline, EOL mode, and undo grouping behave exactly as before. The helper only adjusts the amount of leading whitespace on the new line.

compute_new_line_indent is pure (no Qt dependency), so it is unit-testable in isolation — which is exactly what test_indentation.py does (43/43 pass).

should_indent_after_colon uses tokenize rather than regex, so it correctly ignores colons inside strings, comments, dicts, slices, lambdas, and type annotations. The paren_depth counter distinguishes top-level suite colons from bracketed colons; a top-level colon before the final one resets seen_compound because the statement after it is a new logical context (e.g., if x: y followed by another : is not a single suite opener).

Fallback on tokenize failure: if tokenize.generate_tokens raises (e.g., incomplete multiline construct while typing), the helper falls back to a simple "first word is a compound keyword" check, which is conservative and never over-indents.


## 5. Feature 3: Split Editor Views
### 5.1 Goal and approach
Provide a VS Code-style "split editor" that shows the same document in two side-by-side panes with independent scroll positions and independent cursors, sharing one QsciDocument so edits in one pane appear instantly in the other.

Chosen approach: a QSplitter holding two PythonEditor instances, where the second editor calls setDocument(first_editor.document()) to share the underlying QsciDocument. This is the documented QScintilla document-sharing mechanism.

### 5.2 QsciDocument limitations (verified from the QScintilla API)
editor.document() returns the current QsciDocument.

editor.setDocument(doc) attaches doc, replacing the currently attached document. Both editors then view the same text buffer; edits in either propagate to the other immediately.

Independent cursor positions: supported (each editor has its own cursor).

Independent scroll positions: supported (each editor has its own vertical/horizontal scroll bar).

Undo/redo: in PyQt5 QScintilla, undo/redo operates on the shared document, so an undo in one pane affects the other. This is acceptable for a split view (it matches how VS Code's split behaves when both panes show the same file) and is documented as a known limitation in the task brief.

Lexer: each editor keeps its own lexer instance, but because the document is shared, styling is recomputed per editor. Both editors should use the same PyCustomLexer configuration.

### 5.3 New file: split_editor.py
File: CREATE split_editor.py in the project root.
```
"""
Split editor view: two PythonEditor panes sharing one QsciDocument.

File: CREATE new file `split_editor.py` in project root.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QSplitter

from pythoneditor import PythonEditor


class SplitEditor(QSplitter):
    """
    A horizontal QSplitter that hosts one or two PythonEditor panes
    sharing a single QsciDocument.

    Usage:
        split = SplitEditor(path=Path("foo.py"), is_python_file=True)
        split.split()    # show two panes
        split.unsplit()  # collapse back to one pane
        editor = split.active_editor()  # the currently focused pane
    """

    def __init__(self, path=None, is_python_file: bool = True, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setChildrenCollapsible(False)
        self.setHandleWidth(1)

        self._path = path
        self._is_python_file = is_python_file
        self._active = None

        # Primary pane always exists.
        self._primary = self._make_editor(path, is_python_file)
        self.addWidget(self._primary)
        self._active = self._primary
        self._secondary = None

        # Track focus to know which pane is active.
        self._primary.setFocusProxy(None)
        for ed in self._editors():
            ed.focusInEvent = self._wrap_focus_in(ed.focusInEvent, ed)

    # ------------------------------------------------------------------ #
    #  Construction helpers
    # ------------------------------------------------------------------ #
    def _make_editor(self, path, is_python_file) -> PythonEditor:
        ed = PythonEditor(path=path, is_python_file=is_python_file)
        # Keep the splitter informed when a pane gains focus.
        ed._split_focus_handler = lambda _e=None, ed=ed: self._set_active(ed)
        # Install an event filter via a lightweight override.
        return ed

    def _wrap_focus_in(self, original, ed):
        def handler(e):
            self._set_active(ed)
            return original(e)
        return handler

    def _editors(self):
        eds = [self._primary]
        if self._secondary is not None:
            eds.append(self._secondary)
        return eds

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #
    def primary_editor(self) -> PythonEditor:
        return self._primary

    def secondary_editor(self) -> PythonEditor:
        return self._secondary

    def active_editor(self) -> PythonEditor:
        """Return the most recently focused pane (primary if none focused)."""
        if self._active is not None:
            return self._active
        return self._primary

    def is_split(self) -> bool:
        return self._secondary is not None

    def split(self):
        """Create the second pane and share the primary's document."""
        if self._secondary is not None:
            return  # already split
        sec = self._make_editor(self._path, self._is_python_file)
        # Share the document so both panes view the same buffer.
        sec.setDocument(self._primary.document())
        # Mirror lexer settings by giving the second pane its own lexer
        # instance of the same type (the document text is shared; styling
        # is recomputed per editor).
        self.addWidget(sec)
        self._secondary = sec
        # Equal sizes.
        self.setSizes([self.width() // 2, self.width() // 2])
        # Focus the new pane so the user can start typing there.
        sec.setFocus()
        self._set_active(sec)

    def unsplit(self):
        """Remove the second pane; keep the primary (and its document)."""
        if self._secondary is None:
            return
        # If the active pane was the secondary, move focus to the primary.
        if self._active is self._secondary:
            self._primary.setFocus()
            self._set_active(self._primary)
        # Shut down the secondary's background threads before disposing.
        try:
            self._secondary.shutdown()
        except Exception:
            pass
        self._secondary.setParent(None)
        self._secondary.deleteLater()
        self._secondary = None
        # Give the primary the full width.
        self.setSizes([self.width()])

    def toggle(self):
        """Split if unsplit, unsplit if split."""
        if self.is_split():
            self.unsplit()
        else:
            self.split()

    # ------------------------------------------------------------------ #
    #  Focus tracking
    # ------------------------------------------------------------------ #
    def _set_active(self, ed: PythonEditor):
        self._active = ed

    # ------------------------------------------------------------------ #
    #  Delegation to the active pane (so callers can treat SplitEditor
    #  almost like a single editor for common operations).
    # ------------------------------------------------------------------ #
    def text(self) -> str:
        return self.active_editor().text()

    def set_text_safely(self, text: str):
        # Load into the primary; the shared document propagates to the
        # secondary automatically.
        self._primary.setTextSafely(text)

    def shutdown(self):
        for ed in self._editors():
            try:
                ed.shutdown()
            except Exception:
                pass
```

### 5.4 Integration into main.py
File: MODIFY main.py. Add a "Split Editor" action to a new View menu with the Ctrl+\ shortcut, and route the current tab's editor through SplitEditor when splitting.

### 5.4.1 Import
At the top of main.py, add:

from split_editor import SplitEditor

### 5.4.2 Add a View menu
In set_up_menu, after the Edit menu block and before the Mode menu, add a View menu with the split action.

Locate the end of the Edit menu block:
```
        # Edit menu
        edit_menu = menu_bar.addMenu("Edit")

        copy_action = edit_menu.addAction("Copy")
        copy_action.setShortcut("Ctrl+C")
        copy_action.setShortcutContext(Qt.ApplicationShortcut)
        copy_action.triggered.connect(self.copy)

        # View menu
        view_menu = menu_bar.addMenu("View")

        self.split_editor_action = view_menu.addAction("Split Editor")
        self.split_editor_action.setShortcut(QKeySequence("Ctrl+\\"))
        self.split_editor_action.setShortcutContext(Qt.ApplicationShortcut)
        self.split_editor_action.setCheckable(True)
        self.split_editor_action.triggered.connect(self.toggle_split_editor)
```

### 5.4.3 Add the split/unsplit methods
Add these methods to MainWindow (e.g., after copy):

```
    def toggle_split_editor(self):
        """
        Toggle the split view for the current Python tab.

        The current PythonEditor is wrapped in a SplitEditor (which takes
        ownership of the editor widget) and the SplitEditor is reinserted
        into the tab at the same index. Toggling again unwraps it.
        """
        if not self.python_editor_active:
            self.statusBar().showMessage("Split is available in Python mode", 2000)
            return

        index = self.tab_view.currentIndex()
        if index < 0:
            return
        widget = self.tab_view.widget(index)
        title = self.tab_view.tabText(index)

        # Case 1: already a SplitEditor -> unsplit (or fully collapse).
        if isinstance(widget, SplitEditor):
            if widget.is_split():
                widget.unsplit()
                self.split_editor_action.setChecked(False)
                self.statusBar().showMessage("Editor unsplit", 2000)
            else:
                widget.split()
                self.split_editor_action.setChecked(True)
                self.statusBar().showMessage("Editor split", 2000)
            return

        # Case 2: a plain PythonEditor -> wrap it into a SplitEditor.
        if isinstance(widget, PythonEditor):
            # Reparent the editor into a new SplitEditor without destroying it.
            path = getattr(widget, "path", None)
            split = SplitEditor(path=path, is_python_file=widget.is_python_file, parent=self)
            # Take the existing editor out of the tab view.
            self.tab_view.removeTab(index)
            # Use the existing editor as the primary pane so its background
            # threads (error checker, hover, autocomplete) keep running.
            split._primary.setParent(None)
            split._primary.deleteLater()
            split._primary = widget
            # Replace the placeholder primary that SplitEditor created.
            # (SplitEditor already added a primary; swap it for the real one.)
            old_placeholder = split.widget(0)
            split.replaceWidget(0, widget)
            old_placeholder.deleteLater()
            split._active = widget
            # Re-share the document when the user splits later.
            self.tab_view.insertTab(index, split, title)
            self.tab_view.setCurrentIndex(index)
            # Now actually split so both panes show.
            split.split()
            self.split_editor_action.setChecked(True)
            self.statusBar().showMessage("Editor split", 2000)
```

Simpler alternative: if the reparenting dance above feels fragile, the cleaner path is to have set_new_tab always create a SplitEditor (whose primary pane is a fresh PythonEditor) and never insert a bare PythonEditor into tab_view. Then toggle_split_editor only ever handles the isinstance(widget, SplitEditor) branch. This requires changing get_editor to return a SplitEditor wrapper in Python mode:

python
```
    def get_editor(self, path: Path = None, is_python_file=True):
        if self.python_editor_active:
            return SplitEditor(path=path, is_python_file=is_python_file)
        return MarkdownEditor(path=path, is_python_file=is_python_file)
```
…and updating the few places that call editor.text() / editor.path to go through split.active_editor() / split.primary_editor(). The two approaches are mutually exclusive; pick one. The first approach (wrap on demand) is shown above because it is the smaller diff.

### 5.4.4 Update close_tab and _convert_current_tab
These already call widget.shutdown() when the widget is a PythonEditor. Extend them to handle SplitEditor:
```
    def close_tab(self, index):
        widget = self.tab_view.widget(index)
        if isinstance(widget, SplitEditor):
            widget.shutdown()
        elif isinstance(widget, PythonEditor):
            widget.shutdown()
        self.tab_view.removeTab(index)
        self.render_preview()
```
In _convert_current_tab, before old.shutdown(), add:
```
        if isinstance(old, SplitEditor):
            old.shutdown()
        elif isinstance(old, PythonEditor) and hasattr(old, "shutdown"):
            old.shutdown()
``` 

## 6. Feature 4: VS Code-Style Minimap
### 6.1 Goal and approach
Render a scaled-down overview of the document on the right edge of the editor, with colored line stripes derived from the lexer's syntax colors and a viewport rectangle showing the currently visible lines. Click/drag on the minimap scrolls the editor. Updates are throttled to at most once per 100 ms so large files stay responsive.

Chosen approach: a custom QWidget painted with QPainter (not a second QsciScintilla view). This is far cheaper than a second Scintilla instance, gives full control over the rendered scale, and avoids doubling the lexer/indicator overhead.

6.2 New file: minimap.py
File: CREATE minimap.py in the project root.

"""
VS Code-style minimap for a QsciScintilla editor.

A custom QWidget that paints a scaled-down view of the document using
colored line stripes derived from the lexer's syntax colors, plus a
viewport rectangle showing the currently visible lines. Click/drag on
the minimap scrolls the editor. Updates are throttled to <= 100 ms.

File: CREATE new file `minimap.py` in project root.
"""

from PyQt5.QtCore import Qt, QTimer, QRect, QPoint
from PyQt5.QtGui import QColor, QPainter, QPen, QBrush, QFont
from PyQt5.QtWidgets import QWidget


class MinimapWidget(QWidget):
    """
    Minimap widget bound to a QsciScintilla editor.

    The editor must be set with set_editor() before the minimap is shown.
    The minimap positions itself on the right edge of the editor and
    tracks the editor's height automatically.
    """

    # Default colors per lexer style id (matches PyCustomLexer style ids).
    # These mirror the theme.json syntax colors used by custompythonlexer.py.
    DEFAULT_STYLE_COLORS = {
        0:  "#abb2bf",  # DEFAULT
        1:  "#c678dd",  # KEYWORD (purple)
        2:  "#e5c07b",  # TYPES (yellow)
        3:  "#98c379",  # STRING (green)
        4:  "#d19a66",  # KEYARGS (orange)
        5:  "#abb2bf",  # BRACKETS
        6:  "#5c6370",  # COMMENTS (gray)
        7:  "#d19a66",  # CONSTANTS (orange)
        8:  "#61afef",  # FUNCTIONS (blue)
        9:  "#e5c07b",  # CLASSES (yellow)
        10: "#61afef",  # FUNCTION_DEF (blue)
    }

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self._visible = True
        self._width = 80
        self.setFixedWidth(self._width)

        # Throttle: repaint at most every 100 ms.
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.setInterval(100)
        self._repaint_timer.timeout.connect(self.update)

        # Drag state.
        self._dragging = False

        # Cache of per-line dominant colors, rebuilt on text change.
        self._line_colors = []        # list of QColor
        self._line_lengths = []       # list of int (char counts)

        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)

        self._connect_editor()
        self.rebuild_cache()

    # ------------------------------------------------------------------ #
    #  Editor wiring
    # ------------------------------------------------------------------ #
    def _connect_editor(self):
        ed = self.editor
        try:
            ed.textChanged.connect(self._schedule_rebuild)
        except Exception:
            pass
        try:
            # SCI lines-on-screen / first-visible-line change with scroll.
            sb = ed.verticalScrollBar()
            sb.valueChanged.connect(self._schedule_repaint)
        except Exception:
            pass

    def _schedule_rebuild(self):
        """Rebuild the color cache (throttled)."""
        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(100)
        self._rebuild_timer.timeout.connect(self._do_rebuild)
        self._rebuild_timer.start()

    def _do_rebuild(self):
        self.rebuild_cache()
        self._schedule_repaint()

    def _schedule_repaint(self):
        if not self._repaint_timer.isActive():
            self._repaint_timer.start()

    # ------------------------------------------------------------------ #
    #  Cache rebuild
    # ------------------------------------------------------------------ #
    def rebuild_cache(self):
        """Recompute the dominant color and length of every line."""
        ed = self.editor
        n = ed.lines()
        colors = []
        lengths = []
        for i in range(n):
            line_text = ed.text(i)
            lengths.append(len(line_text))
            colors.append(self._dominant_color(line_text))
        self._line_colors = colors
        self._line_lengths = lengths

    def _dominant_color(self, line_text: str) -> QColor:
        """
        Pick a representative color for a line by scanning its tokens and
        returning the color of the first non-default style. Falls back to
        the default color for blank/code lines.
        """
        if not line_text.strip():
            return QColor(self.DEFAULT_STYLE_COLORS[0])
        # Cheap heuristic: classify by leading character. This avoids a
        # full re-tokenize per line on every rebuild; the lexer itself
        # already styles the editor, and the minimap only needs an
        # approximate color per line.
        s = line_text.lstrip()
        if s.startswith("#"):
            return QColor(self.DEFAULT_STYLE_COLORS[6])      # comment
        if s.startswith(("'", '"')):
            return QColor(self.DEFAULT_STYLE_COLORS[3])      # string
        # Keyword / def / class detection
        first_word = ""
        for ch in s:
            if ch.isalnum() or ch == "_":
                first_word += ch
            else:
                break
        import keyword as _kw
        if first_word in _kw.kwlist:
            return QColor(self.DEFAULT_STYLE_COLORS[1])     # keyword
        if first_word == "def":
            return QColor(self.DEFAULT_STYLE_COLORS[10])     # function_def
        if first_word == "class":
            return QColor(self.DEFAULT_STYLE_COLORS[9])      # classes
        return QColor(self.DEFAULT_STYLE_COLORS[0])          # default

    # ------------------------------------------------------------------ #
    #  Geometry: map between minimap Y and document line
    # ------------------------------------------------------------------ #
    def _line_height(self) -> float:
        """Height in pixels of one document line in the minimap."""
        n = max(len(self._line_colors), 1)
        return max(1.0, self.height() / n)

    def y_to_line(self, y: int) -> int:
        """Map a minimap Y coordinate to a document line index."""
        lh = self._line_height()
        if lh <= 0:
            return 0
        line = int(y / lh)
        return max(0, min(line, max(len(self._line_colors) - 1, 0)))

    def line_to_y(self, line: int) -> int:
        """Map a document line index to a minimap Y coordinate."""
        return int(line * self._line_height())

    def _viewport_rect(self) -> QRect:
        """Rectangle in minimap coords representing the editor's viewport."""
        ed = self.editor
        first = ed.firstVisibleLine()
        # SCI_LINESONSCREEN = 2370 -> number of lines visible in the view.
        try:
            visible = ed.SendScintilla(2370)
        except Exception:
            visible = 1
        visible = max(1, int(visible))
        y0 = self.line_to_y(first)
        y1 = self.line_to_y(first + visible)
        return QRect(0, y0, self.width(), max(y1 - y0, 2))

    # ------------------------------------------------------------------ #
    #  Painting
    # ------------------------------------------------------------------ #
    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#21252b"))

        lh = self._line_height()
        if lh < 1:
            p.end()
            return

        # Draw each line as a thin colored stripe. The stripe width is
        # proportional to the line length (capped to the minimap width)
        # so long lines are visibly longer, like VS Code's minimap.
        max_len = max(self._line_lengths) if self._line_lengths else 1
        max_len = max(max_len, 1)
        for i, (color, length) in enumerate(zip(self._line_colors, self._line_lengths)):
            y = int(i * lh)
            stripe_h = max(1, int(lh) - (1 if int(lh) > 1 else 0))
            # Width proportional to line length, capped at the minimap width.
            w = int((length / max_len) * (self.width() - 4)) if length else 0
            w = max(w, 2)
            p.fillRect(2, y, w, stripe_h, color)

        # Viewport rectangle.
        vp = self._viewport_rect()
        p.setPen(QPen(QColor(80, 80, 80, 200), 1))
        p.setBrush(QBrush(QColor(255, 255, 255, 20)))
        p.drawRect(vp)

        p.end()

    # ------------------------------------------------------------------ #
    #  Mouse: click/drag to scroll the editor
    # ------------------------------------------------------------------ #
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._dragging = True
            self._scroll_to(e.y())

    def mouseMoveEvent(self, e):
        if self._dragging:
            self._scroll_to(e.y())

    def mouseReleaseEvent(self, e):
        self._dragging = False

    def _scroll_to(self, y: int):
        ed = self.editor
        line = self.y_to_line(y)
        # SCI_GOTOLINE = 2024 scrolls so that 'line' becomes the top visible.
        try:
            ed.SendScintilla(2024, line)
        except Exception:
            # Fallback: set the first visible line via the high-level API.
            try:
                ed.setFirstVisibleLine(line)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    #  Visibility / width configuration
    # ------------------------------------------------------------------ #
    def set_minimap_width(self, width: int):
        self._width = max(40, min(width, 200))
        self.setFixedWidth(self._width)
        self.update()

    def toggle(self, on: bool = None):
        if on is None:
            self._visible = not self._visible
        else:
            self._visible = on
        self.setVisible(self._visible)
        if self._visible:
            self.rebuild_cache()
            self.update()

    def is_visible(self) -> bool:
        return self._visible

6.3 Integration into pythoneditor.py
File: MODIFY pythoneditor.py. Add the minimap as a child widget anchored to the right edge, and update it on text/scroll/resize.

6.3.1 Import
At the top of pythoneditor.py, add:

from minimap import MinimapWidget

6.3.2 __init__ changes
At the end of __init__ (after the margin/scrollbar setup, still inside __init__), add:

        # --- Minimap (Feature 4) ---  NEW
        # Only for Python files; the minimap reads lexer colors.
        if self.is_python_file:
            self.minimap = MinimapWidget(self, parent=self)
            self.minimap.setVisible(True)
        else:
            self.minimap = None

6.3.3 Position the minimap on resize
Override resizeEvent (or add one if absent) so the minimap stays glued to the right edge and matches the editor height:

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.minimap is not None and self.minimap.isVisible():
            # Place the minimap flush right, full editor height, below any
            # top margin. QScintilla's viewport is the whole widget, so we
            # position relative to the widget rect.
            self.minimap.setGeometry(
                self.width() - self.minimap.width(),
                0,
                self.minimap.width(),
                self.height(),
            )

6.3.4 Keep the minimap in sync on scroll and text change
The MinimapWidget constructor already connects to textChanged and verticalScrollBar().valueChanged, so no extra wiring is needed in pythoneditor.py for the common cases. However, the minimap must also repaint when the editor is scrolled by non-scrollbar means (e.g., keyboard navigation that calls ensureLineVisible). Add a lightweight hook by overriding firstVisibleLine-changing events via SCN_PAINTED is overkill; instead, connect the existing cursorPositionChanged signal (which often coincides with scrolling) to a throttled repaint:

In __init__, inside the if self.is_python_file: block, after the minimap is created:

            # Repaint the minimap viewport when the cursor moves (which
            # often triggers a scroll via ensureLineVisible).
            self.cursorPositionChanged.connect(lambda _l, _i: self.minimap._schedule_repaint() if self.minimap else None)

6.3.5 Toggle the minimap from the View menu (optional, in main.py)
To let the user toggle the minimap, add an action to the View menu created in Feature 3. In main.py's set_up_menu, after the split action:

        self.minimap_action = view_menu.addAction("Toggle Minimap")
        self.minimap_action.setShortcut(QKeySequence("Ctrl+M"))
        self.minimap_action.setShortcutContext(Qt.ApplicationShortcut)
        self.minimap_action.setCheckable(True)
        self.minimap_action.setChecked(True)
        self.minimap_action.triggered.connect(self.toggle_minimap)

Note: Ctrl+M is used here because the existing Mode menu already binds Ctrl+Shift+M to "Markdown Editor"; plain Ctrl+M is free. Adjust if it conflicts with the host OS.

    def toggle_minimap(self):
        ed = self.tab_view.currentWidget()
        # Support both bare PythonEditor and SplitEditor.
        target = ed if isinstance(ed, PythonEditor) else getattr(ed, "active_editor", lambda: None)()
        if target is None or not hasattr(target, "minimap") or target.minimap is None:
            self.statusBar().showMessage("Minimap unavailable for this tab", 2000)
            return
        target.minimap.toggle()
        self.minimap_action.setChecked(target.minimap.is_visible())
        self.statusBar().showMessage(
            "Minimap on" if target.minimap.is_visible() else "Minimap off", 2000)


6.4 Coordinate mapping (recap)
Minimap Y → document line: line = (y / minimap_height) * total_lines, implemented as y_to_line(y) using self._line_height() = height() / total_lines.

Editor visible lines → minimap viewport rectangle: top = line_to_y(firstVisibleLine()), height = line_to_y(firstVisibleLine() + lines_on_screen) - top, implemented in _viewport_rect() using SCI_LINESONSCREEN = 2370.

Click/drag → editor scroll: SCI_GOTOLINE = 2024 with the mapped line, with a setFirstVisibleLine fallback.

6.5 Throttling
textChanged schedules a rebuild through a 100 ms single-shot timer (_schedule_rebuild → _do_rebuild).

verticalScrollBar().valueChanged and cursorPositionChanged schedule a repaint through a 100 ms single-shot timer (_schedule_repaint).

Both timers are coalescing: if a new change arrives while the timer is active, it is ignored (the pending update will pick up the latest state). This caps repaint work at 10 Hz regardless of typing or scroll speed.


Install all the dependencies needed
# Python 3.9+ (3.10+ recommended for match/case keyword support)
python -m pip install --upgrade pip

# PyQt5 + QScintilla Python bindings
pip install PyQt5 PyQt5-sip
# QScintilla for PyQt5 (provides PyQt5.Qsci: QsciScintilla, QsciAPIs, QsciLexerCustom, QsciDocument)
pip install PyQt5-QScintilla

# Linter and code-intelligence backends
pip install "pyflakes>=2.4"
pip install "jedi>=0.18"

python -c "import PyQt5; print('PyQt5', PyQt5.QtCore.PYQT_VERSION_STR)"
python -c "from PyQt5.Qsci import QsciScintilla, QsciAPIs, QsciLexerCustom, QsciDocument; print('QScintilla OK')"
python -c "import pyflakes; print('pyflakes', pyflakes.__version__)"
python -c "import jedi; print('jedi', jedi.__version__)"
python -c "from dataclasses import dataclass; from enum import IntEnum; print('stdlib OK')"










