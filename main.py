import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu-shader-disk-cache")

import markdown
from PyQt5.Qsci import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWidgets import *

import resources_rc  # noqa: F401 - register resources generated from icons/resources.qrc
from code_inteligence.find_replace import FindReplaceBar
from code_inteligence.multi_tab_view import MultiTabView
from con_term.console_widget import ConsoleWidget
from con_term.terminal_widget import TerminalWidget
from cozy.cat_controller import CatController
from markdown_editor.markdowneditor import MarkdownEditor
from python_editor.python_runner import PythonRunner
from python_editor.pythoneditor import PythonEditor
from ruff_implementation.ruff_lsp_client import RuffLspClient
from side_bar_widgets.code_outline import CodeOutlineTree
from side_bar_widgets.file_manager import FileManager
from side_bar_widgets.fuzzy_searcher import SearchItem, SearchWorker

APP_VERSION = "v1.9.2"


def _excepthook(exc_type, exc, tb):
    try:
        log_dir = Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "crash_log.txt"
        if log_path.exists() and log_path.stat().st_size > 1024 * 1024:
            rotated = log_dir / "crash_log.1.txt"
            rotated.unlink(missing_ok=True)
            log_path.replace(rotated)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}]\n")
            traceback.print_exception(exc_type, exc, tb, file=f)
    except OSError:
        pass
    traceback.print_exception(exc_type, exc, tb)  # also as stderr


sys.excepthook = _excepthook


def resource_path(relative_path):
    """
    Get the absolute path to a bundled resource file.
    Works both when running from source (python main.py)
    and when running as a PyInstaller exe.

    When PyInstaller bundles your app, it extracts data files
    into a temporary folder. sys._MEIPASS points to that folder.
    When running from source, _MEIPASS doesn't exist, so we
    fall back to the current directory.

    Docs: https://pyinstaller.org/en/stable/runtime-information.html
    """
    root = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(__file__).resolve().parent
    return str(root / relative_path)


