from PyQt5.QtCore import QThread, pyqtSignal
from jedi import Script


class HoverHelper(QThread):
    """Run Jedi hover lookups while always replaying the newest request."""

    hover_info_ready = pyqtSignal(int, str)
    hover_info_empty = pyqtSignal(int)

    def __init__(self):
        super().__init__(None)
        self._generation = 0
        self._request = None
        self._pending = None
        self._shutting_down = False
        self.finished.connect(self._start_pending)

    def get_hover(self, line: int, column: int, code: str, file_path: str = None):
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
            results = Script(code=code, path=file_path).help(line=line, column=column)
            if self._shutting_down:
                return
            if not results:
                self.hover_info_empty.emit(generation)
                return
            name = results[0]
            parts = [name.name] if name.name else []
            if name.type:
                parts.append(f"({name.type})")
            try:
                docstring = name.docstring()
            except (AttributeError, TypeError):
                docstring = name.docstring_raw()
            if docstring:
                parts.append("\n" + docstring)
            if parts:
                self.hover_info_ready.emit(generation, "\n".join(parts))
            else:
                self.hover_info_empty.emit(generation)
        except Exception:
            if not self._shutting_down:
                self.hover_info_empty.emit(generation)

    @property
    def generation(self):
        return self._generation

    def shutdown(self):
        self._shutting_down = True
        self._pending = None
        self.requestInterruption()
