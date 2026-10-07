from __future__ import annotations
from dataclasses import dataclass 
from PyQt5.QtCore import QObject
from PyQt5.QtWidgets import QAction, QMenu, QMenuBar

@dataclass(frozen=True)
class Command:
    """One stable application command backed by its existing QAction."""
    
    command_id: str
    label: str
    category: str
    action: QAction
    
    @property
    def searchable_text(self) -> str:
        """Combine category, current label and shortcut into case-insensitive searchable text."""
        return f"{self.category} {self.label} {self.action.shortcut().toString()}".casefold()
    

class CommandRegistry(QObject):
    """Own the commands shared by menus, shortcuts, and the palette."""
    
    def __init__(self, parent=None):
        """Create the command map without copying any QAction callbacks."""
        super().__init__(parent)
        self._commands: dict[str, Command] = {}
        
    def register(self, command: Command) -> None:
        """Retain one command per stable identifier and reject accidental duplicate registrations."""
        if command.command_id in self._commands:
            raise ValueError(f"Duplicate command id: {command.command_id}")
        self._commands[command.command_id] = command
        
    def commands(self) -> tuple[Command, ...]:
        """Return the current discovered commands as a immutable sequence."""
        return tuple(self._commands.values())
    
    def register_menu_bar(self, menu_bar: QMenuBar) -> None:
        """Traversee top-level menus and collect their executable leaf actions."""
        for top_action in menu_bar.actions():
            menu = top_action.menu()
            if menu is not None:
                self._regrister_menu(menu, self._clean(top_action.text()))
                
    def _regrister_menu(self, menu: QMenu, category: str) -> None:
        """Collect leaf actions without retaining invisible menu entries"""
        for action in menu.actions():
            submenu = action.menu()
            label = self._clean(action.text())
            if submenu is not None:
                self._regrister_menu(submenu, f"{category} / {label}")
                continue
            if action.isSeparator() or not action.isVisible() or not label:
                continue
            # objectName is the future stable-ID path. Existing actions do not set one yet,
            # so the normalized menu path is a migration fallback.
            command_id = action.objectName() or self._slug(f"{category}.{label}")
            if command_id not in self._commands:
                self.register(Command(command_id, label, category, action))
                
                
    @staticmethod
    def _clean(text: str) -> str:
        """Remove Qt mnemonic ampersands from a displayed menu label."""
        return text.replace("&", "").strip()
    
    @staticmethod
    def _slug(value: str) -> str:
        """Build a fallback identifier for legacy actions that have no explicit object name."""
        return ".".join(value.casefold().replace("/", " ").split())
    
    def refresh_menus(self, menu_bar):
        """Rebuild discovery so recent-file actions removed by Qt are never retained."""
        self._commands.clear()
        self.register_menu_bar(menu_bar)
        
