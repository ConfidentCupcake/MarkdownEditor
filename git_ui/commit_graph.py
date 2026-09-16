"""Editor-themed history with painted ancestry and safe reference labels."""

import json
from datetime import datetime
from html import escape

from PyQt5.QtCore import QPointF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPalette, QPen, QTextDocument
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QHeaderView,
    QMenu,
    QPlainTextEdit,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from git_implementation.history import GitSnapshot, layout_graph

ROW_ROLE = Qt.UserRole + 1
REF_ROLE = Qt.UserRole + 2
LANE_WIDTH = 22


class HistoryDelegate(QStyledItemDelegate):
    """Paint graph segments and escaped, colored reference labels."""

    def __init__(self, panel):
        """Retain the panel's live palette without duplicating theme state."""
        super().__init__(panel)
        self.panel = panel

    def sizeHint(self, option, index):
        """Give nodes and labels space at any font size."""
        size = super().sizeHint(option, index)
        size.setHeight(max(32, option.fontMetrics.height() + 12))
        return size

    def paint(self, painter, option, index):
        """Draw standard selection first, then graph or reference content."""
        if index.column() not in (0, 2):
            return super().paint(painter, option, index)
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        styled.text = ""
        self.panel.style().drawControl(QStyle.CE_ItemViewItem, styled, painter, self.panel)
        painter.save()
        painter.setClipRect(option.rect)
        if index.column() == 0:
            row = index.data(ROW_ROLE)
            if row is not None:
                self._paint_graph(painter, option.rect, row)
        else:
            self._paint_refs(painter, option, index)
        painter.restore()

    def _paint_graph(self, painter, rect, row):
        """Connect row boundaries using model-supplied parent edges."""
        painter.setRenderHint(QPainter.Antialiasing)
        middle = rect.top() + rect.height() / 2
        left = rect.left() + 16
        for lane, color in row.incoming:
            painter.setPen(QPen(self.panel.lane_color(color), 2))
            x = left + lane * LANE_WIDTH
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, middle))
        for start, end, color in row.outgoing:
            painter.setPen(QPen(self.panel.lane_color(color), 2))
            x1, x2 = left + start * LANE_WIDTH, left + end * LANE_WIDTH
            path = QPainterPath(QPointF(x1, middle))
            bottom = rect.top() + rect.height()
            path.cubicTo(x1, bottom - 4, x2, middle + 4, x2, bottom)
            painter.drawPath(path)
        center = QPointF(left + row.lane * LANE_WIDTH, middle)
        painter.setBrush(self.panel.lane_color(row.color_index))
        painter.setPen(QPen(QColor(self.panel.background), 2))
        painter.drawEllipse(center, 5, 5)
        if "HEAD" in row.commit.decorations:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(self.panel.lane_color(row.color_index), 1))
            painter.drawEllipse(center, 8, 8)

    def _paint_refs(self, painter, option, index):
        """Render escaped labels so Git text cannot become active HTML."""
        row = index.sibling(index.row(), 0).data(ROW_ROLE)
        if row is None:
            return
        badges = []
        for ref in index.data(REF_ROLE) or ():
            color = self.panel.lane_color(3 if ref.startswith("tag: ") else row.color_index).name()
            badges.append(f'<span style="color:{color}; font-weight:600">[{escape(ref)}]</span>')
        doc = QTextDocument()
        doc.setDefaultFont(option.font)
        doc.setDocumentMargin(0)
        doc.setHtml(" &nbsp; ".join(badges))
        painter.translate(
            option.rect.left() + 7,
            option.rect.top() + (option.rect.height() - doc.size().height()) / 2,
        )
        doc.drawContents(painter)


