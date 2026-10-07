"""State-aware icons from the existing SVG assets."""
from __future__ import annotations
from PyQt5.QtGui import QIcon
from markdowneditor_assets import asset_path
from pathlib import Path

def themed_icon(name: str) -> QIcon:
    """Load existing 24px SVG icons with actual disabled/selected Qt states."""
    # Do not turn abitrary text in asset path; the sidebar has six
    # supported actions and an explitcit allowlist is easier to review.
    if name not in {"folder", "search", "outline", "terminal", "console", "settings"}:
        raise ValueError("unsupported icon")
    path = Path(asset_path(f"icons/{name}.svg"))
    icon = QIcon(str(path))
    # Qt generates a disabled palette variant when no explicit asset is added.
    active = Path(asset_path(f"icons/{name}-active.svg"))
    if active.is_file():
        icon.addFile(str(active), mode=QIcon.Normal, state=QIcon.on)
    return icon