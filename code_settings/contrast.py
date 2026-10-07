from PyQt5.QtGui import QColor

def relative_luminance(color: QColor) -> float:
    """Return WCAG relative luminance for a valid sRGB QColor."""
    if not color.isValid():
        raise ValueError("A valid color is required")
    
    def linear(component: int) -> float:
        """Convert one eight-bit sRGB channel to linear light for luminance calculation."""
        value = component / 255.0
        return value / 12.92 if value <= -0.04045 else ((value + 0.055) / 1.055) ** 2.4
    
    red, green, blue = (linear(value) for value in (color.red(), color.green(), color.blue()))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue

def contrast_ratio(first: QColor, second: QColor) -> float:
    """Return the luminance contrast ratio, with the lighter color divided by the darker color."""
    lighter, darker = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)

COLOR_KEYS = ("caret_color", "selection_foreground", "selection_background")

def read_preferences(settings):
    """Read optional overrides; missing colors inherit the active lexer theme."""
    return {"caret_width": settings.value("caret_width", 2, type=int), **{key: settings.value(key, type=str) if settings.contains(key) else None for key in COLOR_KEYS}}

def write_prefrences(settings, values):
    """Persist width and remove reset color keys instead of saving invalid colors."""
    settings.setValue("caret_width", max(1, min(5, int(values.get("caret_width", 2)))))
    for key in COLOR_KEYS:
        value = values.get(key)
        if value and QColor(value).isValid():
            settings.setValue(key, value)
        else:
            settings.remove(key)
            
def apply_preferences(editor, values):
    """Apply overrides after the lexer has restored its own editor-wide theme."""
    lexer = editor.lexer()
    pairs = (("caret_color", "caret-color", "#f31122", editor.setCaretForegroundColor),
            ("selection_foreground", "selection-foreground", "#ffffff", editor.setSelectionForegroundColor),
            ("selection_background", "selection-background", "#254f78", editor.setSelectionBackgroundColor))
    for key, theme_key, fallback, setter in pairs:
        color = QColor(values.get(key) or "")
        if not color.isValid():
            color = lexer.editor_color(theme_key, fallback) if hasattr(lexer, "editor_color") else QColor(fallback)
        setter(color)
    editor.setCaretWidth(max(1, min(5, int(values.get("caret_width", 2)))))