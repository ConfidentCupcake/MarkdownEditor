from PyQt5.QtCore import QThread, pyqtSignal
from jedi import Script

class SignatureHelper(QThread):
    """
    Background thread that rerieves function signature information.
    
    Signals:
        signature_ready(str) - fromatted signature string to display
        signature_empty() - no signature available
    """
    signature_ready = pyqtSignal(str)
    signature_empty = pyqtSignal()
    
    def __init__(self):
        super().__init__(None)
        self.code = ""
        self.file_path = None
        self.line = 1
        self.column = 0
        self._shutting_down = False
        
    def get_signatures(self, line: int, column: int, code: str, file_path: str = None):
        """Start signature lookup, Jedi uses 1-based lines."""
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
            # get_signatures returns a list of Signature objects.
            # Each signature represents a possible function overload.
            # For most functions there's only one signature
            # Docs: https://jedi.readthedocs.io/en/latest/docs/api.html#jedi.Script.get_signatures
            signatures = script.get_signatures(line=self.line, column=self.column)
            
            if self._shutting_down:
                return
            
            if signatures:
                sig = signatures[0]
                
                # sig.name is the function name (e.g. "my_function")
                # sig.params is a list of ParamName objects
                # Each param has: .name (string), .kind (POSITIONAL_ONLY, etc.)
                
                params = []
                for param in sig.params:
                    # Build the parameter string
                    # Some params have default values detected by Jedi
                    p_str = param.name
                    if hasattr(param, "infer_default") and param.infer_default():
                        defaults = param.infer_default()
                        if defaults:
                            p_str += f"={defaults[0].name}"
                    params.append(p_str)
                
                # Format: "function_name(param1, param2, param3)"
                signature_text = f"{sig.name}({', '.join(params)})"
                
                # If there's a docstring, add it
                docstring = sig.docstring_raw()
                if docstring:
                    signature_text += f"\n{docstring}"
                    
                self.signature_ready.emit(signature_text)
                
            else:
                self.signature_empty.emit()
        except Exception:
            if not self._shutting_down:
                self.signature_empty.emit()
    
    def shutdown(self):
        self._shutting_down = True
        self.requestInterruption()
        if self.isRunning():
            self.wait()
        