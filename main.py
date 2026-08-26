import json
import os
import sys
from pathlib import Path

import markdown
from PyQt5.Qsci import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWidgets import *

from console_widget import ConsoleWidget
from file_manager import FileManager
from find_replace import FindReplaceBar
from fuzzy_searcher import SearchItem, SearchWorker
from markdowneditor import MarkdownEditor
from multi_tab_view import MultiTabView
from python_runner import PythonRunner
from pythoneditor import PythonEditor
from terminal_widget import TerminalWidget

APP_VERSION = "v1.9.0"


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
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


class MainWindow(QMainWindow):
    WIDTH = 1400
    HEIGHT = 900

    def __init__(self):
        super().__init__()
        self._dirty_editors = set()
        self.python_runner = PythonRunner(self)
        self.settings = QSettings("CodeEditor", "CodeEditor")
        # Load the saved recent files list, default to empty list
        self.recent_files = self.settings.value("recent_files", [], type=list)
        self.ruff_save_mode = self.settings.value("ruff_save_mode", "safe_format", type=str)

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

        self.set_up_menu()
        self.set_up_body()

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

        # Word count label - shows "Words: X | Chars: Y"
        self.word_count_label = QLabel("Words: 0 | Chars: 0")
        self.word_count_label.setStyleSheet("color: #888; padding: 0 10px;")
        self.statusBar().addPermanentWidget(self.word_count_label)

        self.show()

        QTimer.singleShot(2000, self.check_for_updates)

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

    def get_editor(self, path: Path = None, is_python_file=True) -> QsciScintilla:
        if self.python_editor_active:
            self.editor = PythonEditor(path=path, is_python_file=is_python_file)
        else:
            self.editor = MarkdownEditor(path=path, is_python_file=is_python_file)
        return self.editor

    def current_editor(self):
        """Return the editor focused in the active tab group."""
        return self.tab_view.current_editor()

    def _connect_editor(self, editor):
        """Connect all MainWindow signals required by an editor."""
        editor.textChanged.connect(lambda ed=editor: self._on_editor_text_changed(ed))
        editor.textChanged.connect(self._debounce.start)
        editor.textChanged.connect(self.update_word_count)
        editor.cursorPositionChanged.connect(
            lambda line, column, ed=editor: self._on_editor_cursor_changed(ed, line, column)
        )
        editor.cursorPositionChanged.connect(lambda line, column, ed=editor: self.sync_scroll(ed))
        editor.verticalScrollBar().valueChanged.connect(
            lambda value, ed=editor: self.sync_scroll(ed)
        )
        if isinstance(editor, PythonEditor):
            editor.goto_definition_requested.connect(self._open_file_at_position)

    def _run_ruff_before_save(self, editor, target_path: Path):
        """Apply selected save policy in memory before the existing disk write."""
        if not isinstance(editor, PythonEditor):
            return
        editor.ruff_service.python_executable = self.python_runner.interpreter
        if self.ruff_save_mode == "safe_format":
            editor.apply_safe_fixes_with_ruff(target_path)
            editor.format_with_ruff(target_path)

    def format_current_document(self):
        """Format only the focused Python tab; do not save automatically"""
        editor = self.current_editor()
        if isinstance(editor, PythonEditor):
            editor.ruff_service.python_executable = self.python_runner.interpreter
            editor.format_with_ruff()

    def organize_current_imports(self):
        """Organize imports only in the focused Python tab; do not save automatically."""
        editor = self.current_editor()
        if isinstance(editor, PythonEditor):
            editor.ruff_service.python_executable = self.python_runner.interpreter
            editor.organize_imports_with_ruff()

    def apply_safe_ruff_fixes(self):
        """Apply only safe Ruff fixes to focused Python source; do not save automatically."""
        editor = self.current_editor()
        if isinstance(editor, PythonEditor):
            editor.ruff_service.python_executable = self.python_runner.interpreter
            editor.apply_safe_fixes_with_ruff(editor.full_path or Path.cwd() / "untitled.py")

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

        edit_menu.addSeparator()
        format_action = edit_menu.addAction("Format Document")
        format_action.setShortcut("Ctrl+Alt+L")
        format_action.setShortcutContext(Qt.ApplicationShortcut)
        format_action.triggered.connect(self.format_current_document)

        imports_actions = edit_menu.addAction("Organize Imports")
        imports_actions.setShortcut("Ctrl+Alt+O")
        imports_actions.setShortcutContext(Qt.ApplicationShortcut)
        imports_actions.triggered.connect(self.organize_current_imports)

        fix_action = edit_menu.addAction("Apply Safe Ruff Fixes")
        fix_action.setShortcut("Ctrl+Alt+F")
        fix_action.setShortcutContext(Qt.ApplicationShortcut)
        fix_action.triggered.connect(self.apply_safe_ruff_fixes)

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
            self.save_as()
            path = getattr(editor, "path", None)
            if path is None:
                return
        else:
            self.save_file()

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
            path = getattr(editor, "path", None)

            if path is None:
                continue
            self._run_ruff_before_save(editor, path)
            try:
                path.write_bytes(editor.text().replace("\r\n", "\n").encode("utf-8"))
            except OSError as error:
                QMessageBox.warning(
                    self,
                    "Save All",
                    f"Could not save {path}:\n{error}",
                )
                continue

            self.mark_editor_clean(editor)
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
            self.save_as()
            path = getattr(editor, "path", None)
            if path is None:
                return  # User cancelled the sace dialog

        else:
            self.save_file()

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
            self.python_runner.set_interpreter(path)
            self.statusBar().showMessage(f"Python Interpreter: {path}", 3000)
            self._save_interpreter(path)

    def _save_interpreter(self, path: str):
        """Save the chosen interpreter to settings.json."""
        import json

        settings_path = Path(__file__).parent / "settings.json"
        settings = {}
        if settings_path.exists():
            settings = json.loads(settings_path.read_text())
        settings["interpreter"] = path
        settings_path.write_text(json.dumps(settings, indent=2))

    def _load_interpreter(self) -> str:
        """Load the saved interpreter, or default to sys.executable."""
        import json
        import sys

        settings_path = Path(__file__).parent / "settings.json"
        if settings_path.exists():
            settings = json.loads(settings_path.read_text())
            return settings.get("interpreter", sys.executable)
        return sys.executable

    def is_binary(self, path):
        """
        check if a file is binary
        :param path:
        :return:
        """
        with open(path, "rb") as f:
            return b"\0" in f.read(1024)

    def set_new_tab(self, path: Path, is_new_file=False, target_group=None):
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

        is_python_file = path.suffix.lower() in {".py", ".pyw", ".pyx", "pyi", ".c"}

        if self.python_editor_active:
            editor = PythonEditor(path=path, is_python_file=is_python_file)
        else:
            editor = MarkdownEditor(path=path, is_python_file=False)

        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError as error:
            QMessageBox.critical(
                self,
                "Open File",
                f"Could not open{path}:\n{error}",
            )
            editor.deleteLater()
            return None

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
        self.search_worker.finished.connect(self.search_finished)

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

        body.addWidget(self.side_bar)
        self.hs_split.addWidget(self.side_panel)
        self.hs_split.addWidget(self.tab_view)
        self.hs_split.addWidget(self.preview)
        self.hs_split.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.hs_split.setSizes([300, 550, 550])
        body.addWidget(self.hs_split)
        body_frame.setLayout(body)

        self.setCentralWidget(body_frame)

    def _show_tab_context_menu(self, pos: QPoint):
        """
        Show a context menu when the user right-clicks a tab.

        `pos` is relative to the QTabWidget.
        We need to fing which tab was clicked using the tab bar.
        :return:
        """
        # The QTabWidget has an internal QTabBar that holds the actual tab buttons.
        # We need to ask it which tab is at the click position.
        # But first, convert the pos from QTabWidget coordinates to QTabBar coordinates.
        # mapTo converts a point from one widget's coordinate system to another's.
        # Docs: https://doc.qt.io/qt-5/qwidget.html#mapTo

        tab_bar = self.tab_view.tabBar()
        # Convert the click position from QTabWidget coords to QTabBar coords
        bar_pos = tab_bar.mapFrom(self.tab_view, pos)
        index = tab_bar.tabAt(bar_pos)

        if index < 0:
            # User clicked somewhere that's not a tab (e.g. the empty space after tabs)
            return

        # Create the context menu
        # QMenu is a popup menu. You add actions to it and call exec_ to show it.
        # Docs: https://doc.qt.io/qt-5/qmenu.html
        menu = QMenu(self)

        close_action = menu.addAction("Close")
        close_others_action = menu.addAction("Close Others")
        close_all_action = menu.addAction("Close All")
        menu.addSeparator()
        copy_path_action = menu.addAction("Copy Path")
        reveal_action = menu.addAction("Reveal in File Manager")

        # exec_ shows the menu at the global screen position and blocks
        # until the user selects an item or clicks away.
        # mapToGlobal converts a local position to screen coordinates.
        # Docs: https://doc.qt.io/qt-5/qmenu.html#exec
        action = menu.exec_(self.tab_view.mapToGlobal(pos))

        if action == close_action:
            self.close_tab(index)
        elif action == close_others_action:
            self._close_other_tabs(index)
        elif action == close_all_action:
            self._close_all_tabs()
        elif action == copy_path_action:
            self._copy_tab_path(index)
        elif action == reveal_action:
            self._reveal_tab_in_file_manager(index)

    def _close_other_tabs(self, keep_index: int):
        """Close all tabs except the one at keep_index."""
        # Close tabs from right to left so indices don't shift
        # If you close from left to right, removing tab 0 makes tab 1 become tab 0,
        # and your keep_index would point at the wrong tab.
        for i in range(self.tab_view.count() - 1, -1, -1):
            if i != keep_index:
                self.close_tab(i)

    def _close_all_tabs(self):
        """Close every open tab."""
        for i in range(self.tab_view.count() - 1, -1, -1):
            self.close_tab(i)

    def _copy_tab_path(self, index: int):
        """Copy the file path of the tab at `index` to the clipboard."""
        editor = self.tab_view.widget(index)
        path = getattr(editor, "path", None)
        if path is not None:
            # QApplication.clipboard() gives access to the system clipboard
            # setText() puts text on it
            # Docs: https://doc.qt.io/qt-5/qclipboard.html#setText
            QApplication.clipboard().setText(str(path))
            self.statusBar().showMessage(f"Copied: {path}", 2000)

    def _reveal_tab_in_file_manager(self, index: int):
        """Open the OS file manager at the file's location."""
        editor = self.tab_view.widget(index)
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

    def search_finished(self, items):
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
                    updated_path == new_path
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

        for editor in self.tab_view.all_editors():
            editor_path = getattr(editor, "path", None)

            if editor_path == Path(editor_path):
                continue

            editor_path = Path(editor_path)

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
        }
        # Update icoon states. Reset all to gray, then set active to blue
        icon_map = {
            "folder": (resource_path("icons/folder.png"), resource_path("icons/folder-active.png")),
            "search": (resource_path("icons/search.png"), resource_path("icons/search-active.png")),
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
            "All Files (*);;Text Files (*.txt);;Python Files (*.py);;Markdown Files (*.md)",
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
        self._run_ruff_before_save(editor, path)
        try:
            path.write_bytes(editor.text().replace("\r\n", "\n").encode("utf-8"))
        except OSError as error:
            QMessageBox.critical(self, "Save File", f"Could not save '{path}':\n{error}")
            return False

        self.current_file = path
        self.mark_editor_clean(editor)
        self.statusBar().showMessage(f"Saved {path.name}", 3000)
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
        self._run_ruff_before_save(editor, path)
        try:
            path.write_bytes(editor.text().replace("\r\n", "\n").encode("utf-8"))
        except OSError as error:
            QMessageBox.critical(
                self,
                "Save File",
                f"Could not save {path}:\n{error}",
            )
            return False

        editor.path = path
        editor.full_path = path.absolute()
        self.current_file = path

        self.tab_view.set_editor_tooltip(
            editor,
            str(editor.full_path),
        )

        self.mark_editor_clean(editor)
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
        old = self.current_editor()
        if old is None:
            return None
        if isinstance(old, EditorClass):
            return old

        group = self.tab_view.group_for_editor(old)

        if group is None:
            return None

        index = group.indexOf(old)
        title = group.tabText(index)
        tooltip = group.tabToolTip(index)
        icon = group.tabIcon(index)

        text = old.text()
        path = getattr(old, "path", None)
        was_dirty = old in self._dirty_editors

        new_editor = EditorClass(path=path)
        new_editor.setTextSafely(text)
        self._connect_editor(new_editor)

        group.blockSignals(True)
        group.removeTab(index)
        group.insertTab(index, new_editor, icon, title)
        group.setTabToolTip(index, tooltip)
        group.setCurrentIndex(index)
        group.blockSignals(False)

        if was_dirty:
            self._dirty_editors.discard(old)
            self._dirty_editors.add(new_editor)

        if hasattr(old, "shutdown"):
            old.shutdown()

        old.setParent(None)
        old.deleteLater()

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
        # If there's a selection and it matches the search text, replace it
        if editor.hasSelectedText():
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
            if not self.close_editor(editor):
                event.ignore()
                return
        if hasattr(self, "terminal"):
            self.terminal.stop()
        if hasattr(self, "python_runner") and self.python_runner:
            self.python_runner.stop()
        if hasattr(self, "settings"):
            self.settings.setValue("recent_files", self.recent_files)

        event.accept()

    def check_for_updates(self):
        """
        Check GitHub Releases API for a newer version.
        Runs in a background thread so it doesn't freeze the GUI.
        """
        import json
        import threading
        import urllib.request

        # The GitHub API endpoint for your latest release
        # Replace YOUR_USERNAME and MarkdownEditor with your actual values
        api_url = "https://api.github.com/repos/YOUR_USERNAME/MarkdownEditor/releases/latest"

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

                # Extract the version tag (e.g. "v1.1.0")
                latest_version = data.get("tag_name", "")
                # Remove the "v" prefix if present: "v1.1.0" → "1.1.0"
                if latest_version.startswith("v"):
                    latest_version = latest_version[1:]

                # Extract the download URL for the first asset
                assets = data.get("assets", [])
                download_url = assets[0]["browser_download_url"] if assets else ""

                # Compare versions
                if latest_version and latest_version != APP_VERSION:
                    # Newer version available — show dialog on the main thread
                    # QTimer.singleShot(0, callback) runs the callback on the
                    # main Qt event loop thread. This is important because
                    # you can't create QDialogs from a background thread.
                    # Docs: https://doc.qt.io/qt-5/qtimer.html#singleShot
                    QTimer.singleShot(
                        0, lambda: self._show_update_dialog(latest_version, download_url)
                    )

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
