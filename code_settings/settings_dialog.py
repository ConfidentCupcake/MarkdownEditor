from pathlib import Path
from PyQt5.QtCore import QtCore
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QSpinBox, QCheckBox, QPushButton,
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
        
        self.word_warp_cb = QCheckBox("Enable wrong warp")
        self.word_warp_cb.setChecked(self.settings.get("word_warp", False))
        layout.addRow("", self.word_warp_cb)
        
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
        self.ruff_combo.additem("Safe fixes + format on save", "safe_format")
        current_mode = self.settings.get("ruff_save_mode", "off")
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
        themes_dir = Path(__file__).parent / "themes"
        if themes_dir.is_dir():
            for theme_file in sorted(themes_dir.glob("*json")):
                # userData stores the file name; MarkdownCustonLexer and PyCustomLexer both load themes/themes.json by default, 
                # so a theme switch recreates the lexer
                self.theme_combo.addItem(theme_file.stem, theme_file.name)
        current_theme = self.settings.get("theme", "theme.json")
        index = self.theme_combo.findData(current_theme)
        if index >= 0:
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
            "word_warp": self.word_warp_cb.isChecked(),
            "interpreter": self.interpreter_input.text().strip(),
            "ruff_save_mode": self.ruff_combo.currentData(),
            "theme": self.theme_combo.currentData(),
            "line_numbers": self.line_numbers_cb.isChecked(),
            "highlight_line": self.highlight_line_cb.isChecked(),
        }
        
