import os
import shlex
import sys
from pathlib import Path

from PyQt5.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, pyqtSignal


def split_user_arguments(value: str) -> list[str]:
    """Parse the documented POSIX-style quoting into a QProcess argument list.

    QProcess receives an argv list directly on every platform, so quotes are
    grouping syntax and must not remain in the child arguments on Windows.
    """
    return shlex.split(value, posix=True)


class PythonRunner(QObject):
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

        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._on_finished)
        self.process.stateChanged.connect(self._on_state_changed)
        self.process.started.connect(self.process_started.emit)
        self.process.errorOccurred.connect(self._on_process_error)

    def _start(self, arguments, cwd: Path, env: QProcessEnvironment):
        if self._shutting_down:
            return
        request = (list(arguments), str(cwd), env)
        if self.is_running():
            self._pending_start = request
            self._stop_process(clear_pending=False)
            return
        self._launch(request)

    def _launch(self, request):
        arguments, cwd, env = request
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(cwd)
        self.process.start(self.interpreter, arguments)

    def _build_env(self, project_root: Path) -> QProcessEnvironment:
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        old_path = env.value("PYTHONPATH", "")
        separator = os.pathsep
        root = str(project_root)
        env.insert("PYTHONPATH", root + (separator + old_path if old_path else ""))
        return env

    def run_file(self, path: Path, cwd: Path = None):
        work_dir = cwd or path.parent
        self._start(["-u", str(path)], work_dir, self._build_env(work_dir))

    def run_code(self, code: str, cwd: Path = None):
        work_dir = cwd or Path.cwd()
        self._start(["-u", "-c", code], work_dir, self._build_env(work_dir))

    def run_file_with_args(self, path: Path, args: str, cwd: Path = None):
        work_dir = cwd or path.parent
        try:
            arguments = ["-u", str(path), *split_user_arguments(args)]
        except ValueError as error:
            self.error_ready.emit(f"Invalid command-line arguments: {error}\n")
            return
        self._start(arguments, work_dir, self._build_env(work_dir))

    def run_pip(self, args: str):
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
        if self.process.state() == QProcess.Running:
            self.process.write((text + "\n").encode("utf-8"))

    def stop(self):
        """User-facing stop cancels both current and queued work."""
        self._stop_process(clear_pending=True)

    def _stop_process(self, *, clear_pending: bool):
        if clear_pending:
            self._pending_start = None
        if self.process.state() == QProcess.NotRunning:
            return
        self.process.terminate()
        QTimer.singleShot(2000, self._kill_if_running)

    def _kill_if_running(self):
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()

    def shutdown(self):
        """Application shutdown can never start a queued replacement."""
        self._shutting_down = True
        self._stop_process(clear_pending=True)

    def _read_stdout(self):
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
        self.output_ready.emit(data)

    def _read_stderr(self):
        data = bytes(self.process.readAllStandardError()).decode("utf-8", "replace")
        self.error_ready.emit(data)

    def _on_finished(self, exit_code, _exit_status):
        self.process_finished.emit(exit_code)
        if self._shutting_down or self._pending_start is None:
            return
        request, self._pending_start = self._pending_start, None
        self._launch(request)

    def _on_state_changed(self, state):
        self.state_changed.emit(
            "stopped" if state == QProcess.NotRunning else "running"
        )

    def _on_process_error(self, _error):
        if not self._shutting_down:
            self.error_ready.emit(self.process.errorString() + "\n")

    def set_interpreter(self, path: str):
        self.interpreter = path

    def is_running(self) -> bool:
        return self.process.state() != QProcess.NotRunning
