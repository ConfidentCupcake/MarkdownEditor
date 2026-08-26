"""In-memory Ruff formatting and fix commands for open QScintilla documents."""

from __future__ import annotations
from pathlib import Path
import subprocess
import tempfile

class RuffService:
    """Run Ruff on temporary sibling files, never directly on an open real file.

    The selected Python interpreter runs ``python -m ruff``. A sibling temporary
    file lets Ruff discover the same pyproject.toml as the logical target file.
    """

    def __init__(self, python_executable: str, timeout: int = 10):
        """Store interpreter path and subprocess timeout without running Ruff."""
        self.python_executable = python_executable
        self.timeout = timeout

    def _temporary_file(self, source: str, logical_path: Path) -> Path:
        """Create a temporary source snapshot and return a caller-cleaned path."""
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=logical_path.suffix or ".py",
            prefix=".__ruff_editor_", dir=logical_path.parent, delete=False,
        ) as handle:
            handle.write(source)
            return Path(handle.name)

    def _run(self, arguments: list[str], cwd: Path):
        """Run Ruff; lint violations are expected, so check=False is required."""
        return subprocess.run(
            [self.python_executable, "-m", "ruff", *arguments],
            text=True, capture_output=True, cwd=str(cwd),
            timeout=self.timeout, check=False,
        )

    def _transform(self, source: str, logical_path: Path, arguments: list[str]) -> str:
        """Run a file-transforming Ruff command on temporary source and return text.

        The real file remains untouched. The caller replaces QScintilla text as
        one undoable operation, then MainWindow performs the normal save write.
        """
        temporary = self._temporary_file(source, logical_path)
        try:
            self._run([*arguments, "--no-cache", str(temporary)], logical_path.parent)
            return temporary.read_text(encoding="utf-8")
        finally:
            temporary.unlink(missing_ok=True)

    def safe_fix(self, source: str, logical_path: Path) -> str:
        """Apply only Ruff safe fixes; never enable --unsafe-fixes automatically."""
        return self._transform(source, logical_path, ["check", "--fix"])

    def format(self, source: str, logical_path: Path) -> str:
        """Return Ruff-formatted source without writing the logical file."""
        return self._transform(source, logical_path, ["format"])

    def organize_imports(self, source: str, logical_path: Path) -> str:
        """Return source after only import-sorting I-rule fixes are applied."""
        return self._transform(source, logical_path, ["check", "--select", "I", "--fix"])