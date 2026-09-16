"""Asynchronously retrieve bounded Git history without invoking a shell."""

from pathlib import Path
import subprocess
import time

from PyQt5.QtCore import QObject, QThread, pyqtSignal

from git_implementation.history import GitSnapshot, parse_log


class GitHistoryWorker(QThread):
    """Fetch one immutable Git snapshot outside the GUI thread."""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, folder: Path, limit: int = 500, parent=None) -> None:
        """Store one bounded history request without starting the thread."""
        super().__init__(parent)
        self.folder = Path(folder)
        self.limit = max(1, min(int(limit), 5000))

    def run(self) -> None:
        """Resolve repository root, identity, and bounded commit history."""
        try:
            root = self._git("rev-parse", "--show-toplevel").strip()
            if not root:
                raise RuntimeError("The selected folder is not a Git repository")

            repository = Path(root).resolve()
            branch_result = self._run(
                repository,
                "symbolic-ref",
                "--short",
                "--quiet",
                "HEAD",
            )
            branch = (
                branch_result.stdout.strip()
                if branch_result.returncode == 0
                else None
            )

            head_result = self._run(repository, "rev-parse", "--short", "HEAD")
            head = (
                head_result.stdout.strip()
                if head_result.returncode == 0
                else ""
            )

            log_result = self._run(
                repository,
                "log",
                "--all",
                "--topo-order",
                "--date-order",
                f"--max-count={self.limit}",
                "--format=%x1e%H%x1f%P%x1f%an%x1f%aI%x1f%D%x1f%s",
            )
            if log_result.returncode == 0:
                commits = parse_log(log_result.stdout)
            elif not head:
                # A repository with no first commit is valid.
                commits = ()
            else:
                raise RuntimeError(log_result.stderr.strip() or "Git log failed")

            self.succeeded.emit(
                GitSnapshot(repository, branch, head, commits)
            )
        except (
            OSError,
            subprocess.TimeoutExpired,
            RuntimeError,
            ValueError,
        ) as error:
            self.failed.emit(str(error))

    def _run(
        self,
        cwd: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        """Run Git with a timeout that remains responsive to cancellation.

        ``subprocess.run()`` cannot observe ``requestInterruption()`` while it
        waits. Short ``communicate()`` timeouts let shutdown terminate a Git
        child before Qt destroys the worker thread.
        """
        command = ["git", *arguments]
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 15

        while True:
            if self.isInterruptionRequested():
                process.terminate()
                try:
                    process.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                raise InterruptedError("Git history refresh was cancelled")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                stdout, stderr = process.communicate()
                raise subprocess.TimeoutExpired(
                    command,
                    15,
                    output=stdout,
                    stderr=stderr,
                )

            try:
                stdout, stderr = process.communicate(
                    timeout=min(0.1, remaining)
                )
            except subprocess.TimeoutExpired:
                continue

            return subprocess.CompletedProcess(
                command,
                process.returncode,
                stdout,
                stderr,
            )

    def _git(self, *arguments: str) -> str:
        """Run a checked Git command in the user-selected folder."""
        return self._git_at(self.folder, *arguments)

    def _git_at(self, cwd: Path, *arguments: str) -> str:
        """Return stdout for a successful Git command in ``cwd``."""
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
        """Initialize generation tracking and active-worker ownership."""
        super().__init__(parent)
        self._generation = 0
        self._workers: set[GitHistoryWorker] = set()
        self._shutting_down = False

    def refresh(self, folder: Path) -> None:
        """Start a bounded refresh and invalidate every older result."""
        if self._shutting_down:
            return

        self._generation += 1
        generation = self._generation

        # Old results are already rejected by generation. Interrupting the old
        # work also prevents repeated timer ticks from accumulating processes.
        for existing in tuple(self._workers):
            existing.requestInterruption()

        worker = GitHistoryWorker(folder, parent=self)
        self._workers.add(worker)
        worker.succeeded.connect(
            lambda snapshot, value=generation: self._publish(value, snapshot)
        )
        worker.failed.connect(
            lambda message, value=generation: self._publish_error(value, message)
        )
        worker.finished.connect(
            lambda current=worker: self._workers.discard(current)
        )
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _publish(self, generation: int, snapshot: GitSnapshot) -> None:
        """Publish a snapshot only when it belongs to the newest request."""
        if generation == self._generation:
            self.snapshotReady.emit(snapshot)

    def _publish_error(self, generation: int, message: str) -> None:
        """Publish an error only when it belongs to the newest request."""
        if generation == self._generation:
            self.failed.emit(message)

    def is_running(self) -> bool:
        """Return whether a history worker still owns a Git subprocess."""
        return any(worker.isRunning() for worker in self._workers)

    def shutdown(self) -> None:
        """Cancel active history work and suppress every stale result."""
        self._shutting_down = True
        self._generation += 1
        for worker in tuple(self._workers):
            worker.requestInterruption()