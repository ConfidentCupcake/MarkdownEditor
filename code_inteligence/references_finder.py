from PyQt5.QtCore import QThread, pyqtSignal
from jedi import Script


class ReferencesFinder(QThread):
    """Find references without blocking, replaying only the newest request."""

    references_found = pyqtSignal(int, list)
    references_empty = pyqtSignal(int)
    error = pyqtSignal(int, str)

    def __init__(self):
        super().__init__(None)
        self._generation = 0
        self._request = None
        self._pending = None
        self._shutting_down = False
        self.finished.connect(self._start_pending)

    def find_references(self, line, column, code, file_path=None):
        if self._shutting_down:
            return None
        self._generation += 1
        request = (self._generation, line, column, code, file_path)
        if self.isRunning():
            self._pending = request
        else:
            self._request = request
            self.start()
        return self._generation

    def _start_pending(self):
        if self._shutting_down or self._pending is None:
            return
        self._request, self._pending = self._pending, None
        self.start()

    def run(self):
        generation, line, column, code, file_path = self._request
        try:
            names = Script(code=code, path=file_path).get_references(
                line=line, column=column, include_builtins=False
            )
            if self._shutting_down:
                return
            references = [
                (str(name.module_path), name.line - 1, name.column, name.name)
                for name in names
                if name.module_path is not None
            ]
            if references:
                self.references_found.emit(generation, references)
            else:
                self.references_empty.emit(generation)
        except Exception as exc:
            if not self._shutting_down:
                self.error.emit(generation, str(exc))

    def shutdown(self):
        """Reject future/results requests; completion remains asynchronous."""
        self._shutting_down = True
        self._pending = None
        self.requestInterruption()
