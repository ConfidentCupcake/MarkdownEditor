"""Shared interface colors and density rules."""
from __future__ import annotations

PALETTE = {"paper": "#1e1f22", "surface": "#262a33", "text": "#dcdfe4", 
           "muted": "#969ba7", "accent": "#61afef", "border": "#3d424d"}
           

def theme_qss(density: str = "comfortable") -> str:
    """Generate ine scoped widget stylesheet from shared semnantics tokens."""
    if density not in ("compact", "comfortable"):
        raise ValueError("unknown density")
    # The density choice changes geometry; colors still cone from one palette.
    pad = 3 if density == "compact" else 7
    # Property selectors scope the design to widgets explicitly opted in.
    
    return """
    QWidget[designPanel="true"] { background: %(surface)s; color: %(text)s; }
    QDockWidget::title { background: %(surface)s; color: %(text)s; padding: %(pad)spx; border-bottom: 1px solid %(border)s; }
    QMenu { background: %(surface)s; color: %(text)s; border: 1px solid %(border)s; }
    QMenu::item:selected { background: %(border)s; }
    QPushButton { background: %(surface)s; color: %(text)s; border: 1px solid %(border)s; padding: %(pad)spx; border-radius: 4px; }
    QPushButton:hover { border-color: %(accent)s; }
    QPushButton:disabled { color: %(muted)s; }
    QToolButton { color: %(text)s; background: transparent; border: 1px solid transparent; border-radius: 4px; padding: %(pad)spx; }
    QToolButton:hover, QToolButton:focus { border-color: %(accent)s; }
    QToolButton:checked { background: %(border)s; }
    QToolButton:disabled { color: %(muted)s; }
    QTabBar::tab { background: %(surface)s; color: %(text)s; padding: %(pad)spx 12px; }
    QTabBar::tab:selected { border-bottom: 2px solid %(accent)s; }
    """ % {**PALETTE, "pad": pad}
    
