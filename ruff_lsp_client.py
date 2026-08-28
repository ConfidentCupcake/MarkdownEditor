"""
Qt JSON-RPC transport for the official "ruff server" language server.

This class intentionally contains no QScintilla code. Its Job is only:

1. Start one persistent 'python -m ruff server' process.
2. Send Language Server Protocol (LSP) requests and notifications.
3. Decode LSP Content Length framed messages from the server.
4. Emit Qt signals for diagnostics and server failures.

The GUI/editor layer lives in ruff_lsp_controller.py
"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt5.QtCore import QObject, QProcess, pyqtSignal


class RuffLspClient(QObject):
    """Persistent JSON-RPC client for the official "ruff server" process."""

    # Emitted whenever Ruff sends textDocument/publishDiagnostics.
    # Arguments:
    #   1. uri: LSP document URI, e.g. file:///C:/project/example.py
    #   2. diagnostics: list[dict] in standard LSP Diagnostic format
    #   3. version: LSP document version or None if the server omitted it

    diagnostics_published = pyqtSignal(str, object, object)

    # A non-fatal infrastructure message. MainWindow can show it in the statusBar
    # while detailed text is still printed to the PowerShell log,

    server_error = pyqtSignal(str)

    # Emitted exactly after the initialize -> initialized handshake completes.
    # RuffLspController instances us this to send their didOpen notification.
    server_ready = pyqtSignal()

    def __init__(self, python_executable: str, workspace_root: Path, parent=None):
        """
        Store workspace details and connect QProcess signals.

        Parameters:
            python_executable:
                The user-selected Python interpreter. It must have Ruff installed,
                because the process is started as: <python_executable> -m ruff server.
            workspace_root:
                Folder containing pyproject.toml, ruff.toml, or .ruff.toml.
            parent:
                Normally MainWindow, Qt will then destroy this client
                when the application window is destroyed.
        """
        super().__init__(parent)

        self.python_executable = python_executable
        self.workspace_root = Path(workspace_root).resolve()

        # QProcess asynchronously runs Ruff without freezing the Qt GUI thread.
        # Unlike subprocess.run(), it remains alive for the entire application.
        self.process = QProcess(self)

        # LSP messages can arrive split over several readyRead events, or many messages can arrive together.
        # This buffer holds unread raw bytes until a complete Content-Length-framed JSON message can be assembled.
        self._read_buffer = bytearray()

        # Every JSON-RPC request must have a unique integer ID.
        # Notifications intentionally do NOT have IDs and do not recieve responses.
        self._next_request_id = 1

        # request_id -> callback (result, error)
        # Formatting and code-action requests will use this later.
        self._pending_requests = {}

        # These flags distinguish "process exists" from "LSP handshake finished".
        self._started = False
        self._initialized = False

        # QProcess signals fire on the Qt event loop. All UI-facing work remains
        # in the main Qt thread, which avoids unsafe widget access from threads.
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.errorOccurred.connect(self._on_process_error)
        self.process.finished.connect(self._on_finished)

    @property
    def is_ready(self) -> bool:
        """Return True only after Ruff completed LSP initialization."""
        return self._initialized and self.process.state() == QProcess.Running

    def start(self):
        """
        Start exactly one native Ruff server process.

        A shared workspace server is important. Do not start one server for every tab:
        a server-per-tab design wastes processes and defeats LSP workspace configuration/state handling.
        """
        if self._started:
            # MainWindow can sefely call start more then once without spamming.
            # dublicate Ruff server process
            return

        self._started = True

        # The working directory helps Ruff discover project configuration and
        # makes workspace-relative behavior consistent with 'python main.py'.
        self.process.setWorkingDirectory(str(self.workspace_root))

        # This launches:
        #   C:\...\.venv\Scripts\python.exe -m ruff server
        self.process.start(self.python_executable, ["-m", "ruff", "server"])

        # waitForStarted only waits for process creation. It does not wait for
        # linting, diagnostics, or any normal ongoing server work.
        if not self.process.waitForStarted(5_000):
            self.server_error.emit(
                "Could not start Ruff Language Server: " + self.process.errorString()
            )
            self._started = False
            return

        # An LSP client must initialize before sending didOpen/didChange.
        self._initialize()

    def _initialize(self):
        """
        Send the mandatory LSP initialize request.

        The LSP server will respond to this request with a capabilities object.
        Only after recieving that response may the client send 'initialized'.
        """
        workspace_uri = self.workspace_root.as_uri()

        params = {
            # The LSP field normally accepts an OS process ID. None is valid when the editor
            # foes not need the server to monitor client termination.
            "processId": None,
            # rootUri/workspaceFolders identify where Ruff should discover the
            # project's pyproject.toml, ruff.toml, or .ruff.toml configuration.
            "rootUri": workspace_uri,
            "workspaceFolders": [
                {
                    "uri": workspace_uri,
                    "name": self.workspace_root.name,
                }
            ],
            # Capabilities describe what the CLIENT supports, not what Ruff does.
            "capabilities": {
                "general": {
                    # LSP character position require in agreed encoding. Ask for UTF-8
                    # first because this editor is Scintilla/QScintilla-based.
                    # A future Unicode regression test must verify this selection.
                    "positionEncodings": ["utf-8", "utf-16"],
                },
                "textDocument": {
                    "synchronization": {
                        "dynamicRegistration": False,
                        "willSave": False,
                        "didSave": False,
                    },
                    "publishDiagnostics": {
                        # Accept related information if Ruff provides it.
                        "relatedInformation": True,
                        # Accept document version in pushed diagnostics.
                        # Version values allow controllers to reject stale result ranges.
                        "versionSupport": True,
                    },
                    "codeAction": {
                        # Advertise the action kinds this editor intends to support
                        # in the phase-two quick-fix implementation.
                        "codeActionLiteralSupport": {
                            "codeActionKind": {
                                "valueSet": [
                                    "",
                                    "quickfix",
                                    "source",
                                    "source.fixAll",
                                    "source.organizeImports",
                                ]
                            }
                        }
                    },
                },
                "workspace": {
                    "workspaceFolders": True,
                },
            },
            # Ruff-specific LSP setting. Ruff's existing pyproject.toml remains the source
            # of lint/format configuration. This only controls server integration behavior.
            "initializationOptions": {
                "settings": {
                    "configurationPreference": "filesystemFirst",
                    # Explicitly retain real-time parser/syntax diagnostics.
                    "showSyntaxErrors": True,
                    # Use debug only while integrating.
                    # Change to "warn" after success to reduce stderr logging noise.
                    "logLevel": "debug",
                }
            },
        }

        # 'initialize' is a REQUEST. The callback runs when Ruff responds with
        # matching JSON-RPC id, or with a JSON-RPC error.
        self.request("initialize", params, self._on_initialized)

    def _on_initialized(self, result, error):
        """Complete the LSP startup handshake after initialize response."""
        if error is not None:
            self.server_error.emit(f"Ruff initialize failed: {error}")
            return

        # This is a NOTIFICATION. It has no request id and no response.
        self.notify("initialized", {})

        self._initialized = True
        self.server_ready.emit()

    def open_document(self, uri: str, text: str, version: int):
        """Send the intitial in-memory content for one Python editor tab."""
        if not self.is_ready:
            return

        # After didOpen, LSP treats the supplied text as the authorative buffer.
        # The document does not need to be saved before Ruff can diagnose it.
        self.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "python",
                    "version": version,
                    "text": text,
                }
            },
        )

    def change_document(self, uri: str, text: str, version: int):
        """
        Syncronize a changed QScintilla buffer using full-text replacement.

        LSP supports incremental rangedm but complete buffer sync is safer for the first implementation.
        It elimates range calculation bugs and is practical because Ruff is fast and the controller debounces rapid typing.
        """
        if not self.is_ready:
            return

        self.notify(
            "textDocument/didChange",
            {
                "textDocument": {
                    "uri": uri,
                    "version": version,
                },
                # No 'range' means the entire document is replaced by this text.
                "contentChanges": [{"text": text}],
            },
        )

    def close_document(self, uri: str):
        """Tell Ruff it cna discard in-memory state for a closed tab."""
        if self.is_ready:
            self.notify(
                "textDocument/didClose",
                {"textDocument": {"uri": uri}},
            )

    def request(self, method: str, params: dict, callback):
        """Send one JSON-RPC request and sace its response callback."""
        request_id = self._next_request_id
        self._next_request_id += 1

        # Store before writing. Ruff can respond quickly on a local process
        self._pending_requests[request_id] = callback

        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )

    def notify(self, method: str, params: dict):
        """Send one JSON-RPC notification with no request id."""
        self._send(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
        )

    def _send(self, payload: dict):
        """Frame and write a UTF-8 JSON-RPC message to Ruff stdin."""
        if self.process.state() != QProcess.Running:
            self.server_error.emit("Ruff server is not running.")
            return

        # Compact JSON is optional but produces fewer bytes over the local pipe.
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

        # LSP framing uses the NUMBER OF UTF-8 BYTES, not len(str) characters.
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")

        # QProcess.write queues the bytes asynchronously to Ruff's stdin.
        self.process.write(header + body)

    def _read_stdout(self):
        """Read server output and extract every complete LSP-frame message."""
        self._read_buffer.extend(bytes(self.process.readAllStandardOutput()))

        while True:
            # Every LSP header ends with CRLF CRLF. If it is incomplete, preserve
            # current bytes and wait for Qt to emit readyReadStandardOutput again.
            header_end = self._read_buffer.find(b"\r\n\r\n")
            if header_end < 0:
                return

            header = self._read_buffer[:header_end].decode("ascii", errors="replace")

            content_length = None
            for line in header.split("\r\n"):
                name, seperator, value = line.partition(":")
                if seperator and name.lower() == "content-length":
                    content_length = int(value.strip())
                    break

            if content_length is None:
                # Continueing after a broke header makes framing ambigous.
                # Clear it ans surface and actionable infrastructure error instead.
                self.server_error.emit("Ruff server sent an LSP message with Content-Length.")
                self._read_buffer.clear()
                return

            message_start = header_end + 4
            message_end = message_start + content_length

            # Header arrived, but the JSON body has not finished arriving yet.
            if len(self._read_buffer) < message_end:
                return

            body = bytes(self._read_buffer[message_start:message_end])

            # Remove this complete message before processing it. A handler mey synchronously
            # cuase new server traffic; buffer state stays correct.
            del self._read_buffer[:message_end]

            try:
                message = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                self.server_error.emit(f"Invalid JSON-RPC payload from Ruff: {error}")
                continue
            self._handle_message(message)

    def _handle_message(self, message: dict):
        """Route JSON-RPC responses and Ruff server notifications."""
        is_response = "id" in message and ("result" in message or "error" in message)
        if is_response:
            callback = self._pending_requests.pop(message["id"], None)
            if callback is not None:
                callback(message.get("result"), message.get("error"))
            return

        # Diagnostics are server-pushed notifications, not responses to a
        # specific request. Route them through a Qt signal by a document URI.
        if message.get("method") == "textDocument/publishDiagnostics":
            params = message.get("params") or {}
            self.diagnostics_published.emit(
                params.get("uri", ""),
                params.get("diagnostics", []),
                params.get("version"),
            )

    def _read_stderr(self):
        """Print Ruff LSP logs without confusing them with protocol stdout."""
        text = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        if text.strip():
            print(f"[Ruff LSP] {text.rstrip()}")

    def _on_process_error(self, _error):
        """Convert QProcess startup/runtime failure into a visible Qt signal."""
        self.server_error.emit("Ruff server process error: " + self.process.errorString())

    def _on_finished(self, exit_code, _exit_status):
        """Restart lifecycle flags if Ruff exists enexpectedly or during shutdown."""
        self._initialized = False
        self._started = False
        self.server_error.emit(f"Ruff server stopped with exit code {exit_code}.")

    def shutdown(self):
        """Shut down the language server using the standard LSP lifecycle."""
        if self.process.state() == QProcess.NotRunning:
            return

        def after_shutdown(_result, _error):
            # 'exit' is a notification sent only after shutdown response.
            self.notify("exit", {})

        if self._initialized:
            self.request("shutdown", {}, after_shutdown)

            # Permit the server a short graceful-exit period.
            # This does not run during normal typing and only affects application close.
            if not self.process.waitForFinished(2_000):
                self.process.kill()
                self.process.waitForFinished(2_000)

        else:
            # If initialization never completed, a normal shutdown request is not valid.
            # End the incomplete process direclty.
            self.process.kill()
            self.process.waitForFinished(2_000)
