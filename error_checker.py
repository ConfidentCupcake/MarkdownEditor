import io 
from PyQt5.QtCore import QThread, pyqtSignal

from diagnostics import Diagnostic, DiagnosticSeverity, classify_severity

class ErrorChecker(QThread):
    """
    Background threat that runs pyflake on the current file content.
    Signals:
    - diagnostics_found(list, int) -> list of Diagnostic objects + request id
    - errors_cleared(int)            -> no diagnostics found (+ request id)
    Legacy signals kept for backward compatibility:
    - error_found(list) = list of Diagnostic objects (always emitted with diagnostics_found so old slots will still work)
    """
    