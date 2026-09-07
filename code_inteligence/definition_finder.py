from jedi import Script
from PyQt5.QtCore import QThread, pyqtSignal

class DefinitionFinder(QThread):
    """
    Background thread that finds where a symbol is defined using Jedi.
    Runs in a separate thread so the GUI doesn't freeze during analysis.
    """
    definition_found = pyqtSignal(str, int, int) # module_path, line, column
    definition_not_found = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__(None)
        self.code = ""
        self.file_path = None
        self.line = 1
        self.column = 0
        self._shutting_down = False

    def find(self, line: int, column: int, code: str, file_path: str = None):
        """Start the search, Jedi uses 1-based line numbers."""
        if self.isRunning():
            return

        self.line = line
        self.column = column
        self.code = code
        self.file_path = file_path
        self.start()

    def run(self):
        try:
            script = Script(code = self.code, path = self.file_path)

            definitions = script.goto(
                line = self.line,
                column = self.column,
                follow_imports=True
            )

            if self._shutting_down:
                return

            if definitions:
                definition = definitions[0]
                module_path = definition.module_path
                line = definition.line
                column = definition.column

                if module_path is None:
                    # Built-in or compiled module - can't open the file
                    self.definition_not_found.emit()
                else:
                    self.definition_found.emit(str(module_path), line, column)
            else:
                self.definition_not_found.emit()
        except Exception as err:
            if not self._shutting_down:
                self.error.emit(str(err))

    def shutdown(self):
        self._shutting_down = True
        self.requestInterruption()
        if self.isRunning():
            self.wait()