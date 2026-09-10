from PyQt5.QtCore import QThread, pyqtSignal
from jedi import Script


class DefinitionFinder(QThread):
    """Find definitions asynchronously and replay the most recent request."""

    definition_found = pyqtSignal(int, str, int, int)
    definition_not_found = pyqtSignal(int)
    error = pyqtSignal(int, str)

    def __init__(self):
        super().__init__(None)
        self._generation = 0
        self._request = None
        self._pending = None
        self._shutting_down = False
        self.finished.connect(self._start_pending)

    def find(self, line: int, column: int, code: str, file_path: str = None):
        if self._shutting_down:
            return
        self._generation += 1
        request = (self._generation, line, column, code, file_path)
        if self.isRunning():
            self._pending = request
            return
        self._request = request
        self.start()

    def invalidate(self):
        self._generation += 1
        self._pending = None

    def _start_pending(self):
        if self._shutting_down or self._pending is None:
            return
        self._request, self._pending = self._pending, None
        self.start()

    def run(self):
        generation, line, column, code, file_path = self._request
        try:
            definitions = Script(code=code, path=file_path).goto(
                line=line, column=column, follow_imports=True
            )
            if self._shutting_down:
                return
            if not definitions or definitions[0].module_path is None:
                self.definition_not_found.emit(generation)
                return
            definition = definitions[0]
            self.definition_found.emit(
                generation, str(definition.module_path), definition.line, definition.column
            )
        except Exception as exc:
            if not self._shutting_down:
                self.error.emit(generation, str(exc))

    @property
    def generation(self):
        return self._generation

    def shutdown(self):
        self._shutting_down = True
        self._pending = None
        self.requestInterruption()
        if self.isRunning():
            self.wait(2000)
