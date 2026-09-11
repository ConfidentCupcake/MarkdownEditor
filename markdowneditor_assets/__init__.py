"""Access packaged MarkdownEditor runtime assets.

All CSS, icons, themes, and sprite sheets live below this importable package.
Using :mod:`importlib.resources` makes the same lookup work from a source tree,
an installed wheel, and a PyInstaller bundle that collects this package's data.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import PurePosixPath

def asset_path(relative_path: str) -> str:
    """Return a filesystem path for one package-relative runtime asset.

    Parameters
    ----------
    relative_path:
        A path below ``markdowneditor_assets`` such as
        ``"icons/app-icon.png"`` or ``"themes/theme.json"``.

    Raises
    ------
    ValueError
        If an absolute path or parent traversal is supplied. Callers use only
        application-owned assets; allowing ``..`` would hide path mistakes.

    Notes
    -----
    Normal wheel installs and PyInstaller collections expose resources as real
    files. If zip-import support is added later, return a managed context from
    ``importlib.resources.as_file`` instead of storing this string long-term.
    """
    # Normalize Windows-style separators because callers historically used
    # os.path.join(), which creates backslashes on Windows.
    relative = PurePosixPath(str(relative_path).replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Asset path must stay inside the package: {relative_path!r}")

    resource = files(__name__)
    for part in relative.parts:
        resource = resource.joinpath(part)
    return str(resource)


__all__ = ["asset_path"]