"""Python source symbols and bounded workspace indexing."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import ast
import os


@dataclass(frozen=True)
class Symbol:
    """A workspace symbol with a zero-based QScintilla destination."""
    
    path: Path
    name: str
    kind: str
    line: int
    column: int
    parent: str = ""
    end_line: int = 0
    
def python_symbols(path: Path, source: str) -> list[Symbol]:
    """Parse definitions without executing user code; reject incomplete syntax."""
    # Parsing is safe for an untrusted project: ast.parse builds a syntax tree.
    # and does not import a module or execute any of its top-level statements.
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    result = []
    
    def visit(body, parent=""):
        """Walk immediate members so a method retains its class breadcrumb."""
        for node in body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                # AST lines are 1-based; the editor's line API is 0-based.
                result.append(Symbol(path, node.name, kind, node.lineno - 1, node.col_offset, parent, getattr(node, "end_lineno", node.lineno) - 1))
                visit(node.body, f"{parent}.{node.name}".strip("."))
    visit(tree.body)
    return result

def symbol_at(symbols: list[Symbol], line: int) -> list[Symbol]:
    """Return the enclosing symbol chain for a zero-based source line."""
    # AST end_lineno prevents a method breadcrumb leaking into the next class.
    eligible = [item for item in symbols if item.line <= line <= item.end_line]
    return sorted(eligible, key=lambda item: (item.line, -item.end_line))

def workspace_symbols(root: Path, *, limit: int = 2000) -> list[Symbol]:
    """Build a bounded index, skipping virtualenvs and unreadable files."""
    result = []
    skip = {".git", ".venv", "venv", "__pycache__", "build", "dist"}
    for folder, dirs, files in os.walk(root):
        # Mutating dirs in-place tells os.walk not to descend into these trees.
        dirs[:] = [name for name in dirs if name not in skip]
        for name in files:
            if not name.endswith((".py", ".pyi")):
                continue
            path = Path(folder) / name
            try:
                if path.is_symlink():
                    continue
                if path.stat().st_size > 2000000:
                    continue
                result.extend(python_symbols(path, path.read_text(encoding="utf-8-sig")))
            except (OSError, UnicodeError):
                continue
            if len(result) >= limit:
                return result[:limit]
    return result

