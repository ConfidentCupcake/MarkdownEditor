"""
Cross-platform PTY terminal used by MarkdownEditor.

The shell owns prompts, input editing, completion, command history, and signal handling.
Qt forwards keys and renders the terminal's ANSI/VT screen.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Any, Callable

import pyte
from PyQt5.QtCore import QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QFont, QKeyEvent, QKeySequence, QTextCursor
from PyQt5.QtWidgets import QAction, QApplication, QComboBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget


class _PtySession(QThread):
    """
    Run one shell in a PTY without blocking Qt's GUI thread.
    
    The worker owns one shell process for its entire lifetime. Blocking output reads
    happen in 'run()'. Public methods may be called from the GUI thread, so 
    access to the backend object is protected by a re-entrant lock.
    """
    
    # Text read from th PTY. TerminalWidget connects this to its ANSI parser.
    output_received = pyqtSignal(str)
    # Emitted only after the backend successfully creates the shell process.
    session_started = pyqtSignal()
    # Carries a readable error because exceptions cannot leave QThread.run().
    session_failed = pyqtSignal(str)
    # Carries the exit code. -1 means no reliable code was available.
    session_exited = pyqtSignal(int)
    
    def __init__(self, argv: list[str], cwd: Path, environment: dict[str, str], rows: int, columns: int, parent: QWidget | None = None) -> None:
        """
        Store the shell configuration without starting it yet.
        
        Args:
            argv: Excecutable followe dby its command-line arguments.
            cwd: Initial working directory for the shell.
            environment: Complete environment inherited by the shell.
            rows: Initial terminal height in character cells.
            columns: Initial terminal width in character cells.
            parent: Qt owner responsible for this worker's lifetime.
        """
        super().__init__(parent=parent)
        
        # Keep immutable launch information seperate from runtime state
        self._argv = argv
        self._cwd = cwd
        self._environment = environment
        self._rows = rows
        self._columns = columns
        
        # The backend is created inside run(), not on the GUI thread.
        self._process: Any | None = None
        
        # Event is thread-safe and can be checked inside the blocking read loop.
        self._stop_requested = threading.Event()
        
        # write(), resize_terminal(), and request_stop() can race with run().
        self._lock = threading.RLock()
        
    @staticmethod
    def _process_type() -> type:
        """
        Return the PTY process class for the current operating system.
        
        Returns:
            'winpty.PtyProcess' on Windows or 
            'ptyprocess.PtyProcessUnicode' on Linux/macOS.
            
        Raises:
            RuntimeError: If the dependency required by this platform is missing from the
                          active Python environment.
        """
        if sys.platform == "win32":
            try:
                # pywinpty wraps Windows ConPTY and the older winpty fallback.
                from winpty import PtyProcess
            except ImportError as error:
                raise RuntimeError(
                        "Windows terminal support requiress pywinpty. "
                        "Run: python -m pip install -e ."
                    ) from error
            return PtyProcess
        
        try:
            # Unnicode wrapper performs incremental decoding across read chunks.
            from ptyprocess import PtyProcessUnicode
        except ImportError as error:
            raise RuntimeError (
                    "PTY support requires ptyprocess. "
                    "Run: python -m pip install -e ."
                ) from error
        return PtyProcessUnicode

    def run(self) -> None:
        """Spawn the shell and forward PTY output until the session ends.

        The method executes on the QThread rather than Qt's GUI thread. It
        creates the platform backend, emits decoded output, and guarantees that
        the process is closed and `session_exited` is emitted on every path.
        """
        process: Any | None = None
        exit_code = -1

        try:
            process_type = self._process_type()

            # Both PTY backends accept the same core launch options.
            spawn_options: dict[str, Any] = {
                "cwd": str(self._cwd),
                "env": self._environment,
                "dimensions": (self._rows, self._columns),
            }

            if sys.platform != "win32":
                # PtyProcessUnicode accepts decoder options through spawn().
                # Replacing malformed input prevents one bad byte from
                # terminating the entire reader thread.
                spawn_options.update(
                    encoding="utf-8",
                    codec_errors="replace",
                )

            # This creates a PTY/ConPTY, not ordinary stdin/stdout pipes.
            process = process_type.spawn(self._argv, **spawn_options)

            with self._lock:
                # Publish the process only after spawn() succeeds. GUI writes,
                # resizes, and stop requests can now use it safely.
                self._process = process

            # stop() may have been requested while spawn() was still running.
            # Event's real query method is is_set(), not it_set().
            if self._stop_requested.is_set():
                self._close_process(process)
                return

            self.session_started.emit()

            while not self._stop_requested.is_set() and process.isalive():
                try:
                    # A larger buffer reduces signal overhead during commands
                    # that generate substantial terminal output.
                    chunk = process.read(65536)
                except EOFError:
                    # EOF is normal when the shell or terminal closes.
                    break
                except OSError as error:
                    if self._stop_requested.is_set():
                        # request_stop() closes the PTY to unblock read().
                        break
                    raise RuntimeError(
                        f"Could not read from terminal: {error}"
                    ) from error

                if not chunk:
                    continue

                # pywinpty normally returns str. This branch keeps the worker
                # safe if a backend version returns bytes instead.
                if isinstance(chunk, bytes):
                    chunk = chunk.decode("utf-8", errors="replace")

                # Qt delivers this signal to TerminalWidget on the GUI thread.
                self.output_received.emit(chunk)

            status = getattr(process, "exitstatus", None)
            if status is not None:
                exit_code = int(status)
            elif self._stop_requested.is_set():
                # User-requested shutdown is successful even when the backend
                # does not provide a child exit code.
                exit_code = 0

        except Exception as error:
            # Exceptions cannot propagate from QThread.run() to MainWindow.
            if not self._stop_requested.is_set():
                self.session_failed.emit(str(error))

        finally:
            # Always close process resources and complete the lifecycle signal.
            if process is not None:
                self._close_process(process)

            with self._lock:
                self._process = None

            self.session_exited.emit(exit_code)
            
    @staticmethod
    def _close_process(process: Any) -> None:
        """
        Close a PTY and force its child to exit if it is still alive.
        
        Args:
            process: A pywinpty or ptyprocess process object.
            
        The method intentionally tolerates a repeated close because natural
        process exit and user-requested shutdown can happen at the same time.
        """
        try:
            process.close(force=True)
        except Exception:
            # Backend implementations raise different exception types when a 
            # process has already reached EOF. At this point, closure succeeded
            # in practical terms, so no second error should reach the GUI.
            pass
            
    def write(self, text: str) -> None:
        """
        Send keyboard or pasted text to the active shell.
        
        Args:
            text: Unicode text or VT control characters to send.
            
        Input is not appended to the Qt document. The PTY driver and shell echo
        it at the live prompt, avoiding duplicate characters.
        """
        with self._lock:
            process = self._process
            
            # Input can arrive while the process is starting or stopping.
            if process is None or self._stop_requested.is_set():
                return
            
            try:
                process.write(text)
            except (EOFError, OSError):
                # The shell may exit between the state check and write().
                pass
            
    def resize_terminal(self, rows: int, columns: int) -> None:
        """
        Update the terminal geometry seen by the foreground application.
        
        Args:
            rows: New height in character cells.
            columns: New width in character cells.
        """
        with self._lock:
            # Save the size even when spawn() has not completed yet.
            self._rows = rows
            self._columns = columns
            process = self._process
            
            if process is None:
                return
            
            try:
                # Both selected backends use the order (rows, columns).
                process.setwinsize(rows, columns)
            except (EOFError, OSError):
                # A resize racing with process exit is harmless.
                pass
    
    def request_stop(self) -> None:
        """
        Request worker shutdown and unblock a pending PTY read.
        
        Setting the event stops future loop iterations. Closing the backend is also necessary 
        because the worker may currently be blocked inside 'process.read()' and unable to check the event.
        """
        self._stop_requested.set()
        
        with self._lock:
            process = self._process
            
        if process is not None:
            self._close_process(process)
        
        
        
class TerminalView(QPlainTextEdit):
    """Display protected terminal text and forward keys to the active PTY.

    The Qt document remains read-only. Users may select and copy historical
    output, but typing always goes to the shell's current input cursor.
    """

    # These strings are the VT sequences expected by interactive shells.
    _KEY_SEQUENCES = {
        Qt.Key_Up: "\x1b[A",
        Qt.Key_Down: "\x1b[B",
        Qt.Key_Right: "\x1b[C",
        Qt.Key_Left: "\x1b[D",
        Qt.Key_Home: "\x1b[H",
        Qt.Key_End: "\x1b[F",
        Qt.Key_Insert: "\x1b[2~",
        Qt.Key_Delete: "\x1b[3~",
        Qt.Key_PageUp: "\x1b[5~",
        Qt.Key_PageDown: "\x1b[6~",
        Qt.Key_F1: "\x1bOP",
        Qt.Key_F2: "\x1bOQ",
        Qt.Key_F3: "\x1bOR",
        Qt.Key_F4: "\x1bOS",
        Qt.Key_F5: "\x1b[15~",
        Qt.Key_F6: "\x1b[17~",
        Qt.Key_F7: "\x1b[18~",
        Qt.Key_F8: "\x1b[19~",
        Qt.Key_F9: "\x1b[20~",
        Qt.Key_F10: "\x1b[21~",
        Qt.Key_F11: "\x1b[23~",
        Qt.Key_F12: "\x1b[24~",
    }
    
    def __init__(self, writer: Callable[[str], None], parent: QWidget | None = None) -> None:
        """
        Create a selectable but non-editable terminal surface.
        
        Args:
            writer: Callback that accepts text to send to the PTY.
            parent: Qt owner if the view.
        """
        super().__init__(parent)
        self._writer = writer
        
        # This is the central protection rule: Qt never edits terminal output
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        
        # Terminals use fixed columns; wrapping would corrupt cursor position;
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setFocusPolicy(Qt.StrongFocus)

        # Read-only does not prevent selecting text for copying.
        self.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Translate a Qt key press into terminal input or clipboard behavior.

        Args:
            event: Qt event containing the key, modifiers, and typed text.

        The base QPlainTextEdit implementation is intentionally never called,
        because doing so could move or edit the protected document caret.
        """
        modifiers = event.modifiers()
        key = event.key()
        control = bool(modifiers & Qt.ControlModifier)
        shift = bool(modifiers & Qt.ShiftModifier)

        # Ctrl+Shift+C and Ctrl+Insert are explicit terminal copy shortcuts.
        # They never become Ctrl+C/interrupt when no selection exists.
        if (control and shift and key == Qt.Key_C) or (
                control and key == Qt.Key_Insert
        ):
            if self.textCursor().hasSelection():
                self.copy()
            return

        # Standard Ctrl+C/Cmd+C copies selected terminal output.
        if event.matches(QKeySequence.Copy) and self.textCursor().hasSelection():
            self.copy()
            return

        # Support both GUI-standard and conventional terminal paste shortcuts.
        if (
                event.matches(QKeySequence.Paste)
                or (control and shift and key == Qt.Key_V)
                or (shift and key == Qt.Key_Insert)
        ):
            self._paste_clipboard()
            return

        # Shift+PageUp/PageDown scrolls local history. Without Shift, the key
        # is sent to the foreground terminal application.
        if shift and key in (Qt.Key_PageUp, Qt.Key_PageDown):
            scrollbar = self.verticalScrollBar()
            direction = -1 if key == Qt.Key_PageUp else 1
            scrollbar.setValue(
                scrollbar.value() + direction * scrollbar.pageStep()
            )
            return

        # A terminal Enter key is carriage return, not a GUI newline insert.
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._writer("\r")
            return

        if key == Qt.Key_Backspace:
            self._writer("\x7f")
            return

        if key == Qt.Key_Tab:
            self._writer("\t")
            return

        if key == Qt.Key_Backtab:
            self._writer("\x1b[Z")
            return

        if key == Qt.Key_Escape:
            self._writer("\x1b")
            return

        sequence = self._KEY_SEQUENCES.get(key)
        if sequence is not None:
            self._writer(sequence)
            return

        # Ctrl+A..Ctrl+Z are ASCII values 1..26. This includes Ctrl+C for
        # interrupt, Ctrl+D for EOF, and Ctrl+L for clear screen.
        if (
                control
                and not (modifiers & Qt.AltModifier)
                and Qt.Key_A <= key <= Qt.Key_Z
        ):
            self._writer(chr(key - Qt.Key_A + 1))
            return

        text = event.text()
        if text and not (modifiers & Qt.MetaModifier):
            # Alt+key is encoded as Escape followed by the typed character.
            # Ctrl+Alt is left untouched so AltGr can type @, {, }, and similar
            # characters on international Windows keyboards.
            if modifiers & Qt.AltModifier and not control:
                text = "\x1b" + text
            self._writer(text)

    def _paste_clipboard(self) -> None:
        """Send clipboard text to the shell using terminal newlines.

        Qt clipboard text may contain LF or CRLF line endings. A terminal Enter
        key is represented by carriage return, so pasted line endings are
        normalized before the text is sent.
        """
        text = QApplication.clipboard().text()
        if not text:
            return

        # Replace CRLF first so its LF is not converted a second time.
        terminal_text = text.replace("\r\n", "\r").replace("\n", "\r")
        self._writer(terminal_text)

    def contextMenuEvent(self, event) -> None:
        """Show Copy, Paste, and Select All actions for the terminal.

        Args:
            event: Context-menu event providing the global popup position.

        The default read-only QPlainTextEdit menu disables Paste, so the menu is
        rebuilt to send pasted text through the PTY callback.
        """
        menu = self.createStandardContextMenu()
        menu.clear()

        copy_action = QAction("Copy", menu)
        copy_action.setShortcut(QKeySequence.Copy)
        copy_action.setEnabled(self.textCursor().hasSelection())
        copy_action.triggered.connect(self.copy)
        menu.addAction(copy_action)

        paste_action = QAction("Paste", menu)
        paste_action.setShortcut(QKeySequence.Paste)
        paste_action.triggered.connect(self._paste_clipboard)
        menu.addAction(paste_action)

        menu.addSeparator()

        select_all_action = QAction("Select All", menu)
        select_all_action.setShortcut(QKeySequence.SelectAll)
        select_all_action.triggered.connect(self.selectAll)
        menu.addAction(select_all_action)

        menu.exec_(event.globalPos())
        menu.deleteLater()

