import ast
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QTreeWidget, QTreeWidgetItem


class CodeOutlineTree(QTreeWidget):
    """
    A tree widget that shows the structure of a Python File.
    Displays classes and functions in a collapsible tree.
    Clicking an item jumps to that line in the editor.    
    """    
    
    # Signals emitted when the user clicks a symbol
    # Paramters: line number (0-based), column number (0-based)
    symbol_clicked = pyqtSignal(int, int)
    
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setHeaderHidden(True)
        self.setStyleSheet("""
            QTreeWidget {
                background-color: #21252b;
                color: #dcdfe4;
                border: none;
                padding: 4px;
            }
            QTreeWidget::item {
                padding: 2px 0px;
            }
            QTreeWidget::item:selected {
                background-color: #2c313a;
            }
        """)
        self.itemClicked.connect(self._on_item_clicked)
        
    def update_outline(self, code: str):
        """Parse Python code and rebuild the tree."""
        self.clear()
        
        try:
            tree = ast.parse(code)
        except SyntaxError:
            # Code has syntax errors -- can't parse item
            return
        
        # Walk the top-level nodes of the module body
        # We only look at top-level classes and functions.
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                class_item = QTreeWidgetItem(self)
                class_item.setText(0, f"class {node.name}")
                class_item.setData(0, Qt.UserRole, (node.lineno - 1, node.col_offset))
                class_item.setForeground(0, self._color("#e5c07b"))
                
                # Add methods as children
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_item = QTreeWidgetItem(class_item)
                        method_item.setText(0, f"def {child.name}()")
                        method_item.setData(0, Qt.UserRole, (child.lineno - 1, child.col_offset))
                        method_item.setForeground(0, self._color("#61afef"))
            
            elif isinstance(ast.FunctionDef, ast.AsyncFunctionDef):
                function_item = QTreeWidgetItem(self)
                function_item.setText(0, f"def {node.name}()")
                function_item.setData(0, Qt.UserRole, (node.lineno - 1, node.col_offset))
                function_item.setForeground(0, self._color("#61afef"))
                
        # Expand all class nodes by default
        self.expandAll()
        
    def _on_item_clicked(self, item: QTreeWidgetItem, column: int):
        """When the user clicks a symbol, emit the line/column"""
        data = item.data(0, Qt.UserRole)
        if data is not None:
            line, col = data
            self.symbol_clicked.emit(line, column)
            
    def _color(self, hex_color: str):
        from PyQt5.QtGui import QColor
        return QColor(hex_color)
        
            
                