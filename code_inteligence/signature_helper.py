from PyQt5.QtCore import QThread, pyqtSignal
from jedi import Script


class SignatureHelper(QThread):
    """Run Jedi signature lookups and discard superseded results."""

    signature_ready = pyqtSignal(int, str)
    signature_empty = pyqtSignal(int)

    def __init__(self):
        super().__init__(None)
        self._generation = 0
        self._request = None
        self._pending = None
        self._shutting_down = False
        self.finished.connect(self._start_pending)

    def get_signatures(self, line: int, column: int, code: str, file_path: str = None):
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
            signatures = Script(code=code, path=file_path).get_signatures(
                line=line, column=column
            )
            if self._shutting_down:
                return
            if not signatures:
                self.signature_empty.emit(generation)
                return
            signature = signatures[0]
            params = [param.to_string() for param in signature.params]
            result = f"{signature.name}({', '.join(params)})"
            docstring = signature.docstring(raw=True)
            if docstring:
                result += f"\n{docstring}"
            self.signature_ready.emit(generation, result)
        except Exception:
            if not self._shutting_down:
                self.signature_empty.emit(generation)

    @property
    def generation(self):
        return self._generation

    def shutdown(self):
        self._shutting_down = True
        self._pending = None
        self.requestInterruption()
        if self.isRunning():
            self.wait(2000)
