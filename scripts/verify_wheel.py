"""Fail a release build if wheel metadata or runtime assets are incomplete."""

from __future__ import annotations

import argparse
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from markdown_python_editor import __version__


REQUIRED_ASSETS = {
    "markdowneditor_assets/css/style.qss",
    "markdowneditor_assets/icons/app-icon.png",
    "markdowneditor_assets/icons/app-icon-256.png",
    "markdowneditor_assets/icons/close-icon.svg",
    "markdowneditor_assets/themes/theme.json",
}


def verify_wheel(wheel: Path) -> None:
    """Validate one wheel's version metadata and essential asset contents."""
    with ZipFile(wheel) as archive:
        names = set(archive.namelist())
        missing = sorted(REQUIRED_ASSETS - names)
        if missing:
            raise SystemExit("Wheel is missing assets:\n" + "\n".join(missing))

        # A representative sprite proves nested package-data globs worked.
        if not any(
            PurePosixPath(name).parts[:3]
            == ("markdowneditor_assets", "icons", "cat")
            and name.endswith(".png")
            for name in names
        ):
            raise SystemExit("Wheel contains no individual cat frames")

        metadata_name = next(
            (name for name in names if name.endswith(".dist-info/METADATA")),
            None,
        )
        if metadata_name is None:
            raise SystemExit("Wheel contains no dist-info/METADATA")
        metadata = BytesParser().parsebytes(archive.read(metadata_name))
        if metadata["Version"] != __version__:
            raise SystemExit(
                f"Wheel version {metadata['Version']} != source version {__version__}"
            )

    print(f"Verified {wheel.name}: version {__version__}, assets present")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    verify_wheel(args.wheel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())