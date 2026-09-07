"""New Ruff-specific QScintilla diagnostics visualisation layer."""

from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QToolTip
from PyQt5.Qsci import QsciScintilla
from ruff_implementation.ruff_diagnostics_model import RuffDiagnostic, RuffSeverity

class RuffDiagnosticView:
    """
    Paint exact Ruff ranges, gutter markers, hover cards and fix markers.
    
    This class owns only new Ruff visual IDs. 
    It never reads or clears old pyflakes indicator IDs, so migration cannot accidentally mix systems.
    """
    
    ERROR_INDICATOR = 20
    WARNING_INDICATOR = 21
    INFO_INDICATOR = 22
    FIX_INDICATOR = 23
    ERROR_MARKER = 20
    WARNING_MARKER = 21
    INFO_MARKER = 22
    FIX_MARKER = 23
    
    def __init__(self, editor: QsciScintilla):
        """Define a completely independent Ruff colour and marker palette."""
        self.editor = editor
        self.by_line: dict[int, list[RuffDiagnostic]] = {}
        self._define_visuals()
        
    def _define_visuals(self):
        """Reserve QScintilla IDs and assign visible Ruff colors/styles."""
        
        # Indicators paint text ranges in the editor body. They are independent of marker
        # IDs, which paint optional symbols in a seperate gutter margin.
        self.editor.indicatorDefine(QsciScintilla.SquiggleIndicator, self.ERROR_INDICATOR)
        self.editor.setIndicatorForegroundColor(QColor("#ff5c57"), self.ERROR_INDICATOR)
        
        self.editor.indicatorDefine(QsciScintilla.SquiggleIndicator, self.WARNING_INDICATOR)
        self.editor.setIndicatorForegroundColor(QColor("#ffbd2e"), self.WARNING_INDICATOR)
        
        # Use a blue squiggle rather than DotBotIndicator. A DotBox is to subtle against the current dark theme
        # and made information/hint diagnostics look as if they were missing.
        self.editor.indicatorDefine(QsciScintilla.SquiggleIndicator, self.INFO_INDICATOR)
        self.editor.setIndicatorForegroundColor(QColor("#55aaff"), self.INFO_INDICATOR)
        
        # FIX_INDICATOR is visually distinct because it marks diagnostics for which 
        # the later codeAction implementation can offer an automatic resolution.
        self.editor.indicatorDefine(QsciScintilla.RoundBoxIndicator, self.FIX_INDICATOR)
        self.editor.setIndicatorForegroundColor(QColor("#a6e22e"), self.FIX_INDICATOR)
        
        # Marker IDs are seperate from indicator IDs even when numerical values happen to
        # be the same. Markers require a QScintilla SymbolMargin to show.
        for marker, color in (
            (self.ERROR_MARKER, "#ff5c57"),
            (self.WARNING_MARKER, "#ffbd2e"),
            (self.INFO_MARKER, "#55aaff"),
            (self.FIX_MARKER, "#a6e22e"),
        ):
            self.editor.markerDefine(QsciScintilla.Circle, marker)
            self.editor.setMarkerForegroundColor(QColor(color), marker)
            self.editor.setMarkerBackgroundColor(QColor(color), marker)
    
    def clear(self):
        """Remoce only Ruff visual state across the current document."""
        lines = self.editor.lines()
        if lines:
            end = self.editor.lineLength(lines - 1)
            for indicator in (self.ERROR_INDICATOR, self.WARNING_INDICATOR, self.INFO_INDICATOR, self.FIX_INDICATOR):
                self.editor.clearIndicatorRange(0, 0, lines - 1, end, indicator)
                
        for marker in (self.ERROR_MARKER, self.WARNING_MARKER, self.INFO_MARKER, self.FIX_MARKER):
            self.editor.markerDeleteAll(marker)
        self.by_line = {}
    
    def render(self, diagnostics: list[RuffDiagnostic]):
        """Paint exact source ranges and cache diagnostics for hover/context menus."""
        self.clear()
        for diagnostic in diagnostics:
            if diagnostic.start.line < 0 or diagnostic.start.line >= self.editor.lines():
                continue
            # Ruff zero-length ranges still need a visible one-character span.
            end_line = max(diagnostic.end.line, diagnostic.start.line)
            end_column = diagnostic.end.column
            if end_line == diagnostic.start.line and end_column <= diagnostic.start.column:
                end_column = min(diagnostic.start.column + 1, self.editor.lineLength(diagnostic.start.line))
            indicator = {RuffSeverity.ERROR: self.ERROR_INDICATOR, RuffSeverity.WARNING: self.WARNING_INDICATOR, RuffSeverity.INFO: self.INFO_INDICATOR}[diagnostic.severity]
            marker = {RuffSeverity.ERROR: self.ERROR_MARKER, RuffSeverity.WARNING: self.WARNING_MARKER, RuffSeverity.INFO: self.INFO_MARKER}[diagnostic.severity]
            self.editor.fillIndicatorRange(diagnostic.start.line, diagnostic.start.column, end_line, end_column, indicator)
            self.editor.markerAdd(diagnostic.start.line, marker)
            if diagnostic.fix:
                self.editor.markerAdd(diagnostic.start.line, self.FIX_MARKER)
            self.by_line.setdefault(diagnostic.start.line, []).append(diagnostic)

    def at_line(self, line: int) -> RuffDiagnostic | None:
        """Return highest severity diagnostic on one line for hover/menu use."""
        items = self.by_line.get(line, [])
        return max(items, key=lambda item: item.severity) if items else None

    def tooltip(self, diagnostic: RuffDiagnostic) -> str:
        """Build a new Ruff-only hover card; no Jedi pyflakes content."""
        fix_line = "\nQuick fix available: right-click the diagnostic." if diagnostic.fix else ""
        return f"{diagnostic.code} - {diagnostic.message}{fix_line}"