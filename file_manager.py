from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.Qsci import *

from pathlib import Path
import shutil
import os
import sys
import subprocess

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

        self.model: QFileSystemModel = QFileSystemModel()
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
            self.action_new_folder()
        elif action.text() == "New File":
            self.action_new_file(ix)
        elif action.text() == "Open In File Manager":
            self.action_open_in_file_manager()
        else:
            pass

    def show_dialog(self, title, msg) -> int:
        dialog = QMessageBox(self)
        dialog.setFont(self.manager_font)
        dialog.font().setPointSize(13)
        dialog.setWindowTitle(title)
        dialog.setWindowIcon(QIcon(":/icons/close-icon.png"))
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
            self.main_window.on_file_renamed(
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

    def action_new_folder(self):
        f = Path(self.model.rootPath()) / "New Folder"
        count = 1
        while f.exists():
            f = Path(f.parent / f"New Folder{count}")
            count += 1
        idx = self.model.mkdir(self.rootIndex(), f.name)
        # edit that index
        self.edit(idx)

    def action_open_in_file_manager(self, ix: QModelIndex):
        path = os.path.abspath(self.model.filePath(ix))
        is_dir = self.model.isDir(ix)
        if os.name == "nt":
            # Windows
            if is_dir:
                subprocess.Popen(f"explorer '{path}'")
            else:
                subprocess.Popen(f'explorer /select,"{path}"')
        elif os.name == "posix":
            # Linux or Mac OS
            if sys.platform == "darwin":
                # Mac Os
                if is_dir:
                    subprocess.Popen(["open", path])
                else:
                    subprocess.Popen(["open", "-R", path])
            else:
                # Linux
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
        else: raise OSError(f"Unsupported OS: {os.name}")

    def dropEvent(self, e:QDropEvent) -> None:
        root_path = Path(self.model.rootPath())
        if e.mimeData().hasUrls():
            for url in e.mimeData().urls():
                path = Path(url.toLocalFile())
                if path.is_dir():
                    shutil.copytree(path, root_path / path.name)
                else:
                    if not path.parent.samefile(root_path):
                        shutil.copy(path, root_path / path.name)
                    else:
                        shutil.move(path, root_path / path.name)

        e.accept()

        return super().dropEvent(e)
