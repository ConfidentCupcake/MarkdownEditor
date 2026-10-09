"""Bridge one PythonEditor document to the shared Ruff languafe server."""

from __future__ import annotations

from itertools import count
from pathlib import Path
from uuid import uuid4
from typing import TYPE_CHECKING

from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from PyQt5.QtWidgets import QToolTip


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

    def context_menu(self, event, view=None) -> bool:
        """Display current Ruff quick fixes without retaining the temporary Qt event."""
        return False
        
        
    def request_quick_fixes(self, diagnostic, callback):
        """Request action with raw provider data and capture the ecaxt document identity."""
        uri, revision, client = self.uri, self.document_version, self.client
        if self._closed or diagnostic.revision != revision or not client.is_ready:
            callback([], {"message": "Diagnostic is stale"}, uri, revision)
            return
        
        self.change_timer.stop()
        self._send_change()
        
        def receive(result, error):
            """Discard repolies after closure, relocation, editing or server replacement."""
            if self._closed or client is not self.client or self.uri != uri or self.document_version != revision:
                return
            callback(result if isinstance(result, list) else [], error, uri, revision)
        client.request("textDocument/codeAction", {
            "textDocument": {"uri": uri}, "range": diagnostic.raw["range"],
            "context": {"diagnostics": [diagnostic.raw], "only": ["quickfix"], "triggerKind": 1}}, receive)
            
            
    def apply_code_action(self, action, uri, revision):
        """Apply a direct edit completely or report an unsupported action form."""
        if action.get("disabled") or action.get("command") or not isinstance(action.get("edit"), dict):
            raise WorkspaceEditError("This action needs an unsupported command or resolve step")
        WorkspaceEditApplier(self).apply(action["edit"], uri, revision)


    def _clear_problem_rows(self) -> None:
        """Remove every provider's results when this source version is invalid.

        The local variable has type DiagnosticsManager | None. The guard narrows
        it to DiagnosticsManager, so clear_document is a known, valid method.
        """
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

        
        
