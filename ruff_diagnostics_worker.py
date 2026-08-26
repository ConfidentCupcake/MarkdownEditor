"""Background Ruff JSON worker. This module never imports QScintilla."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from ruff_diagnostics_model import RuffDiagnostic, RuffFix, RuffPosition, RuffSeverity, RuffTextEdit


class RuffDiagnosticsWorker(QThread):
    """Run Ruff on newest queued source without blocking the Qt GUI thread."""

    completed = pyqtSignal(list, int)  # diagnostics, document revision
    failed = pyqtSignal(str, int)  # message, document revision

    def __init__(self, python_executable: str, parent=None):
        """Create an idle worker tied to one selected Python interpreter."""
        super().__init__(parent)
        self.python_executable = python_executable
        self._pending = None
        self._shutting_down = False

    def request(self, source: str, logical_path: Path, revision: int):
        """
        Queue the newest source snapshot; start thread only when idle.

        A newer queued request replaces and older pending request. The controller
        also verifies revisions, so old results cannot paint new editor text.
        """
        self._pending = (source, Path(logical_path), revision)
        if not self.isRunning():
            self.start()

    def _temporary_file(self, source: str, logical_path: Path) -> Path:
        """
        Write the editor source to the operating system temporary directory.
        The file must not be created inside the project folder because QFileSystemModel watches that directory and would briefly show the temporary file in the editor's file tree.
        """
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=logical_path.suffix or ".py",
            prefix="ruff_editor_",
            delete=False,
        ) as handle:
            handle.write(source)
            return Path(handle.name)
            
    def _find_ruff_config(self, logical_path: Path) -> Path | None:
        """
        Search upward from the real editor file for a Ruff configuration file.
        
        The temporary source file lives outside the project, so Ruff cannot discover project.toml from that temporary path automatically.
        This method finds the nearest project configuratio and lets the subprocess recieve it through Ruff's --config argument.
        """
        for directory in logical_path, *logical_path.parents:
            for filename in "pyproject.toml", "ruff.toml", ".ruff.toml":
                candidate = directory / filename
                if candidate.is_file():
                    return candidate
                    
        return None

    def _parse_fix(self, raw_fix: dict | None) -> RuffFix | None:
        """Convert Ruff JSON fix edits to zero-based immutable edit objects."""
        if not raw_fix:
            return None
        edits = []
        for raw_edit in raw_fix.get("edits") or []:
            start = raw_edit["location"]
            end = raw_edit["end_location"]
            edits.append(
                RuffTextEdit(
                    RuffPosition(start["row"] - 1, start["column"] - 1),
                    RuffPosition(end["row"] - 1, end["column"] - 1),
                    raw_edit.get("content"),
                )
            )
        return RuffFix(
            raw_fix.get("message") or "Apply Ruff fix", raw_fix.get("applicability"), tuple(edits)
        )

    def _parse_diagnostic(self, raw: dict, revision: int) -> RuffDiagnostic:
        """Map one Ruff JSON object to the standalone model."""
        start = raw.get("location") or {"row": 1, "column": 1}
        end = raw.get("end_location") or start
        code = raw.get("code") or "Ruff"
        severity = RuffSeverity.ERROR if code.startswith(("E", "F")) else RuffSeverity.WARNING
        return RuffDiagnostic(
            code=code,
            message=raw.get("message", "Ruff diagnostic"),
            severity=severity,
            start=RuffPosition(start["row"] - 1, start["column"] - 1),
            end=RuffPosition(end["row"] - 1, end["column"] - 1),
            revision=revision,
            fix=self._parse_fix(raw.get("fix")),
            raw=raw,
        )

    def run(self):
        """Run Ruff diagnostics for queued editor snapshots."""
        while self._pending is not None and not self._shutting_down:
            source, logical_path, revision = self._pending
            self._pending = None
            temporary_path = self._temporary_file(source, logical_path)
            try:
                config_path = self._find_ruff_config(logical_path)
                command = [
                    self.python_executable, "-m", "ruff", "check",
                    "--output-format", "json", "--no-cache",
                ]
                if config_path is not None:
                    command.extend(["--config", str(config_path)])
                command.append(str(temporary_path))
                result = subprocess.run(
                    command, text=True, capture_output=True,
                    cwd=str(logical_path.parent), timeout=10, check=False,
                )
                raw_items = json.loads(result.stdout or "[]")
                if self._pending is None:
                    diagnostics = [
                        self._parse_diagnostic(item, revision)
                        for item in raw_items
                    ]
                    self.completed.emit(diagnostics, revision)
            except Exception as error:
                if self._pending is None:
                    self.failed.emit(str(error), revision)
            finally:
                temporary_path.unlink(missing_ok=True)

    def shutdown(self):
        """Stop accepting useful work and wait for current process thread exit."""
        self._shutting_down = True
        self._pending = None
        if self.isRunning():
            self.wait()
