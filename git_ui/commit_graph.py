"""Dock content for a bounded, topologically ordered Git history."""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFontDatabase
from PyQt5.QtWidgets import QTreeWidget, QTreeWidgetItem

from git_implementation.history import GitSnapshot, layout_graph


class CommitGraphPanel(QTreeWidget):
    """Render compact lanes and emit a commit id when a row is activated."""

    commitActivated = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setColumnCount(4)
        self.setHeaderLabels(("Graph", "Commit", "Author", "Date"))
        self.setUniformRowHeights(True)
        self.itemActivated.connect(self._activate)

    def set_snapshot(self, snapshot: GitSnapshot) -> None:
        """Replace all displayed rows from one internally consistent snapshot."""
        self.clear()
        fixed = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        for row in layout_graph(snapshot.commits):
            commit = row.commit
            subject = commit.subject
            if commit.decorations:
                subject = f"{subject}  ({commit.decorations})"
            item = QTreeWidgetItem((
                row.graph_text,
                f"{commit.sha[:8]}  {subject}",
                commit.author,
                commit.authored_at,
            ))
            item.setFont(0, fixed)
            item.setData(0, Qt.UserRole, commit.sha)
            self.addTopLevelItem(item)

    def _activate(self, item: QTreeWidgetItem, _column: int) -> None:
        sha = item.data(0, Qt.UserRole)
        if sha:
            self.commitActivated.emit(str(sha))