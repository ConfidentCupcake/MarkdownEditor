import os
import shutil
import subprocess
import sys
from pathlib import Path

from PyQt5.QtCore import QDir, QModelIndex, QPoint, Qt
from PyQt5.QtGui import QColor, QDragEnterEvent, QDropEvent, QFont, QIcon
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QFileSystemModel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QTreeView,
)

class FileManager(QTreeView):
    def __init__(self, set_new_tab, main_window, parent=None):
        super(FileManager, self).__init__(None)

        self.set_new_tab = set_new_tab
        self.main_window = main_window
        
        self._rename_old_path = None
        self._rename_is_directory = False
        self.is_renaming = False
        self.current_edit_index = None

        self.manager_font = QFont("sans-serif", 13)

        self.model = GitAwareFileSystemModel(self)
        self.model.setRootPath(os.getcwd())
        # File system filters
        self.model.setFilter(QDir.NoDotAndDotDot | QDir.AllDirs | QDir.Files | QDir.Drives)
        self.model.setReadOnly(False)
        self.setFocusPolicy(Qt.NoFocus)

        self.setFont(self.manager_font)
        self.setModel(self.model)
        self.setRootIndex(self.model.index(os.getcwd()))
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSelectionBehavior(QTreeView.SelectRows)
        self.setEditTriggers(QTreeView.EditTrigger.NoEditTriggers)

        # add text menu
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

        # hank
        self.clicked.connect(self.tree_view_clicked)
        self.setIndentation(10)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Hiding other columns except for name
        self.setHeaderHidden(True)
        self.setColumnHidden(1,True)
        self.setColumnHidden(2,True)
        self.setColumnHidden(3,True)

        # enable drag and drop
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)

        # enable file name editing
        # renaming
        self.itemDelegate().closeEditor.connect(self._on_closeEditor)
        
        from python_editor.git_integration import GitStatusChecker
        self.git_checker = GitStatusChecker()
        self.git_checker.status_ready.connect(self._on_git_status)
        self.git_statuses = {}
        
    def check_git_status(self):
        """Start a git status check for the current root path."""
        self.git_checker.check(self.model.rootPath())
        
    def _on_git_status(self, statuses: dict):
        """
        Store the new statuses and repaint.

        BUGFIX (Windows): the checker emits keys from Path.resolve()
        (backslash paths, e.g. C:\repo\file.py), while
        QFileSystemModel.filePath() reports FORWARD slashes
        (C:/repo/file.py). The lookup in get_git_color() never matched,
        so no file was ever colored. Normalizing both sides with
        os.path.normpath (unifies separators) + os.path.normcase
        (lowercases drive letters on Windows) makes the keys comparable.
        """
        self.git_statuses = {
            os.path.normcase(os.path.normpath(p)): code
            for p, code in statuses.items()
        }
        self.viewport().update()    # repaint with new colors

    def get_git_color(self, filepath: str):
        """Color for a file path as QFileSystemModel reports it."""
        status = self.git_statuses.get(
            os.path.normcase(os.path.normpath(filepath)))
        if status is None:
            return None
        return GitAwareFileSystemModel.GIT_COLORS.get(status)
    
    def _on_closeEditor(self, editor: QLineEdit):
        if self.is_renaming:
            self.rename_file_with_index()

    def tree_view_clicked(self, index: QModelIndex):
        path = self.model.filePath(index)
        p = Path(path)
        if p.is_file():
            self.set_new_tab(p)

        # drag & drop functionality
    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.accept()
        else:
            e.ignore()

    def show_context_menu(self, pos: QPoint):
        ix = self.indexAt(pos)
        menu = QMenu()
        menu.addAction("New File")
        menu.addAction("New Folder")
        menu.addAction("Open In File Manager")

        if ix.column() == 0:
            menu.addAction("Rename")
            menu.addAction("Delete")

        action = menu.exec_(self.viewport().mapToGlobal(pos))

        if not action:
            return

        if action.text() == "Rename":
            self.action_rename(ix)
        elif action.text() == "Delete":
            self.action_delete(ix)
        elif action.text() == "New Folder":
            self.action_new_folder(ix)
        elif action.text() == "New File":
            self.action_new_file(ix)
        elif action.text() == "Open In File Manager":
            self.action_open_in_file_manager(ix)
        else:
            pass

    def show_dialog(self, title, msg) -> int:
        dialog = QMessageBox(self)
        dialog.setFont(self.manager_font)
        dialog.font().setPointSize(13)
        dialog.setWindowTitle(title)
        root = (
            Path(sys._MEIPASS)
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent.parent
        )
        dialog.setWindowIcon(QIcon(str(root / "icons" / "close-icon.svg")))
        dialog.setText(msg)
        dialog.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        dialog.setDefaultButton(QMessageBox.No)
        dialog.setIcon(QMessageBox.Warning)
        return dialog.exec_()

    def rename_file_with_index(self):
        """
        Notify MainWindow after QFileSystemModel completes a rename.
        
        QFileSystemModel performs the actual disk rename. This method only
        propagates the changed path to any editor tabs that represent it.
        """
        try:
            old_path = self._rename_old_path
            if old_path is None:
                return
            new_path = Path(
                self.model.filePath(self.current_edit_index)
            )

            if new_path == old_path:
                return
            self.main_window.on_file_rename(
                old_path=old_path,
                new_path=new_path,
                is_directory=self._rename_is_directory,
            )
        finally:
            self._rename_old_path = None
            self._rename_is_directory = False
            self.current_edit_index = None
            self.is_renaming = False

    def action_rename(self, ix):
        """Starts QFileSystemModel inline rename and remember the original path."""
        if not ix.isValid():
            return
        
        self._rename_old_path = Path(self.model.filePath(ix))
        self._rename_is_directory = self.model.isDir(ix)
        self.current_edit_index = ix
        self.is_renaming = True
        self.edit(ix)

    def delete_file(self, path: Path):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


    def action_delete(self, index: QModelIndex):
        """Close affected editor tabs first, then delete selected filesystem paths."""
        if not index.isValid():
            return
        
        file_name = self.model.fileName(index)
        answer = self.show_dialog("Delete", f"Are you sure you want to delete {file_name}?")
        
        if answer != QMessageBox.Yes:
            return
        selected_indexes = self.selectionModel().selectedRows()    
        if not selected_indexes:
            selected_indexes = [index]
        selected_paths = [
            (
                Path(self.model.filePath(item)),
                self.model.isDir(item),
            )
            for item in selected_indexes
        ]
        # If both parent folder and one of its children are selected,
        # delete only the parent. The child disappears with it.
        top_level_paths = []
        
        for path, is_directory in selected_paths:
            has_selected_parent = any(
                other_path != path and
                other_path in path.parents
                for other_path, _ in selected_paths
            )

            if not has_selected_parent:
                top_level_paths.append((path, is_directory))

        for path, is_directory in top_level_paths:
            can_delete = self.main_window.close_editors_for_path(
                target_path=path,
                is_directory=is_directory,
            )

            if not can_delete:
                return

        for path, _ in top_level_paths:
            try:
                self.delete_file(path)
            except OSError as error:
                QMessageBox.critical(
                    self,
                    "Delete",
                    f"Could not delete '{path.name}':\n{error}",
                )
                return
                
    def action_new_file(self, ix: QModelIndex):
        root_path = self.model.rootPath()
        if ix.column() != -1 and self.model.isDir(ix):
            self.expand(ix)
            root_path = self.model.filePath(ix)

        f = Path(root_path) / "file"
        count = 1
        while f.exists():
            f = Path(f.parent / f"file{count}")
            count +=1
        f.touch()
        idx = self.model.index(str(f.absolute()))
        self.edit(idx)

    def action_new_folder(self, ix=None):
        parent = Path(self.model.rootPath())
        if ix is not None and ix.isValid():
            selected = Path(self.model.filePath(ix))
            parent = selected if selected.is_dir() else selected.parent
        f = parent / "New Folder"
        count = 1
        while f.exists():
            f = Path(f.parent / f"New Folder{count}")
            count += 1
        idx = self.model.mkdir(self.model.index(str(parent)), f.name)
        # edit that index
        self.edit(idx)

    def action_open_in_file_manager(self, ix: QModelIndex):
        if not ix.isValid():
            return
        path = os.path.abspath(self.model.filePath(ix))
        is_dir = self.model.isDir(ix)
        try:
            if os.name == "nt":
                if is_dir:
                    subprocess.Popen(["explorer", path])
                else:
                    subprocess.Popen(["explorer", f"/select,{path}"])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path] if is_dir else ["open", "-R", path])
            elif os.name == "posix":
                subprocess.Popen(["xdg-open", path if is_dir else os.path.dirname(path)])
            else:
                raise OSError(f"Unsupported OS: {os.name}")
        except OSError as error:
            QMessageBox.warning(self, "Open in file manager", str(error))

    def dropEvent(self, e:QDropEvent) -> None:
        if not e.mimeData().hasUrls():
            e.ignore()
            return
        index = self.indexAt(e.pos())
        if index.isValid():
            selected = Path(self.model.filePath(index))
            target_dir = selected if selected.is_dir() else selected.parent
        else:
            target_dir = Path(self.model.rootPath())
        copy_requested = bool(e.keyboardModifiers() & Qt.ControlModifier)
        try:
            for url in e.mimeData().urls():
                source = Path(url.toLocalFile()).resolve()
                destination = (target_dir / source.name).resolve()
                if source == destination:
                    continue
                if source.is_dir() and source in destination.parents:
                    raise OSError("Cannot copy a folder into itself")
                if destination.exists():
                    raise FileExistsError(f"Destination already exists: {destination}")
                if copy_requested:
                    if source.is_dir():
                        shutil.copytree(source, destination)
                    else:
                        shutil.copy2(source, destination)
                else:
                    shutil.move(str(source), str(destination))
        except OSError as error:
            QMessageBox.critical(self, "File operation", str(error))
            e.ignore()
            return
        e.setDropAction(Qt.CopyAction if copy_requested else Qt.MoveAction)
        e.accept()
class GitAwareFileSystemModel(QFileSystemModel):
    """QFileSystemModel that colors filenames by git status."""

    # Colors tuned to the editor's CustomDark palette
    GIT_COLORS = {
        " M": "#e5c07b",   # modified (not staged) — yellow
        "M ": "#98c379",   # modified (staged)     — green
        "A ": "#98c379",   # added (staged)        — green
        "R ": "#98c379",   # renamed (staged)      — green
        "??": "#56b6c2",   # untracked             — cyan
        "D ": "#e06c75",   # deleted (staged)      — red
        " D": "#e06c75",   # deleted (not staged)  — red
    }

    def __init__(self, file_manager):
        super().__init__()
        self.file_manager = file_manager

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.ForegroundRole:
            color = self.file_manager.get_git_color(self.filePath(index))
            if color:
                return QColor(color)
        return super().data(index, role)
