"""Asynchronous Python process runner used by the editor console."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from PyQt5.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, pyqtSignal


def split_user_arguments(value: str) -> list[str]:
    """Parse user-entered POSIX-style quoting into a direct argv list.

    ``QProcess`` receives a list, so quote characters group an argument but are
    not passed to the child. A malformed quote raises ``ValueError`` and is
    reported in the console instead of starting a partial command.
    """
    return shlex.split(value, posix=True)


class PythonRunner(QObject):
    """Own one QProcess and safely serialize replacement run requests."""

    output_ready = pyqtSignal(str)
    error_ready = pyqtSignal(str)
    process_finished = pyqtSignal(int)
    state_changed = pyqtSignal(str)
    process_started = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.interpreter = sys.executable
        self._pending_start = None
        self._shutting_down = False

        # Every launch gets a unique generation. The force-kill timer records
        # which generation it is allowed to kill, so it can never kill a
        # replacement process that happens to use the same QProcess object.
        self._process_generation = 0
        self._terminating_generation = None
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.setInterval(2_000)
        self._kill_timer.timeout.connect(self._kill_if_still_stopping)

        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._on_finished)
        self.process.stateChanged.connect(self._on_state_changed)
        self.process.started.connect(self.process_started.emit)
        self.process.errorOccurred.connect(self._on_process_error)

    def _start(self, arguments, cwd: Path, env: QProcessEnvironment):
        """Launch now or replace the running process asynchronously."""
        if self._shutting_down:
            return

        request = (list(arguments), str(cwd), env)
        if self.is_running():
            # Only the newest requested run matters. Termination remains
            # asynchronous so the GUI event loop never blocks.
            self._pending_start = request
            self._stop_process(clear_pending=False)
            return
        self._launch(request)

    def _launch(self, request):
        """Start one request and invalidate every predecessor kill timer."""
        arguments, cwd, env = request
        self._kill_timer.stop()
        self._terminating_generation = None
        self._process_generation += 1
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(cwd)
        self.process.start(self.interpreter, arguments)

    def _build_env(self, project_root: Path) -> QProcessEnvironment:
        """Build an unbuffered UTF-8 environment with project imports enabled."""
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        old_path = env.value("PYTHONPATH", "")
        root = str(project_root)
        env.insert("PYTHONPATH", root + (os.pathsep + old_path if old_path else ""))
        return env

    def run_file(self, path: Path, cwd: Path | None = None):
        """Run a saved Python file with its directory as the default cwd."""
        work_dir = cwd or path.parent
        self._start(["-u", str(path)], work_dir, self._build_env(work_dir))

    def run_code(self, code: str, cwd: Path | None = None):
        """Run selected Python source through ``python -c``."""
        work_dir = cwd or Path.cwd()
        self._start(["-u", "-c", code], work_dir, self._build_env(work_dir))

    def run_file_with_args(self, path: Path, args: str, cwd: Path | None = None):
        """Run a file with validated user-entered command-line arguments."""
        work_dir = cwd or path.parent
        try:
            arguments = ["-u", str(path), *split_user_arguments(args)]
        except ValueError as error:
            self.error_ready.emit(f"Invalid command-line arguments: {error}\n")
            return
        self._start(arguments, work_dir, self._build_env(work_dir))

    def run_pip(self, args: str):
        """Run pip through the selected interpreter without a command shell."""
        try:
            arguments = ["-m", "pip", *split_user_arguments(args)]
        except ValueError as error:
            self.error_ready.emit(f"Invalid pip arguments: {error}\n")
            return
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        self._start(arguments, Path.home(), env)

    def send_input(self, text: str):
        """Send one UTF-8 input line only while the child is running."""
        if self.process.state() == QProcess.Running:
            self.process.write((text + "\n").encode("utf-8"))

    def stop(self):
        """Cancel queued work and asynchronously stop the current process."""
        self._stop_process(clear_pending=True)

    def _stop_process(self, *, clear_pending: bool):
        """Request graceful termination and arm a generation-bound kill."""
        if clear_pending:
            self._pending_start = None
        if self.process.state() == QProcess.NotRunning:
            self._kill_timer.stop()
            self._terminating_generation = None
            return

        self._terminating_generation = self._process_generation
        self.process.terminate()
        self._kill_timer.start()

    def _kill_if_still_stopping(self):
        """Force-kill only the exact generation that was asked to stop."""
        same_generation = self._terminating_generation == self._process_generation
        if same_generation and self.process.state() != QProcess.NotRunning:
            self.process.kill()

    def shutdown(self):
        """Prevent new launches and stop current/queued work during app exit."""
        self._shutting_down = True
        self._stop_process(clear_pending=True)

    def _read_stdout(self):
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
        self.output_ready.emit(data)

    def _read_stderr(self):
        data = bytes(self.process.readAllStandardError()).decode("utf-8", "replace")
        self.error_ready.emit(data)

    def _on_finished(self, exit_code, _exit_status):
        """Retire the completed generation, then launch the newest request."""
        self._kill_timer.stop()
        self._terminating_generation = None
        self.process_finished.emit(exit_code)
        if self._shutting_down or self._pending_start is None:
            return
        request, self._pending_start = self._pending_start, None
        self._launch(request)

    def _on_state_changed(self, state):
        self.state_changed.emit("stopped" if state == QProcess.NotRunning else "running")

    def _on_process_error(self, _error):
        if not self._shutting_down:
            self.error_ready.emit(self.process.errorString() + "\n")

    def set_interpreter(self, path: str):
        """Select the executable used by subsequent launches."""
        self.interpreter = path

    def is_running(self) -> bool:
        """Return true for both starting and running process states."""
        return self.process.state() != QProcess.NotRunning