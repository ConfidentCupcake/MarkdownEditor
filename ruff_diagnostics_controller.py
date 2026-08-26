"""Coordinate new Ruff worker, revision tracking, visiualiser and fixes."""

from pathlib import Path
from PyQt5.QtCore import QObject, QTimer
from PyQt5.QtWidgets import QMenu, QToolTip
from ruff_diagnostics_model import RuffFix
from ruff_diagnostics_worker import RuffDiagnosticsWorker
from ruff_diagnostics_view import RuffDiagnosticView

class RuffDiagnosticsController(QObject):
    """Standalone lifecycle owner for all Ruff diagnostics in one PythonEditor."""
    
    def __init__(self, editor, python_executable: str):
        """Attach a new timer, worker, and visualiser without old diagnostics code."""
        super().__init__(editor)
        self.editor = editor
        self.revision = 0
        self.view = RuffDiagnosticView(editor)
        self.worker = RuffDiagnosticsWorker(python_executable, self)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self._submit)
        self.worker.completed.connect(self._completed)
        self.worker.failed.connect(self._failed)
        editor.textChanged.connect(self.changed)
        
    def changed(self):
        """Advance revision and deboounce one new Ruff request after user typing."""
        self.revision += 1
        self.timer.start()
        
    def initial_check(self):
        """Schedule diagnostics after a file was loaded with blocked signals."""
        self.changed()
        
    def _logical_path(self) -> Path:
        """Provide Ruff configuration discovery path for saved or untitled tabs."""
        return self.editor.full_path or (Path.cwd() / "untitled.py")
        
    def _submit(self):
        """Send current source and revision to worker after debounce expires."""
        self.worker.request(self.editor.text(), self._logical_path(), self.revision)
        
    def _completed(self, diagnostics, revision: int):
        """Render only results matching the current document revision."""
        if revision == self.revision:
            self.view.render(diagnostics)
        
    def _failed(self, message: str, revision: int):
        """Report Ruff infrastructure errors without painting stale diagnostics."""
        if revision == self.revision:
            print(f"Ruff diagnostics failed: {message}")
            
    def hover(self, position):
        """Show a Ruff-only hover card for the diagnostic line under mouse."""
        byte_position = self.editor.SendScintilla(2022, position.x(), position.y())
        if byte_position < 0:
            QToolTip.hideText()
            return
        prefix = self.editor.text().encode("utf-8")[:byte_position].decode("utf-8", errors="ignore")
        diagnostic = self.view.at_line(prefix.count("\n"))
        if diagnostic:
            QToolTip.showText(self.editor.mapToGlobal(position), self.view.tooltip(diagnostic), self.editor)
        else:
            QToolTip.hideText()
            
    def context_menu(self, event) -> bool:
        """Show user-approved Ruff fix actions; return True ir menu handled."""
        byte_position = self.editor.SendScintilla(2022, event.pos().x(), event.pos().y())
        if byte_position < 0:
            return False
        
        prefix = self.editor.text().encode("utf-8")[:byte_position].decode("utf-8", errors="ignore")
        diagnostic = self.view.at_line(prefix.count("\n"))
        if not diagnostic or not diagnostic.fix or diagnostic.revision != self.revision:
            return False
        
        menu = QMenu(self.editor)
        action = menu.addAction(diagnostic.fix.title)
        action.triggered.connect(lambda: self.apply_fix(diagnostic.fix, diagnostic.revision))
        menu.exec_(event.globalPos())
        return True
        
    def apply_fix(self, fix: RuffFix, revision: int):
        """Apply a selected fic only if source has not changed since analysis."""
        if revision != self.revision:
            return
        self.editor.beginUndoAction()
        try:
            for edit in sorted(fix.edits, key=lambda item: (item.start.line, item.start.column), reverse=True):
                self.editor.setSelection(edit.start.line, edit.start.column, edit.end.line, edit.end.column)
                self.editor.replace(edit.content or "")
        finally:
            self.editor.endUndoAction()
            
    def shutdown(self):
        """Stop new Ruff work and erase only the standalone Ruff visual layer."""
        self.timer.stop()
        self.worker.shutdown()
        self.view.clear()
        
        
        
        
        
        
        
        
        