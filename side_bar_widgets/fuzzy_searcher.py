import os
import re
from dataclasses import dataclass
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import QListWidgetItem


MAX_RESULTS = 5_000
EXCLUDED_DIRS = {
    ".git", ".svn", ".hg", ".bzr", ".idea", ".vscode",
    "__pycache__", "venv", ".venv", "env", "build", "dist",
}
EXCLUDED_SUFFIXES = {
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico",
    ".exe", ".dll", ".pyd", ".so", ".pyc", ".qm",
}


class SearchItem(QListWidgetItem):
    """One exact match, with start/end columns for correct navigation."""

    def __init__(self, name, full_path, lineno, start, end, line):
        self.name = name
        self.full_path = full_path
        self.lineno = lineno
        self.start = start
        self.end = end
        self.line = line
        self.formatted = f"{name}:{lineno + 1}:{start + 1} - {line}"
        super().__init__(self.formatted)

    def __str__(self):
        return self.formatted

    def __repr__(self):
        return self.formatted


class SearchWorker(QThread):
    results_ready = pyqtSignal(int, list, bool)
    search_error = pyqtSignal(int, str)

    def __init__(self):
        super().__init__(None)
        self.generation = 0
        self._request = None
        self._pending = None
        self._shutting_down = False
        self.finished.connect(self._start_pending)

    @staticmethod
    def _is_binary(path):
        with open(path, "rb") as handle:
            return b"\0" in handle.read(1024)

    @staticmethod
    def _walk(path, include_modules):
        """Walk project files; optional module mode includes hidden module dirs.

        The old checkbox had no semantics. Here unchecked search omits all
        hidden directories; checked search includes hidden directories except
        explicit VCS/cache/environment exclusions.
        """
        for root, dirs, files in os.walk(path, topdown=True):
            dirs[:] = [
                directory
                for directory in dirs
                if directory not in EXCLUDED_DIRS
                and (include_modules or not directory.startswith("."))
            ]
            yield root, files

    def update(self, text, path, include_modules, regex=False, case_sensitive=False):
        if self._shutting_down:
            return
        self.generation += 1
        request = (
            self.generation,
            text,
            str(path),
            bool(include_modules),
            bool(regex),
            bool(case_sensitive),
        )
        if self.isRunning():
            self._pending = request
        else:
            self._start_request(request)

    def _start_request(self, request):
        self._request = request
        self.start()

    def _start_pending(self):
        if not self._shutting_down and self._pending is not None:
            request, self._pending = self._pending, None
            self._start_request(request)

    def shutdown(self):
        self._shutting_down = True
        self._pending = None
        self.requestInterruption()

    def run(self):
        generation, text, path, include_modules, regex_mode, case_sensitive = self._request
        if not text or not text.strip():
            self.results_ready.emit(generation, [], False)
            return

        expression = text if regex_mode else re.escape(text)
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(expression, flags)
        except re.error as error:
            self.search_error.emit(generation, str(error))
            return

        items = []
        for root, files in self._walk(path, include_modules):
            if self._shutting_down or self.isInterruptionRequested():
                return
            for file_name in files:
                file_path = Path(root) / file_name
                if file_path.suffix.lower() in EXCLUDED_SUFFIXES:
                    continue
                try:
                    if self._is_binary(file_path):
                        continue
                    with file_path.open("r", encoding="utf-8") as handle:
                        for line_number, line in enumerate(handle):
                            if self._shutting_down or self.isInterruptionRequested():
                                return
                            for match in pattern.finditer(line):
                                # Zero-length matches are not useful navigation
                                # targets and can create enormous result sets.
                                if match.start() == match.end():
                                    continue
                                items.append(
                                    SearchItem(
                                        file_name,
                                        str(file_path),
                                        line_number,
                                        match.start(),
                                        match.end(),
                                        line.strip()[:80],
                                    )
                                )
                                if len(items) >= MAX_RESULTS:
                                    self.results_ready.emit(generation, items, True)
                                    return
                except (OSError, UnicodeError):
                    continue
        self.results_ready.emit(generation, items, False)