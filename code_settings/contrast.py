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
        