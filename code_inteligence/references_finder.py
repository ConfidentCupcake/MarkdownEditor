from PyQt5.QtCore import QThread, pyqtSignal
from jedi import Script

class ReferencesFinder(QThread):
    """
    Background thread that finds all references to a symbol using Jedi.
    
    Signals:
        references_found(list) -- list of (file_path, line, column, line_text) tuples
        references_empty() -- no references found
    """
    references_found = pyqtSignal(list)
    references_empty = pyqtSignal()
    
    def __init__(self):
        super().__init__(None)
        self.code = ""
        self.file_path = None
        self.line = 1
        self.column = 0
        self._shutting_down = False
    
    def find_references(self, line: int, column: int, code: str, file_path:str = None):
        """Start the refernce search. Jedi uses 1-based lines."""
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
            
            # get_references_all returns a list of Name objects
            # Each name represents a usage of the symbol - the definition itself, imports and all call sites.
            references = script.get_references_all(line=self.line, column=self.column)
            
            if self._shutting_down:
                return 
            