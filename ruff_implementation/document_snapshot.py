"""Immutable source buffers shared by independent diagnostic providers."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DocumentSnapshot:
    """
    Describe one exact, in-memory version of an open Python document.
    
    Attributes:
        uri: Stable LSP identity; untitled buffers receive a syntetic file URI.
        path: Actual disk path, or None for a unsaved document.
        text: Full source text copied on the GUI thread, never read by worker
            from a live editor widget.
        revision: Increasing version shared by Ruff, type analysis and spelling.
        
    Providers must discard a result if its snapshot no longer matches the current
    document. Freezing this object prevents accidential cross-thread changes
    while a spelling job is running.
    """
    
    uri: str
    path: Path | None
    text: str
    revision: int