class TerminalWidget(QWidget):
    """Coordinate the shell selector, PTY worker, and ANSI terminal screen.

    One TerminalWidget owns at most one active _PtySession. Shell changes are
    queued until the previous worker reports that it has exited.
    """

    _MIN_COLUMNS = 20
    _MIN_ROWS = 4
    _SCROLLBACK_LINES = 5000

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the terminal and start the preferred available shell.

        Args:
            parent: Qt owner, normally MarkdownEditor’s MainWindow.
        """
        super().__init__(parent)

        # Each entry is (display name, executable path, argument list).
        self._shells: list[tuple[str, str, list[str]]] = []

        # Only one reader/session may be active at a time.
        self._session: _PtySession | None = None

        # A requested replacement waits here while the current shell closes.
        self._queued_shell: tuple[str, list[str]] | None = None
        self._stopping = False

        # Safe defaults are replaced with measured values before PTY launch.
        self._rows = 24
        self._columns = 80

        # Dock resizing emits many events; the timer combines them into one
        # PTY resize after the user pauses dragging.
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(60)
        self._resize_timer.timeout.connect(self._apply_terminal_size)

        self._init_ui()
        self._connect_signals()
        self._reset_emulator()

        # Adding the first QComboBox item changes currentIndex. Blocking signals
        # prevents accidental shell launch while the list is being populated.
        self.shell_combo.blockSignals(True)
        self._populate_shell_combo_method()
        self.shell_combo.blockSignals(False)
        self._detect_and_start_default()

        app = QApplication.instance()
        if app is not None:
            # Final bounded wait prevents QThread destruction during app exit.
            app.aboutToQuit.connect(self._shutdown_and_wait)

    def _init_ui(self) -> None:
        """Create a toolbar above a terminal view that fills this widget.

        Passing `self` to QVBoxLayout installs the layout on TerminalWidget.
        The output view then receives all remaining dock space instead of
        retaining only its small size hint.
        """
        # Supplying self is the functional fix for the narrow terminal.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)

        self.status_label = QLabel("● Terminal ready")
        self.status_label.setStyleSheet(
            "color:#98c379; font-size:12px;"
        )

        self.shell_combo = QComboBox()
        self.shell_combo.setToolTip("Select which shell to use")
        self.shell_combo.setStyleSheet(
            """
            QComboBox {
                background-color:#2c313a;
                color:#dcdfe4;
                border:1px solid #3d324d;
                border-radius:3px;
                padding:3px 8px;
                min-width:100px;
            }
            QComboBox:hover {
                border:1px solid #4b5263;
            }
            QComboBox::drop-down {
                border:none;
                width:20px;
            }
            QComboBox QAbstractItemView {
                background-color:#2c313a;
                color:#dcdfe4;
                selection-background-color:#3d424d;
                border:1px solid #3d424d;
            }
            """
        )

        self.restart_btn = QPushButton("Restart")
        self.restart_btn.setToolTip(
            "Restart using the selected shell"
        )

        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setToolTip("Clear terminal output")

        toolbar.addWidget(self.status_label)
        toolbar.addWidget(self.shell_combo)

        # The stretch consumes middle space and keeps action buttons aligned
        # to the right edge of the dock.
        toolbar.addStretch()
        toolbar.addWidget(self.restart_btn)
        toolbar.addWidget(self.clear_btn)
        layout.addLayout(toolbar)

        # There is deliberately no QLineEdit. TerminalView is protected from
        # editing, while keyPressEvent forwards input to PowerShell's cursor.
        self.output = TerminalView(self._write_to_shell, self)
        self.output.setFont(QFont("Consolas", 12))
        self.output.setStyleSheet(
            "QPlainTextEdit {"
            "background:#1e2127;"
            "color:#abb2bf;"
            "border:none;"
            "padding:4px;"
            "selection-background-color:#3e4451;"
            "}"
        )
        self.output.setToolTip(
            "Up/Down: command history. "
            "Ctrl+Shift+C/V: copy/paste."
        )

        # With the layout installed on self, this widget expands in both
        # directions to occupy the dock's remaining area.
        layout.addWidget(self.output, stretch=1)

    def is_running(self) -> bool:
        """Return whether this widget still owns a running PTY worker.

        Returns:
            True while the active _PtySession QThread is running. False when no
            session exists or its worker has completed.

        MainWindow uses this method while asynchronously waiting for background
        services to stop during application shutdown.
        """
        session = self._session

        # Keep the private worker implementation inside TerminalWidget. Callers
        # receive one stable Boolean instead of depending on QThread details.
        return session is not None and session.isRunning()

    def _connect_signals(self) -> None:
        """Connect controls that exist for the widget's entire lifetime."""
        self.clear_btn.clicked.connect(self._clear)
        self.restart_btn.clicked.connect(self._restart_shell)
        self.shell_combo.currentIndexChanged.connect(self._on_shell_changed)

    def _detect_available_shells(self) -> list[tuple[str, str, list[str]]]:
        """Return installed shells in preferred launch order.

        Returns:
            Tuples containing display name, executable path, and arguments.
            PowerShell 7 is preferred on Windows. The user's SHELL is preferred
            on POSIX systems, with duplicates removed.
        """
        shells: list[tuple[str, str, list[str]]] = []

        if sys.platform == "win32":
            pwsh = shutil.which("pwsh")
            powershell = shutil.which("powershell")

            if pwsh:
                # -NoLogo starts faster and avoids an unnecessary banner.
                shells.append(("PowerShell 7", pwsh, ["-NoLogo"]))

            if powershell:
                shells.append(("PowerShell", powershell, ["-NoLogo"]))

            # COMSPEC normally points to the correct system cmd.exe.
            command_prompt = os.environ.get("COMSPEC", "cmd.exe")
            shells.append(("Command Prompt", command_prompt, ["/Q"]))
            return shells

        preferred: list[str] = []
        login_shell = os.environ.get("SHELL")
        if login_shell:
            preferred.append(Path(login_shell).name)

        # macOS normally uses Zsh; Linux commonly uses Bash.
        if sys.platform == "darwin":
            preferred.extend(["zsh", "bash", "fish"])
        else:
            preferred.extend(["bash", "zsh", "fish"])

        labels = {"bash": "Bash", "zsh": "Zsh", "fish": "Fish"}
        seen: set[str] = set()

        for name in preferred:
            path = shutil.which(name)
            if path and path not in seen:
                # -i explicitly requests interactive line editing and history.
                shells.append((labels.get(name, name), path, ["-i"]))
                seen.add(path)

        return shells

    def _populate_shell_combo_method(self) -> None:
        """Fill the shell selector without starting any process."""
        self._shells = self._detect_available_shells()
        self.shell_combo.clear()

        for display_name, _path, _arguments in self._shells:
            self.shell_combo.addItem(display_name)

    def _detect_and_start_default(self) -> None:
        """Start the preferred shell or display a no-shell message."""
        if not self._shells:
            self._feed_text("No supported shell was found.\r\n")
            return

        # Shell detection already sorted the list by preference.
        _display_name, shell_path, arguments = self._shells[0]
        self._start_shell(shell_path, arguments)

    def _on_shell_changed(self, index: int) -> None:
        """Request a restart with the shell selected in the toolbar.

        Args:
            index: New QComboBox index. Invalid indices are ignored.
        """
        if 0 <= index < len(self._shells):
            _display_name, shell_path, arguments = self._shells[index]
            self._start_shell(shell_path, arguments)

    def _restart_shell(self) -> None:
        """Restart the shell currently selected in the combo box."""
        self._on_shell_changed(self.shell_combo.currentIndex())

    def _start_shell(self, shell_path: str, args: list[str] | None = None) -> None:
        """Queue a shell and stop the old session before launching it.

        Args:
            shell_path: Absolute path or executable name for the new shell.
            args: Optional arguments passed directly to the shell.

        The newest request replaces any older queued request. This prevents two
        shells and two reader threads from owning the same terminal surface.
        """
        self._queued_shell = (shell_path, list(args or []))
        self._stopping = False

        if self._session is not None:
            self.status_label.setText("● Restarting terminal…")
            self._session.request_stop()
            return

        self._launch_queued_shell()

    def _launch_queued_shell(self) -> None:
        """
        Create the emulator and PTY worker for the queued shell.

        The method does nothing during application shutdown or when no shell is queued.
        Worker-specific signals are connected before start(), ensuring that fast
        startup failures cannot be missed.
        :return:
        """
        if self._queued_shell is None or self._stopping:
            return

        shell_path, arguments = self._queued_shell
        self._queued_shell = None

        self._measure_terminal_size()
        self._reset_emulator()

        # Copy instead of mutating os.environ globally.
        environment = dict(os.environ)

        # These values tell console programs which terminal capabilities exist.
        # They describe the PTY; they do not create it.
        environment.setdefault("TERM", "xterm-256color")
        environment.setdefault("COLORTERM", "truecolor")

        session = _PtySession([shell_path, *arguments], Path.home(), environment, self._rows, self._columns, self)

        # These connections are queued across the worker/GUI thread boundary.
        session.output_received.connect(self._feed_text)
        session.session_started.connect(self._on_session_started)
        session.session_failed.connect(self._on_session_failed)
        session.session_exited.connect(self._on_session_exited)

        self._session = session
        self.status_label.setText("● Starting terminal…")
        self.status_label.setStyleSheet("color:#e5c07b; font-size:12px;")
        session.start()

    def _reset_emulator(self) -> None:
        """Create an empty VT screen with bounded scrollback history."""
        self._screen = pyte.HistoryScreen(
            self._columns,
            self._rows,
            history=self._SCROLLBACK_LINES,
            ratio=0.5,
        )

        # Some VT queries ask the terminal to send a response to the process.
        self._screen.write_process_input = self._write_to_shell
        self._stream = pyte.Stream(self._screen)
        self._render_screen(force_bottom=True)

    def _feed_text(self, text: str) -> None:
        """Apply PTY output to the ANSI screen and repaint the Qt view.

        Args:
            text: Unicode output emitted by the PTY reader thread.
        """
        # feed() interprets escape sequences instead of displaying them.
        self._stream.feed(text)
        self._render_screen()

    @staticmethod
    def _history_line_text(line: Any, columns: int) -> str:
        """Convert one sparse pyte history row into plain display text.

        Args:
            line: Mapping from column indices to pyte character objects.
            columns: Current terminal width.

        Returns:
            The row text with unused trailing cells removed.
        """
        return "".join(
            line[column].data for column in range(columns)
        ).rstrip()

    def _render_screen(self, force_bottom: bool = False) -> None:
        """Render terminal history and the active screen into the protected view.

        Args:
            force_bottom: If True, scroll to the newest output regardless of the
                user's previous scrollbar position.

        A user reading older output remains at the same scrollbar value. A user
        already at the bottom follows new output automatically.
        """
        scrollbar = self.output.verticalScrollBar()

        # A two-pixel tolerance avoids rounding differences at the bottom.
        at_bottom = force_bottom or scrollbar.value() >= scrollbar.maximum() - 2
        old_value = scrollbar.value()

        history = [
            self._history_line_text(line, self._screen.columns)
            for line in self._screen.history.top
        ]

        # screen.display already reflects ANSI cursor movement and erasure.
        visible = [line.rstrip() for line in self._screen.display]
        self.output.setPlainText("\n".join([*history, *visible]))

        if at_bottom:
            scrollbar.setValue(scrollbar.maximum())
        else:
            scrollbar.setValue(min(old_value, scrollbar.maximum()))

        if at_bottom and not self._screen.cursor.hidden:
            # History lines appear before terminal-screen row zero.
            block_number = len(history) + self._screen.cursor.y
            block = self.output.document().findBlockByNumber(block_number)

            if block.isValid():
                cursor = QTextCursor(block)

                # A trimmed row can be shorter than the virtual cursor column.
                safe_column = min(
                    self._screen.cursor.x,
                    max(0, block.length() - 1),
                )
                cursor.movePosition(
                    QTextCursor.Right,
                    QTextCursor.MoveAnchor,
                    safe_column,
                )
                self.output.setTextCursor(cursor)

    def _write_to_shell(self, text: str) -> None:
        """Forward input or a terminal response to the active PTY.

        Args:
            text: Typed text, pasted text, a control character, or a VT response.
        """
        if self._session is not None:
            self._session.write(text)

    def _clear(self) -> None:
        """Clear local scrollback and ask the shell to redraw its prompt."""
        self._screen.reset()
        self._render_screen(force_bottom=True)

        # Ctrl+L is the clear-screen/readline command in supported shells.
        self._write_to_shell("\x0c")
        self.output.setFocus()

    def resizeEvent(self, event) -> None:
        """Schedule a terminal resize after the dock stops changing size.

        Args:
            event: Qt resize event. The base implementation still receives it.
        """
        super().resizeEvent(event)

        # Restarting a single-shot timer postpones work until resize activity
        # pauses for 60 milliseconds.
        self._resize_timer.start()

    def _measure_terminal_size(self) -> None:
        """Convert the output viewport from pixels to character cells."""
        metrics = self.output.fontMetrics()

        # Monospace "M" width represents one terminal column.
        cell_width = max(1, metrics.horizontalAdvance("M"))
        cell_height = max(1, metrics.lineSpacing())
        viewport = self.output.viewport().size()

        self._columns = max(
            self._MIN_COLUMNS,
            viewport.width() // cell_width,
            )
        self._rows = max(
            self._MIN_ROWS,
            viewport.height() // cell_height,
            )

    def _apply_terminal_size(self) -> None:
        """Resize the ANSI screen and PTY when measured geometry changed."""
        old_size = (self._rows, self._columns)
        self._measure_terminal_size()

        if old_size == (self._rows, self._columns):
            return

        # pyte resize arguments are (lines/rows, columns).
        self._screen.resize(self._rows, self._columns)

        if self._session is not None:
            self._session.resize_terminal(self._rows, self._columns)

        self._render_screen()

    def _on_session_started(self) -> None:
        """Mark a successfully spawned PTY as running and focus its view."""
        self.status_label.setText("● Terminal running")
        self.status_label.setStyleSheet("color:#98c379; font-size:12px;")
        self.output.setFocus()

    def _on_session_failed(self, message: str) -> None:
        """Display a worker startup or read error without crashing the editor.

        Args:
            message: Human-readable exception text emitted by _PtySession.
        """
        self.status_label.setText("● Terminal error")
        self.status_label.setStyleSheet("color:#e06c75; font-size:12px;")

        # Feed the message through the same screen pipeline as process output.
        self._feed_text(f"\r\n[Terminal error: {message}]\r\n")

    def _on_session_exited(self, exit_code: int) -> None:
        """Release the old worker and launch a queued replacement.

        Args:
            exit_code: Child exit status, or -1 when the backend had no status.
        """
        session = self.sender()

        # Ignore a stale queued signal from a worker no longer owned here.
        if session is not self._session:
            return

        self._session = None
        session.deleteLater()

        if self._queued_shell is not None and not self._stopping:
            self._launch_queued_shell()
            return

        if not self._stopping and exit_code != -1:
            self._feed_text(
                f"\r\n[Process exited with code {exit_code}]\r\n"
            )

        self.status_label.setText("● Terminal stopped")
        self.status_label.setStyleSheet("color:#e06c75; font-size:12px;")

    def stop(self) -> None:
        """Stop the active shell and discard every queued restart."""
        self._stopping = True
        self._queued_shell = None

        if self._session is not None:
            self._session.request_stop()

    def _shutdown_and_wait(self) -> None:
        """Perform a bounded wait before Qt destroys the PTY worker."""
        self.stop()

        if self._session is not None:
            # A short bound prevents indefinite GUI shutdown while giving the
            # reader time to leave after its PTY was closed.
            self._session.wait(1500)
