import sys
from pathlib import Path
import shlex

from PyQt5.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, pyqtSignal

class PythonRunner(QObject):
    output_ready = pyqtSignal(str)
    error_ready = pyqtSignal(str)
    process_finished = pyqtSignal(int)
    state_changed = pyqtSignal(str)
    process_started = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.interpreter = sys.executable # gives the path to the Python interpreter currently running the app.
        self._pending_start = None

        self.process.readyReadStandardOutput.connect(self._read_stdout) # New data arrives on stdout
        self.process.readyReadStandardError.connect(self._read_stderr) # New data arrives on stderr
        self.process.finished.connect(self._on_finished) # The subprocess exists
        self.process.stateChanged.connect(self._on_stage_changed) # Running/Stopped transition
        self.process.started.connect(self.process_started.emit)
        self.process.errorOccurred.connect(
            lambda _error: self.error_ready.emit(self.process.errorString() + "\n")
        )

    def _start(self, arguments, cwd: Path, env: QProcessEnvironment):
        request = (arguments, str(cwd), env)
        if self.is_running():
            self._pending_start = request
            self.stop()
            return
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(cwd))
        self.process.start(self.interpreter, arguments)
        
    def run_file(self, path: Path, cwd: Path = None):
        """Run a .py file with unbuffered output."""
        work_dir = cwd or path.parent
        self._start(["-u", str(path)], work_dir, self._build_env(work_dir))
        
        
    def _build_env(self, project_root: Path) -> QProcessEnvironment:
        env = QProcessEnvironment.systemEnvironment() #Starts with a copy of the current system environment. Important for (PATH, TEMP, HOME)
        env.insert("PYTHONUNBUFFERED", "1") # This does the same thing as the -u flag. Some C extensions check the env var rather than the comment-line flag.
        env.insert("PYTHONIOENCODING", "utf-8") # Forces Python to use UTF-8 for stdout/stderr. Without this Windows might throw a UnicodEncodeErr
        
        # Add a project root to PYTHONPATH so local imports work
        root_str = str(project_root)
        old_pp = env.value("PYTHONPATH", "") 
        sep = ";" if sys.platform == "win32" else ":"
        env.insert("PYTHONPATH", root_str + (f"{sep}{old_pp}" if old_pp else ""))
        
        return env
        
    def run_code(self, code: str, cwd: Path = None):
        """Run a code string via python -u -c 'code'."""
        work_dir = cwd or Path.cwd()
        self._start(["-u", "-c", code], work_dir, self._build_env(work_dir))
        
    def send_input(self, text: str):
        """Send user input to the running process's stdin."""
        if self.process.state() == QProcess.Running:
            self.process.write((text + "\n").encode("utf-8"))
            
    def stop(self):
        """Stop the process without blocking the GUI event loop."""
        if self.process.state() != QProcess.NotRunning:
            self.process.terminate()
            QTimer.singleShot(
                2000,
                lambda: self.process.kill()
                if self.process.state() != QProcess.NotRunning else None,
            )
            
    def _read_stdout(self):
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.output_ready.emit(data)
        
    def _read_stderr(self):
        data = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        self.error_ready.emit(data)
        
    def _on_finished(self, exit_code, _exit_status):
        self.process_finished.emit(exit_code)
        if self._pending_start is not None:
            arguments, cwd, env = self._pending_start
            self._pending_start = None
            self.process.setProcessEnvironment(env)
            self.process.setWorkingDirectory(cwd)
            self.process.start(self.interpreter, arguments)
    
    def _on_stage_changed(self, state):
        if state == QProcess.NotRunning:
            self.state_changed.emit("stopped")
        else:
            self.state_changed.emit("running")
            
    def set_interpreter(self, path: str):
        """Set the Python executable to use (e.g. a venv python.exe)"""
        self.interpreter = path
    
    def is_running(self) -> bool:
        return self.process.state() != QProcess.NotRunning

    def run_file_with_args(self, path: Path, args: str, cwd: Path = None):
        """
        Run a .py file with command-line arguments

        `args` is a string like "--verbose --output result.txt"
        It gets split into a list: ["--verbose", "--output", "result.txt"]
        The Final command is: python -u script.py --verbose --output result.txt
        :param path:
        :param args:
        :param cwd:
        :return:
        """
        import shlex

        work_dir = cwd or path.parent
        env = self._build_env(work_dir)
        # Build the argument list: ["-u", "script.py", "--verbose", "--output", "result.txt"]
        # shlex.split parses the args stings the same way a shell would:
        # '--output "my file.txt"' → ["--output", "my file.txt"]
        # (preserves quoted strings with spaces)
        # Docs: https://docs.python.org/3/library/shlex.html#shlex.split
        arg_list = ["-u", str(path)] + shlex.split(args, posix=sys.platform != "win32")
        self._start(arg_list, work_dir, env)
    
    def run_pip(self, args: str):
        """
        Run a pip command using the selected interpreter.
        
        Examples:
            args = "install requests"           -> python -m pip install requests
            args = "uninstall numpy"           -> python -m pip uninstall numpy
            args = "list"           -> python -m pip list
         
        We use 'python -m pip' instead of calling pip.exe directly
        because pip.exe might not be on the PATH, but 'python -m pip'
        always works as long as the interpreter has pip installed.
        """
        import os
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        
        self._start(
            ["-m", "pip"] + shlex.split(args, posix=sys.platform != "win32"),
            Path(os.path.expanduser("~")),
            env,
        )
