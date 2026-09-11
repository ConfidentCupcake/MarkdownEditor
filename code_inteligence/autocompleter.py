from PyQt5.QtCore import QThread, pyqtSignal
from jedi import Script


class AutoCompleter(QThread):
    completions_ready = pyqtSignal(int, list)
    error = pyqtSignal(str)

    def __init__(self, file_path, api=None):
        super().__init__(None)
        self.file_path = file_path
        self.api = api
        self._generation = 0
        self._running_generation = 0
        self._request = (1, 0, "")
        self._pending = None
        self._shutting_down = False
        self.finished.connect(self._start_pending)

    def get_completions(self, line, index, text):
        if self._shutting_down:
            return None
        self._generation += 1
        request = (self._generation, line, index, text)
        if self.isRunning():
            self._pending = request
        else:
            self._start_request(request)
        return self._generation

    def invalidate(self):
        """Invalidate a running result when the editor revision/position changes."""
        self._generation += 1
        self._pending = None

    def _start_request(self, request):
        generation, line, index, text = request
        lines = text.splitlines() or [""]
        line = max(1, min(line, len(lines)))
        index = max(0, min(index, len(lines[line - 1])))
        self._running_generation = generation
        self._request = (line, index, text)
        self.start()

    def _start_pending(self):
        if not self._shutting_down and self._pending is not None:
            request, self._pending = self._pending, None
            self._start_request(request)

    def run(self):
        try:
            line, index, text = self._request
            names = [item.name for item in Script(
                code=text, path=self.file_path
            ).complete(line, index)]
            if not self._shutting_down:
                self.completions_ready.emit(self._running_generation, names)
        except Exception as error:
            if not self._shutting_down:
                self.error.emit(str(error))

    def shutdown(self):
        self._shutting_down = True
        self._pending = None
        self.requestInterruption()

    @property
    def generation(self):
        return self._generation
