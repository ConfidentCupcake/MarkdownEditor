"""Bridge one PythonEditor document to the shared Ruff languafe server."""

from __future__ import annotations

from itertools import count
from functools import partial
from pathlib import Path
from uuid import uuid4
from typing import TYPE_CHECKING

from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from PyQt5.QtWidgets import QToolTip, QMessageBox
from PyQt5 import sip


from ruff_implementation.workspace_edit import WorkspaceEditApplier, WorkspaceEditError
from ruff_implementation.diagnostics_manager import DiagnosticsManager
from ruff_implementation.document_snapshot import DocumentSnapshot
from ruff_implementation.ruff_diagnostics_model import RuffDiagnostic, RuffPosition, RuffSeverity
from ruff_implementation.ruff_diagnostics_view import RuffDiagnosticView
from ruff_implementation.ruff_lsp_client import RuffLspClient

if TYPE_CHECKING:
    from python_editor.pythoneditor import PythonEditor

# A reopened URI must not reuse a version from its previous editor instance.
_VERSIONS = count(1)

class RuffLspController(QObject):
    """Keep one QScintilla Python document synchronized with Ruff LSP."""
    diagnostics_changed = pyqtSignal(object, list)
    document_changed = pyqtSignal(object)
    document_closed = pyqtSignal(str)
    
    def __init__(self, editor: PythonEditor, client: RuffLspClient, parent=None):
        # Parent this controller to the editor unless a different explicit parent was supplied.
        # Destroying the tab then also destroys its controller.
        super().__init__(parent or editor)

        self.editor = editor
        self.client = client
        self.diagnostics_manager: DiagnosticsManager | None = None
        self._uri = self._uri_for_path(editor.full_path)
        
        # Reuse the existing visual layer. It remains responsible only for QScintilla
        # indicators, marker circles, tooltips and range caching.
        self.view = RuffDiagnosticView(editor)
        
        # LSP versions begin at an integer and must increase for every didChange.
        self.document_version = next(_VERSIONS)
        
        # didOpen must be sent exactly once per URI/server lifecycle.
        self._opened = False
        self._closed = False

        self._type_diagnostics_attached = False
        self._spelling_attached = False
        self._warned_versionless = False

        self._quick_fix_menu = None
        self._quick_fix_generation = 0
        
        # Full-buffer updates are delayed slightly while a user is typing.
        # This prevents sending one didChange messafe for every individual keypress.
        self.change_timer = QTimer(self)
        self.change_timer.setSingleShot(True)
        self.change_timer.setInterval(180)
        self.change_timer.timeout.connect(self._send_change)
        
        # QScintilla emits textChanged for user input, pasted text, fixes, etc.
        editor.textChanged.connect(self._on_text_changed)
        
        # If Ruff is still starting when this tab is constructed, open_document
        # wil run as soon as initialize/initialized completes.
        client.server_ready.connect(self.open_document)
        client.server_stopped.connect(self._on_server_stopped)

        client.diagnostics_published.connect(self._on_diagnostics_published)
        if client.is_ready:
            self.open_document()
    
    @property
    def uri(self) -> str:
        """Return the stable LSP URI identifying this document to Ruff."""
        return self._uri

    def snapshot(self):
        path = Path(self.editor.full_path).resolve() if self.editor.full_path else None
        return DocumentSnapshot(self.uri, path, self.editor.text(), self.document_version)


    def bind_manager(self, manager: DiagnosticsManager) -> None:
        self.diagnostics_manager = manager


    def _uri_for_path(self, path) -> str:
        if path is None:
            path = self.client.workspace_root / f"untitled-{uuid4().hex}.py"
        return Path(path).resolve().as_uri()

    def _on_server_stopped(self):
        self._close_quick_fix_menu()
        self._opened = False
        manager = self.diagnostics_manager
        if manager is not None:
            manager.replace("ruff", self.uri, [])

    def relocate(self, path):
        """Migrate this open buffer to a new URI after Save As or rename."""
        self.change_timer.stop()
        self._clear_problem_rows()
        old_uri = self._uri
        self.document_closed.emit(old_uri)
        if self._opened and self.client.is_ready:
            self.client.close_document(old_uri)
        self._uri = self._uri_for_path(path)
        self._opened = False
        self.document_version = next(_VERSIONS)
        self.open_document()
        self.document_changed.emit(self.snapshot())

    def set_client(self, client):
        """Reconnect this document when the selected Ruff interpreter changes."""
        self._close_quick_fix_menu()
        self.client.server_ready.disconnect(self.open_document)
        self.client.server_stopped.disconnect(self._on_server_stopped)
        self.client.diagnostics_published.disconnect(self._on_diagnostics_published)
        self.client = client
        self._opened = False
        self._warned_versionless = False
        client.server_ready.connect(self.open_document)
        client.server_stopped.connect(self._on_server_stopped)
        client.diagnostics_published.connect(self._on_diagnostics_published)
        if client.is_ready:
            self.open_document()
        
    def open_document(self):
        """Send didOpen once after the shared server is ready."""
        if self._closed or self._opened or not self.client.is_ready:
            return
        
        self.client.open_document(
            self.uri,
            self.editor.text(),
            self.document_version,
        )
        self._opened = True
        
    def sync_from_editor(self):
        """Syncronize editor text after programmatic loading with blocked signals."""
        if self._closed:
            return
        self._clear_problem_rows()
        # setTextSafely() blocks editor.textChanged while loading a disk file.
        # Therefore this method manually creates a new LSP document version for the newly loaded source text.
        self.document_version = next(_VERSIONS)
        self.document_changed.emit(self.snapshot())
        
        if not self._opened:
            self.open_document()
        else:
            self._send_change()
    
    def _on_text_changed(self):
        """Advance document version and schedule one debounced didChange."""
        if self._closed:
            return
        
        # Each text state must have a stricly newer version that the last.
        self.document_version = next(_VERSIONS)
        self._clear_problem_rows()
        self.document_changed.emit(self.snapshot())
        # Normally MainWindow starts Ruff before editors are made. This fallback
        # makes the controller safe if a tab is created during server startup.
        if not self._opened:
            self.open_document()
        
        if self._opened:
            # Calling start() again on s single-shot timer resets its countdown.
            # One change is sent only after typing has been idle for 180 ms.
            self.change_timer.start()

    def _send_change(self):
        """Transmit the current complete QScintilla text buffer to Ruff."""
        if self._closed or not self._opened or not self.client.is_ready:
            return

        self.client.change_document(
            self.uri,
            self.editor.text(),
            self.document_version,
        )

    def _lsp_column_to_character(self, line: int, column: int) -> int:
        """Convert Ruff's negotiated character units into Python characters.

        UTF-8 measures bytes and UTF-16 measures code units. They differ for
        non-ASCII source; truncating an incomplete encoded character is safe.
        """
        if not 0 <= line < self.editor.lines():
            return 0
        value = self.editor.text(line).rstrip("\r\n")
        column = max(0, int(column))
        encoding = self.client.position_encoding
        if encoding == "utf-8":
            return len(value.encode("utf-8")[:column].decode("utf-8", errors="ignore"))
        if encoding == "utf-16":
            return len(value.encode("utf-16-le")[:2 * column].decode("utf-16-le", errors="ignore"))
        return min(column, len(value))

    def _on_diagnostics_published(self, uri: str, raw_diagnostics, version):
        """Forward current Ruff results without clearing another provider's UI.

        The existing MainWindow handler converts these records into generic
        diagnostics. Drawing occurs only after the store merges all providers.
        """
        if self._closed or uri != self.uri:
            return
        if version is None:
            if not self._warned_versionless:
                self.client.server_error.emit(
                    "Ruff omitted document versions; stale-safe diagnostics require an updated Ruff server."
                )
                self._warned_versionless = True
            return
        if version != self.document_version:
            return
        diagnostics = [self._to_diagnostic(raw) for raw in raw_diagnostics]
        self.diagnostics_changed.emit(self.editor, diagnostics)

    def _to_diagnostic(self, raw: dict) -> RuffDiagnostic:
        """Translate one Ruff result into the existing editor record format.

        Clamp line numbers to the live buffer and preserve the original LSP
        payload. Severity mapping is explicit because LSP numbers are reversed.
        """
        span = raw.get("range") or {}
        start = span.get("start") or {}
        end = span.get("end") or start
        last_line = max(0, self.editor.lines() - 1)
        first = min(last_line, max(0, int(start.get("line", 0))))
        last = min(last_line, max(first, int(end.get("line", first))))
        levels = {1: RuffSeverity.ERROR, 2: RuffSeverity.WARNING,
                  3: RuffSeverity.INFO, 4: RuffSeverity.INFO}
        return RuffDiagnostic(
            code=str(raw.get("code", "Ruff")),
            message=str(raw.get("message", "Ruff diagnostic")),
            severity=levels.get(raw.get("severity"), RuffSeverity.WARNING),
            start=RuffPosition(first, self._lsp_column_to_character(first, start.get("character", 0))),
            end=RuffPosition(last, self._lsp_column_to_character(last, end.get("character", 0))),
            revision=self.document_version,
            raw=raw,
        )

    def hover(self, position) -> bool:
        """Show every finding on the pointed line, including provider names.

        The shared view now contains Ruff, type and spelling records. Returning
        True lets the existing editor suppress a competing Jedi hover popup.
        """
        byte_position = self.editor.SendScintilla(2022, position.x(), position.y())
        if byte_position < 0:
            QToolTip.hideText()
            return False
        prefix = self.editor.text().encode("utf-8")[:byte_position].decode("utf-8", errors="ignore")
        items = self.view.by_line.get(prefix.count("\n"), [])
        if not items:
            QToolTip.hideText()
            return False
        text = "\n\n".join(self.view.tooltip(item) for item in items)
        QToolTip.showText(self.editor.mapToGlobal(position), text, self.editor)
        return True

    def _close_quick_fix_menu(self):

        self._quick_fix_generation += 1
        menu = self._quick_fix_menu
        self._quick_fix_menu = None
        if menu is not None and not sip.isdeleted(menu):
            menu.close()
            menu.deleteLater()

    def _forget_quick_fix_menu(self, menu) -> None:
        """Release a dismissed popup without invalidating its selected action.

        Qt can hide a menu before emitting QAction.triggered. Do not advance the
        generation here: the selected action still needs its final document and
        server checks. Late request replies are rejected by the menu-identity check.
        """
        if self._quick_fix_menu is menu:
            self._quick_fix_menu = None
        if not sip.isdeleted(menu):
            menu.deleteLater()

    def context_menu(self, event, view=None) -> bool:
        """Show native editing commands plus Ruff quick fixes for the clicked line.

        Args:
            event: Temporary QContextMenuEvent; used synchronously, never captured.
            view: View receiving the click, or the controller's own editor.

        Returns:
            True when this method displays the menu. False lets PythonEditor's
            existing documentation/default-menu behavior handle the click.

        Only current Ruff records can request Ruff actions. Type and spelling
        diagnostics may share the same line but are never sent to Ruff as input.
        """
        target = self.editor if view is None else view
        if self._closed or not self.client.is_ready or not self._opened:
            return False
        byte_position = target.SendScintilla(2022, event.pos().x(), event.pos().y())
        if byte_position < 0:
            return False
        line = target.SendScintilla(target.SCI_LINEFROMPOSITION, byte_position)
        diagnostics = [item for item in self.view.by_line.get(line, [])
                       if item.provider == "ruff"
                       and item.revision == self.document_version
                       and isinstance(item.raw.get("range"), dict)]
        if not diagnostics:
            return False

        self._close_quick_fix_menu()
        generation = self._quick_fix_generation
        client = self.client
        # Copy the position before returning; the Qt event will not stay alive.
        global_position = event.globalPos()
        menu = target.createStandardContextMenu()
        self._quick_fix_menu = menu
        menu.aboutToHide.connect(partial(self._forget_quick_fix_menu, menu))
        menu.addSeparator()
        fixes = menu.addMenu("Ruff quick fixes")
        groups = []
        for diagnostic in diagnostics:
            group = fixes.addMenu(f"{diagnostic.code}: {diagnostic.message[:80]}")
            group.addAction("Loading fixes…").setEnabled(False)
            groups.append((diagnostic, group))
        menu.popup(global_position)
        for diagnostic, group in groups:
            self.request_quick_fixes(
                diagnostic,
                partial(self._populate_quick_fixes, menu, group, generation, client),
            )
        return True


    def request_quick_fixes(self, diagnostic, callback) -> None:
        """Request Ruff code actions using the unchanged provider diagnostic.

        Args:
            diagnostic: Current RuffDiagnostic with provider='ruff' and raw LSP data.
            callback: Receives (actions, error, uri, revision). Stale asynchronous
                replies are discarded rather than attached to newer source text.

        Flush the pending buffer before requesting actions so Ruff's ranges refer
        to the same revision. The raw diagnostic contains Ruff-specific fix data;
        rebuilding it from only a displayed message would lose that information.
        """
        uri, revision, client = self.uri, self.document_version, self.client
        if (self._closed or not self._opened or not client.is_ready
                or diagnostic.provider != "ruff" or diagnostic.revision != revision
                or not isinstance(diagnostic.raw, dict)
                or not isinstance(diagnostic.raw.get("range"), dict)):
            callback([], {"message": "Ruff diagnostic is stale or has no action data"}, uri, revision)
            return
        self.change_timer.stop()
        self._send_change()

        def receive(result, error):
            """Forward only replies still owned by this document and server.

            Closing, editing, renaming or replacing the client invalidates this
            request. The popup separately checks its generation and visibility.
            """
            if (self._closed or client is not self.client or not client.is_ready
                    or not self._opened or self.uri != uri
                    or self.document_version != revision):
                return
            callback(result if isinstance(result, list) else [], error, uri, revision)

        client.request("textDocument/codeAction", {
            "textDocument": {"uri": uri},
            "range": diagnostic.raw["range"],
            "context": {"diagnostics": [diagnostic.raw], "only": ["quickfix"], "triggerKind": 1},
        }, receive)

    def _populate_quick_fixes(self, menu, group, generation, client,
                              actions, error, uri, revision) -> None:
        """Replace one loading submenu with validated, explicit user choices.

        This callback runs later on the GUI thread. Check menu identity before
        touching any Qt submenu that could already have been deleted. Unsupported
        command/resolve forms are displayed disabled instead of partly executed.
        """
        if (self._closed or self._quick_fix_menu is not menu
                or sip.isdeleted(menu) or sip.isdeleted(group)
                or generation != self._quick_fix_generation
                or client is not self.client or not client.is_ready
                or self.uri != uri or self.document_version != revision):
            return
        group.clear()
        if error:
            message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            group.addAction("Ruff request failed: " + message).setEnabled(False)
            return
        if not actions:
            group.addAction("No Ruff quick fix available").setEnabled(False)
            return
        for action in actions:
            if not isinstance(action, dict):
                continue
            title = str(action.get("title", "Ruff action"))
            reason = None
            disabled = action.get("disabled")
            if disabled is not None:
                reason = disabled.get("reason", "Disabled by Ruff") if isinstance(disabled, dict) else str(disabled)
            elif action.get("command") is not None or not isinstance(action.get("edit"), dict):
                reason = "Requires a command or resolve step not supported by this editor"
            entry = group.addAction(title if reason is None else f"{title} — {reason}")
            if reason is not None:
                entry.setEnabled(False)
            else:
                # partial binds this loop's action; a late-binding lambda would
                # make every menu item apply the final action in the list.
                entry.triggered.connect(partial(
                    self._activate_quick_fix, action, uri, revision, client, generation
                ))
    def _activate_quick_fix(self, action, uri, revision, client, generation, checked=False) -> None:
        """Apply the selected action only if its document/server snapshot is current.

        The checked argument is supplied by QAction.triggered and is unused.
        Recheck after menu dismissal because hide may occur before triggered.
        Validation failures are shown to the user; they never escape a Qt slot.
        """
        if (self._closed or generation != self._quick_fix_generation
                or client is not self.client or not client.is_ready
                or self.uri != uri or self.document_version != revision):
            return
        try:
            self.apply_code_action(action, uri, revision)
        except WorkspaceEditError as error:
            QMessageBox.warning(self.editor, "Ruff quick fix", str(error))

    def apply_code_action(self, action, uri, revision):
        """Apply a direct edit completely or report an unsupported action form."""
        if (self._closed or not self.client.is_ready
                or self.uri != uri or self.document_version != revision):
            raise WorkspaceEditError("Document or Ruff connection changed after the request")
        if (not isinstance(action, dict) or action.get("disabled") is not None
                or action.get("command") is not None
                or not isinstance(action.get("edit"), dict)):
            raise WorkspaceEditError("This action is disabled or requires a command/resolve step")
        WorkspaceEditApplier(self).apply(action["edit"], uri, revision)


    def _clear_problem_rows(self) -> None:
        """Remove every provider's results when this source version is invalid.

        The local variable has type DiagnosticsManager | None. The guard narrows
        it to DiagnosticsManager, so clear_document is a known, valid method.
        """
        self._close_quick_fix_menu()
        manager = self.diagnostics_manager
        if manager is not None:
            manager.clear_document(self.uri)

    def shutdown(self) -> None:
        """Close this document in all providers before its editor is destroyed.

        Services receive the URI while the controller is still alive. Pending
        asynchronous results are rejected after their document records disappear.
        """
        if self._closed:
            return
        self._closed = True
        self.change_timer.stop()
        self._clear_problem_rows()
        self.document_closed.emit(self.uri)
        if self._opened:
            self.client.close_document(self.uri)
        self.client.server_ready.disconnect(self.open_document)
        self.client.server_stopped.disconnect(self._on_server_stopped)
        self.client.diagnostics_published.disconnect(self._on_diagnostics_published)
        self.view.clear()

        
        
