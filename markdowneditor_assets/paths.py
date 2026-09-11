import sys
from importlib.resources import files
from pathlib import Path


def asset_path(relative_path: str) -> str:
    """Return a filesystem path in source, wheel, and PyInstaller builds."""
    if hasattr(sys, "_MEIPASS"):
        return str(Path(sys._MEIPASS) / relative_path)
    return str(files("markdowneditor_assets").joinpath(relative_path))