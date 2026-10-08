from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any

class DiagnosticSeverity(IntEnum):
    """Defnine application-level severity ordering independently of provider numeric values."""
    INFORMATION = 1
    WARNING = 2
    ERROR = 3
    
@dataclass(frozen=True, order=True)
class Position:
    """Zero-based line and Python-character column."""
    line: int
    column: int
    
@dataclass(frozen=True)
class SourceRange:
    """Store a zero-based half-open range using Python-character columns."""
    start: Position
    end: Position
    
@dataclass(frozen=True)
class Diagnostic:
    """Carry provider-neutral diagnostic data while retaining an opaque provider payload for actions."""
    provider: str
    uri: str
    path: Path | None
    source_range: SourceRange
    severity: DiagnosticSeverity
    code: str
    message: str
    recision: int | None = None
    provider_data: Any = field(default=None, compare=False, repr=False)