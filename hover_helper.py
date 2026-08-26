from PyQt5.QtCore import QThread, pyqtSignal
from jedi import Script

class HoverHelper(QThread):
    """
    Background thread that retrieves hover information for
    the symbol at a given position using Jedi.
    """
    hover_info_ready = pyqtSignal(str)
    hover_info_empty = pyqtSignal()
    
    def __init__(self):
        super().__init__(None)
        self.code = ""
        self.file_path = None
        self.line = 1
        self.column = 0
        self._shutting_down = False
        
    def get_hover(self, line: int, column: int, code: str, file_path: str = None):
        """Starts the hover lookup. Jedi uses 1-based line numbers."""
        if self.isRunning():
            return
        self.line = line
        self.column = column
        self.code = code
        self.file_path = file_path
        self.start()
        
    def run(self):
        try:
            script = Script(code=self.code, path=self.file_path)
            
            # help() returns Name objects for the symbol at the position
            help_results = script.help(line = self.line, column = self.column)
            
            if self._shutting_down:
                return None
                
            if help_results:
                name = help_results[0]
                parts = []
            
                if name.name:
                    parts.append(name.name)
                    
                if name.type:
                    parts.append(f"({name.type})")
                
                # Jedi 0.20+: use docstring(), older: use docstring_raw()
                docstring = ""
                try:
                    docstring = name.docstring()
                except(AttributeError, TypeError):
                    try:
                        docstring = name.docstring_raw()
                    except(AttributeError, TypeError):
                        pass
                
                if docstring:
                    parts.append("\n" + docstring)
                    
                if len(parts) > 0:
                    self.hover_info_ready.emit("\n".join(parts))
                else:
                    self.hover_info_empty.emit()
            else:
                self.hover_info_empty.emit()
        except Exception as e:
            if not self._shutting_down:
                print(f"HoverHelper error: {e}")
                self.hover_info_empty.emit()
                
    def shutdown(self):
        self._shutting_down = True
        self.requestInterruption()
        if self.isRunning():
            self.wait()
                