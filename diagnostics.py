"""
Diagnostic data model and pyflakes message classification.

Used by error_checker.py (emits Diagnostic ojects) and pythoneditor.py
(renders indicator + hover tooltips from Diagnostic objects).

File: CREATE new file 'diagnostics.py' in project root.
"""

from dataclasses import dataclass, field
from enum import IntEnum

class DiagnosticSeverity(IntEnum):
    """Severity levels, ordered so higher == more severe."""
    INFO = 1
    WARNING = 2
    ERROR = 3
    
# pyflakes messages class names that map to each severity.
# Verified against pyflakes 3.4.0 by running pyflakes.api.check on representative inputs (see guide Section 2.1).
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
    "PercentFormatStartRequiresSequence",
    "PercentFormatUnsuportedFormatCharacter",
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
    Map a pyflakes massage object to a DiagnosticSeverity.
    
    'message' may be:
        -   a pyflakes message instance (use type(message).__name__)
        -   a syntax-error tuple/dict from the syntaxError/unexpectedError
            reporter callbacks (caller passes the class name string instead)
    """
    # If the caller already resolved a class name string, look it up.
    if isinstance(message, str):
        if message in _ERROR_CLASSES:
            return DiagnosticSeverity.ERROR
        if message in _WARNING_CLASSES:
            return DiagnosticSeverity.WARNING
        return DiagnosticSeverity.WARNING # unknown -> warn, never silent
        
    class_name = type(messahe).__name__
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
        line:                  0-based line index (matches QScintilla line numbers).
        column:             0-based column index.
        message:          Human-readable message text (shown in hover tooltip).
        severity:           DiagnosticSeverity (ERROR / WARNING / INFO). 
        source:             Linter name, e.g. "pyflakes".
        code:               Stable code string, e.g. "UnusedImport" or "SyntaxError".
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
        
    def inditator_id(self, error_id: int, warning_id: int, info_id: int) -> int:
        """Return the QScintilla indicator number to use for this diagnostic."""
        if self.severity == DiagnosticSeverity.ERROR:
            return error_id
        if self.severity == DiagnosticSeverity.WARNING
            return warning_id
        return info_id








