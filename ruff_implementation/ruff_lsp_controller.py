"""Bridge one PythonEditor document to the shared Ruff languafe server."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from PyQt5.QtCore import QObject, QTimer
from PyQt5.QtWidgets import QToolTip

from ruff_implementation.ruff_diagnostics_model import RuffDiagnostic, RuffPosition, RuffSeverity
from ruff_implementation.ruff_diagnostics_view import RuffDiagnosticView

class RuffLspController(QObject):
    """Keep one QScintilla Python document synchronized with Ruff LSP."""
    
    def __init__(self, editor, client, parent=None):
        # Parent this controller to the editor unless a different explicit parent was supplied.
        # Destroying the tab then also destroys its controller.
        super().__init__(parent or editor)

        self.editor = editor
        self.client = client
        self._uri = self._uri_for_path(editor.full_path)
        
        # Reuse the existing visual layer. It remains responsible only for QScintilla
        # indicators, marker circles, tooltips and range caching.
        self.view = RuffDiagnosticView(editor)
        
        # LSP versions begin at an integer and must increase for every didChange.
        self.document_version = 1
        
        # didOpen must be sent exactly once per URI/server lifecycle.
        self._opened = False
        self._closed = False
        
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

    @staticmethod
    def _uri_for_path(path) -> str:
        if path is None:
            path = Path.cwd() / f"untitled-{uuid4().hex}.py"
        return Path(path).resolve().as_uri()

    def _on_server_stopped(self):
        self._opened = False

    def relocate(self, path):
        """Migrate this open buffer to a new URI after Save As or rename."""
        old_uri = self._uri
        if self._opened and self.client.is_ready:
            self.client.close_document(old_uri)
        self._uri = self._uri_for_path(path)
        self._opened = False
        self.document_version += 1
        self.open_document()

    def set_client(self, client):
        """Reconnect this document when the selected Ruff interpreter changes."""
        try:
            self.client.server_ready.disconnect(self.open_document)
            self.client.server_stopped.disconnect(self._on_server_stopped)
            self.client.diagnostics_published.disconnect(self._on_diagnostics_published)
        except TypeError:
            pass
        self.client = client
        self._opened = False
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
            
        # setTextSafely() blocks editor.textChanged while loading a disk file.
        # Therefore this method manually creates a new LSP document version for the newly loaded source text.
        self.document_version += 1
        
        if not self._opened:
            # Ruff may still be starting. If it is ready, this sends didOpen with the current loaded text.
            # If it is not ready yet, the existing server_ready -> open_document signal connection will do so later.
            self.open_document()
            return
        
        if not self.client.is_ready:
            return
            
        # The Ruff server already knows this document, but perbiously recieved the empty editor text during 
        # PythonEditor construction. Replace it immediately with the actual source read from disk.
        self.client.change_document(self.uri, self.editor.text(), self.document_version)
    
    def _on_text_changed(self):
        """Advance document version and schedule one debounced didChange."""
        if self._closed:
            return
        
        # Each text state must have a stricly newer version that the last.
        self.document_version += 1
        
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

    def _on_diagnostics_published(self, uri: str, raw_diagnostics, version):
        """Convert and render only current diagnostics for this document."""
        if self._closed or uri != self.uri:
            # This notification belongs to another Python tab.
            return

        # Server versions are optional in LSP. If Ruff sends one, reject output
        # from an old document snapshot to avoid stale squiggles after typing.
        if version is not None and version != self.document_version:
            return

        diagnostics = [
            self._to_diagnostic(raw)
            for raw in raw_diagnostics
        ]

        # The view always clears the old visual state before painting the new list.
        # An empty server list removes indicators after the user fixes an error.
        self.view.render(diagnostics)

    def _to_diagnostic(self, raw: dict) -> RuffDiagnostic:
        """Adapt one standard LSP Diagnostic dictionary to the app model."""
        raw_range = raw.get("range") or {}
        start = raw_range.get("start") or {}
        end = raw_range.get("end") or start

        # LSP DiagnosticSeverity uses integer values:
        # 1=Error, 2=Warning, 3=Information, 4=Hint.
        # The app has three display categories, so Hint becomes INFO.
        severity_by_number = {
            1: RuffSeverity.ERROR,
            2: RuffSeverity.WARNING,
            3: RuffSeverity.INFO,
            4: RuffSeverity.INFO,
        }

        # LSP `code` is allowed to be string, integer, or CodeDescription-like
        # data in generic servers. Convert it to a displayable string safely.
        code = raw.get("code", "Ruff")
        if not isinstance(code, str):
            code = str(code)

        return RuffDiagnostic(
            code=code,
            message=raw.get("message", "Ruff diagnostic"),
            severity=severity_by_number.get(
                raw.get("severity"),
                RuffSeverity.WARNING,
            ),
            # LSP and QScintilla both use zero-based line positions for this
            # integration. Character encoding behavior is covered by a Unicode
            # regression test after ASCII diagnostics are verified.
            start=RuffPosition(
                start.get("line", 0),
                start.get("character", 0),
            ),
            end=RuffPosition(
                end.get("line", 0),
                end.get("character", 0),
            ),
            revision=self.document_version,
            raw=raw,
        )

    def hover(self, position) -> bool:
        """Show a short diagnostic tooltip for the current line, if any."""
        # SCI_POSITIONFROMPOINT (2022) returns a Scintilla byte position at the
        # mouse coordinate. -1 means the pointer is outside text content.
        byte_position = self.editor.SendScintilla(
            2022,
            position.x(),
            position.y(),
        )
        if byte_position < 0:
            QToolTip.hideText()
            return False

        # Convert byte position to a zero-based line number. UTF-8 decode with
        # errors=ignore handles a pointer position inside a multi-byte character.
        prefix = self.editor.text().encode("utf-8")[:byte_position].decode(
            "utf-8",
            errors="ignore",
        )
        diagnostic = self.view.at_line(prefix.count("\n"))

        if diagnostic:
            QToolTip.showText(
                self.editor.mapToGlobal(position),
                self.view.tooltip(diagnostic),
                self.editor,
            )
            return True

        QToolTip.hideText()
        return False

    def context_menu(self, event) -> bool:
        """Phase-one placeholder; LSP codeAction handling is added later."""
        # Return False so PythonEditor keeps showing the normal QScintilla menu.
        return False
    
    def _on_ruff_diagnostics(self, editor, diagnostics: list):
        """
        C4+C2: cat reacts to Ruff errors; XP when the last one clears.
        
        EDGE-TRIGGERED by design: reacts only to transitions (clean -> dirty, dirty -> clean),
        stashing the previous state on the editor. A level-triggered version would re-alert on 
        every keystroke while an error exists - the cat would vibrate while you edit a broke line.
        Same pattern as the lexer's in_string state: remember, then compare.
        
        :param editor: the editor the diagnostics belong to
        :param diagnostics: current diagnostic list (truhly = has errors)
        """
        
        had_errors = getattr(editor, "_had_ruff_errors", False)
        has_errors = bool(diagnostics)
        
        if has_errors and not had_errors:
            self.cat.set_state("alert", 2000)
        elif not has_errors and had_errors:
            self.cat.add_xp(5)
            self.cat.set_state("stretch", 1500)
            
        editor._had_ruff_errors = has_errors

    def shutdown(self):
        """Stop synchronization, tell Ruff the tab is closed, clear visuals."""
        if self._closed:
            return

        self._closed = True
        self.change_timer.stop()

        # didClose is sent only after didOpen; otherwise the server has never
        # heard about this document and closing it would be meaningless.
        if self._opened:
            self.client.close_document(self.uri)

        try:
            self.client.server_ready.disconnect(self.open_document)
            self.client.server_stopped.disconnect(self._on_server_stopped)
            self.client.diagnostics_published.disconnect(self._on_diagnostics_published)
        except TypeError:
            pass

        self.view.clear()

        
        
