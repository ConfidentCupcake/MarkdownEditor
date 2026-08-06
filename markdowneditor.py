from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.Qsci import *
import keyword
import pkgutil
import os
from pathlib import Path
from markdowncustomlexer import MarkdownCustomLexer


class MarkdownEditor(QsciScintilla):
    def __init__(self, parent=None, path: Path=None, is_python_file: bool=False):
        super(MarkdownEditor, self).__init__(parent)
        self.path = path
        self.full_path = self.path.absolute() if self.path else None
        self.is_python_file = is_python_file
        self.BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        # encoding
        self.setUtf8(True)
        # font
        self.window_font = QFont("sans-serif")
        self.window_font.setPointSize(13)
        self.setFont(self.window_font)

        # brace matching
        self.setBraceMatching(QsciScintilla.SloppyBraceMatch)

        # indentation
        self.setIndentationGuides(True)
        self.setTabWidth(2)
        self.setIndentationsUseTabs(False)
        self.setAutoIndent(True)

        self.setEolMode(QsciScintilla.EolWindows)
        self.setEolVisibility(False)

        # autocomplete
        self.setAutoCompletionSource(QsciScintilla.AcsAll)
        self.setAutoCompletionThreshold(1)
        self.setAutoCompletionCaseSensitivity(False)
        self.setAutoCompletionUseSingle(QsciScintilla.AcusNever)

        # caret
        self.setCaretForegroundColor(QColor('#f31122'))  # sets text caret color
        self.setCaretLineVisible(True)  # enables the background color of the caret line
        self.setCaretWidth(2)  # sets the caret with
        self.setCaretLineBackgroundColor(QColor('#3d424d')) # changes the background color of the caret line

        # set lexer
        self.md_lexer = MarkdownCustomLexer(self)
        self.md_lexer.setFont(self.window_font)

        self.api = QsciAPIs(self.md_lexer)
        for key in keyword.kwlist + dir(__builtins__):
            self.api.add(key)
        for _, name, _ in pkgutil.iter_modules():
            self.api.add(name)
        self.api.prepare()
        self.md_lexer.setAPIs(self.api)

        self.setLexer(self.md_lexer)

        self.setMarginType(0, QsciScintilla.NumberMargin)
        self.setMarginWidth(0, "000")
        self.setMarginsForegroundColor(QColor('#ff888888'))
        self.setMarginsBackgroundColor(QColor('#282c34'))
        self.setMarginsFont(self.window_font)

    # No need to assign any function to handle key press, this overloads the function from base class
    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.modifiers() == Qt.KeyboardModifier.ControlModifier and e.key() == Qt.Key.Key_Space:
            self.autoCompleteFromAll()
        else:
            return super().keyPressEvent(e)