class CommitGraphPanel(QTreeWidget):
    """Display readable history without changing repository state."""

    commitActivated = pyqtSignal(str)

    def __init__(self, parent=None):
        """Configure columns, selection, delegates, and read-only actions."""
        super().__init__(parent)
        self.setObjectName("GitHistoryTree")
        self._snapshot = None
        self._rows = {}
        self.setColumnCount(6)
        self.setHeaderLabels(
            ("Graph", "Commit message", "Branches / tags", "Commit", "Author", "Date")
        )
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTreeWidget.SelectRows)
        self.setSelectionMode(QTreeWidget.SingleSelection)
        self.setSortingEnabled(False)  # Sorting would falsify ancestry.
        self.setHorizontalScrollMode(QTreeWidget.ScrollPerPixel)
        self.setItemDelegate(HistoryDelegate(self))
        self.header().setStretchLastSection(False)
        self.header().setMinimumSectionSize(60)
        for column, width in enumerate((90, 330, 240, 100, 155, 155)):
            self.header().setSectionResizeMode(column, QHeaderView.Interactive)
            self.setColumnWidth(column, width)
        self.itemActivated.connect(self._activate)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self.apply_theme()

    def sizeHint(self):
        """Request useful initial dock space for commit summaries."""
        return QSize(1100, 340)

    def resizeEvent(self, event):
        """Keep summaries readable in narrow docks; scroll other columns."""
        super().resizeEvent(event)
        occupied = sum(self.columnWidth(i) for i in (0, 2, 3, 4, 5))
        self.setColumnWidth(1, max(330, self.viewport().width() - occupied))

    def lane_color(self, number):
        """Return a repeatable color for an ancestry line, not a row number."""
        return QColor(self._lane_colors[number % len(self._lane_colors)])

    def apply_theme(self, theme_path=None):
        """Read the editor's theme file with safe dark-theme fallbacks."""
        theme = {}
        if theme_path:
            try:
                with open(theme_path, encoding="utf-8") as stream:
                    theme = json.load(stream).get("theme", {})
            except (OSError, ValueError, AttributeError):
                theme = {}
        if not isinstance(theme, dict):
            theme = {}
        editor = theme.get("editor", {})
        editor = editor if isinstance(editor, dict) else {}
        paper = editor.get("paper-color", "#1e1f22")
        background = QColor(paper if isinstance(paper, str) else "#1e1f22")
        if not background.isValid():
            background = QColor("#1e1f22")
        dark = background.lightness() < 128
        self.background = background.name()
        foreground = "#bcbec4" if dark else "#24292f"
        surface = background.lighter(125).name() if dark else background.darker(105).name()
        selection = "#35465d" if dark else "#d6e8ff"
        border = "#393b40" if dark else "#c8cdd3"
        self._lane_colors = (
            ("#82aaff", "#c792ea", "#66d9a8", "#ecc48d", "#f78c9c", "#89ddff")
            if dark
            else ("#185abc", "#753ab7", "#197347", "#8c5900", "#b32243", "#006a80")
        )
        font_settings = editor.get("font", {})
        font_settings = font_settings if isinstance(font_settings, dict) else {}
        try:
            size = max(9, min(20, int(font_settings.get("font-size", 11))))
        except (ValueError, TypeError):
            size = 11
        self.setFont(QFont(str(font_settings.get("family", "monospace")), size))
        # Measure fixed metadata columns at the selected font size, not at the
        # system default. Otherwise even an eight-character SHA gets ellipsized.
        metrics = self.fontMetrics()
        self.setColumnWidth(2, max(280, metrics.horizontalAdvance("feature/git-history") + 40))
        self.setColumnWidth(3, metrics.horizontalAdvance("01234567") + 30)
        self.setColumnWidth(4, metrics.horizontalAdvance("David Schubba") + 30)
        self.setColumnWidth(5, metrics.horizontalAdvance("2026-09-16 12:48") + 30)
        palette = self.palette()
        palette.setColor(QPalette.Base, background)
        palette.setColor(QPalette.Text, QColor(foreground))
        self.setPalette(palette)
        self.setStyleSheet(f"""
            QTreeWidget#GitHistoryTree {{ background:{self.background}; color:{foreground};
                alternate-background-color:{surface}; border:0; outline:0; }}
            QTreeWidget#GitHistoryTree::item {{ padding:0 7px; border:0; }}
            QTreeWidget#GitHistoryTree::item:selected {{ background:{selection}; color:{foreground}; }}
            QHeaderView::section {{ background:{surface}; color:{foreground};
                padding:9px 8px; border:0; border-bottom:1px solid {border}; }}
            QScrollBar {{ background:{self.background}; width:12px; height:12px; }}
            QScrollBar::handle {{ background:{border}; border-radius:4px; min-height:24px; min-width:24px; }}
            QMenu {{ background:{surface}; color:{foreground}; }}
        """)
        self.viewport().update()

    def set_snapshot(self, snapshot: GitSnapshot):
        """Replace history atomically and retain selection during refreshes."""
        if snapshot == self._snapshot:
            return
        rows = layout_graph(snapshot.commits)  # Validate before clearing old data.
        current = self.currentItem()
        same_root = self._snapshot is not None and self._snapshot.root == snapshot.root
        selected = current.data(0, Qt.UserRole) if current and same_root else None
        scroll = self.verticalScrollBar().value() if same_root else 0
        self.setUpdatesEnabled(False)
        try:
            self.clear()
            self._snapshot = snapshot
            self._rows = {row.commit.sha: row for row in rows}
            for row in rows:
                commit = row.commit
                try:
                    date = datetime.fromisoformat(commit.authored_at).strftime("%Y-%m-%d %H:%M")
                except ValueError:
                    date = commit.authored_at
                refs = tuple(filter(None, commit.decorations.split(", ")))
                item = QTreeWidgetItem(
                    ("", commit.subject, "  ".join(refs), commit.sha[:8], commit.author, date)
                )
                item.setData(0, Qt.UserRole, commit.sha)
                item.setData(0, ROW_ROLE, row)
                item.setData(2, REF_ROLE, refs)
                for column, value in enumerate(
                    (
                        commit.sha,
                        commit.subject,
                        commit.decorations,
                        commit.sha,
                        commit.author,
                        commit.authored_at,
                    )
                ):
                    item.setToolTip(column, "<qt>" + escape(value) + "</qt>")
                self.addTopLevelItem(item)
                if commit.sha == selected:
                    self.setCurrentItem(item)
            lanes = max((row.lane_count for row in rows), default=1)
            self.setColumnWidth(0, max(75, 32 + lanes * LANE_WIDTH))
            # Recompute scrollbar limits before restoring the previous position.
            self.doItemsLayout()
            self.verticalScrollBar().setValue(scroll)
        finally:
            self.setUpdatesEnabled(True)

    def clear(self):
        """Invalidate cached history when the repository disappears."""
        self._snapshot = None
        self._rows = {}
        super().clear()

    def _activate(self, item, _column):
        """Emit the SHA and display non-mutating details on activation."""
        sha = item.data(0, Qt.UserRole)
        if sha:
            self.commitActivated.emit(str(sha))
            self._show_details(str(sha))

    def _show_details(self, sha):
        """Show snapshot metadata without executing repository commands."""
        row = self._rows.get(sha)
        if row is None:
            return
        commit = row.commit
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.setWindowTitle("Commit details")
        dialog.resize(720, 330)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit(dialog)
        text.setReadOnly(True)
        text.setFont(self.font())
        text.setStyleSheet(
            f"background:{self.background};color:{self.palette().color(QPalette.Text).name()};"
        )
        text.setPlainText(
            f"Commit: {sha}\nAuthor: {commit.author}\nDate: {commit.authored_at}\n"
            f"Refs: {commit.decorations or '(none)'}\n"
            f"Parents: {', '.join(commit.parents) or '(root commit)'}\n\n{commit.subject}"
        )
        layout.addWidget(text)
        dialog.show()

    def _context_menu(self, position):
        """Offer copy and inspect actions, never checkout or discard."""
        item = self.itemAt(position)
        if item is None:
            return
        sha = item.data(0, Qt.UserRole)
        menu = QMenu(self)
        menu.addAction("Copy commit SHA", lambda: QApplication.clipboard().setText(sha))
        menu.addAction(
            "Copy commit message", lambda: QApplication.clipboard().setText(item.text(1))
        )
        menu.addAction("Commit details", lambda: self._show_details(sha))
        menu.exec_(self.viewport().mapToGlobal(position))
