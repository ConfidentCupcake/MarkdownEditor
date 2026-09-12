import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from PyQt5.QtCore import QDir, QModelIndex, QPoint, Qt, QDir, QItemSelectionModel
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
from markdowneditor_assets import asset_path
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
        """Display a consistently styled destructive-operation prompt."""
        dialog = QMessageBox(self)
        dialog.setFont(self.manager_font)
        dialog.font().setPointSize(13)
        dialog.setWindowTitle(title)
        dialog.setWindowIcon(QIcon(asset_path("icons/close-icon.svg")))
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

    @staticmethod
    def _path_exists(path: Path) -> bool:
        """Return true for normal paths and broken symbolic links."""
        return os.path.lexists(path)

    def delete_file(self, path: Path):
        """Delete exactly ``path``; never follow a directory symlink."""
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    def action_delete(self, index: QModelIndex):
        """Stage all paths, then close tabs and commit permanent deletion.

        Prompts occur before touching disk, but tabs remain alive until every
        staging rename succeeds. A failed stage can therefore restore paths
        without losing editor widgets or discarded in-memory buffers.
        """
        if not index.isValid():
            return

        rows = list(self.selectionModel().selectedRows())
        if index not in rows:
            self.selectionModel().clearSelection()
            self.selectionModel().select(
                index,
                QItemSelectionModel.Select | QItemSelectionModel.Rows,
            )
            rows = [index]

        selected = [(Path(self.model.filePath(row)), self.model.isDir(row)) for row in rows]
        targets = [
            (path, is_dir)
            for path, is_dir in selected
            if not any(other != path and other in path.parents for other, _ in selected)
        ]
        label = f"'{targets[0][0].name}'" if len(targets) == 1 else f"{len(targets)} selected items"
        answer = self.show_dialog(
            "Permanent Delete",
            f"Permanently delete {label}? This cannot be undone.",
        )
        if answer != QMessageBox.Yes:
            return

        # Gather all Save/Discard/Cancel decisions first. Save is performed now;
        # Discard does not close the editor until staging succeeds.
        for path, is_directory in targets:
            if not self.main_window.can_close_editors_for_path(path, is_directory=is_directory):
                return

        staged = []
        try:
            for path, _is_directory in targets:
                temporary = path.with_name(
                    f".{path.name}.markdowneditor-delete-{uuid4().hex}"
                )
                path.rename(temporary)
                staged.append((path, temporary))
        except OSError as error:
            rollback_errors = []
            for original, temporary in reversed(staged):
                try:
                    temporary.rename(original)
                except OSError as rollback_error:
                    rollback_errors.append(str(rollback_error))
            details = "\n".join(rollback_errors)
            QMessageBox.critical(
                self,
                "Delete",
                f"Nothing was deleted because staging failed:\n{error}"
                + (f"\nRollback errors:\n{details}" if details else ""),
            )
            return

        # Every disk target is now staged. Closing cannot invalidate rollback,
        # and no prompt is repeated because decisions were collected above.
        for path, is_directory in targets:
            self.main_window.close_editors_for_path(
                path,
                is_directory=is_directory,
                prompt=False,
            )

        cleanup_errors = []
        for original, temporary in staged:
            try:
                self.delete_file(temporary)
            except OSError as error:
                # The hidden staged path remains available for manual recovery.
                cleanup_errors.append(f"{original.name}: {error}")
        if cleanup_errors:
            QMessageBox.critical(
                self,
                "Delete cleanup",
                "Some staged recovery items could not be removed:\n"
                + "\n".join(cleanup_errors),
            )
                
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

    def dropEvent(self, event: QDropEvent) -> None:
        """Move/copy a validated batch without dereferencing symlink sources.

        Lexical paths identify the filesystem objects being manipulated.
        Resolved paths are used only for directory-containment validation.
        Copying a symlink preserves the link; moving a symlink moves the link.
        """
        if not event.mimeData().hasUrls():
            event.ignore()
            return

        index = self.indexAt(event.pos())
        if index.isValid():
            selected = Path(self.model.filePath(index))
            target_dir = selected if selected.is_dir() else selected.parent
        else:
            target_dir = Path(self.model.rootPath())

        copy_requested = bool(event.keyboardModifiers() & Qt.ControlModifier)
        operations = []
        try:
            resolved_target_dir = target_dir.resolve(strict=True)
            for url in event.mimeData().urls():
                # absolute() preserves a symlink's identity; resolve() would
                # silently turn the operation into one on its target.
                source = Path(url.toLocalFile()).absolute()
                if not self._path_exists(source):
                    raise FileNotFoundError(f"Source does not exist: {source}")

                destination = target_dir / source.name
                if source == destination:
                    continue
                if self._path_exists(destination):
                    raise FileExistsError(f"Destination already exists: {destination}")

                # A real directory cannot be copied/moved into itself. A
                # directory symlink is treated as a link object, not traversed.
                if source.is_dir() and not source.is_symlink():
                    resolved_source = source.resolve(strict=True)
                    resolved_destination = (resolved_target_dir / source.name).resolve(
                        strict=False
                    )
                    if resolved_source in resolved_destination.parents:
                        raise OSError("Cannot copy a folder into itself")
                operations.append((source, destination))

            completed = []
            for source, destination in operations:
                if copy_requested:
                    if source.is_symlink():
                        # copy2(..., follow_symlinks=False) recreates the link
                        # rather than copying the target's bytes/tree.
                        shutil.copy2(source, destination, follow_symlinks=False)
                    elif source.is_dir():
                        shutil.copytree(source, destination)
                    else:
                        shutil.copy2(source, destination)
                else:
                    shutil.move(str(source), str(destination))
                completed.append((source, destination))
        except OSError as error:
            rollback_errors = []
            for source, destination in reversed(locals().get("completed", [])):
                try:
                    if copy_requested:
                        self.delete_file(destination)
                    elif self._path_exists(destination) and not self._path_exists(source):
                        shutil.move(str(destination), str(source))
                except OSError as rollback_error:
                    rollback_errors.append(str(rollback_error))
            details = "\n".join(rollback_errors)
            QMessageBox.critical(
                self,
                "File operation",
                str(error) + (f"\nRollback errors:\n{details}" if details else ""),
            )
            event.ignore()
            return

        event.setDropAction(Qt.CopyAction if copy_requested else Qt.MoveAction)
        event.accept()
        
        
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
