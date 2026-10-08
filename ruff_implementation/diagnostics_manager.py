from PyQt5.QtCore import QObject, pyqtSignal

from ruff_implementation.diagnostic_model import Diagnostic

class DiagnosticsManager(QObject):
    """Store complete diagnostic snapshot bv provider and document."""
    
    changed = pyqtSignal(list)
    
    def __init__(self, parent=None):
        """Create an empty provider/document snapshot map owned by the GUI thread."""
        super().__init__(parent)
        self._collections: dict[tuple[str, str], tuple[Diagnostic, ...]] = {}
        
    def replace(self, provider:str, uri: str, diagnostics: list[Diagnostic]) -> None:
        """Validate snapshot identity and replace or remove exactly one provider/document collection."""
        if any(item.provider != provider or item.uri != uri for item in diagnostics):
            raise ValueError("Diagnostic collection identity does not match its contents")
        key = (provider, uri)
        if diagnostics:
            self._collections[key] = tuple(diagnostics)
        else:
            self._collections.pop(key, None)
        self.changed.emit(self.all())
        
    def clear_document(self, uri: str) -> None:
        """Remove every provider collection belogning to one document URI."""
        self._collections = {key: value for key, value in self._collections.items() if key[1] != uri}
        self.changed.emit(self.all())
        
    def clear_provider(self, provider: str) -> None:
        """Remove a provider after its environment server instance changes."""
        self._collections = {key: value for key, value in self._collections.items() if key[0] != provider}
        self.changed.emit(self.all())
        
    def all(self) -> list[Diagnostic]:
        """Return a deterministic severity/path/position ordering of all retained diagnostics."""
        values = [item for group in self._collections.values() for item in group]
        return sorted(values, key=lambda item: (-int(item.severity), str(item.path or ""), item.source_range.start.line, item.source_range.start.column))
    
    