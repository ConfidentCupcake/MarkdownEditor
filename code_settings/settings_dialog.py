from pathlib import Path
import sys

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLineEdit, QSpinBox, QCheckBox, QPushButton,
    QFontComboBox, QComboBox, QFileDialog, QFormLayout,
)

class SettingsDialog(QDialog):
    """
    Tabbed settings dialog. Reads/writes the same keys the app already uses:
        - settings.json -> "interpreter"
        - QSettings -> "ruff_save_mode"
        - new keys -> font_family, font_size, tab_width, word_warp,
                      theme, line_numbers, highlight_line
    """
    
    def __init__(self, current_settings: dict, parent=None):
        super().__init__(parent)
        self.settings = current_settings.copy()
        self.setWindowTitle("Settings")
        self.setMinimumSize(520, 420)
        
        layout = QVBoxLayout(self)
        
        self.tabs = QTabWidget()
        self.tabs.addTab(self._create_editor_tab(), "Editor")
        self.tabs.addTab(self._create_python_tab(), "Python")
        self.tabs.addTab(self._create_appearance_tab(), "Appearance")
        layout.addWidget(self.tabs)
        
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(save_btn)        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)        
        layout.addLayout(btn_row)
        
    # --- Editor tab --------------------------------------------------------- #
    def _create_editor_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        
        self.font_combo = QFontComboBox()
        self.font_combo.setCurrentFont(
            QFont(self.settings.get("font_family", "sans-serif"))
        )
        layout.addRow("Font family:", self.font_combo)
        
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 32)
        self.font_size_spin.setValue(self.settings.get("font_size", 13))
        layout.addRow("Font size:", self.font_size_spin)
        
        self.tab_width_spin = QSpinBox()
        self.tab_width_spin.setRange(1, 8)
        # Markdown Editor defaults to 2, Python Editor to 4
        # use 4 as the shared setting and let Markdown keep 2 if you prefer per-mode.
        self.tab_width_spin.setValue(self.settings.get("tab_width", 4))
        layout.addRow("Tab width:", self.tab_width_spin)
        
        self.word_warp_cb = QCheckBox("Enable word wrap")  # BUGFIX: typo "wrong warp"
        # BUGFIX: read "word_warp" but main.py stores "word_wrap",
        # so the checkbox never showed the saved value.
        self.word_warp_cb.setChecked(self.settings.get("word_wrap", False))
        layout.addRow("", self.word_warp_cb)

        self.restore_tabs_cb = QCheckBox("Restore open tabs on startup")
        self.restore_tabs_cb.setChecked(self.settings.get("restore_tabs", True))
        layout.addRow("", self.restore_tabs_cb)
        
        return tab
    
    # --- Python tab ----------------------------------------------------------- #    
    def _create_python_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        
        interp_row = QHBoxLayout()
        self.interpreter_input = QLineEdit()
        # Pre-filled with tht interpreter the runner is currently using,
        # falling back to the settings.json value.
        self.interpreter_input.setText(self.settings.get("interpreter", ""))
        interp_row.addWidget(self.interpreter_input)
        
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_interpreter)
        interp_row.addWidget(browse_btn)
        layout.addRow("Python interpreter:", interp_row)
        
        # Maps onto the existing QSettings "ruff_save_mode" key that 
        # _run_ruff_before_save() already checks (safe_format" or "off")
        self.ruff_combo = QComboBox()
        self.ruff_combo.addItem("Off (never run Ruff on save)", "off")
        self.ruff_combo.addItem("Safe fixes + format on save", "safe_format")
        current_mode = self.settings.get("ruff_save_mode", "safe_format")
        index = self.ruff_combo.findData(current_mode)
        if index >= 0:
            self.ruff_combo.setCurrentIndex(index)
        layout.addRow("Ruff on save:", self.ruff_combo)
        
        return tab
    
    # --- Appearance tab ------------------------------------------------------------ #
    def _create_appearance_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        
        self.theme_combo = QComboBox()
        # BUGFIX: settings_dialog.py lives in code_settings/, so
        # Path(__file__).parent / "themes" pointed at code_settings/themes/
        # which does not exist -> the combo stayed EMPTY. Go one level up,
        # and honour PyInstaller's bundle dir when frozen.
        if getattr(sys, "frozen", False):
            themes_dir = Path(sys._MEIPASS) / "themes"
        else:
            themes_dir = Path(__file__).resolve().parent.parent / "themes"
        if themes_dir.is_dir():
            for theme_file in sorted(themes_dir.glob("*.json")):
                # userData stores the file name; both lexer classes load
                # themes/theme.json by default, so a theme switch recreates
                # the lexer with the chosen file instead.
                self.theme_combo.addItem(theme_file.stem, theme_file.name)
        if self.theme_combo.count() == 0:
            # Fallback so the combo is never empty and saving a None
            # theme is impossible.
            self.theme_combo.addItem("theme", "theme.json")
        current_theme = self.settings.get("theme") or "theme.json"
        index = self.theme_combo.findData(current_theme)
        # If the stored theme file was deleted, fall back to the first
        # entry instead of leaving the combo blank.
        if index < 0:
            index = 0
        self.theme_combo.setCurrentIndex(index)
        layout.addRow("Theme:", self.theme_combo)
        
        self.line_numbers_cb = QCheckBox("Show Line Numbers")
        self.line_numbers_cb.setChecked(self.settings.get("line_numbers", True))
        layout.addRow("", self.line_numbers_cb)
        
        self.highlight_line_cb = QCheckBox("Highlight Current Line")
        self.highlight_line_cb.setChecked(self.settings.get("highlight_line", True))
        layout.addRow("", self.highlight_line_cb)
        
        return tab
    
    def _browse_interpreter(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Python Interpreter", "",
            "Python Executable (python.exe);;All Files (*)",
        )
        if path:
            self.interpreter_input.setText(path)
            
            
    def get_settings(self) -> dict:
        return {
            "font_family": self.font_combo.currentFont().family(),
            "font_size": self.font_size_spin.value(),
            "tab_width": self.tab_width_spin.value(),
            "word_wrap": self.word_warp_cb.isChecked(),
            "restore_tabs": self.restore_tabs_cb.isChecked(),
            "interpreter": self.interpreter_input.text().strip(),
            "ruff_save_mode": self.ruff_combo.currentData(),
            "theme": self.theme_combo.currentData() or "theme.json",
            "line_numbers": self.line_numbers_cb.isChecked(),
            "highlight_line": self.highlight_line_cb.isChecked(),
        }
        
