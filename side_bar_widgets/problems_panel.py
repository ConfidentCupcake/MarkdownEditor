from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QTreeWidget, QTreeWidgetItem

from ruff_implementation.diagnostic_model import Diagnostic, DiagnosticSeverity


class ProblemsPanel(QTreeWidget):
    """Provider-neutral, navigable diagnostic list."""
    
    diagnosticActivated = pyqtSignal(object)
    
    def __init__(self, parent=None):
        """Configure six columns and connects row activation to diagnostic navigation."""
        super().__init__(parent)
        self.setColumnCount(6)
        self.setHeaderLabels(("Severity", "File", "Line", "Column", "Code", "Message"))
        self.itemActivated.connect(self._activate)
        
    def set_diagnostics(self, diagnostics: list[Diagnostic]) -> None:
        """Replace displayed rows with a complete provider-neutral snapshot."""
        self.clear()
        labels = {
            DiagnosticSeverity.ERROR: "Error",
            DiagnosticSeverity.WARNING: "Warning",
            DiagnosticSeverity.INFORMATION: "Info",
        }
        
        for diagnostic in diagnostics:
            start = diagnostic.source_range.start
            row = QTreeWidgetItem((
                labels[diagnostic.severity],
                diagnostic.path.name if diagnostic.path else diagnostic.uri,
                str(start.line + 1), str(start.column + 1),
                diagnostic.code, diagnostic.message,
            ))
            row.setData(0, Qt.UserRole, diagnostic)
            self.addTopLevelItem(row)
            
    def _activate(self, item: QTreeWidgetItem, _column: int) -> None:
        """Emnit the diagnostic stored on the activated row without parsing visible text."""
        diagnostic = item.data(0, Qt.UserRole)
        if diagnostic is not None:
            self.diagnosticActivated.emit(diagnostic)