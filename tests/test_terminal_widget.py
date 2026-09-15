"""Regression tests for protected terminal input and keyboard behavior."""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QKeyEvent, QTextCursor
from PyQt5.QtWidgets import QApplication

from con_term.terminal_widget import TerminalView


def _press(
        view: TerminalView,
        key: int,
        text: str = "",
        modifiers=Qt.NoModifier,
) -> None:
    """Deliver one synthetic key press directly to a terminal view.

    Args:
        view: TerminalView under test.
        key: Qt key constant such as Qt.Key_Up.
        text: Printable Unicode associated with the event.
        modifiers: Ctrl, Shift, Alt, or Meta modifier flags.
    """
    event = QKeyEvent(QKeyEvent.KeyPress, key, modifiers, text)
    view.keyPressEvent(event)

def test_history_and_editing_keys_are_forwarded(qtbot) -> None:
    """Translate history and line-editing keys to their VT sequences."""
    written: list[str] = []
    view = TerminalView(written.append)
    qtbot.addWidget(view)

    _press(view, Qt.Key_Up)
    _press(view, Qt.Key_Down)
    _press(view, Qt.Key_Left)
    _press(view, Qt.Key_Backspace)
    _press(view, Qt.Key_Return)

    assert written == [
        "\x1b[A",
        "\x1b[B",
        "\x1b[D",
        "\x7f",
        "\r",
    ]

def test_typing_cannot_edit_previous_output(qtbot) -> None:
    """Keep rendered output unchanged when typing after clicking an old line."""
    written: list[str] = []
    view = TerminalView(written.append)
    qtbot.addWidget(view)
    view.setPlainText("PS C:\\project> ")

    # Simulate clicking at the very start of historical terminal output.
    cursor = view.textCursor()
    cursor.setPosition(0)
    view.setTextCursor(cursor)

    _press(view, Qt.Key_A, "a")

    # Qt did not insert the character into its read-only document.
    assert view.toPlainText() == "PS C:\\project> "

    # The character was forwarded to the shell writer instead.
    assert written == ["a"]

def test_ctrl_c_copies_selection_or_interrupts(qtbot) -> None:
    """Copy selected text but interrupt the shell when nothing is selected."""
    written: list[str] = []
    view = TerminalView(written.append)
    qtbot.addWidget(view)
    view.setPlainText("output")

    # With no selection, Ctrl+C becomes ASCII ETX (code point 3).
    _press(view, Qt.Key_C, "\x03", Qt.ControlModifier)
    assert written == ["\x03"]

    cursor = view.textCursor()
    cursor.select(QTextCursor.Document)
    view.setTextCursor(cursor)

    # With a selection, Ctrl+C uses the normal Qt clipboard behavior.
    _press(view, Qt.Key_C, "\x03", Qt.ControlModifier)

    assert QApplication.clipboard().text() == "output"
    assert written == ["\x03"]

def test_paste_is_sent_without_editing_document(qtbot) -> None:
    """Send pasted text to the shell and preserve the protected Qt document."""
    written: list[str] = []
    view = TerminalView(written.append)
    qtbot.addWidget(view)
    view.setPlainText("PS> ")
    QApplication.clipboard().setText("one\ntwo")

    _press(view, Qt.Key_V, "v", Qt.ControlModifier)

    # The QPlainTextEdit remains a rendering surface, not the input buffer.
    assert view.toPlainText() == "PS> "

    # The newline was converted to the terminal Enter representation.
    assert written == ["one\rtwo"]