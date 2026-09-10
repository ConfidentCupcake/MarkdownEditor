import subprocess
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

class GitStatusChecker(QThread):
    """
    Background thread running 'git status --porcelain'.
    
    Porcelain format:
        "XY filename" where X = staged, Y = working tree.
        " M" modified   "M " staged-modified    "A " staged-new
        "??" untracked  "D " / " D" deleted     "R " renamed (steaged)
    Docs: https://git-scm.com/git-status#_porcelain_format
    """
    
    status_ready = pyqtSignal(dict) # {absolute_path: "XY"}
    
    def __init__(self):
        super().__init__(None)
        self.repo_path = ""
        self._shutting_down = False
        
    def check(self, repo_path: str):
        if self.isRunning() or not repo_path:
            return
        # BUGFIX: was "self._repo_path = repo_path" (leading underscore),
        # but run()/_git() read "self.repo_path". The mismatch left
        # repo_path at "" forever, so every git command ran with cwd=""
        # -> FileNotFoundError -> silently swallowed -> status always {}.
        self.repo_path = repo_path
        self.start()
        
    def shutdown(self):
        self._shutting_down = True
        self.requestInterruption()
        if self.isRunning():
            self.wait(2000)
            
    def _git(self, *args):
        """Run a git command in repo_path. Returns stdout or None."""
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        if result.returncode != 0:
            return None
        return result.stdout
    
    def run(self):
        if self._shutting_down:
            return
        try:
            # Nit a repo (or git missing) -> empty status
            toplevel = self._git("rev-parse", "--show-toplevel")
            if toplevel is None:
                self.status_ready.emit({})
                return
            repo_root = Path(toplevel.strip())
            if self._shutting_down or self.isInterruptionRequested():
                return
            
            output = self._git("status", "--porcelain")
            if output is None:
                # BUGFIX: was "self.status.ready.emit({})" — a typo that
                # raised AttributeError and relied on the blanket except
                # below to emit the empty status anyway.
                self.status_ready.emit({})
                return
            
            statuses = {}
            
            for line in output.splitlines():
                if len(line) < 4:
                    continue
                
                code = line[:2]
                filepath = line[3:].strip('"')
                if " -> " in filepath:
                    filepath = filepath.split(" -> ", 1)[1]
                # Porcelain paths are relative to the REPO root, which mey
                # be a parent fo the folder opened in the FileManager.
                statuses[str((repo_root / filepath).resolve())] = code
            
            self.status_ready.emit(statuses)
            
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            self.status_ready.emit({})
            
        except Exception:
            self.status_ready.emit({})
            
            