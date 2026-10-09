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
        length = self.editor.SendScintilla(QsciScintilla.SCI_GETLENGTH)
        for number in (20, 21, 22, 23):
            self.editor.SendScintilla(QsciScintilla.SCI_SETINDICATORCURRENT, number)
            self.editor.SendScintilla(QsciScintilla.SCI_INDICATORCLEARRANGE, 0, length)
            self.editor.markerDeleteAll(number)
        self.by_line.clear()


    def render(self, diagnostics: list[RuffDiagnostic]) -> None:
        """Paint one complete merged snapshot and cache all findings for hover.

        The caller filters by source revision. Clearing once here is safe only
        because the supplied list includes every provider, not merely the latest
        server response. Empty ranges receive a neighboring character when possible.
        """
        self.clear()
        for item in diagnostics:
            if not 0 <= item.start.line < self.editor.lines():
                continue
            end_line = min(self.editor.lines() - 1, max(item.start.line, item.end.line))
            start = self._byte_position(item.start.line, item.start.column)
            end = self._byte_position(end_line, item.end.column)
            if end <= start:
                line_text = self.editor.text(item.start.line).rstrip("\r\n")
                if item.start.column < len(line_text):
                    end = self._byte_position(item.start.line, item.start.column + 1)
                elif item.start.column > 0:
                    start = self._byte_position(item.start.line, item.start.column - 1)
                    end = self._byte_position(item.start.line, item.start.column)
            number = {RuffSeverity.ERROR: 20, RuffSeverity.WARNING: 21, RuffSeverity.INFO: 22}[item.severity]
            if end > start:
                self.editor.SendScintilla(QsciScintilla.SCI_SETINDICATORCURRENT, number)
                self.editor.SendScintilla(QsciScintilla.SCI_INDICATORFILLRANGE, start, end - start)
            self.editor.markerAdd(item.start.line, number)
            if item.fix is not None:
                self.editor.markerAdd(item.start.line, self.FIX_MARKER)
            for line in range(item.start.line, end_line + 1):
                self.by_line.setdefault(line, []).append(item)

    def at_line(self, line: int) -> RuffDiagnostic | None:
        """Return highest severity diagnostic on one line for hover/menu use."""
        items = self.by_line.get(line, [])
        return max(items, key=lambda item: item.severity) if items else None

    def tooltip(self, diagnostic: RuffDiagnostic) -> str:
        """Build a new Ruff-only hover card; no Jedi pyflakes content."""
        message = f"{diagnostic.code}\n{diagnostic.message}"
        if diagnostic.provider == "ruff" and isinstance(diagnostic.raw.get("range"), dict):
            message += "\nRight-click to request Ruff quick fixes."
        return message

    def _byte_position(self, line: int, column: int) -> int:

        value = self.editor.text(line).rstrip("\r\n")
        prefix = value[:max(0, min(column, len(value)))].encode("utf-8")
        return self.editor.SendScintilla(QsciScintilla.SCI_POSITIONFROMLINE, line) + len(prefix)