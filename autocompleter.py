from PyQt5.QtCore import QThread, pyqtSignal
from jedi import Script


class AutoCompleter(QThread):
    completions_ready = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, file_path, api=None):
        super(AutoCompleter, self).__init__(None)
        self.file_path = file_path
        self.api = api
        self.line = 1
        self.index = 0
        self.text = ""

    def run(self):
        try:
            script = Script(code=self.text, path=self.file_path)
            names = [c.name for c in script.complete(self.line, self.index)]
            self.completions_ready.emit(names)
        except Exception as err:
            self.error.emit(str(err))

    def get_completions(self, line: int, index: int, text: str):
        if self.isRunning():
            return
        lines = text.splitlines() or [""]
        safe_line = max(1, min(line, len(lines)))
        safe_index = max(0, min(index, len(lines[safe_line - 1])))
        self.line = safe_line
        self.index = safe_index
        self.text = text
        self.start()
