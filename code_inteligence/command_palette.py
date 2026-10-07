from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from code_inteligence.command_regristry import Command, CommandRegistry

class CommandPaletteDialog(QDialog):
    """Keyboard-first fiiltered view over a CommandRegistry"""
    
    def __init__(self, registry: CommandRegistry, parent=None):
        """Build a search field and result list bound to the shared command registry."""
        super().__init__(parent, Qt.Popup)
        self.registry = registry
        self.setMinimumWidth(620)
        
        layout = QVBoxLayout(self)
        
        self.query = QLineEdit(self)
        self.query.setPlaceholderText("Type a command...")
        self.results = QListWidget(self)
        
        layout.addWidget(self.query)
        layout.addWidget(self.results)
        
        self.query.textChanged.connect(self._refill)
        self.query.returnPressed.connect(self._execute_current)
        self.results.itemActivated.connect(lambda _item: self._execute_current())

    def open_palette(self) -> None:
        """Refresh live menu actions, reset the query and focus the popup search field."""
        self.registry.refresh_menus(self.parent().menuBar())
        self.query.clear()
        self._refill("")
        self.show()
        self.raise_()
        self.query.setFocus()
        
    def _refill(self, query: str) -> None:
        """Filter by all query tokens while displaying current shortcuts and disabled state."""
        tokens = query.casefold().split()
        self.results.clear()
        for command in self.registry.commands():
            if all(token in command.searchable_text for token in tokens):
                shortcut = command.action.shortcut().toString()
                suffix = f"    {shortcut}" if shortcut else ""
                item = QListWidgetItem(f"{command.category}: {command.label}{suffix}")
                item.setData(Qt.UserRole, command)
                item.setFlags(item.flags() | Qt.ItemIsEnabled if command.action.isEnabled() else item.flag() & ~Qt.ItemIsEnabled)
                self.results.addItem(item)
        
        if self.results.count():
            self.results.setCurrentRow(0)
                
    def _execute_current(self) -> None:
        """Close the popup and trigger the selected existing action only when enabled."""
        item = self.results.currentItem()
        command: Command | None = item.data(Qt.UserRole) if item else None
        if command is None or not command.action.isEnabled():
            return
        self.accept()
        command.action.trigger()
        
    def keyPressEvent(self, event) -> None:
        """Move through results with arrow keys and preserve normal dialog Escape behavior."""
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            delta = 1 if event.key() == Qt.Key.Key_Down else - 1
            count = self.results.count()
            if count:
                self.results.setCurrentRow((self.results.currentRow() + delta) % count)
            return
        super().keyPressEvent(event)
            