class MainWindow(QMainWindow):
    update_available = pyqtSignal(str, str)
    WIDTH = 1400
    HEIGHT = 900

    def __init__(self):
        super().__init__()
        self._dirty_editors = set()
        self.python_runner = PythonRunner(self)
        self.settings = QSettings("CodeEditor", "CodeEditor")
        self._hacker = self.settings.value("theme", "theme.json", type=str) == "hacker.json"
        self._pre_hacker_theme = "theme.json"
        # Load the saved recent files list, default to empty list
        self.recent_files = self.settings.value("recent_files", [], type=list)
        self.ruff_save_mode = self.settings.value("ruff_save_mode", "safe_format", type=str)
        self.update_available.connect(self._show_update_dialog)

        # Detect if running as a bundled exe
        if hasattr(sys, "_MEIPASS"):
            # Running as PyInstaller exe — sys.executable is the editor itself.
            # Try to find a real Python installation on the system.
            import shutil

            found_python = shutil.which("python") or shutil.which("python3")
            if found_python:
                self.python_runner.set_interpreter(found_python)
            else:
                # No Python found — load from settings or let user pick
                saved = self._load_interpreter()
                if saved != sys.executable:
                    self.python_runner.set_interpreter(saved)
                else:
                    # Prompt user to select a Python interpreter
                    QMessageBox.warning(
                        self,
                        "Python Interpreter Required",
                        "No Python installation found.\n"
                        "Please select your Python interpreter (python.exe).",
                    )
                    self.choose_interpreter()
        else:
            # Running from source — sys.executable is the real Python
            self.python_runner.set_interpreter(self._load_interpreter())
        # Create exactly one persistent Ruff server after the selected Python interpreter is known.
        # The interpreter must be the same environment where 'python -m ruff --version' succeeds.
        self.ruff_lsp_client = RuffLspClient(
            python_executable=self.python_runner.interpreter,
            workspace_root=Path(__file__).resolve().parent,
            parent=self,
        )
        # Infrastructure errors must be visible; otherwise a missing Ruff package or failed server
        # startup looks exactly like "no diagnostics" to the user.
        self.ruff_lsp_client.server_error.connect(self._on_ruff_lsp_error)
        self.ruff_lsp_client.start()

        self.console = None  # will be created in set_up_console_dock
        self.python_editor_active = False
        self.current_file = None
        self.md = markdown.Markdown(
            extensions=[
                "extra",
                "codehilite",
                "toc",
                "sane_lists",
                "nl2br",
                "admonition",
                "smarty",
                "meta",
            ]
        )
        self._preview_css = """
           body { font-family: sans-serif; max-width: 800px; margin: 2em auto 0;
                  padding: 0 1em 4em; background:#1e1f22; color:#dcdfe4; }
           h1,h2 { border-bottom:1px solid #444; padding-bottom:.3em; }
           code { background:#1e1f22; padding:2px 5px; border-radius:3px; }
           pre { background:#1e1f22; padding:1em; border-radius:6px; overflow-x:auto; }
           """
        self.init_ui()

        if hasattr(sys, "_MEIPASS"):
            # We're running as a bundled exe
            import os

            home = os.path.expanduser("~")
            self.file_manager.model.setRootPath(home)
            self.file_manager.setRootIndex(self.file_manager.model.index(home))
        # Reopen the tabs from the previous sessions (no-op when
        # the feature is disabled or nothing was saved)
        self._apply_settings(self._load_settings())
        if self._hacker:
            self._set_scanlines_visible(True)
        self._restore_session()

    def init_ui(self):
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(100)
        self._debounce.timeout.connect(self.render_preview)

        self.setWindowTitle("Code Editor")
        self.setWindowIcon(QIcon(resource_path("icons/app-icon-256.png")))
        self.resize(self.WIDTH, self.HEIGHT)
        self.window_font = QFont("sans-serif")
        self.window_font.setPointSize(13)
        self.setFont(self.window_font)

        qss_path = resource_path("css/style.qss")
        print(f"QSS path: {qss_path}")
        print(f"QSS exists: {os.path.exists(qss_path)}")
        try:
            with open(qss_path) as f:
                self.setStyleSheet(f.read())
        except FileNotFoundError as e:
            print(f"STYLESHEET ERROR: {e}")
            # Fallback: apply basic dark styling inline
            self.setStyleSheet("""
                QMainWindow { background-color: #1e1f22; color: #d3d3d3; }
                QMenuBar { background-color: #2d2d2d; color: floralwhite; }
                QTabWidget { background-color: #1e1f22; color: #d3d3d3; }
            """)

        self._outline_debounce = QTimer(self)
        self._outline_debounce.setSingleShot(True)
        self._outline_debounce.setInterval(500)
        # BUGFIX: was connected to its own start() -> restarted itself forever
        self._outline_debounce.timeout.connect(self._update_outline)

        self.set_up_menu()
        self.set_up_body()

        self._git_refresh_timer = QTimer(self)
        self._git_refresh_timer.setInterval(15000)
        self._git_refresh_timer.timeout.connect(self.file_manager.check_git_status)
        self._git_refresh_timer.start()

        # Find/Replace bar. Hidden by default
        self.find_bar = FindReplaceBar(show_replace=False, parent=self)
        self.find_bar.hide()

        # Connect the find bar's signals to our search methods
        self.find_bar.find_next_requested.connect(self._do_find_next)
        self.find_bar.find_prev_requested.connect(self._do_find_prev)
        self.find_bar.replace_requested.connect(self._do_replace)
        self.find_bar.replace_all_requested.connect(self._do_replace_all)

        self.set_up_console_dock()

        self._preview_ready = False
        self.preview.loadFinished.connect(self._on_preview_loaded)
        base = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{self._preview_css}</style></head><body><div id="content"></div></body></html>"""
        self.preview.setHtml(base)
        self.statusBar().setFont(self.window_font)
        self.statusBar().setStyleSheet("""
                color: #CCCCCC;
        """)
        self.statusBar().showMessage(f"Interpreter: {self.python_runner.interpreter}")
        self.cursor_pos_label = QLabel("Ln 1, Col 1")
        self.cursor_pos_label.setStyleSheet("color: #888; padding: 0 10px;")
        self.statusBar().addPermanentWidget(self.cursor_pos_label)

        # --- Cozy Mode: the status bar cat ---------------------------------------------------------- #
        self.cat = CatController(self)
        self.statusBar().addPermanentWidget(self.cat)

        from cozy.neko import NekoChaser

        self.neko = NekoChaser(self)
        self.neko.start()

        # C2: level-ups plat rhe Dance frames and announce themselves
        self.cat.level_up.connect(
            lambda lvl, title: self.statusBar().showMessage(
                f"Cat leveled up: {title} (level {lvl})", 3000
            )
        )

        # C1: pet milestones (achievement logic lives in the handler, NOT in the lambda
        # keep lambdas dump, they cannot be extended
        self.cat.petted.connect(self._on_cat_petted)

        from cozy.power_mode import PowerModeController

        # Honor the saved Power Mode states from the View menu dropdown.
        self.power = PowerModeController(
            self,
            enabled=self.power_mode_action.isChecked(),
            shake_intensity=1,
            particles_on=self.power_particles_action.isChecked(),
            shake_on=self.power_shake_action.isChecked(),
            glow_enabled=self.power_glow_action.isChecked(),
        )

        self.python_runner.process_started.connect(self._on_run_started)
        self.python_runner.process_finished.connect(self._on_run_finished)

        # Word count label - shows "Words: X | Chars: Y"
        self.word_count_label = QLabel("Words: 0 | Chars: 0")
        self.word_count_label.setStyleSheet("color: #888; padding: 0 10px;")
        self.statusBar().addPermanentWidget(self.word_count_label)

        self.show()

        QTimer.singleShot(2000, self.check_for_updates)

        # --- idle choreography: sleepy -> box -> sleep ------------------ #
        # Escalation ladder: 60 s idle = yawn; 3 min = deep sleep; 10 min =
        # the cat moves into a box (and pops back out when you return).
        # Timers re-arm on activity via _connect_editor's textChanged below.
        self._cat_sleepy_timer = QTimer(self)
        self._cat_sleepy_timer.setSingleShot(True)
        self._cat_sleepy_timer.timeout.connect(lambda: self.cat.set_state("sleepy", 120000))

        self._cat_sleep_timer = QTimer(self)
        self._cat_sleep_timer.setSingleShot(True)
        self._cat_sleep_timer.timeout.connect(lambda: self.cat.set_state("sleep", 300000))

        self._cat_box_timer = QTimer(self)
        self._cat_box_timer.setSingleShot(True)
        self._cat_box_timer.timeout.connect(self._cat_moves_into_box)

        self._arm_cat_timers()

    def _arm_cat_timers(self):
        """(Re)start every idle-escalation timer. Called at startup and on
        every keystroke — a 60/180/600 s ladder, all restarting together."""
        self._cat_sleepy_timer.start(60000)
        self._cat_sleep_timer.start(90000)
        self._cat_box_timer.start(240000)

    def _cat_moves_into_box(self):
        """
        10 minutes idle: the cat moves into a box (Box1 frames) and dozes
        there (box_idle). When you type again, _connect_editor's textChanged
        pops it out with box_pop (Box2: rising out of the box) — a tiny
        welcome-back animation. DeadCat/lay_down have their own triggers
        (crash log / long combo) and stay out of this ladder.
        """
        self.cat.set_state("box", 5_000)
        self._cat_boxed = True
        QTimer.singleShot(5_000, lambda: self.cat.set_state("box_idle", 120_000))

    def _update_outline(self):
        editor = self.current_editor()
        if isinstance(editor, PythonEditor):
            self.outline_tree.update_outline(editor.text())

    def update_word_count(self):
        """Update the word/character count in the status bar."""
        editor = self.current_editor()

        # Only show word count for Markdown files
        if editor is None or not isinstance(editor, MarkdownEditor):
            self.word_count_label.setText("")
            return

        text = editor.text()
        # Count words: split the text by whitespace and count the pieces
        # split() with no argument splits on ANY whitespace (spaces, tabs, newlines) and removes empty strings automatically.
        words = len(text.split())

        # Count characters: len(text) includes whitespace and newlines
        chars = len(text)

        self.word_count_label.setText(f"Words: {words} | Chars: {chars}")

    def _on_cat_petted(self, total_pets: int):
        """
        C1: react to pet milestones.

        Fires on EVERY pet (the sigggnal carries the total), but the checks are
        exact matches so each milestone fires exactlz one. A >= check would re-triggggger
        on every pet past the treshold.

        :param total_pets: new lifetime pet count.
        """
        if total_pets == 1000:
            self.cat.set_party_hat(True)
            self.statusBar().showMessage("Achievement unlocked: Certified Cat Person", 5000)

    def _on_run_started(self):
        """
        C4: excited typing (Excited frames) while the process is alive.
        60 s duration: the finished handler overrides by priority —
        stretch/look_away/cry are priority 6, type_excited is 4.
        """
        self.cat.set_state("type_excited", 40000)

    def _on_run_finished(self, exit_code: int):
        """
        C4: stretch + XP on success; sad (look_away) or crying on failure.

        :param exit_code: process exit code; 0 = success
        """
        if exit_code == 0:
            self.cat.set_state("stretch", 2500)
            self.cat.add_xp(10)
            r = self.tab_view.geometry()  # center of the editor area
            self.power.particles.burst(
                r.width() // 2, r.height() // 3, count=40
            )  # big celebratory burst
        else:
            # Sad frames for a normal failure, Cry frames when the console
            # shows a traceback (crash). If you don't distinguish yet, use
            # look_away for both.
            self.cat.set_state("look_away", 3000)

    def _toggle_power_mode(self, on: bool):
        """
        B1/C7: master switch for Power Mode (View -> Power Mode -> Enabled).

        Turning it off also clears any in-flight particles, stops the
        shake and the glow (the controller's set_enabled does that), so
        nothing keeps playing after the switch. The individual effect
        toggles stay as they were and take effect again when the master
        switch is back on. Persisted in QSettings so the next launch
        starts in the same mode.

        :param on: True = Power Mode enabled (subject to effect toggles)
        """
        power = getattr(self, "power", None)
        if power is not None:
            power.set_enabled(on)
        self.settings.setValue("power_mode", on)
        self.statusBar().showMessage("Power Mode ON" if on else "Power Mode OFF", 2000)

    def _toggle_power_particles(self, on: bool):
        """B1: particles-only switch (View -> Power Mode -> Particles)."""
        power = getattr(self, "power", None)
        if power is not None:
            power.set_particles(on)
        self.settings.setValue("power_particles", on)
        self.statusBar().showMessage("Particles ON" if on else "Particles OFF", 2000)

    def _toggle_power_shake(self, on: bool):
        """B1: screen-shake-only switch (View -> Power Mode -> Screen Shake)."""
        power = getattr(self, "power", None)
        if power is not None:
            power.set_shake_enabled(on)
        self.settings.setValue("power_shake", on)
        self.statusBar().showMessage("Screen Shake ON" if on else "Screen Shake OFF", 2000)

    def _toggle_power_glow(self, on: bool):
        """C7: combo-glow-only switch (View -> Power Mode -> Combo Glow)."""
        power = getattr(self, "power", None)
        if power is not None:
            power.set_glow(on)
        self.settings.setValue("power_glow", on)
        self.statusBar().showMessage("Combo Glow ON" if on else "Combo Glow OFF", 2000)

    def _on_typing_xp(self):
        """
        C2: 1 XP per 10 textChanged invocations.

        Why count invocations instead of document length:
            textChanged also fires on deletions, pastes and programmatic setText. A length-based
            rule would grant XP for OPENING a big file; a fixed 10-invocation counter
            is immune to document size and direction fo the edit.
        """
        from cozy.cat_controller import XP_RULES

        self._xp_key_counter = getattr(self, "_xp_key_counter", 0) + 1
        if self._xp_key_counter >= XP_RULES["keys_per_xp"]:
            self._xp_key_counter = 0
            self.cat.add_xp(1)

    def _on_editor_text_changed(self, editor):
        """Mark the specific editor that emitted textChanged as dirty."""
        if editor is None:
            return
        if getattr(editor, "_loading_text", False):
            return
        self._dirty_editors.add(editor)
        path = getattr(editor, "path", None)
        base_title = path.name if path is not None else "Untitled"
        self.tab_view.set_editor_title(editor, f"● {base_title}")
        if editor is self.current_editor():
            self.update_word_count()
            if isinstance(editor, MarkdownEditor):
                self._debounce.start()

    def mark_editor_clean(self, editor):
        """Remove dirty state after a sucessfull save."""
        if editor is None:
            return
        self._dirty_editors.discard(editor)
        editor.setModified(False)
        path = getattr(editor, "path", None)
        title = path.name if path is not None else "Untitled"
        self.tab_view.set_editor_title(editor, title)
        if getattr(editor, "path", None) is not None:
            self.file_manager.check_git_status()

    def update_cursor_position(self, line: int, index: int):
        """
        Update the Ln/Col display in the status bar.

        QScintilla uses 0-based line and column numbers.
        Users expect 1-based, so we add 1 to both.
        """
        self.cursor_pos_label.setText(f"Ln {line + 1}, Col {index + 1}")

    def set_up_console_dock(self):
        """Create the bottom console panel."""
        self.console = ConsoleWidget(self.python_runner, self)
        self.console.run_btn.clicked.connect(self.run_current_file)

        dock = QDockWidget("Python Console", self)
        dock.setWidget(self.console)
        dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)

        dock.hide()
        self.console_dock = dock

        # Terminal (new)
        self.terminal = TerminalWidget(self)
        terminal_dock = QDockWidget("Terminal", self)
        terminal_dock.setWidget(self.terminal)
        terminal_dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        self.addDockWidget(Qt.BottomDockWidgetArea, terminal_dock)

        terminal_dock.hide()
        self.terminal_dock = terminal_dock

    def get_sidebar_label(self, path, name):
        label = QLabel(self)
        label.setPixmap(QPixmap(path).scaled(QSize(25, 25)))
        label.setAlignment(Qt.AlignmentFlag.AlignTop)
        label.setFont(self.window_font)
        label.mousePressEvent = lambda e: self.show_hide_tab(e, name)
        return label

    def get_editor(self, path: Path = None, is_python_file=None) -> QsciScintilla:
        """Create the correct editor type for this individual document."""
        # A saved file chooses its editor based on file extension. This prevents
        # a .py file from being opened as MarkdownEditor, where Ruff cannot run.
        if path is not None and is_python_file is None:
            path = Path(path)
            is_python_file = path.suffix.lower() in {".py", ".pyw", ".pyi"}
        # An untitled document has no extension yet, so use the active editor mode.
        elif is_python_file is None:
            is_python_file = self.python_editor_active
        if is_python_file:
            # A .py tab recieves the shared language-server client. Each tab creats its own RuffLspController,
            # but all controllers share this one QProcess
            editor = PythonEditor(
                path=path, is_python_file=True, ruff_lsp_client=self.ruff_lsp_client
            )
        else:
            # Markdown documents never open an LSP Python document
            editor = MarkdownEditor(path=path, is_python_file=False)

        # BUGFIX: a newly opened tab used the hard-coded __init__ look and
        # ignored everything saved in the Settings dialog. Apply the saved
        # settings (font, wrap, margins, theme) so every tab matches.
        saved = getattr(self, "_current_settings", None)
        if saved:
            self._apply_editor_settings(editor, saved)
        return editor

    def current_editor(self):
        """Return the editor focused in the active tab group."""
        return self.tab_view.current_editor()

    def _connect_editor(self, editor):
        """Connect all MainWindow signals required by an editor."""
        editor.textChanged.connect(lambda ed=editor: self._on_editor_text_changed(ed))
        editor.textChanged.connect(self._debounce.start)
        editor.textChanged.connect(self._outline_debounce.start)
        editor.textChanged.connect(self.update_word_count)
        editor.textChanged.connect(self._on_typing_xp)
        editor.textChanged.connect(lambda: self.power.attach_editor(editor))
        editor.textChanged.connect(self._arm_cat_timers)
        editor.textChanged.connect(self._cat_unbox)
        editor.cursorPositionChanged.connect(
            lambda line, column, ed=editor: self._on_editor_cursor_changed(ed, line, column)
        )
        editor.cursorPositionChanged.connect(lambda line, column, ed=editor: self.sync_scroll(ed))
        editor.verticalScrollBar().valueChanged.connect(
            lambda value, ed=editor: self.sync_scroll(ed)
        )
        if isinstance(editor, PythonEditor):
            editor.goto_definition_requested.connect(self._open_file_at_position)

    def _cat_unbox(self):
        """
        If the cat was boxed (10+ min idle), pop it out on the first
        keystroke - then stop reacting until it boxes again.
        """
        if getattr(self, "_cat_boxed", False):
            self._cat_boxed = False
            self.cat.set_state("box_pop", 2000)

    def _on_ruff_lsp_error(self, message: str):
        """Expose Ruff LSP startup and protocol failures to the user."""
        print(f"Ruff LSP error {message}")
        self.statusBar().showMessage(message, 8000)

    def _restart_ruff(self, python_executable: str):
        """Replace the shared Ruff server and reconnect every Python tab."""
        old_client = self.ruff_lsp_client
        client = RuffLspClient(
            python_executable=python_executable,
            workspace_root=Path(__file__).resolve().parent,
            parent=self,
        )
        client.server_error.connect(self._on_ruff_lsp_error)
        self.ruff_lsp_client = client
        for editor in self.tab_view.all_editors():
            if isinstance(editor, PythonEditor) and editor.ruff_lsp is not None:
                editor.ruff_lsp.set_client(client)
        old_client.shutdown()
        client.start()

    def _on_editor_cursor_changed(self, editor, line: int, column: int):
        """Update the status bar only for the focused editor."""
        if editor is not self.current_editor():
            return
        self.update_cursor_position(line, column)

    def set_up_menu(self):
        menu_bar = self.menuBar()

        # File menu
        file_menu = menu_bar.addMenu("File")

        new_file = file_menu.addAction("New")
        new_file.setShortcut("Ctrl+N")
        new_file.setShortcutContext(Qt.ApplicationShortcut)
        new_file.triggered.connect(self.new_file)

        open_file = file_menu.addAction("Open File")
        open_file.setShortcut("Ctrl+O")
        open_file.setShortcutContext(Qt.ApplicationShortcut)
        open_file.triggered.connect(self.open_file)

        # Open Recent submenu
        # addMenu returns a QMenu object. We store it as an instance
        # variable so we can rebuild it when the recent files list changes.
        self.recent_menu = file_menu.addMenu("Open Recent")
        self._update_recent_menu()

        open_folder = file_menu.addAction("Open Folder")
        open_folder.setShortcut("Ctrl+K")
        open_folder.setShortcutContext(Qt.ApplicationShortcut)
        open_folder.triggered.connect(self.open_folder)

        file_menu.addSeparator()
        save_file = file_menu.addAction("Save")
        save_file.setShortcut("Ctrl+S")
        save_file.setShortcutContext(Qt.ApplicationShortcut)
        save_file.triggered.connect(self.save_file)

        save_as = file_menu.addAction("Save As")
        save_as.setShortcut("Ctrl+Shift+S")
        save_as.setShortcutContext(Qt.ApplicationShortcut)
        save_as.triggered.connect(self.save_as)

        save_all_action = file_menu.addAction("Save All")
        save_all_action.setShortcut("Ctrl+Shift+A")
        save_all_action.setShortcutContext(Qt.ApplicationShortcut)
        save_all_action.triggered.connect(self.save_all)

        close_all_action = file_menu.addAction("Close All")
        close_all_action.setShortcut("Ctrl+Shift+W")
        close_all_action.setShortcutContext(Qt.ApplicationShortcut)
        close_all_action.triggered.connect(self._close_all_tabs)

        # Edit menu
        edit_menu = menu_bar.addMenu("Edit")

        undo_action = edit_menu.addAction("Undo")
        undo_action.setShortcut("Ctrl+Z")
        undo_action.setShortcutContext(Qt.ApplicationShortcut)
        undo_action.triggered.connect(self.undo)

        redo_action = edit_menu.addAction("Redo")
        redo_action.setShortcut("Ctrl+Shift+Z")
        redo_action.setShortcutContext(Qt.ApplicationShortcut)
        redo_action.triggered.connect(self.redo)

        edit_menu.addSeparator()

        cut_action = edit_menu.addAction("Cut")
        cut_action.setShortcut("Ctrl+X")
        cut_action.setShortcutContext(Qt.ApplicationShortcut)
        cut_action.triggered.connect(self.cut)

        copy_action = edit_menu.addAction("Copy")
        copy_action.setShortcut("Ctrl+C")
        copy_action.setShortcutContext(Qt.ApplicationShortcut)
        copy_action.triggered.connect(self.copy)

        paste_action = edit_menu.addAction("Paste")
        paste_action.setShortcut("Ctrl+V")
        paste_action.setShortcutContext(Qt.ApplicationShortcut)
        paste_action.triggered.connect(self.paste)

        edit_menu.addSeparator()

        select_all_action = edit_menu.addAction("Select All")
        select_all_action.setShortcut("Ctrl+A")
        select_all_action.setShortcutContext(Qt.ApplicationShortcut)
        select_all_action.triggered.connect(self.select_all)

        delete_line_action = edit_menu.addAction("Delete Line")
        delete_line_action.setShortcut("Ctrl+Shift+K")
        delete_line_action.setShortcutContext(Qt.ApplicationShortcut)
        delete_line_action.triggered.connect(self.delete_line)

        edit_menu.addSeparator()

        find_action = edit_menu.addAction("Find")
        find_action.setShortcut("Ctrl+F")
        find_action.setShortcutContext(Qt.ApplicationShortcut)
        find_action.triggered.connect(self.show_find_bar)

        replace_action = edit_menu.addAction("Replace")
        replace_action.setShortcut("Ctrl+H")
        replace_action.setShortcutContext(Qt.ApplicationShortcut)
        replace_action.triggered.connect(self.show_replace_bar)

        edit_menu.addSeparator()

        goto_action = edit_menu.addAction("Go to Line")
        goto_action.setShortcut("Ctrl+G")
        goto_action.setShortcutContext(Qt.ApplicationShortcut)
        goto_action.triggered.connect(self.goto_line)

        edit_menu.addSeparator()

        toggle_comment_action = edit_menu.addAction("Toggle Comment")
        toggle_comment_action.setShortcut("Ctrl+1")
        toggle_comment_action.setShortcutContext(Qt.ApplicationShortcut)
        toggle_comment_action.triggered.connect(self.toggle_comment)

        edit_menu.addSeparator()
        goto_definition_action = edit_menu.addAction("Go to Definition")
        goto_definition_action.setShortcut("F12")
        goto_definition_action.setShortcutContext(Qt.ApplicationShortcut)
        goto_definition_action.triggered.connect(self._trigger_goto_definition)

        # Mode menu
        mode_menu = menu_bar.addMenu("Mode")

        # change to Python-Editor
        self.python_editor_action = mode_menu.addAction("Python Editor")
        self.python_editor_action.setShortcut(QKeySequence("Ctrl+Shift+P"))
        self.python_editor_action.setShortcutContext(Qt.ApplicationShortcut)
        self.python_editor_action.triggered.connect(self.change_editor_python)

        # change to Markdown-Editor
        self.markdown_editor_action = mode_menu.addAction("Markdown Editor")
        self.markdown_editor_action.setShortcut(QKeySequence("Ctrl+Shift+M"))
        self.markdown_editor_action.setShortcutContext(Qt.ApplicationShortcut)
        self.markdown_editor_action.triggered.connect(self.change_editor_markdown)

        run_menu = self.menuBar().addMenu("Run")

        run_file_action = run_menu.addAction("Run File")
        run_file_action.setShortcut("F5")
        run_file_action.setShortcutContext(Qt.ApplicationShortcut)
        run_file_action.triggered.connect(self.run_current_file)

        run_with_args_action = run_menu.addAction("Run with Arguments")
        run_with_args_action.setShortcut("Shift+F5")
        run_with_args_action.setShortcutContext(Qt.ApplicationShortcut)
        run_with_args_action.triggered.connect(self.run_with_arguments)

        run_selection_action = run_menu.addAction("Run Selection")
        run_selection_action.setShortcut("Ctrl+Return")
        run_selection_action.setShortcutContext(Qt.ApplicationShortcut)
        run_selection_action.triggered.connect(self.run_selection)

        stop_action = run_menu.addAction("Stop")
        stop_action.setShortcut("F6")
        stop_action.setShortcutContext(Qt.ApplicationShortcut)
        stop_action.triggered.connect(self.python_runner.stop)

        run_menu.addSeparator()

        interpreter_action = run_menu.addAction("Choose Interpreter...")
        interpreter_action.triggered.connect(self.choose_interpreter)

        # Viewmenu for toggling the Sidebar

        view_menu = self.menuBar().addMenu("View")

        toggle_sidebar_action = view_menu.addAction("Toggle Sidebar")
        toggle_sidebar_action.setShortcut("Ctrl+B")
        toggle_sidebar_action.setShortcutContext(Qt.ApplicationShortcut)
        toggle_sidebar_action.triggered.connect(self.toggle_sidebar)

        toggle_preview_action = view_menu.addAction("Toggle Preview")
        toggle_preview_action.setShortcut("Ctrl+J")
        toggle_preview_action.setShortcutContext(Qt.ApplicationShortcut)
        toggle_preview_action.triggered.connect(self.toggle_preview)

        toggle_console_action = view_menu.addAction("Toggle Console")
        toggle_console_action.setShortcut("Ctrl+Shift+-")
        toggle_console_action.setShortcutContext(Qt.ApplicationShortcut)
        toggle_console_action.triggered.connect(self.toggle_console)

        toggle_terminal_action = view_menu.addAction("Toggle Terminal")
        toggle_terminal_action.setShortcut("Ctrl+Shift+T")
        toggle_terminal_action.setShortcutContext(Qt.ApplicationShortcut)
        toggle_terminal_action.triggered.connect(self._toggle_terminal)

        view_menu.addSeparator()

        split_right_action = view_menu.addAction("Split Right")
        split_right_action.setShortcut("Ctrl+Alt+Right")
        split_right_action.setShortcutContext(Qt.ApplicationShortcut)
        split_right_action.triggered.connect(self.split_current_editor_right)

        unsplit_action = view_menu.addAction("Unsplit")
        unsplit_action.setShortcut("Ctrl+Alt+Left")
        unsplit_action.setShortcutContext(Qt.ApplicationShortcut)
        unsplit_action.triggered.connect(self.unsplit_active_group)

        view_menu.addSeparator()

        fullscreen_editor = view_menu.addAction("Fullscreen")
        fullscreen_editor.setShortcut("F11")
        fullscreen_editor.setShortcutContext(Qt.ApplicationShortcut)
        fullscreen_editor.triggered.connect(self._show_full_screen)

        starting_window_size = view_menu.addAction("Startup Window Size")
        starting_window_size.setShortcut("Shift+F11")
        starting_window_size.setShortcutContext(Qt.ApplicationShortcut)
        starting_window_size.triggered.connect(self._startup_window_size)

        view_menu.addSeparator()

        git_refresh_action = view_menu.addAction("Refresh Git Status")
        git_refresh_action.setShortcut("Ctrl+Shift+G")
        git_refresh_action.setShortcutContext(Qt.ApplicationShortcut)
        # BUGFIX: was "connect(self.file_manager.check_git_status)" — a
        # direct bound-method reference resolves self.file_manager RIGHT
        # HERE, but set_up_menu() runs BEFORE set_up_body() creates the
        # FileManager (line order in init_ui), so the attribute does not
        # exist yet -> AttributeError at startup. The lambda defers the
        # lookup to trigger time — the menu can only fire after the
        # window is shown, long after file_manager exists (same
        # launch-order rule as the cat / power-mode handlers).
        git_refresh_action.triggered.connect(lambda: self.file_manager.check_git_status())

        settings_action = view_menu.addAction("Settings")
        settings_action.setShortcut("Ctrl+Alt+S")
        settings_action.setShortcutContext(Qt.ApplicationShortcut)
        settings_action.triggered.connect(self.open_settings)

        hacker_action = view_menu.addAction("Hacker-Mode")
        hacker_action.setShortcut("Ctrl+Shift+H")
        hacker_action.setShortcutContext(Qt.ApplicationShortcut)
        hacker_action.setCheckable(True)
        hacker_action.setChecked(self._hacker)
        hacker_action.toggled.connect(self.set_hacker_mode)
        self.hacker_action = hacker_action

        # B1/C7: Power Mode dropdown — master switch plus one toggle per
        # effect. All four states persist in QSettings (power_mode,
        # power_particles, power_shake, power_glow) like ruff_save_mode.
        # The handlers guard on self.power because the menu is built
        # before the controller is created in init_ui().
        power_menu = view_menu.addMenu("Power Mode")

        self.power_mode_action = power_menu.addAction("Enabled")
        self.power_mode_action.setCheckable(True)
        self.power_mode_action.setChecked(self.settings.value("power_mode", True, type=bool))
        self.power_mode_action.setShortcut("Ctrl+Shift+X")
        self.power_mode_action.setShortcutContext(Qt.ApplicationShortcut)
        self.power_mode_action.toggled.connect(self._toggle_power_mode)

        power_menu.addSeparator()

        self.power_particles_action = power_menu.addAction("Particles")
        self.power_particles_action.setCheckable(True)
        self.power_particles_action.setChecked(
            self.settings.value("power_particles", True, type=bool)
        )
        self.power_particles_action.toggled.connect(self._toggle_power_particles)

        self.power_shake_action = power_menu.addAction("Screen Shake")
        self.power_shake_action.setCheckable(True)
        self.power_shake_action.setChecked(self.settings.value("power_shake", True, type=bool))
        self.power_shake_action.toggled.connect(self._toggle_power_shake)

        self.power_glow_action = power_menu.addAction("Combo Glow")
        self.power_glow_action.setCheckable(True)
        self.power_glow_action.setChecked(self.settings.value("power_glow", True, type=bool))
        self.power_glow_action.toggled.connect(self._toggle_power_glow)

        help_menu = menu_bar.addMenu("Help")

        check_updates_action = help_menu.addAction("Check for Updates")
        check_updates_action.triggered.connect(self.check_for_updates)

    def split_current_editor_right(self):
        editor = self.current_editor()

        if editor is None:
            return

        self.tab_view.split_right(editor)

    def unsplit_active_group(self):
        self.tab_view.unsplit_active_group()

    def _toggle_terminal(self):
        """Show or hide the terminal dock"""
        if not self.terminal_dock.isVisible():
            self.terminal_dock.show()
        else:
            self.terminal_dock.hide()

    def _trigger_goto_definition(self):
        editor = self.current_editor()
        if isinstance(editor, PythonEditor):
            editor.goto_definition()

    def _open_file_at_position(self, file_path: str, line: int, column: int):
        """Open a file and jump to a specific line/column."""
        editor = self.set_new_tab(Path(file_path))
        if editor is None:
            return
        editor.setCursorPosition(line, column)
        editor.ensureLineVisible(line)
        editor.setFocus()

    def run_with_arguments(self):
        """Save the current file, ask for arguments, then run it."""
        editor = self.current_editor()
        if editor is None:
            return

        path = getattr(editor, "path", None)
        if path is None:
            if not self.save_as():
                return
            path = getattr(editor, "path", None)
        elif not self.save_file():
            self.statusBar().showMessage("Run cancelled: save failed", 4000)
            return

        # QInputDialog.getText shows a dialog with a single text input.
        # Parameters: parent, title, label, echo mode, default text
        # Returns: (text, ok) where ok is True if user clicked OK
        args, ok = QInputDialog.getText(
            self,
            "Run with Arguments",
            "Command-line arguments",
            QLineEdit.Normal,
            "",
        )
        if not ok:
            # User cancelled
            return

        # Show the console
        self.console_dock.show()
        # Run the file with the arguments
        self.python_runner.run_file_with_args(Path(path), args, cwd=Path(path).parent)

    def save_all(self):
        saved_count = 0
        original_editor = self.current_editor()

        for editor in list(self.tab_view.all_editors()):
            if editor not in self._dirty_editors:
                continue
            path = getattr(editor, "path", None)

            if path is None:
                self.tab_view.focus_editor(editor)
                if self.save_as():
                    saved_count += 1
                    continue
                break
            if self._save_editor_to_path(editor, Path(path)):
                saved_count += 1

        if original_editor is not None:
            self.tab_view.focus_editor(original_editor)

        self.statusBar().showMessage(
            f"Saved {saved_count} file(s)",
            3000,
        )

    def _update_recent_menu(self):
        """Rebuild the 'Open Recent' submenu from the recent_files list."""
        self.recent_menu.clear()

        if not self.recent_files:
            empty_action = self.recent_menu.addAction("(No recent files)")
            empty_action.setEnabled(False)
            return

        for path in self.recent_files:
            action = self.recent_menu.addAction(Path(path).name)
            action.setData(path)
            action.triggered.connect(lambda checked, p=path: self.open_recent_file(p))

    def open_recent_file(self, path: str):
        """Open a file from the recent files list."""
        file_path = Path(path)
        if not file_path.exists():
            QMessageBox.information(
                self, "File Not Found", f"The file '{file_path.name}' no longer exists."
            )
            self.recent_files.remove(path)
            self._save_recent_files()
            self._update_recent_menu()
            return
        self.set_new_tab(file_path)

    def _add_to_recent_files(self, path: str):
        """Add a file path to the recent files list (max 10)."""
        path = str(path)

        if path in self.recent_files:
            self.recent_files.remove(path)

        self.recent_files.insert(0, path)
        self.recent_files = self.recent_files[:10]
        self._save_recent_files()
        self._update_recent_menu()

    def _save_recent_files(self):
        """Persist the recent files list to QSettings."""
        self.settings.setValue("recent_files", self.recent_files)

    def _show_full_screen(self):
        self.setWindowState(Qt.WindowState.WindowMaximized)
        self.show()

    def _startup_window_size(self):
        self.setWindowState(Qt.WindowState.WindowNoState)
        self.resize(self.WIDTH, self.HEIGHT)
        self.show()

    def toggle_comment(self):
        """Toggle # comment on the current line or selected lines."""
        editor = self.current_editor()
        if editor is None:
            return

        if not isinstance(editor, PythonEditor):
            return

        if editor.hasSelectedText():
            self._toggle_comment_selection(editor)
        else:
            self._toggle_comment_single_line(editor)

    def _toggle_comment_single_line(self, editor):
        """Toggle comment on the line where the cursor currently is."""
        line, index = editor.getCursorPosition()

        # Read the text of the current line
        # editor.text(line) returns the text of line `line`INCLUDING the newline
        line_text = editor.text(line)

        stripped = line_text.lstrip()
        if stripped.startswith("#"):
            # Uncomment: remove the first # we find
            hash_pos = line_text.index("#")

            # Check if there's a space after # (common fomratting: "# code")
            if hash_pos + 1 < len(line_text) and line_text[hash_pos + 1] == " ":
                # Remove just "# + space"
                editor.setSelection(line, hash_pos, line, hash_pos + 2)
            else:
                # Remove just "#"
                editor.setSelection(line, hash_pos, line, hash_pos + 1)
            editor.replace("")
        else:
            # Comment: insert "# " at the beginning of the line
            # insertAt(text, line, index) inserts text at the given position
            editor.insertAt("# ", line, 0)
        # Move cursor back to a sensible position
        editor.setCursorPosition(line, 0)
        editor.ensureLineVisible(line)

    def _toggle_comment_selection(self, editor):
        """Toggle comments on all lines in the current selection."""
        # Get the selection boundaries
        # getSelection returns (lineFrom, indexFrom, lineTo, indexTo)
        line_from, index_from, line_to, index_to = editor.getSelection()

        if line_from > line_to:
            line_from, line_to = line_to, line_from

        first_line_text = editor.text(line_from)
        is_commented = first_line_text.lstrip().startswith("#")

        # We need to modify lines from bottom to top when INSERTING text, because inserting at line N shifts all llines below it.
        # If we go top-to-bottom, line numbers would be wrong after the first insert.
        # When REMOVING text, we also go bottom-to-top to keep line numbers stable.

        if is_commented:
            # Uncomment all selected lines
            for line in range(line_to, line_from - 1, -1):
                line_text = editor.text(line)
                stripped = line_text.lstrip()
                if stripped.startswith("#"):
                    hash_pos = line_text.index("#")
                    if hash_pos + 1 < len(line_text) and line_text[hash_pos + 1] == " ":
                        editor.setSelection(line, hash_pos, line, hash_pos + 2)
                    else:
                        editor.setSelection(line, hash_pos, line, hash_pos + 1)
                    editor.replace("")
        else:
            # Comment ll selected lines
            # Go from BOTTOM to TOP so inserting "# " doesn't shift line numbers
            for line in range(line_to, line_from - 1, -1):
                editor.insertAt("# ", line, 0)
        # Restore the selection to cover all modified lines
        editor.setSelection(line_from, 0, line_to, editor.lineLength(line_to))

    def goto_line(self):
        """Open a dialog to jump to a specific line number."""
        editor = self.current_editor()
        if editor is None:
            return

        max_lines = editor.lines()
        # editor.lines () returns the total number of lines in the document.
        # We us this a sthe maximum value for the spin box so the user can't enter a line number that doesn't exitst.

        # QInputDialog.getInt shows a small dialog with a spin box.
        # Parameters: parent, title, label, default value, minimum, maximum
        # Returns: (value, ok) where ok is True if the user clicked ok
        # Docs: https://doc.qt.io/qt-5/qinputdialog.html#getInt
        line_num, ok = QInputDialog.getInt(
            self,
            "Go to Line",
            f"Line number (1-{max_lines}):",
            1,  # default value shown in the spin box
            1,  # minumum value
            max_lines,  # maximum value
        )

        if not ok:
            return

        target_line = line_num - 1
        editor.setCursorPosition(target_line, 0)
        editor.ensureLineVisible(target_line)
        editor.setFocus()

    def toggle_sidebar(self):
        """Hide/show the sidebar (side_bar + side_panel)."""
        # self.side_bar is the thin icon strip
        # self.side_panel is the wider panel with file manager / search
        currently_visible = self.side_bar.isVisible()
        self.side_bar.setVisible(not currently_visible)
        self.side_panel.setVisible(not currently_visible)

        # If showing, restore the splitter sizes so the panel has width
        if not currently_visible:
            # Give the sidebar 250px, let the editor and preview share the rest
            self.hs_split.setSizes([250, 575, 575])
        else:
            # Collapsing: set sidebar with to 0
            self.hs_split.setSizes([0, 575, 575])

    def toggle_preview(self):
        """Hide/Show the Markdown preview panel."""
        currently_visible = self.preview.isVisible()
        self.preview.setVisible(not currently_visible)

        if currently_visible:
            # Hiding preview: give all space to sidebar + editor
            self.hs_split.setSizes([250, 850, 0])
        else:
            # Showing preview: restore even split
            self.hs_split.setSizes([250, 575, 575])

    def toggle_console(self):
        """Hide/show the Python console dock."""
        if self.console_dock.isVisible():
            self.console_dock.hide()
        else:
            self.console_dock.show()

    def run_current_file(self):
        """Save and run the current Python file"""
        editor = self.current_editor()
        if editor is None:
            return

        path = getattr(editor, "path", None)
        if path is None:
            # Untitled file -> need to save first
            if not self.save_as():
                return
            path = getattr(editor, "path", None)
            if path is None:
                return  # User cancelled the sace dialog

        elif not self.save_file():
            self.statusBar().showMessage("Run cancelled: save failed", 4000)
            return

        # Show the console
        self.console_dock.show()

        # Run the file
        self.python_runner.run_file(Path(path), cwd=Path(path).parent)

    def run_selection(self):
        """Run just the selected text in the current editor."""
        editor = self.current_editor()
        if editor is None:
            return

        # Get selected text from QsciScintilla
        selected_text = editor.selectedText()
        if not selected_text:
            return

        self.console_dock.show()
        self.python_runner.run_code(selected_text)

    def choose_interpreter(self):
        """Let the user pick a Python executable."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Python Interpreter", "", "Python Executable (python.exe);;All Files (*)"
        )
        if path:
            previous = self.python_runner.interpreter
            self.python_runner.set_interpreter(path)
            self.statusBar().showMessage(f"Python Interpreter: {path}", 3000)
            self._save_interpreter(path)
            if previous != path and hasattr(self, "ruff_lsp_client"):
                self._restart_ruff(path)

    def _save_interpreter(self, path: str):
        """Persist the selected interpreter in the platform settings store."""
        self.settings.setValue("interpreter", path)
        self.settings.sync()

    def _load_interpreter(self) -> str:
        """Load a valid saved interpreter, or default to this Python."""
        path = self.settings.value("interpreter", sys.executable, type=str)
        return path if path and Path(path).is_file() else sys.executable

    def _load_settings(self) -> dict:
        """
        Load all user preferences from the platform-native QSettings store.
        """

        # --- QSettings side --- #
        # .value(key, default, type=) coerces the stored values: QSettings serialises booleans/ints as strings
        # on some platforms, and the type= argument converts them back safely.
        #
        # THEME-MANAGED values: font family/size and the paper color are NOT
        # stored in QSettings anymore — the active theme.json is their single
        # source of truth (read here, written by _write_theme_editor()).
        theme_editor = self._read_theme_editor()

        return {
            "interpreter": self.settings.value(
                "interpreter", self.python_runner.interpreter, type=str
            ),
            # BUGFIX: read/write key mismatch - this read "ruff_safe_mode"
            # while _save_settings() writes "ruff_save_mode", so the saved
            # Ruff mode never survived a restart.
            "ruff_save_mode": self.settings.value("ruff_save_mode", "safe_format", type=str),
            "font_family": theme_editor.get("font_family", "JetBrains Mono"),
            "font_size": theme_editor.get("font_size", 13),
            # paper: QSettings override (color picker) wins over the theme;
            # theme_paper is the theme's OWN value, so the dialog's
            # "Reset to theme" button can drop the override.
            "paper_color": (
                self.settings.value("paper_color", type=str)
                if self.settings.contains("paper_color")
                else None
            ),
            "theme_paper": theme_editor.get("paper_color", "#1e1f22"),
            "tab_width": self.settings.value("tab_width", 4, type=int),
            "word_wrap": self.settings.value("word_wrap", False, type=bool),
            "restore_tabs": self.settings.value("restore_tabs", True, type=bool),
            "theme": self.settings.value("theme", "theme.json", type=str),
            "line_numbers": self.settings.value("line_numbers", True, type=bool),
            "highlight_line": self.settings.value("highlight_line", True, type=bool),
        }

    def _save_settings(self, new_settings: dict):
        """Persist an edited settings dictionary to QSettings.
        Called only after the user clicked Save in the dialog.

        Parameters
        ----------
        new_settings : dict
            The dictionary returned by SettingsDialog.get_settings().
        """
        self.settings.setValue("interpreter", new_settings["interpreter"])

        # --- QSettings -------------------------------------------------- #
        # NOTE: font_family / font_size are NOT QSettings keys anymore —
        # they live in the active theme.json (see _write_theme_editor()).
        for key in (
            "ruff_save_mode",
            "tab_width",
            "word_wrap",
            "restore_tabs",
            "theme",
            "line_numbers",
            "highlight_line",
        ):
            self.settings.setValue(key, new_settings[key])
        if new_settings.get("paper_color") is None:
            self.settings.remove("paper_color")
        else:
            self.settings.setValue("paper_color", new_settings["paper_color"])
        self.settings.sync()

    def open_settings(self):
        """Open the settings dialog and apply the result if Save was hit.

        Flow: build dialog from current values → exec_() blocks until the
        dialog closes → on Accepted, persist and apply. exec_() runs a
        nested event loop, so the main window keeps painting while the
        dialog is open, but no other user code in this method runs until
        the dialog is dismissed (standard modal behaviour).
        """
        # Local import: the dialog module is only needed here, and a
        # local import keeps startup time down and avoids a hard
        # dependency if settings_dialog.py is temporarily broken.
        from code_settings.settings_dialog import SettingsDialog

        dialog = SettingsDialog(self._load_settings(), self)
        if dialog.exec_() == QDialog.Accepted:
            new_settings = dialog.get_settings()

            # Font changes are persisted INTO the active theme.json (its
            # editor.font block) — the theme is the single source of truth.
            # Everything else still goes through _save_settings/QSettings.
            if (new_settings["font_family"], new_settings["font_size"]) != (
                self._current_settings.get("font_family"),
                self._current_settings.get("font_size"),
            ):
                self._write_theme_editor(new_settings["font_family"], new_settings["font_size"])

            self._save_settings(new_settings)

            # ruff_save_mode is cached in __init__; keep the cache in
            # sync so _run_ruff_before_save() sees the new value
            # immediately (no restart needed).
            self.ruff_save_mode = new_settings["ruff_save_mode"]

            self._apply_settings(new_settings)
            self.statusBar().showMessage("Settings saved", 2000)

    def _apply_settings(self, settings: dict):
        """
        Push a settings dictionary onto every open editor.

        MultiTabView.all_editors() yields the editors of ALL tab groups
        (both halves of a split view), so one loop covers everything.
        The settings are also cached on self so new tabs opened later
        can inherit them via _apply_editor_settings().
        """
        self._current_settings = settings

        for editor in self.tab_view.all_editors():
            self._apply_editor_settings(editor, settings)

        if settings["interpreter"]:
            previous = self.python_runner.interpreter
            self.python_runner.set_interpreter(settings["interpreter"])
            self.statusBar().showMessage(f"Interpreter: {self.python_runner.interpreter}", 3000)
            if previous != settings["interpreter"] and hasattr(self, "ruff_lsp_client"):
                self._restart_ruff(settings["interpreter"])

    def set_hacker_mode(self, on: bool):
        """
        C17: the full bundle - CRT theme + sanlines in one switch.

        Reuses the settings machinery delliberately: theme switching, lexer recreation,
        thr margin color, fix and per-editor application were already built and debugged.
        Never write a second theme pipeline when one exists.

        :param on: True = hacker.json + scanlines; False = default theme
        """
        if on and not self._hacker:
            self._pre_hacker_theme = self.settings.value("theme", "theme.json", type=str)
        self._hacker = bool(on)
        self._set_scanlines_visible(self._hacker)

        settings = self._load_settings()
        settings["theme"] = "hacker.json" if self._hacker else self._pre_hacker_theme
        self._save_settings(settings)
        self._apply_settings(settings)

        self.statusBar().showMessage("HACK THE PLANET" if on else "Back to reality", 2500)

    def _set_scanlines_visible(self, visible: bool):
        from cozy.overlays import ScanlineOverlay

        if not hasattr(self, "scanlines"):
            self.scanlines = ScanlineOverlay(self)
        self.scanlines.setGeometry(self.rect())
        self.scanlines.setVisible(visible)
        if visible:
            self.scanlines.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "scanlines"):
            self.scanlines.setGeometry(self.rect())

    def _active_theme_path(self):
        """Absolute path of the theme file the settings currently name."""
        return self._theme_path(self.settings.value("theme", "theme.json", type=str))

    def _read_theme_editor(self) -> dict:
        """
        Read the editor section of the ACTIVE theme (font + paper).

        Returns a flat dict with font_family / font_size / paper_color so
        _load_settings can hand them to the Settings dialog like any other
        value. Falls back to JetBrains Mono 13 / #1e1f22 when the theme has
        no editor section (old themes keep working).

        The theme file is the single source of truth for these values —
        QSettings only stores WHICH theme is active.
        """
        path = self._active_theme_path() or str(
            Path(__file__).resolve().parent / "themes" / "theme.json"
        )
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            editor = data.get("theme", {}).get("editor", {})
            gfont = editor.get("font", {})
            return {
                "font_family": gfont.get("family", "JetBrains Mono"),
                "font_size": int(gfont.get("font-size", 13)),
                "paper_color": editor.get("paper-color", "#1e1f22"),
            }
        except (OSError, json.JSONDecodeError, ValueError):
            return {"font_family": "JetBrains Mono", "font_size": 13, "paper_color": "#1e1f22"}

    def _write_theme_editor(self, font_family: str, font_size: int) -> None:
        """
        Persist a font change INTO the active theme.json.

        This is the write side of the theme-as-source-of-truth model the
        user chose: the Settings dialog edits the theme file itself, so a
        font choice belongs to that theme and switching themes switches
        the font with it. Family and size are written to the GLOBAL
        editor.font block; per-style italic/weight in the syntax section
        are untouched.

        :param font_family: e.g. "JetBrains Mono"
        :param font_size: point size
        """
        source = self._active_theme_path()
        if not source:
            return
        theme_name = self.settings.value("theme", "theme.json", type=str)
        config_root = (
            Path(QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation)) / "themes"
        )
        config_root.mkdir(parents=True, exist_ok=True)
        path = config_root / theme_name
        if not path.exists():
            shutil.copy2(source, path)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            editor = data.setdefault("theme", {}).setdefault("editor", {})
            editor.setdefault("font", {})
            editor["font"]["family"] = font_family
            editor["font"]["font-size"] = int(font_size)
            temporary = path.with_suffix(path.suffix + ".tmp")
            with open(temporary, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, path)
        except (OSError, json.JSONDecodeError) as e:
            if "temporary" in locals():
                temporary.unlink(missing_ok=True)
            self.statusBar().showMessage(f"Could not write theme: {e}", 4000)

    def _theme_path(self, theme_name):
        """
        Absolute path of a theme FILE NAME (e.g. 'theme.json') from the
        themes/ folder next to main.py. Returns None when the file does
        not exist, so the lexer falls back to its built-in default.
        """
        if not theme_name:
            return None
        user_candidate = (
            Path(QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation))
            / "themes"
            / theme_name
        )
        if user_candidate.is_file():
            return str(user_candidate)
        base = (
            Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
        )
        candidate = base / "themes" / theme_name
        return str(candidate) if candidate.is_file() else None

    def _apply_editor_settings(self, editor, settings: dict):
        """
        Apply font/wrap/margin/theme settings to ONE editor.

        Split out of _apply_settings() so newly opened tabs (get_editor)
        can receive the same look without duplicating this code.
        """
        from PyQt5.Qsci import QsciScintilla

        from markdown_editor.markdowncustomlexer import MarkdownCustomLexer
        from markdown_editor.markdowneditor import MarkdownEditor
        from python_editor.custompythonlexer import PyCustomLexer
        from python_editor.pythoneditor import PythonEditor

        # THEME-MANAGED STYLING: font family/size and the paper color now
        # come from the theme's editor section (the Settings dialog writes
        # them there — see _write_theme_editor). The lexers apply them to
        # every style themselves; main.py no longer fights the lexer with
        # editor-level font overrides. That fight is exactly why the old
        # code "worked" for Markdown (setFont on ALL styles) but was
        # invisible on Python (setDefaultFont only touched style 0).

        # Paper override from the Settings color picker (QSettings key,
        # wins over the theme's own paper until reset to default).
        paper = QColor(settings["paper_color"]) if settings.get("paper_color") else None

        # QsciScintilla.WrapWord soft-wraps at the right edge;
        # WrapNone keeps the horizontal scrollbar behaviour.
        wrap = QsciScintilla.WrapWord if settings["word_wrap"] else QsciScintilla.WrapNone

        editor.setTabWidth(settings["tab_width"])
        editor.setWrapMode(wrap)
        editor.setCaretLineVisible(settings["highlight_line"])

        # Margin 0 is the line-number gutter. Width "0000" fits a
        # 4-digit line count; width 0 collapses it entirely.
        if settings["line_numbers"]:
            editor.setMarginWidth(0, "0000")
        else:
            editor.setMarginWidth(0, 0)

        # Recreate the lexer so a theme switch (or font change written
        # into the theme) takes effect now. A QScintilla can only host
        # ONE lexer at a time; the old one is replaced on the C++ side
        # by setLexer().
        theme_path = self._theme_path(settings.get("theme"))
        if isinstance(editor, MarkdownEditor):
            editor.md_lexer = MarkdownCustomLexer(editor, theme=theme_path, paper=paper)
            editor.setLexer(editor.md_lexer)
            # Re-push the theme's editor-wide look (font, margins, caret,
            # paper). The editor's own method handles the reset-a-lexer-
            # wipes-margin-colors problem in exactly one place.
            editor._apply_theme_editor_style()
        elif isinstance(editor, PythonEditor):
            editor.py_lexer = PyCustomLexer(editor, theme=theme_path, paper=paper)
            editor.setLexer(editor.py_lexer)
            editor._apply_theme_editor_style()

            # QsciAPIs is bound to the lexer instance it was created with,
            # so reattach a fresh one to the new lexer. The AutoCompleter
            # thread repopulates the word list as soon as the user types.
            from PyQt5.Qsci import QsciAPIs

            if getattr(editor, "_api", None) is not None:
                editor._api = QsciAPIs(editor.py_lexer)
                editor.auto_completer.api = editor._api

        # BUGFIX (phantom strings after theme switches): setLexer() does NOT
        # reliably restyle the whole document - old style bytes from the
        # PREVIOUS lexer/theme survive in the buffer, so text typed after
        # switching themes inherits stale styles (everything after the caret
        # rendered as neon-green "strings" until a docstring quote "closed"
        # the phantom string). SCI_COLOURISE (4003) with (0, -1) forces the
        # NEW lexer to restyle the entire document right now.
        editor.SendScintilla(4003, 0, -1)

    def is_binary(self, path):
        """
        check if a file is binary
        :param path:
        :return:
        """
        with open(path, "rb") as f:
            return b"\0" in f.read(1024)

    def set_new_tab(self, path: Path, is_new_file=False, target_group=None, is_python_file=None):
        path = Path(path) if path is not None else None
        if is_new_file:
            return self.new_file(target_group=target_group)
        if path is None or not path.is_file():
            return None
        if self.is_binary(path):
            self.statusBar().showMessage(
                "Cannot Open Binary File",
                2000,
            )
            return None
        existing = self.tab_view.find_editor_by_path(path)
        if existing is not None:
            self.tab_view.focus_editor(existing)
            return existing

        # IMPORTANT:
        # Do not select PythonEditor/MarkdownEditor here based on the global python_editor_active flag.
        # Existing files must be selected from their own extensions, not from whichever editor mode was last active.
        editor = self.get_editor(path=path, is_python_file=is_python_file)

        if isinstance(editor, PythonEditor):
            self.outline_tree.update_outline(editor.text())
        else:
            self.outline_tree.clear()

        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8-sig", errors="replace")
        except OSError as error:
            QMessageBox.critical(
                self,
                "Open File",
                f"Could not open{path}:\n{error}",
            )
            editor.deleteLater()
            return None

        # QScintilla normalizes its internal text, so retain the dominant
        # on-disk newline convention for subsequent saves.
        crlf_count = raw.count(b"\r\n")
        lf_count = raw.count(b"\n") - crlf_count
        cr_count = raw.count(b"\r") - crlf_count
        if crlf_count >= max(lf_count, cr_count) and crlf_count:
            editor.setEolMode(QsciScintilla.EolWindows)
        elif cr_count > max(crlf_count, lf_count):
            editor.setEolMode(QsciScintilla.EolMac)
        else:
            editor.setEolMode(QsciScintilla.EolUnix)

        editor.setTextSafely(text)
        self._connect_editor(editor)

        self.tab_view.add_editor(editor, path.name, target_group)
        self.tab_view.set_editor_tooltip(editor, str(path.absolute()))

        self.current_file = path
        self._add_to_recent_files(str(path))

        return editor

    def get_frame(self) -> QFrame:
        frame = QFrame()
        frame.setFrameShape(QFrame.NoFrame)
        frame.setFrameShadow(QFrame.Plain)
        frame.setContentsMargins(0, 0, 0, 0)
        frame.setStyleSheet("""
            QFrame {
                background-color: #1e1f22;
                border-radius: 0px;
                border: none;
                padding: 5px;
                color: #D3D3D3;
            }
            QFrame::hover {
                color: white;
            }
        """)
        return frame

    def set_up_body(self):
        # Body
        body_frame = QFrame()
        body_frame.setFrameShape(QFrame.Shape.NoFrame)
        body_frame.setLineWidth(0)
        body_frame.setMidLineWidth(0)
        body_frame.setContentsMargins(0, 0, 0, 0)
        body_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body_frame.setLayout(body)

        self.tab_view = MultiTabView(self)
        self.tab_view.setContentsMargins(0, 0, 0, 0)
        self.tab_view.currentEditorChanged.connect(self._on_current_editor_changed)
        self.tab_view.closeEditorRequested.connect(self.close_editor)
        # BUGFIX: the tab context menu was never wired up - connect it here.
        self.tab_view.tabContextMenuRequested.connect(self._show_tab_context_menu)

        # editor_container = QWidget()
        # editor_layout = QStackedWidget(editor_container)

        self.preview = QWebEngineView()

        # --- Setup for the Sidebar
        self.side_bar = QFrame()
        self.side_bar.setFrameShape(QFrame.Shape.StyledPanel)
        self.side_bar.setFrameShadow(QFrame.Shadow.Plain)
        self.side_bar.setStyleSheet(f"""
            background-color: {"#1e1f22"};
        """)

        side_bar_layout = QVBoxLayout()
        side_bar_layout.setContentsMargins(5, 15, 5, 0)
        side_bar_layout.setSpacing(0)
        side_bar_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignCenter)

        # --- Setup for labels
        self.sidebar_labels = {}

        folder_label = self.get_sidebar_label(resource_path("icons/folder.png"), "folder")
        self.sidebar_labels["folder"] = folder_label
        side_bar_layout.addWidget(folder_label)

        search_label = self.get_sidebar_label(resource_path("icons/search.png"), "search")
        self.sidebar_labels["search"] = search_label
        side_bar_layout.addWidget(search_label)
        self.side_bar.setLayout(side_bar_layout)

        outline_label = self.get_sidebar_label(resource_path("icons/code.png"), "outline")
        self.sidebar_labels["outline"] = outline_label
        side_bar_layout.addWidget(outline_label)

        self.outline_tree = CodeOutlineTree()
        self.outline_tree.symbol_clicked.connect(self._goto_symbol)

        self.outline_frame = self.get_frame()
        outline_layout = QVBoxLayout()
        outline_layout.setContentsMargins(0, 0, 0, 0)
        outline_layout.setSpacing(0)

        outline_label = QLabel("Outline")
        outline_label.setStyleSheet("color: #636d83; padding: 4px 8px; font-size: 12px;")
        outline_layout.addWidget(outline_label)
        outline_layout.addWidget(self.outline_tree)
        self.outline_frame.setLayout(outline_layout)

        # split view
        self.hs_split = QSplitter(Qt.Orientation.Horizontal)

        # --- Frame and layout to hold the tree view (FILE MANAGER)
        self.file_manager_frame = self.get_frame()

        self.file_manager_layout = QVBoxLayout()
        self.file_manager_layout.setContentsMargins(0, 0, 0, 0)
        self.file_manager_layout.setSpacing(0)
        self.file_manager = FileManager(
            set_new_tab=self.set_new_tab,
            main_window=self,
            parent=self,
        )

        # setup layout
        self.file_manager_layout.addWidget(self.file_manager)
        self.file_manager_frame.setLayout(self.file_manager_layout)

        # search manager
        self.search_frame = self.get_frame()

        search_layout = QVBoxLayout()
        search_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        search_layout.setContentsMargins(0, 10, 0, 0)
        search_layout.setSpacing(0)

        search_input = QLineEdit()
        search_input.setPlaceholderText("Search")
        search_input.setFont(self.window_font)
        search_input.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.search_checkbox = QCheckBox("Search in Modules")
        self.search_checkbox.setFont(self.window_font)
        self.search_checkbox.setStyleSheet("color: white; margin-bottom: 10px;")

        self.search_worker = SearchWorker()
        self.search_worker.results_ready.connect(self.search_finished)

        search_input.textChanged.connect(
            lambda text: self.search_worker.update(
                text,
                self.file_manager.model.rootDirectory().absolutePath(),
                self.search_checkbox.isChecked(),
            )
        )

        self.search_list_view = QListWidget()
        self.search_list_view.setFont(QFont("sans-serif", 13))
        self.search_list_view.setStyleSheet("""
        QListWidget {
            background-color: #21252b;
            border-radius: 5px;
            border: 1px solid #D3D3D3;
            padding: 5px;
            color: #D3D3D3;
        }
        """)

        self.search_list_view.itemClicked.connect(self.search_list_view_clicked)

        search_layout.addWidget(self.search_checkbox)
        search_layout.addWidget(search_input)
        search_layout.addSpacerItem(QSpacerItem(5, 5, QSizePolicy.Minimum, QSizePolicy.Minimum))

        search_layout.addWidget(self.search_list_view)
        self.search_frame.setLayout(search_layout)

        # --- Side panel: QStackedWidget so only one panel is visible at a time ---
        self.side_panel = QStackedWidget()
        self.side_panel.setMinimumWidth(280)  # was 200 — too narrow
        self.side_panel.setMaximumWidth(450)  # was 400 — give more room
        self.side_panel.addWidget(self.file_manager_frame)
        self.side_panel.addWidget(self.search_frame)
        self.side_panel.addWidget(self.outline_frame)

        body.addWidget(self.side_bar)
        self.hs_split.addWidget(self.side_panel)
        self.hs_split.addWidget(self.tab_view)
        self.hs_split.addWidget(self.preview)
        self.hs_split.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.hs_split.setSizes([300, 550, 550])
        body.addWidget(self.hs_split)
        body_frame.setLayout(body)

        self.setCentralWidget(body_frame)

    def _goto_symbol(self, line: int, column: int):
        """Jump to a symbol in the current editor."""
        editor = self.current_editor()
        if editor is not None:
            editor.setCursorPosition(line, column)
            editor.ensureLineVisible(line)
            editor.setFocus()

    # BUGFIX: this whole block used the plain QTabWidget API (tabBar(), count(),
    # widget(index), close_tab()) which does not exist on MultiTabView, and
    # close_tab() was never defined at all -> AttributeError/NameError on every
    # menu action. Rewritten to work on editor objects via MultiTabView's real API.

    def _show_tab_context_menu(self, group, pos: QPoint):
        """
        Show a context menu when the user right-clicks a tab.

        `group` is the QTabWidget (tab group) the click happened in,
        `pos` is the click position in that group's coordinates.
        """
        tab_bar = group.tabBar()
        # Convert the click position from QTabWidget coords to QTabBar coords
        bar_pos = tab_bar.mapFrom(group, pos)
        index = tab_bar.tabAt(bar_pos)

        if index < 0:
            # User clicked somewhere that's not a tab (e.g. the empty space after tabs)
            return

        editor = group.widget(index)
        if editor is None:
            return

        menu = QMenu(self)
        close_action = menu.addAction("Close")
        close_others_action = menu.addAction("Close Others")
        close_all_action = menu.addAction("Close All")
        menu.addSeparator()
        copy_path_action = menu.addAction("Copy Path")
        reveal_action = menu.addAction("Reveal in File Manager")

        action = menu.exec_(group.mapToGlobal(pos))

        if action == close_action:
            self.close_editor(editor)
        elif action == close_others_action:
            self._close_other_tabs(editor)
        elif action == close_all_action:
            self._close_all_tabs()
        elif action == copy_path_action:
            self._copy_tab_path(editor)
        elif action == reveal_action:
            self._reveal_tab_in_file_manager(editor)

    def _close_other_tabs(self, keep_editor):
        """Close all tabs except the one holding `keep_editor` (across all split groups)."""
        for editor in list(self.tab_view.all_editors()):
            if editor is not keep_editor:
                # close_editor returns False only when the user cancels the
                # save prompt - stop closing in that case.
                if self.close_editor(editor) is False:
                    break

    def _close_all_tabs(self):
        """Close every open tab (across all split groups)."""
        for editor in list(self.tab_view.all_editors()):
            if self.close_editor(editor) is False:
                break

    def _copy_tab_path(self, editor):
        """Copy the file path of the tab's editor to the clipboard."""
        path = getattr(editor, "path", None)
        if path is not None:
            QApplication.clipboard().setText(str(path))
            self.statusBar().showMessage(f"Copied: {path}", 2000)

    def _reveal_tab_in_file_manager(self, editor):
        """Open the OS file manager at the editor file's location."""
        path = getattr(editor, "path", None)
        if path is None:
            return

        # Reuse the logic from FileManager.action_open_in_file_manager
        # but with the tab's path instead of a tree view index
        import subprocess
        import sys

        if sys.platform == "win32":
            subprocess.Popen(f'explorer /select,"{path}"')
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])

    def _position_find_bar(self):
        """Position the find bar at the top-right of the tab view area."""
        tab_rect = self.tab_view.geometry()
        # geometry() returns a QRect with x, y, width, height
        # Docs: https://doc.qt.io/qt-5/qwidget.html#geometry

        bar_width = 450
        x = tab_rect.right() - bar_width - 20
        y = tab_rect.top() + 35

        self.find_bar.move(x, y)
        self.find_bar.resize(bar_width, self.find_bar.sizeHint().height())
        self.find_bar.raise_()  # bring to front
        # raise_() puts widget on top of siblings

    def set_cursor_pointer(self, e):
        self.setCursor(Qt.PointingHandCursor)

    def set_cursor_arrow(self, e):
        self.setCursor(Qt.ArrowCursor)

    def search_finished(self, generation, items):
        if generation != self.search_worker.generation:
            return
        self.search_list_view.clear()
        for i in items:
            self.search_list_view.addItem(i)

    def search_list_view_clicked(self, item: SearchItem):
        editor = self.set_new_tab(Path(item.full_path))
        if editor is None:
            return
        editor.setCursorPosition(item.lineno, item.end)
        editor.setFocus()

    def close_editor(self, editor):
        if editor is None:
            return

        if editor in self._dirty_editors:
            path = getattr(editor, "path", None)
            name = path.name if path is not None else "Untitled"

            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                f"Save Changes to '{name}'?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )

            if reply == QMessageBox.Cancel:
                return False

            if reply == QMessageBox.Save:
                self.tab_view.focus_editor(editor)
                if not self.save_file():
                    return False

        self._dirty_editors.discard(editor)
        self.tab_view.remove_editor(editor)

        if hasattr(editor, "shutdown"):
            editor.shutdown()

        editor.setParent(None)
        editor.deleteLater()

        self.render_preview()
        return True

    def on_file_rename(self, old_path: Path, new_path: Path, is_directory: bool = False):
        """Update every editor affected by a filesystem rename.

        FileManager has already renamed the entry in QFileSystemModel.
        This method updates the in-memory editor metadata and the tab UI.
        """

        old_path = Path(old_path)
        new_path = Path(new_path)

        for editor in self.tab_view.all_editors():
            editor_path = getattr(editor, "path", None)

            if editor_path is None:
                continue

            editor_path = Path(editor_path)

            if is_directory:
                if editor_path == old_path:
                    # BUGFIX: '==' (comparison) instead of '=' (assignment) -> UnboundLocalError
                    updated_path = new_path
                elif old_path in editor_path.parents:
                    relative_path = editor_path.relative_to(old_path)
                    updated_path = new_path / relative_path
                else:
                    continue

            else:
                if editor_path != old_path:
                    continue

                updated_path = new_path

            editor.path = updated_path
            editor.full_path = updated_path.absolute()
            if isinstance(editor, PythonEditor):
                if editor.ruff_lsp is not None:
                    editor.ruff_lsp.relocate(updated_path)
                editor.auto_completer.file_path = str(editor.full_path)

            desired_class = PythonEditor if updated_path.suffix.lower() == ".py" else MarkdownEditor
            if not isinstance(editor, desired_class):
                editor = self._convert_editor(editor, desired_class)

            is_dirty = editor in self._dirty_editors
            title = updated_path.name

            if is_dirty:
                title = f"● {title}"

            self.tab_view.set_editor_title(editor, title)
            self.tab_view.set_editor_tooltip(editor, str(editor.full_path))

            if editor is self.current_editor():
                self.current_file = updated_path

    def close_editors_for_path(self, target_path: Path, is_directory: bool = False):
        """
        Close open editors affected by deletion.

        Returns True when all affected editors closed successfully.
        Returns False when the uder cancels an unsaved-changes prompt.
        """

        target_path = Path(target_path)

        affected_editors = []

        target_path = target_path.resolve()
        for editor in self.tab_view.all_editors():
            editor_path = getattr(editor, "path", None)
            if editor_path is None:
                continue
            editor_path = Path(editor_path).resolve()

            if is_directory:
                is_affected = editor_path == target_path or target_path in editor_path.parents
            else:
                is_affected = editor_path == target_path

            if is_affected:
                affected_editors.append(editor)

        for editor in affected_editors:
            if not self.close_editor(editor):
                return False

        return True

    def show_hide_tab(self, e, type_):
        panels = {
            "folder": self.file_manager_frame,
            "search": self.search_frame,
            "outline": self.outline_frame,
        }
        # Update icoon states. Reset all to gray, then set active to blue
        icon_map = {
            "folder": (resource_path("icons/folder.png"), resource_path("icons/folder-active.png")),
            "search": (resource_path("icons/search.png"), resource_path("icons/search-active.png")),
            "outline": (
                resource_path("icons/code.png"),
                resource_path("icons/code-active.png"),
            ),
        }
        # Reset all sidebar icons to inactive gray
        for name, (inactive, active) in icon_map.items():
            label = self.sidebar_labels.get(name)
            if label:
                label.setPixmap(QPixmap(inactive).scaled(QSize(25, 25)))

        target = panels.get(type_)
        if target is None:
            return

        # If the target panel is already showing, hide the side panel
        if self.side_panel.isVisible() and self.side_panel.currentWidget() is target:
            # Clicking the active panel hiddes is, resets to gray
            self.side_panel.hide()
            return

        # Otherwise, switch to the target panel and show it
        self.side_panel.setCurrentWidget(target)
        self.side_panel.show()

        label = self.sidebar_labels.get(type_)
        if label:
            label.setPixmap(QPixmap(icon_map[type_][1]).scaled(QSize(25, 25)))
        # Restore splitter sizes if the side panel was hidden (width 0)
        sizes = self.hs_split.sizes()
        if sizes and sizes[0] == 0:
            self.hs_split.setSizes([300, 550, 550])

    def tree_view_context_menu(self, pos): ...

    def new_file(self, target_group=None):
        """Create an untitled editor in the active group."""

        editor = self.get_editor()
        self._connect_editor(editor)

        self.tab_view.add_editor(editor, "Untitled", target_group)
        self.current_file = None
        self.statusBar().showMessage("Created new file", 3000)

        return editor

    def open_file(self):
        # open file
        ops = QFileDialog.Options()
        ops |= QFileDialog.DontUseNativeDialog
        new_file, _ = QFileDialog.getOpenFileName(
            self,
            "Pick A File",
            "",
            "All Files (*);;Text Files (*.txt);;Python Files (*.py);;Markdown Files (*.md);;C Files (*.c)",
            options=ops,
        )

        if new_file == "":
            self.statusBar().showMessage("Cancelled", 2000)
            return
        f = Path(new_file)
        self.set_new_tab(f)
        self._add_to_recent_files(str(f))

    def open_folder(self):
        # open folder
        ops = QFileDialog.Options()
        ops |= QFileDialog.DontUseNativeDialog

        new_folder = QFileDialog.getExistingDirectory(self, "Pick A Folder", "", options=ops)

        if not new_folder:
            return

        self.file_manager.model.setRootPath(new_folder)
        self.file_manager.setRootIndex(self.file_manager.model.index(new_folder))
        self.statusBar().showMessage(f"Opened {new_folder}", 2000)
        self.file_manager.check_git_status()

    def _run_ruff_before_save(self, path: Path, text: str) -> str:
        """Apply Ruff safe fixes and formatting to a temporary Python file."""
        if self.ruff_save_mode != "safe_format" or path.suffix.lower() != ".py":
            return text

        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.stem}.",
                suffix=path.suffix,
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(text)

            commands = (
                [self.python_runner.interpreter, "-m", "ruff", "check", "--fix", str(temporary)],
                [self.python_runner.interpreter, "-m", "ruff", "format", str(temporary)],
            )
            for index, command in enumerate(commands):
                result = subprocess.run(
                    command,
                    cwd=str(path.parent),
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )
                allowed = {0, 1} if index == 0 else {0}
                if result.returncode not in allowed:
                    message = result.stderr.strip() or result.stdout.strip() or "Ruff failed"
                    raise RuntimeError(message)
            return temporary.read_text(encoding="utf-8")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _encode_editor_text(editor, text: str) -> bytes:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        if editor.eolMode() == QsciScintilla.EolWindows:
            normalized = normalized.replace("\n", "\r\n")
        elif editor.eolMode() == QsciScintilla.EolMac:
            normalized = normalized.replace("\n", "\r")
        return normalized.encode("utf-8")

    def _save_editor_to_path(self, editor, path: Path) -> bool:
        """Format when requested, then atomically persist one editor."""
        path = Path(path)
        original = editor.text()
        formatted = original
        try:
            formatted = self._run_ruff_before_save(path, original)
        except (OSError, subprocess.SubprocessError, RuntimeError) as error:
            reply = QMessageBox.warning(
                self,
                "Ruff on save",
                f"Ruff could not process this file:\n{error}\n\nSave without Ruff?",
                QMessageBox.Save | QMessageBox.Cancel,
                QMessageBox.Save,
            )
            if reply != QMessageBox.Save:
                return False

        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = self._encode_editor_text(editor, formatted)
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=f".{path.name}.", delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if path.exists():
                os.chmod(temporary, path.stat().st_mode)
            os.replace(temporary, path)
            temporary = None
        except OSError as error:
            QMessageBox.critical(self, "Save File", f"Could not save '{path}':\n{error}")
            return False
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

        if formatted != original:
            editor.setTextSafely(formatted)
        self.mark_editor_clean(editor)
        return True

    def save_file(self):
        """
        Save the currently focused editor.

        An editor opened from disk has an existing 'editor.path', so it is written
        directly to that path. Only an untitled editor has path=None and must open
        the Save As dialog.
        """
        editor = self.current_editor()
        if editor is None:
            return False

        path = getattr(editor, "path", None)

        # Only untitled editors need  a user-selected destination.
        if path is None:
            return self.save_as()

        path = Path(path)
        if not self._save_editor_to_path(editor, path):
            return False

        self.current_file = path
        self.statusBar().showMessage(f"Saved {path.name}", 3000)
        self.cat.add_xp(1)
        self.cat.set_state("stretch", 2500)
        return True

    def save_as(self):
        editor = self.current_editor()

        if editor is None:
            return False

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save As",
            os.getcwd(),
        )

        if not file_path:
            self.statusBar().showMessage(
                "Cancelled",
                2000,
            )
            return False

        path = Path(file_path)
        if not self._save_editor_to_path(editor, path):
            return False

        editor.path = path
        editor.full_path = path.absolute()
        if isinstance(editor, PythonEditor):
            if editor.ruff_lsp is not None:
                editor.ruff_lsp.relocate(path)
            editor.auto_completer.file_path = str(editor.full_path)
        self.current_file = path

        self.tab_view.set_editor_tooltip(
            editor,
            str(editor.full_path),
        )

        self.mark_editor_clean(editor)

        desired_class = PythonEditor if path.suffix.lower() == ".py" else MarkdownEditor
        if not isinstance(editor, desired_class):
            editor = self._convert_editor(editor, desired_class)
        self._add_to_recent_files(str(path))

        self.statusBar().showMessage(
            f"Saved {path.name}",
            2000,
        )

        return True

    def copy(self):
        editor = self.current_editor()
        if editor is not None:
            editor.copy()

    def undo(self):
        editor = self.current_editor()
        if editor is not None:
            editor.undo()

    def redo(self):
        editor = self.current_editor()
        if editor is not None:
            editor.redo()

    def cut(self):
        editor = self.current_editor()
        if editor is not None:
            editor.cut()

    def paste(self):
        editor = self.current_editor()
        if editor is not None:
            editor.paste()

    def select_all(self):
        editor = self.current_editor()
        if editor is not None:
            editor.selectAll()

    def delete_line(self):
        editor = self.current_editor()
        if editor is None:
            return
        # Get the current cursor position
        # Returns (line, index) both 0-based
        line, _ = editor.getCursorPosition()
        total_lines = editor.lines()
        # we need to handle two cases.
        # 1. Not the last line: select from start of current line to start of next line

        #   This includes the newline character, so th eline is fully removed
        # 2. Last line: select the entire line content (no newline after it to remove)
        if line < total_lines - 1:
            editor.setSelection(line, 0, line + 1, 0)
        else:
            line_len = editor.lineLength(line)
            editor.setSelection(line, 0, line, line_len)
        editor.removeSelectedText()

    def _on_current_editor_changed(self, editor):
        """Refresh MainWindow state when focus moved between editors."""
        if editor is None:
            self.current_file = None
            self.cursor_pos_label.setText("")
            self.word_count_label.setText("")
            return

        self.current_file = getattr(editor, "path", None)

        line, column = editor.getCursorPosition()
        self.update_cursor_position(line, column)
        self.update_word_count()

        if isinstance(editor, PythonEditor):
            self.outline_tree.update_outline(editor.text())
        else:
            self.outline_tree.clear()

        if isinstance(editor, MarkdownEditor):
            self.preview.show()
            self.render_preview()
        else:
            self.preview.hide()

    def change_editor_python(self):
        self.python_editor_active = True
        self._swap_editor(PythonEditor)
        self.preview.hide()
        self.statusBar().showMessage("Python-Editor applied", 2000)

    def change_editor_markdown(self):
        self.python_editor_active = False
        self._swap_editor(MarkdownEditor)
        self.preview.show()
        QTimer.singleShot(0, self.render_preview)
        self.statusBar().showMessage("Markdown-Editor applied", 2000)

    def render_preview(self):
        editor = self.current_editor()
        if not isinstance(editor, MarkdownEditor) or not self._preview_ready:
            return
        self.md.reset()
        body = self.md.convert(editor.text())
        payload = json.dumps(body)
        js = f'document.getElementById("content").innerHTML = {payload};'
        self.preview.page().runJavaScript(js)

    def _on_preview_loaded(self, ok):
        self._preview_ready = True
        self.render_preview()

    def sync_scroll(self, editor):
        if not self._preview_ready or editor is not self.current_editor():
            return
        total = editor.lines()
        visible = editor.SendScintilla(2370)
        first = editor.firstVisibleLine()
        denom = max(total - visible, 1)
        ratio = min(max(first / denom, 0.0), 1.0)
        js = f"""
        var h = document.documentElement.scrollHeight - window.innerHeight;
        window.scrollTo(0, h * {ratio});
        """
        self.preview.page().runJavaScript(js)

    def _convert_current_tab(self, EditorClass):
        return self._convert_editor(self.current_editor(), EditorClass)

    def _convert_editor(self, old, EditorClass):
        if old is None:
            return None
        if isinstance(old, EditorClass):
            return old

        group = self.tab_view.group_for_editor(old)

        if group is None:
            return None

        index = group.indexOf(old)
        was_current = old is self.current_editor()
        previous_current = group.currentWidget()
        title = group.tabText(index)
        tooltip = group.tabToolTip(index)
        icon = group.tabIcon(index)

        text = old.text()
        path = getattr(old, "path", None)
        was_dirty = old in self._dirty_editors
        line, column = old.getCursorPosition()
        selection = old.getSelection()
        first_visible = old.firstVisibleLine()

        new_editor = self.get_editor(path=path, is_python_file=(EditorClass is PythonEditor))
        new_editor.setTextSafely(text)
        self._connect_editor(new_editor)

        group.blockSignals(True)
        group.removeTab(index)
        group.insertTab(index, new_editor, icon, title)
        group.setTabToolTip(index, tooltip)
        if was_current:
            group.setCurrentIndex(index)
        elif previous_current is not None and previous_current is not old:
            group.setCurrentWidget(previous_current)
        group.blockSignals(False)

        if was_dirty:
            self._dirty_editors.discard(old)
            self._dirty_editors.add(new_editor)

        if hasattr(old, "shutdown"):
            old.shutdown()

        old.setParent(None)
        old.deleteLater()

        new_editor.setCursorPosition(line, column)
        if selection[0] >= 0:
            new_editor.setSelection(*selection)
        new_editor.setFirstVisibleLine(first_visible)
        if was_current:
            new_editor.setFocus()
            self.tab_view.focus_editor(new_editor)
        return new_editor

    def _swap_editor(self, EditorClass):
        return self._convert_current_tab(EditorClass)

    def show_find_bar(self):
        """Show the find bar (Ctrl+F mode)."""
        # Pre-fill with selected text if any
        editor = self.current_editor()
        selected = editor.selectedText() if editor else ""
        self.find_bar.set_search_text(selected)
        self.find_bar.show_find()
        self._position_find_bar()

    def show_replace_bar(self):
        """Show the find+replace bar (Ctrl+H mode)."""
        editor = self.current_editor()
        selected = editor.selectedText() if editor else ""
        self.find_bar.set_search_text(selected)
        self.find_bar.show_replace()
        self._position_find_bar()

    def _do_find_next(self, text, case_sensitive, whole_word, regex):
        """Search forward from the current cursor position."""
        editor = self.current_editor()
        if editor is None:
            return

        line, index = editor.getCursorPosition()
        # getCursorPosition returns (line, index) as a tuple
        # Docs: https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html#a2d0e8b6e0a3e3a9c0e3a3e3a3e3a3e3a

        editor.findFirst(
            text,  # the search string or regex
            regex,  # is it a regex?
            case_sensitive,  # case-sensitive?
            whole_word,  # whole-word match only?
            True,  # wrap around to top when reaching bottom?
            True,  # search forward?
            line,  # start line
            index,  # start column
            True,  # show the match (scroll to it)?
            False,  # POSIX regex mode (False = use Python regex)
        )

    def _do_find_prev(self, text, case_sensitive, whole_word, regex):
        """Search backward from the current cursor position."""
        editor = self.current_editor()
        if editor is None:
            return

        line, index = editor.getCursorPosition()
        if editor.hasSelectedText():
            line, index, _line_to, _index_to = editor.getSelection()
        if index > 0:
            index -= 1
        elif line > 0:
            line -= 1
            index = max(0, editor.lineLength(line) - 1)
        else:
            line, index = -1, -1

        editor.findFirst(
            text,
            regex,
            case_sensitive,
            whole_word,
            True,  # wrap around
            False,  # search BACKWARD
            line,
            index,
            True,  # show the match
            False,
        )

    def _do_replace(self, find_text, replace_text, case_sensitive, whole_word, regex):
        """Replace the currently selected match, then find the next one."""
        editor = self.current_editor()
        if editor is None:
            return
        selected = editor.selectedText() if editor.hasSelectedText() else ""
        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = find_text if regex else re.escape(find_text)
        if whole_word:
            pattern = rf"\b(?:{pattern})\b"
        try:
            matches = bool(selected) and re.fullmatch(pattern, selected, flags) is not None
        except re.error:
            matches = False
        if matches:
            editor.replace(replace_text)
            # replace() swaps the currently selected Tect with replace_text
            # Docs: https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html#a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a
        # Find the next match
        self._do_find_next(find_text, case_sensitive, whole_word, regex)

    def _do_replace_all(self, find_text, replace_text, case_sensitive, whole_word, regex):
        """Replace all occurrences in the current document."""
        editor = self.current_editor()
        if editor is None:
            return
        if regex:
            try:
                if re.compile(find_text).match("") is not None:
                    self.statusBar().showMessage("Zero-length regex cannot be replaced", 4000)
                    return
            except re.error:
                return

        # Move cursor to the start of the Document
        # sendScintilla sends a raw Scintilla message
        # SCI_DOCUMENTSTART = 2318 moves the cursor to position 0
        # Docs: https://www.scintilla.org/ScintillaDoc.html#SCI_DOCUMENTSTART
        editor.setCursorPosition(0, 0)

        count = 0
        found = editor.findFirst(
            find_text,
            regex,
            case_sensitive,
            whole_word,
            False,  # don't warp. We start from the top then go down
            True,  # forward
            0,
            0,  # start at line 0, col 0
            True,
            False,
        )
        while found:
            editor.replace(replace_text)
            count += 1
            # findNext continues from where the last match was
            # Docs: https://www.riverbankcomputing.com/static/Docs/QScintilla/classQsciScintilla.html#a4a4a4a4a4a4a4a4a4a4a4a4a4a4a4a4a
            found = editor.findNext()
        self.statusBar().showMessage(f"Replace {count} occurrences", 3000)

    def closeEvent(self, event):
        for editor in list(self.tab_view.all_editors()):
            if editor not in self._dirty_editors:
                continue
            self.tab_view.focus_editor(editor)
            path = getattr(editor, "path", None)
            name = Path(path).name if path is not None else "Untitled"
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                f"Save Changes to '{name}'?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )
            if reply == QMessageBox.Cancel:
                event.ignore()
                return
            if reply == QMessageBox.Save and not self.save_file():
                event.ignore()
                return

        self.save_session()
        for editor in list(self.tab_view.all_editors()):
            if hasattr(editor, "shutdown"):
                editor.shutdown()
        if hasattr(self, "terminal"):
            self.terminal.stop()
        if hasattr(self, "python_runner") and self.python_runner:
            self.python_runner.stop()
        # BUGFIX: was "hasattr(self, 'git_checker')" — that attribute
        # never exists on MainWindow (the checker lives on the file
        # manager), so the guard was always False and the git thread was
        # never shut down: "QThread: Destroyed while thread is still
        # running" on exit. Also moved BEFORE super().closeEvent() so
        # shutdown finishes before the window widgets are torn down.
        fm = getattr(self, "file_manager", None)
        if fm is not None and hasattr(fm, "git_checker"):
            fm.git_checker.shutdown()
        if hasattr(self, "settings"):
            self.settings.setValue("recent_files", self.recent_files)
        self.ruff_lsp_client.shutdown()
        event.accept()
        super().closeEvent(event)

    def save_session(self):
        """
        Persist the open-tab layout so the next launch can restore it.

        The session is stored as One JSON string under QSettings key "session"
        (QSettings cannot reliably round-trip nested Python dicts, but strings are always safe.

        Saved per tab:
            path -> absolute file path; untitled editors (path=None) are skipped,
                    they cannot be reopened from disk.
            python -> True if the tab is a PythonEditor. Needed because set_new_tab() picks the editor class
                      from the self.python_editor_active flag, so the flag must be flipped per file during restore.
            group -> index of the split group (0 = left/first group)
                     so a split layout survives a restart.

        Also saved:
            active  -> path of the focused tab (re-focused on restore)
            python_mode -> the global mode flag, so NEW files open after a restart behave as before.

        Silently does nothing when the user disabled session restore in the Settings dialog
        (QSettings key "restore_tabs").
        """
        import json

        if not self.settings.value("restore_tabs", True, type=bool):
            return

        # group() returns the life QTabWidgets in left-to-right order;
        # its index for an editor's group is exactly the number we save.
        groups = list(self.tab_view.groups())
        tabs = []

        for editor in self.tab_view.all_editors():
            path = getattr(editor, "path", None)
            if path is None:
                continue
            group = self.tab_view.group_for_editor(editor)
            tabs.append(
                {
                    "path": str(path),
                    # isinstance() is the ground truth: the class IS the mode.
                    "python": isinstance(editor, PythonEditor),
                    "group": groups.index(group) if group in groups else 0,
                }
            )

        active = self.current_editor()
        active_path = getattr(active, "path", None)
        session = {
            "tabs": tabs,
            "active": str(active_path) if active_path is not None else None,
            "python_mode": bool(self.python_editor_active),
        }
        self.settings.setValue("session", json.dumps(session))

    def _restore_session(self):
        """
        Reopen the tabs saved by _save_session().

        Strategy:
        1.  Parse the stored JSON (any error -> give up silently; a fresh session is always
            a valid state).
        2.  Create enough tab groups to reproduce the split layout.
        3.  Reopen each file with set_new_tab(), flipping self.python_editor_active
            per file so each tab gets the right editor class.
        4.  Re-focus the tab that was active at close time.

        set_new_tab() already handles the hard parts for us: binary-file rejection,
        duplicate detection (find_editor_by_path), recent-file bookkeeping and dirty-state signal wiring.
        """
        import json

        if not self.settings.value("restore_tabs", True, type=bool):
            return

        raw = self.settings.value("session", "", type=str)
        if not raw:
            return

        try:
            session = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return

        tabs = session.get("tabs", [])
        if not isinstance(tabs, list) or len(tabs) > 100:
            return
        tabs = [
            entry
            for entry in tabs
            if (
                isinstance(entry, dict)
                and isinstance(entry.get("path"), str)
                and isinstance(entry.get("python", False), bool)
                and isinstance(entry.get("group", 0), int)
                and 0 <= entry.get("group", 0) < 20
            )
        ]
        if not tabs:
            return

        # --- 2. Recreate the split layout --- #
        # Tab gourps are created lazily by MultiTabView; to place tab into group 2 we must make sure
        # groups 0..2 exist first. _create_group() is techincally private - optionally rename it to
        # create_group() in multi_tab_view.py and update this call
        # (plus its two internal callers) to keep things clean.
        max_group = max(entry["group"] for entry in tabs)
        while len(self.tab_view.groups()) <= max_group:
            self.tab_view._create_group()
        groups = list(self.tab_view.groups())

        # --- 2. Reopen every tab --- #
        active_editor = None
        for entry in tabs:
            path = Path(entry["path"])
            if not path.is_file():
                continue  # delete/move since last session - skip it

            # set_new_tab() branches on self.python_editor_active to choose PythonEditor vs MarkdownEditor.
            # Setting it per file restores each tab in the mode it was last edited with.
            editor = self.set_new_tab(
                path,
                target_group=groups[entry["group"]],
                is_python_file=entry["python"],
            )
            if editor is not None and session.get("active") == str(path):
                active_editor = editor

        # --- 3. Restore the global mode + focus --- #
        # The global flag governs NEW tabs opened after startup, so it refelcts
        # the mode the app was in at close time.
        self.python_editor_active = session.get("python_mode", False)

        if active_editor is not None:
            self.tab_view.focus_editor(active_editor)

        self.statusBar().showMessage(f"Restore {len(tabs)} tabs from last session", 5000)

    def check_for_updates(self):
        """
        Check GitHub Releases API for a newer version.
        Runs in a background thread so it doesn't freeze the GUI.
        """
        import json
        import threading
        import urllib.request

        api_url = "https://api.github.com/repos/ConfidentCupcake/MarkdownEditor/releases/latest"

        def _check():
            try:
                # Create a request with a User-Agent header
                # GitHub's API requires a User-Agent header, otherwise it returns 403
                # Docs: https://docs.github.com/en/rest/overview/resources-in-the-rest-api#user-agent-required
                req = urllib.request.Request(api_url)
                req.add_header("User-Agent", "MarkdownEditor")

                # urlopen fetches the URL and returns a response object
                # Docs: https://docs.python.org/3/library/urllib.request.html#urllib.request.urlopen
                with urllib.request.urlopen(req, timeout=5) as response:
                    data = json.loads(response.read().decode("utf-8"))

                from packaging.version import InvalidVersion, Version

                latest_tag = data.get("tag_name", "")
                try:
                    latest = Version(latest_tag.removeprefix("v"))
                    current = Version(APP_VERSION.removeprefix("v"))
                except InvalidVersion:
                    return
                if latest <= current:
                    return
                assets = data.get("assets") or []
                if sys.platform == "win32":
                    suffixes = (".exe", ".msi")
                elif sys.platform == "darwin":
                    suffixes = (".dmg", ".pkg", ".zip")
                else:
                    suffixes = (".appimage", ".deb", ".rpm", ".tar.gz")
                download_url = next(
                    (
                        asset.get("browser_download_url", "")
                        for asset in assets
                        if asset.get("name", "").lower().endswith(suffixes)
                    ),
                    data.get("html_url", ""),
                )
                if download_url:
                    self.update_available.emit(str(latest), download_url)

            except Exception:
                # Network error, timeout, or API rate limit — fail silently
                # GitHub's API allows 60 requests/hour for unauthenticated requests
                # Docs: https://docs.github.com/en/rest/overview/resources-in-the-rest-api#rate-limiting
                pass

        # Run the check in a background daemon thread
        # daemon=True means the thread won't prevent the app from closing
        # Docs: https://docs.python.org/3/library/threading.html#threading.Thread
        thread = threading.Thread(target=_check, daemon=True)
        thread.start()

    def _show_update_dialog(self, version: str, download_url: str):
        """Show a dialog telling the user about the update."""
        reply = QMessageBox.information(
            self,
            "Update Available",
            f"A new version ({version}) is available!\n\n"
            f"You are currently running {APP_VERSION}.\n\n"
            f"Click OK to open the download page in your browser.",
            QMessageBox.Ok | QMessageBox.Cancel,
            QMessageBox.Ok,
        )
        if reply == QMessageBox.Ok:
            # webbrowser.open opens the URL in the user's default browser
            # Docs: https://docs.python.org/3/library/webbrowser.html#webbrowser.open
            import webbrowser

            webbrowser.open(download_url)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    main = MainWindow()
    sys.exit(app.exec())
