from pathlib import Path
import sys

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLineEdit, QSpinBox, QCheckBox, QPushButton,
    QFontComboBox, QComboBox, QFileDialog, QFormLayout, QColorDialog,
)

# ---------------------------------------------------------------------- #
#  Dark stylesheet - mirrors the editor's CustomDark palette so the
#  settings window is part of the app, not a Windows-default guest.
#  Colors: paper #1e1f22, panels #262a33, borders #3c4048,
#          text #bcbec4, dim #8b8f99, accent #ff5700 (CustomDark keyword).
# ---------------------------------------------------------------------- #
SETTINGS_QSS = """
QDialog { background-color: #1e1f22; }

/* --- tab strip: flat tabs, accent underline on the active one --------- */
QTabWidget::pane {
    border: 1px solid #3c4048;
    border-radius: 6px;
    background: #22252d;
}
QTabBar { background: transparent; }
QTabBar::tab {
    background: transparent;
    color: #8b8f99;
    padding: 8px 20px;
    margin-right: 2px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}
QTabBar::tab:selected {
    color: #ffffff;
    background: #262a33;
    border-bottom: 2px solid #ff5700;
}
QTabBar::tab:hover { color: #bcbec4; }

/* --- labels & form rows ----------------------------------------------- */
QLabel { color: #bcbec4; background: transparent; }

/* --- inputs -------------------------------------------------------------- */
QLineEdit, QSpinBox, QFontComboBox, QComboBox {
    background-color: #262a33;
    color: #e8e9ec;
    border: 1px solid #3c4048;
    border-radius: 4px;
    padding: 5px 8px;
    min-height: 18px;
    selection-background-color: #3d424d;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QFontComboBox:focus {
    border: 1px solid #ff5700;
}

/* combo dropdowns: dark list, accent selection */
QComboBox QAbstractItemView {
    background-color: #22252d;
    color: #bcbec4;
    border: 1px solid #3c4048;
    selection-background-color: #3d424d;
    selection-color: #ffffff;
}

/* spinbox steppers: image arrows drawn at runtime (_build_pixmaps) so
   they render identically on every platform style (Fusion, windowsvista) */
QSpinBox::up-button, QSpinBox::down-button {
    background: #262a33;
    border: none;
    width: 18px;
    subcontrol-origin: border;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #3d424d; }
QSpinBox::up-arrow   { image: url({UP_ARROW});   width: 7px; height: 5px; }
QSpinBox::down-arrow { image: url({DOWN_ARROW}); width: 7px; height: 5px; }

/* --- checkboxes: pixmap indicators drawn at runtime --------------------- *
 * A stylesheet on ::indicator hides the native check glyph on most
 * platform styles, so the checkmark is baked into the image itself. */
QCheckBox { color: #bcbec4; spacing: 8px; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    image: url({CHECK_OFF});
}
QCheckBox::indicator:checked { image: url({CHECK_ON}); }
QCheckBox::indicator:hover { }

/* --- buttons: accent primary, flat secondary -------------------------- */
QPushButton {
    background: #262a33;
    color: #bcbec4;
    border: 1px solid #3c4048;
    border-radius: 4px;
    padding: 6px 16px;
}
QPushButton:hover { background: #3d424d; color: #ffffff; }
QPushButton:pressed { background: #ff5700; color: #ffffff; }
QPushButton#saveButton {
    background: #ff5700;
    color: #ffffff;
    border: 1px solid #ff5700;
    font-weight: bold;
}
QPushButton#saveButton:hover { background: #ff6f1f; }
QPushButton#saveButton:pressed { background: #e04c00; }
"""


def _build_pixmaps() -> dict:
    """
    Draw the small QSS images at runtime (needs a QApplication to exist,
    which is why this is a function and not module-level code).

    Why images instead of pure QSS: styling ::indicator with a stylesheet
    suppresses the native check glyph on most platform styles (empty
    orange box), and ::up-arrow / ::down-arrow render as flat dashes on
    some styles. Baking the glyphs into 16x16 / 7x5 pixmaps makes the
    dialog render identically on Fusion AND the Windows native style.

    :return: {"CHECK_ON": path, "CHECK_OFF": path,
              "UP_ARROW": path, "DOWN_ARROW": path} - absolute file paths
             injected into SETTINGS_QSS as url() targets.
    """
    import tempfile
    from PyQt5.QtCore import Qt, QRectF
    from PyQt5.QtGui import QPixmap, QPainter, QColor, QPen, QBrush, QPainterPath

    out = {}

    def _save(pix, key):
        path = str(Path(tempfile.gettempdir()) / f"cozy_settings_{key}.png")
        pix.save(path)
        # QSS url() wants forward slashes even on Windows
        out[key] = path.replace("\\", "/")

    # --- checkbox indicators ------------------------------------------- #
    for checked, key in ((True, "CHECK_ON"), (False, "CHECK_OFF")):
        pix = QPixmap(16, 16)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#565b66"), 1))
        p.setBrush(QBrush(QColor("#ff5700") if checked else QColor("#2b2f38")))
        p.drawRoundedRect(QRectF(0.5, 0.5, 15, 15), 3, 3)
        if checked:
            # white checkmark path
            p.setPen(QPen(QColor("#ffffff"), 2.2))
            p.setBrush(Qt.NoBrush)
            path = QPainterPath()
            path.moveTo(3.6, 8.2)
            path.lineTo(6.6, 11.2)
            path.lineTo(12.4, 4.6)
            p.drawPath(path)
        p.end()
        _save(pix, key)

    # --- spinbox arrows ------------------------------------------------- #
    for up, key in ((True, "UP_ARROW"), (False, "DOWN_ARROW")):
        pix = QPixmap(7, 5)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor("#bcbec4")))
        path = QPainterPath()
        if up:
            path.moveTo(0.5, 4.5); path.lineTo(6.5, 4.5); path.lineTo(3.5, 0.5)
        else:
            path.moveTo(0.5, 0.5); path.lineTo(6.5, 0.5); path.lineTo(3.5, 4.5)
        path.closeSubpath()
        p.drawPath(path)
        p.end()
        _save(pix, key)

    return out


