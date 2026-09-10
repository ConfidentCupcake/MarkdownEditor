from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import QListWidgetItem

import os
from pathlib import Path
import re

class SearchItem(QListWidgetItem):
    def __init__(self, name, full_path, lineno, end, line):
        self.name = name
        self.full_path = full_path
        self.lineno = lineno
        self.end = end
        self.line = line
        self.formatted = f'{self.name}:{self.lineno + 1}:{self.end + 1} - {self.line} ...'
        super().__init__(self.formatted)


    def __str__(self):
        return self.formatted

    def __repr__(self):
        return self.formatted


class SearchWorker(QThread):
    results_ready = pyqtSignal(int, list)

    def __init__(self):
        super(SearchWorker, self).__init__(None)
        self.items = []
        self.search_path: str = None
        self.search_text: str = None
        self.search_project: bool = None
        self.generation = 0
        self._pending = None
        self.finished.connect(self._start_pending)

    def is_binary(self, path):
            '''
            Check if file is binary
            '''
            with open(path, 'rb') as f:
                return b'\0' in f.read(1024)

    def walkdir(self, path, exclude_dirs: list, exclude_files: list):
        for root, dirs, files, in os.walk(path, topdown=True):
            # filtering
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            files[:] = [f for f in files if Path(f).suffix not in exclude_files]
            yield root, dirs, files

    def search(self):
        debug = False
        self.items = []

        # Guard: empty search text matches everything, which is useless
        # and floods the results list.
        if not self.search_text or not self.search_text.strip():
            self.results_ready.emit(self.generation, [])
            return

        # you can add more
        exclude_dirs = {
            ".git", ".svn", ".hg", ".bzr", ".idea", ".vscode",
            "__pycache__", "venv", ".venv", "env", "build", "dist",
        }
        exclude_files = {
            ".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico",
            ".exe", ".dll", ".pyd", ".so", ".pyc", ".qm",
        }

        # Snapshot the search parameters at the start so mutations from
        # update() during a running search don't cause inconsistent state.
        pattern = self.search_text
        search_path = self.search_path
        try:
            reg = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            if debug: print(e)
            self.results_ready.emit(self.generation, [])
            return

        for root, _, files in self.walkdir(search_path, exclude_dirs, exclude_files):
            # total search limit
            if len(self.items) > 5_000:
                break
            for file_ in files:
                full_path = os.path.join(root, file_)
                try: 
                    if self.is_binary(full_path):
                        continue
                    with open(full_path, 'r', encoding='utf8') as f:
                        try:
                            for i, line in enumerate(f):
                                if m := reg.search(line):
                                    fd = SearchItem(
                                        file_,
                                        full_path,
                                        i,
                                        m.end(),
                                        line[m.start():].strip()[:50],
                                    )
                                    self.items.append(fd)
                        except re.error as e:
                            if debug: print(e)
                except (OSError, UnicodeError) as e:
                    if debug: print(e)
                    continue

        self.results_ready.emit(self.generation, self.items)

    def run(self):
        self.search()

    def update(self, pattern, path, search_project):
        self.generation += 1
        request = (self.generation, pattern, path, search_project)
        if self.isRunning():
            self._pending = request
            return
        self._start_request(request)

    def _start_request(self, request):
        self.generation, self.search_text, self.search_path, self.search_project = request
        self.start()

    def _start_pending(self):
        if self._pending is not None:
            request, self._pending = self._pending, None
            self._start_request(request)
