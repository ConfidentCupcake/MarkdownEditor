from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt, QEvent, pyqtSignal
from PyQt5.QtWidgets import QSplitter, QTabWidget, QWidget

class MultiTabView(QSplitter):
    currentEditorChanged = pyqtSignal(object)       # Emitted whenever the active Editor changes
    closeEditorRequested = pyqtSignal(object)       # Emitted when the user presses the close button.
    editorMoved = pyqtSignal(object)                     # Emitted after an Editor moves from one tab group to another
    
    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        
        self.setChildrenCollapsible(False)
        self.setHandleWidth(2)
        
        self._groups = []
        self._active_group = None
        self._create_group()
        
    def _create_group(self, after=None):
        """
        Creates and registers a new QTabWidget.
        
        Parameters:
            - after:  Optional existing tab group. When provided, the new group is inserted
                        Immediately after it. When omitted, the group id appended.
        
        Returns:
            - The newly created QTabWidget
        """
        group = QTabWidget(self)
        group.setTabsClosable(True)
        group.setMovable(True)
        group.setDocumentMode(True)
        group.setElideMode(Qt.ElideMiddle)
        
        group.currentChanged.connect(
            lambda index, tab_group=group:
            self._on_group_current_changed(tab_group, index)
        )
        group.tabCloseRequested.connect(
            lambda index, tab_group=group:
            self._on_group_close_requested(tab_group, index)
        )
        
        group.tabBar().installEventFilter(self)
        
        if after is None:
            insert_position = len(self._groups)
        else:
            insert_position = self._groups.index(after) + 1
            
        self._groups.insert(insert_position, group)
        self.insertWidget(insert_position, group)
        self._set_active_group(group)
        self._resize_groups()
        
        return group
        
    def eventFilter(self, watched, event):
        """
        Tracks user interaction with tab bars and editor widgets.
        
        Parameters:
            watched: The Qt object that recieved the event.
            event: The even being processed
            
        Returns:
            The result of the parent eventFilter() implementation.
        """
        if event.type() in (
            QEvent.MouseButtonPress,
            QEvent.FocusIn,
        ):
            for group in self._groups:
                if watched is group.tabBar():
                    self._set_active_group(group)
                    break
                    
            group = self.group_for_editor(watched)
            if group is not None:
                self._set_active_group(group)
        return super().eventFilter(watched, event)
        
    def _set_active_group(self, group):
        if group not in self._groups:
            return
        self._active_group = group
        editor = group.currentWidget()
        
        if editor is not None:
            self.currentEditorChanged.emit(editor)
            
    def _on_group_current_changed(self, group, index):
        if index < 0:
            return
        self._set_active_group(group)
        
    def _on_group_close_requested(self, group, index):
        editor = group.widget(index)
        if editor is not None:
            self.closeEditorRequested.emit(editor)
            
            
    def _resize_groups(self):
        if not self._groups:
            return
        available = max(self.width(), 1)
        size = available // len(self._groups)
        self.setSizes([size] * len(self._groups))
        
    def groups(self):
        return tuple(self._groups)
        
    def active_group(self):
        if self._active_group in self._groups:
            return self._active_group
            
        return self._groups[0] if self._groups else None
        
    def currentWidget(self):
        group = self.active_group()
        return group.currentWidget() if group is not None else None
        
    def current_editor(self):
        return self.currentWidget()
        
    def all_editors(self):
        editors = []
        
        for group in self._groups:
            for index in range(group.count()):
                editor = group.widget(index)
                if editor is not None:
                    editors.append(editor)
                    
        return editors
        
    def group_for_editor(self, editor):
        for group in self._groups:
            if group.indexOf(editor) >= 0:
                return group
                
        return None
        
    def find_editor_by_path(self, path):
        if path is None:
            return None
            
        wanted = Path(path).resolve()
        
        for editor in self.all_editors():
            editor_path = getattr(editor, "path", None)
            if editor_path is None:
                continue
                
            try:
                if Path(editor_path).resolve() == wanted:
                    return editor
            except OSError:
                if Path(editor_path) == Path(path):
                    return editor
        
        return None
        
    def add_editor(self, editor, title, group=None):
        destination = group or self.active_group()
        
        if destination is None:
            destination = self._create_group()
            
        index = destination.addTab(editor, title)
        editor.installEventFilter(self)
        
        destination.setCurrentIndex(index)
        self._set_active_group(destination)
        editor.setFocus()
        
        if hasattr(editor, "focused"):
            editor.focused.connect(self._on_editor_focused)
        
        return editor
        
    def focus_editor(self, editor):
        group = self.group_for_editor(editor)
        if group is None:
            return False
        
        index = group.indexOf(editor)
        group.setCurrentIndex(index)
        self._set_active_group(group)
        editor.setFocus()
        
        return True 
        
    def title_for_editor(self, editor):
        group = self.group_for_editor(editor)
        if group is None:
            return ""
            
        index = group.indexOf(editor)
        return group.tabText(index)
        
    def set_editor_title(self, editor, title):
        group = self.group_for_editor(editor)
        if group is None:
            return
            
        index = group.indexOf(editor)
        group.setTabText(index, title)
        
    def set_editor_tooltip(self,  editor, tooltip):
        group = self.group_for_editor(editor)
        if group is None:
            return
        
        index = group.indexOf(editor)
        group.setTabToolTip(index, tooltip)
        
    def split_right(self, editor=None):
        editor = editor or self.current_editor()
        source = self.group_for_editor(editor)
        
        if source is None:
            return None
            
        destination = self._create_group(after=source)
        
        if editor is not None:
            self.move_editor(editor, destination)
            
        return destination
            
    def move_editor(self, editor, destination):
        source = self.group_for_editor(editor)
        
        if source is None or destination not in self._groups:
            return False
            
        if source is destination:
            return True
            
        source_index = source.indexOf(editor)
        title = source.tabText(source_index)
        tooltip = source.tabToolTip(source_index)
        icon = source.tabIcon(source_index)
        
        source.removeTab(source_index)
        
        destination_index = destination.addTab(editor, icon, title)
        destination.setTabToolTip(destination_index, tooltip)
        destination.setCurrentIndex(destination_index)
        
        self._set_active_group(destination)
        editor.setFocus()
        
        self.editorMoved.emit(editor)
        self._remove_empty_group(source)
        self._resize_groups()
        
        return True
        
    def remove_editor(self, editor):
        group = self.group_for_editor(editor)
        if group is None:
            return False
            
        index = group.indexOf(editor)
        group.removeTab(index)
        
        self._remove_empty_group(group)
        
        current = self.current_editor()
        self.currentEditorChanged.emit(current)
        
        return True
        
    def _remove_empty_group(self, group):
        if group.count() != 0 or len(self._groups) == 1:
            return
            
        index = self._groups.index(group)
        self._groups.remove(group)
        
        group.setParent(None)
        group.deleteLater()
        
        replacement_index = min(index, len(self._groups) - 1)
        self._set_active_group(self._groups[replacement_index])
        self._resize_groups()
        
    def unsplit_active_group(self):
        source = self.active_group()
    
        if source is None or len(self._groups) == 1:
            return
        source_index = self._groups.index(source)
        destination_index = source_index - 1 if source_index > 0 else 1
        destination = self._groups[destination_index]

        while source.count():
            editor = source.widget(0)
            self.move_editor(editor, destination)

        self._set_active_group(destination)

    def close_all_groups(self):
        for editor in list(self.all_editors()):
            self.closeEditorRequested.emit(editor)
            
    def _on_editor_focused(self, editor):
        group = self.group_for_editor(editor)
        if group is not None:
            self._set_active_group(group)