class SettingsDialog(QDialog):
    """
    Tabbed settings dialog. Storage model (single source of truth per value):

        - QSettings      -> interpreter, ruff_save_mode, tab_width, word_wrap,
                            restore_tabs, theme (active theme NAME),
                            line_numbers, highlight_line, paper_color
        - theme.json     -> font_family + font_size (written by main.py's
                            _write_theme_editor into the ACTIVE theme's
                            editor.font block)

    The dialog itself never touches files — get_settings() returns the
    values, and main.py decides where each one is persisted.
    """
    
    def __init__(self, current_settings: dict, parent=None):
        super().__init__(parent)
        self.settings = current_settings.copy()
        self.setWindowTitle("Settings")
        self.setMinimumSize(520, 440)

        # Dark CustomDark styling + the editor's theme font, so the dialog
        # belongs to the app visually (was: default white Windows style).
        # The indicator/arrow pixmaps are drawn at runtime because a
        # QApplication exists only now (see _build_pixmaps).
        qss = SETTINGS_QSS
        for key, path in _build_pixmaps().items():
            qss = qss.replace("{" + key + "}", path)
        self.setStyleSheet(qss)
        self.setFont(QFont(self.settings.get("font_family", "JetBrains Mono"),
                           self.settings.get("font_size", 13)))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._create_editor_tab(), "Editor")
        self.tabs.addTab(self._create_python_tab(), "Python")
        self.tabs.addTab(self._create_appearance_tab(), "Appearance")
        layout.addWidget(self.tabs, 1)   # stretch: tabs eat the space, not the gap

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("Save")
        save_btn.setObjectName("saveButton")       # styled as accent primary
        save_btn.clicked.connect(self.accept)
        save_btn.setDefault(True)                   # Enter saves
        btn_row.addWidget(save_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)
        
    def _style_form(self, layout: QFormLayout) -> QFormLayout:
        """Uniform paddings/gaps for every tab's form (keeps labels from
        colliding with the padded dark inputs)."""
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setHorizontalSpacing(26)     # label <-> input gap
        layout.setVerticalSpacing(14)
        layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.setFormAlignment(Qt.AlignTop | Qt.AlignLeft)
        return layout

    # --- Editor tab --------------------------------------------------------- #
    def _create_editor_tab(self) -> QWidget:
        tab = QWidget()
        layout = self._style_form(QFormLayout(tab))
        
        self.font_combo = QFontComboBox()
        # Pre-selected with the theme's editor font (JetBrains Mono is
        # the default family in every theme the factory generates).
        self.font_combo.setCurrentFont(
            QFont(self.settings.get("font_family", "JetBrains Mono"))
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
        layout = self._style_form(QFormLayout(tab))
        
        interp_row = QHBoxLayout()
        self.interpreter_input = QLineEdit()
        # Pre-filled with the interpreter currently stored in QSettings.
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
        layout = self._style_form(QFormLayout(tab))
        
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
        
        # Paper color picker: a QSettings override on top of the active
        # theme's paper (theme switches do NOT reset it; the swatch shows
        # the effective color and starts from the current value).
        paper_row = QHBoxLayout()
        self.paper_btn = QPushButton()
        self._paper_override = self.settings.get("paper_color") or None
        self._paper_color = QColor(
            self._paper_override
            or self.settings.get("theme_paper", "#1e1f22")
        )
        self._refresh_paper_swatch()
        self.paper_btn.clicked.connect(self._pick_paper_color)
        paper_row.addWidget(self.paper_btn)

        self.paper_reset_btn = QPushButton("Reset to theme")
        self.paper_reset_btn.setToolTip("Drop the override - the active\n"
                                        "theme's own paper-color is used.")
        self.paper_reset_btn.clicked.connect(self._reset_paper_color)
        paper_row.addWidget(self.paper_reset_btn)
        paper_row.addStretch()
        layout.addRow("Paper color:", paper_row)

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
            
            
    # --- Appearance helpers -------------------------------------------- #
    def _refresh_paper_swatch(self):
        """Show the effective paper color on the picker button."""
        self.paper_btn.setStyleSheet(
            f"background-color: {self._paper_color.name()};"
            f"border: 1px solid #555; min-width: 60px; min-height: 22px;")

    def _pick_paper_color(self):
        """Open the color dialog; a valid pick updates the swatch only
        (get_settings() reports the value when the user hits Save)."""
        color = QColorDialog.getColor(self._paper_color, self,
                                      "Editor background color")
        if color.isValid():
            self._paper_color = color
            self._paper_override = color.name()
            self._refresh_paper_swatch()

    def _reset_paper_color(self):
        """Drop the override: back to the theme's own paper default."""
        self._paper_override = None
        self._paper_color = QColor(self.settings.get("theme_paper", "#1e1f22"))
        self._refresh_paper_swatch()

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
            "paper_color": self._paper_override,
            "line_numbers": self.line_numbers_cb.isChecked(),
            "highlight_line": self.highlight_line_cb.isChecked(),
        }
        
