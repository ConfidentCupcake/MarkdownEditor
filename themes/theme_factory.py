"""
theme_factory.py -> generate theme variants from themes/theme.json.

Usage:      python theme_factory.py
Writes:     themes/<name>.json for every palette below.

Each palette maps style-name -> hex color. "paper" is special: it becomes the default
style's paper-color (the editor background). Styles NOT listed keep their
original color from theme.json -> so a palette only needs to name what it actually changes.
"""

import json
from pathlib import Path

PALETTES = {
    "sunset": {
        "paper": "#241a1c",
        "default": "#e8c8b0",
        "keyword": "#ff5700",
        "string": "#ffb347",
        "docstring": "#8f6b5a",
        "comments": "#7a6e6a",
        "numbers": "#ffd27f",
        "function_def": "#ff8c61",
        "function_call": "#e07be0",
        "local_variable": "#f0a8a0",
        "operators": "#ffffff",
    },
    "midnight": {
        "paper": "#0d1220",
        "default": "#c8d4f0",
        "keyword": "#7aa2f7",
        "string": "#9ece6a",
        "docstring": "#565f89",
        "comments": "#565f89",
        "numbers": "#ff9e64",
        "function_def": "#7dcfff",
        "function_call": "#bb9af7",
        "local_variable": "#c0caf5",
        "operators": "#ffffff",
    },
    "bloodmoon": {
        "paper": "#180a0a",
        "default": "#d8c0c0",
        "keyword": "#ff2222",
        "string": "#ff8844",
        "docstring": "#6f4a4a",
        "comments": "#7a5555",
        "numbers": "#ffcc44",
        "function_def": "#ff6655",
        "function_call": "#cc4444",
        "local_variable": "#e8b8b8",
        "operators": "#ffffff",
    },
    "forest": {
        "paper": "#101a12",
        "default": "#cfe8d0",
        "keyword": "#66d977",
        "string": "#a8e05f",
        "docstring": "#5a7a5f",
        "comments": "#5f7a66",
        "numbers": "#e5c07b",
        "function_def": "#7ee787",
        "function_call": "#4ec9b0",
        "local_variable": "#b8e6c0",
        "operators": "#ffffff",
    },
}

def make_theme(name: str, palette: dict, src: str = "themes/theme.json"):
    """
    Recolor theme.json with 'palette' and write themes/<name>.json.

    The generated theme inherits EVERYTHING from the source theme
    (editor.font with its family/size, all 62 styles, weights, italics)
    and only the colors listed in the palette change. The editor section
    is recolored too so paper, margins and caret match the palette.

    :param name: output file name (without .json)
    :param palette: {style_name: hex color}; "paper" is the background
    :param src: source theme to inherit everything else from
    """
    theme = json.loads(Path(src).read_text(encoding="utf-8"))

    # --- syntax colors ------------------------------------------------- #
    for entry in theme["theme"]["syntax"]:
        style_name = list(entry.keys())[0]      # BUGFIX: was keys()[0],
                                                # which crashed with a
                                                # TypeError on every run
        if style_name == "default":
            if "paper" in palette:
                entry["default"]["paper-color"] = palette["paper"]
            if "default" in palette:
                entry["default"]["color"] = palette["default"]
        elif style_name in palette:
            entry[style_name]["color"] = palette[style_name]

    # --- editor section: paper, margins and caret follow the palette --- #
    editor = theme["theme"].setdefault("editor", {})
    if "paper" in palette:
        editor["paper-color"] = palette["paper"]
        editor["margin-background"] = palette["paper"]
    editor.setdefault("font", {})   # family/size are inherited from the
                                    # source theme (theme = single source
                                    # of truth for fonts)

    out = Path("themes") / f"{name}.json"
    out.write_text(json.dumps(theme, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    
if __name__ == "__main__":
    for name, palette in PALETTES.items():
        make_theme(name, palette)