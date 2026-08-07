import sys
from pathlib import Path

from PyQt5.QtCore import QObject, QProcess, QProcessEnvironment, pyqtSignal

class PythonRunner(QObject):
    output_ready = pyqtSignal(str)
    error_ready = pyqtSignal(str)
    process_finished = pyqtSignal(int)
    state_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.interpreter = sys.executable # gives the path to the Python interpreter currently running the app.

        self.process.readyReadStandardOutput.connect(self._read_stdout) # New data arrives on stdout
        self.process.readyReadStandardError.connect(self._read_stderr) # New data arrives on stderr
        self.process.finished.connect(self._on_finished) # The subprocess exists
        self.process.stateChanged.connect(self._on_stage_changed) # Running/Stopped transition
        
    def run_file(self, path: Path, cwd: Path = None):
        """Run a .py file with unbuffered output."""
        if self.is_running(): # if a previous script is still running, kill it first
            self.stop()
            
        env = self._build_env(cwd or path.parent) # Build the environment
        
        self.process.setProcessEnvironment(env)  # Apply the custom environment to the subprocess
        self.process.setWorkingDirectory(str(cwd or path.parent)) # Set the working environment. If the working environment is C:\projects\myapp\main.py -> the working directory is C:\projects\myapp.
        self.process.start(self.interpreter, ["-u", str(path)]) # Takes the programm and a list of arguments = "C:\Python311\python.exe" -u "C:\projects\myapp\main.py"
        
        
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
        if self.is_running():
            self.stop()
        
        work_dir = str(cwd or Path.cwd())
        env = self._build_env(Path(work_dir))
        
        # Same as run_file but uses the -c flag. This runs a code string directly of a file.
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(work_dir)
        self.process.start(self.interpreter, ["-u", "-c", code])
        
    def send_input(self, text: str):
        """Send user input to the running process's stdin."""
        if self.process.state() == QProcess.Running:
            self.process.write((text + "\n").encode("utf-8"))
            
    def stop(self):
        """Kill the running process."""
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()
            self.process.waitForFinished(2000)
            
    def _read_stdout(self):
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.output_ready.emit(data)
        
    def _read_stderr(self):
        data = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        self.error_ready.emit(data)
        
    def _on_finished(self, exit_code, _exit_status):
        self.process_finished.emit(exit_code)
    
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

        if self.is_running():
            self.stop()

        env = self._build_env(cwd or path.parent)

        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(cwd or path.parent))
        # Build the argument list: ["-u", "script.py", "--verbose", "--output", "result.txt"]
        # shlex.split parses the args stings the same way a shell would:
        # '--output "my file.txt"' → ["--output", "my file.txt"]
        # (preserves quoted strings with spaces)
        # Docs: https://docs.python.org/3/library/shlex.html#shlex.split
        arg_list = ["-u", str(path)] + shlex.split(args)
        self.process.start(self.interpreter, arg_list)
    
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
        if self.is_running():
            self.stop()
        
        import os
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(os.path.expanduser("~"))
        
        self.process.start(self.interpreter, ["-m", "pip"] + args.split())