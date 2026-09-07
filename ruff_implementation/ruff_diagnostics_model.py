"""Ruff-only immutable data structure used by worker, controller, and view."""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

class RuffSeverity(IntEnum):
    """Visual severity odering; larger calues are more important."""
    INFO = 1
    WARNING = 2
    ERROR = 3
    
@dataclass(frozen=True)
class RuffPosition:
    """Zero-based QScintilla position converted from Ruff's one-based JSON."""
    line: int
    column: int
    
@dataclass(frozen=True)
class RuffTextEdit:
    """One replacement range belonging to a user-approved Ruff fix."""
    start: RuffPosition
    end: RuffPosition
    content: str | None # None means delete the selected range. 
    
@dataclass(frozen=True)
class RuffFix:
    """A Ruff code action with one or more source edits."""
    title: str
    applicability: str | None
    edits: tuple[RuffTextEdit, ...]
    
@dataclass(frozen=True)
class RuffDiagnostic:
    """
    Complete diagnostic state for new visualisation and quick fixes.
    
    revision is the editor document revision that produced this result.
    It prevents old diagnostics and olf fixes from changing newer source text.
    """
    code: str
    message: str
    severity: RuffSeverity
    start: RuffPosition
    end: RuffPosition
    revision: int
    fix: RuffFix | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False)
    