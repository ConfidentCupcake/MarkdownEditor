"""
Split editor view: two PythonEditor panes sharing one QsciDocument.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QSplitter

from pythoneditor import PythonEditor

class SplitEditor(QSplitter):
    """
    A horizontal QSplitter that hosts one or two PythonEditor panes sharing a single QsciDocument
    
    Usage:
        split = SplitEditor(path=Path("foo.py"), is_python_file=True)
        split.split()            # shows two panes
        split.unsplit()        # collapse back to one pane
        editor = split.activate_editor()    # the currently focused pane
    """
    def __init__(self, path=None, is_python_file: bool = True, path_sec_file = None, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setChildrenCollapsible(False)
        self.setHandleWidth(1)
        
        self._path = path
        self._is_python_file = is_python_file
        self._active = None
        self._path_sec_file = path_sec_file
        
        # Primary pane always exists.
        self._primary = self._make_editor(path, is_python_file)
        self.addWidget(self._primary)
        self._active = self._primary
        self._secondary = None
        
        # Track focus to know which pane is active.
        self._primary.setFocusProxy(None)
        for ed in self._editors():
            ed.focusInEvent = self._warp_focus_in(ed.focusInEvent, ed)
            
    # --- Construction Helpers
    def _make_editor(self, path, is_python_file) -> PythonEditor:
        ed = PythonEditor(path=path, is_python_file=is_python_file)
        # Keep the splitter informed when a pane gets focus.
        ed._split_focus_handler = lambda _e=None, ed=ed: self._set_active(ed)
        # Install an event filter via a lightweight override.
        return ed
        
    def _warp_focus_in(self, original, ed):
        def handler(e):
            self._set_active(ed)
            return original(e)
        return handler
        
    def _editors(self):
        eds = [self._primary]
        if self._secondary is not None:
            eds.append(self._secondary)
        return eds
            
            
    # Public API
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
        """Create the second pane and share the primary document."""
        if self._secondary is not None:
            return
        self._secondary = self._make_editor(self._path_sec_file, self._is_python_file)
        
        self._secondary.setDocument(self._secondary.document())
        
        self.addWidget(self._secondary)
        self.setSizes([self.width() // 2, self.width() // 2])
        self._secondary.setFocus()
        self._set_active(self._secondary)
        
        
    def unsplit(self):
        """Remove the second pane; keep the primary (and its document)."""
        if self._secondary is None:
            return
        
        if self._active is self._secondary:
            self._primary.setFocus()
            self._set_active(self._primary)
            
        try:
            self._secondary.shutdown()
        except Exception:
            pass
        
        self._secondary.setParent(None)
        self._secondary.deleteLater()
        self._secondary = None
        # give the primary the full width.
        self.setSizes([self.width()])
        
        
    def toggle(self):
        if self.is_split():
            self.unsplit()
        else:
            self.split()
            
    def _set_active(self, ed: PythonEditor):
        self._active = ed
        
    def text(self) -> str:
        return self.activate_editor().text()
        
    def shutdown(self):
        for ed in self._editors():
            try:
                ed.shutdown()
            except Exception:
                pass
             
            
            
            
            
            
            
            
            
            
    