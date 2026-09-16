"""Asynchronously retrieve bounded Git history without invoking a shell."""

from pathlib import Path
import subprocess

from PyQt5.QtCore import QObject, QThread, pyqtSignal

from git_implementation.history import GitSnapshot, parse_log


class GitHistoryWorker(QThread):
    """Fetch one immutable Git snapshot outside the GUI thread."""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, folder: Path, limit: int = 500, parent=None) -> None:
        super().__init__(parent)
        self.folder = Path(folder)
        self.limit = max(1, min(int(limit), 5000))

    def run(self) -> None:
        """Resolve repository root, identity and history with finite timeouts."""
        try:
            root = self._git("rev-parse", "--show-toplevel").strip()
            if not root:
                raise RuntimeError("The selected folder is not a Git repository")
            repository = Path(root).resolve()
            branch_result = self._run(repository, "symbolic-ref", "--short", "--quiet", "HEAD")
            branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None
            head_result = self._run(repository, "rev-parse", "--short", "HEAD")
            head = head_result.stdout.strip() if head_result.returncode == 0 else ""
            log_result = self._run(
                repository, "log", "--all", "--topo-order", "--date-order",
                f"--max-count={self.limit}",
                "--format=%x1e%H%x1f%P%x1f%an%x1f%aI%x1f%D%x1f%s",
            )
            if log_result.returncode == 0:
                commits = parse_log(log_result.stdout)
            elif not head:
                commits = ()  # valid repository with an unborn branch
            else:
                raise RuntimeError(log_result.stderr.strip() or "Git log failed")
            self.succeeded.emit(GitSnapshot(repository, branch, head, commits))
        except (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError) as error:
            self.failed.emit(str(error))

    def _run(self, cwd: Path, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *arguments], cwd=str(cwd), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=15, check=False,
        )

    def _git(self, *arguments: str) -> str:
        return self._git_at(self.folder, *arguments)

    def _git_at(self, cwd: Path, *arguments: str) -> str:
        completed = self._run(cwd, *arguments)
        if completed.returncode != 0:
            message = completed.stderr.strip() or "Git command failed"
            raise RuntimeError(message)
        return completed.stdout


class GitService(QObject):
    """Publish only the newest asynchronous history request."""

    snapshotReady = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._generation = 0
        self._workers: set[GitHistoryWorker] = set()

    def refresh(self, folder: Path) -> None:
        """Start a bounded refresh and invalidate every older result."""
        self._generation += 1
        generation = self._generation
        worker = GitHistoryWorker(folder, parent=self)
        self._workers.add(worker)
        worker.succeeded.connect(
            lambda snapshot, value=generation: self._publish(value, snapshot)
        )
        worker.failed.connect(
            lambda message, value=generation: self._publish_error(value, message)
        )
        worker.finished.connect(lambda current=worker: self._workers.discard(current))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _publish(self, generation: int, snapshot: GitSnapshot) -> None:
        if generation == self._generation:
            self.snapshotReady.emit(snapshot)

    def _publish_error(self, generation: int, message: str) -> None:
        if generation == self._generation:
            self.failed.emit(message)