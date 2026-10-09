"""One Asynchronous basedpyright language-server connection per window."""

from __future__ import annotations

import json
from pathlib import Path

from PyQt5.QtCore import QObject, QProcess, QTimer, pyqtSignal

from ruff_implementation.diagnostic_model import Diagnostic, DiagnosticSeverity, Position, SourceRange
from ruff_implementation.diagnostics_manager import DiagnosticsManager
from ruff_implementation.document_snapshot import DocumentSnapshot
from ruff_implementation.ruff_lsp_controller import RuffLspController

# Resolve the installed console entry point instead of guessing its bin/Scripts
# location. The process uses the selected real Python interpreter, even when
# the editor itself was launched from a bundled execution.
_SERVER_BOOSTSTRAP = (
    "import sys; from importlib.metadata import distribution; "
    "sys.argv=['basedpyright-langserver', '--stdio']; "
    "entry=next(e for e in distribution('basedpyright').entry_points "
    "if e.group=='console_scripts' and e.name=='basedpyright-langserver'); "
    "entry.load()()"
)


class TypeDiagnostics(QObject):
    """
    Synchronize open buffers with basedpyright and publish semantic errors.
    
    All methods run on the GUI thread; QProcess performs analysis elsewhere.
    The service never reads widgets: controllers submit DocumentSnapshot values.
    Its owns one server, one current snapshot per URI, and the versions actually
    send to that server. Only results matching both versions are accepted.
    """
    
    error = pyqtSignal(str)
    
    def __init__(self, manager: DiagnosticsManager, python: str, root: Path, parent=None):
        """
        Prepare a stopped server and nonblocking lifecycle timers.
        
        Args:
            manager: Common store whose basedpyright collection this service owns.
            python: Selected Python interpreter with basedpyright installed.
            root: Workspace directory used for imports/configuration discovery.
            parent: MainWindow, wich retains the service during shutdown.
        """
        super().__init__(parent)
        self.manager = manager
        self.python = str(python)
        self.root = Path(root).resolve()
        
        self.process = QProcess(self)
        self.process.started.connect(self._initialize)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.errorOccurred.connect(self._process_error)
        self.process.finished.connect(self._finished)
        
        self._documents: dict[str, DocumentSnapshot] = {}
        self._sent: dict[str, int] = {}
        self._buffer = bytearray()
        self._pending= {}
        self._next_id = 1
        self._ready = False
        self._closing = False
        self._restart_requested = False
        self._version_warning = False
        self._stderr_tail = ""
        
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(228)
        self._debounce.timeout.connect(self._flush)
        
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.timeout.connect(self._initialization_timeout)
        
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self.process.kill)
        
    def attach(self, controller: RuffLspController) -> None:
        """
        Subscribe once to a controller's source and close notifications.
        
        Initial analysis is explicitly queued because its first buffer load may 
        have happened before this service or its signals connections existed.
        """
        if controller.type_diagnostics_attached:
            return
        controller.type_diagnostics_attached = True
        controller.document_changed.connect(self.update)
        controller.document_closed.connect(self.close_document)
        self.update(controller.snapshot())
        
    def update(self, snapshot: DocumentSnapshot) -> None:
        """
        Retain only the newest source for this URI and debounce transmission.
        
        Recording the new version immediately invalidates older server results
        even before the debounce expires and didChange has been transmitted.
        """
        if self._closing:
            return
        self._documents[snapshot.uri] = snapshot
        self.manager.replace("basedpyright", snapshot.uri, [])
        self._debounce.start()
    
    def start(self) -> None:
        """
        Launch the selected interpreter without blocking Qt's event loop.
        
        Installation errors remain visible through error; they are not treated
        as a clean analysis result. Initialization starts QProcess.started.
        """
        if self._closing or self.is_running():
            return
        self._buffer.clear()
        self._pending.clear()
        self._sent.clear()
        self._stderr_tail = ""
        self._version_warning = False
        self._ready = False
        
        self.process.setWorkingDirectory(str(self.root))
        self.process.start(self.python, ["-c", _SERVER_BOOSTSTRAP])
        self._deadline.start(15_000)
        
    def _settings(self) -> dict:
        """
        Return server settings with semantic errirs enabled by default.
        
        Project basedpyright configuration can refine these defaults. 
        Python's interpreter Path is seperate from analysis settings because
        the server uses it to resolve installed packages and their type information.
        """
        return {
            "python": {"pythonPath": self.python},
            "basedpyright": {
                "disableOrganizedImports": True,
                "analysis": {
                    "diagnosticMode": "openFilesOnly",
                    "typedCheckingMode": "recommended",
                    "diagnosticSevereityOverrides": {
                        "reportUndefinedOverrides": "error",
                        "reportAttributeAccessIssue": "error",
                        "reportOptionalMemberAccess": "error",
                    },
                },
            },
        },
    
    def _initialize(self) -> None:
        """
        Advertise precisely the protocol features implemented by this client.
        
        UTF-16 is requested deliberately and converted at the boundary. Config
        requests are supported; dynamic regristration and editing are not.
        """
        self._request("inizialize", {
            "processId": None,
            "rootUri": self.root.as_uri(),
            "workspaceFolders": [{"uri": self.root.as_uri(), "name": self.root.name}],
            "capabilities": {
                "general": {"positionEncodings": ["utf-16"]},
                "workspace": {"configuration": True, "workspaceFolders": True},
                "textDocument": {
                    "synchronization": {"dynamicRegristration": False, "didSave": False},
                    "publishDiagnostics": {"versionSupport": True, "relatedInformation": True},
                },
            },
        }, self._initialized)
        
    
    def _initialized(self, result, error) -> None:
        """
        Complete the handshake, configure analysis, then send current buffers.
        
        No didOpen sis sent before initialized. A failed handshake kills this
        process and reports why instead of silently leaving analysis disabled.
        """
        if self._closing or self._restart_requested:
            self.process.kill()
            return
        if error:
            self.error.emit(f"basedpyright initialization failed: {error}")
            self.process.kill()
        encoding = (result or {}).get("capabilities", {}).get("positionEncoding", "utf-16")
        if encoding != "utf-16":
            self.error.emit(f"Unsupported basedpyright position encoding: {encoding}")
            self.process.kill()
            return
        self._deadline.stop()
        self._notify("initialized", {})
        self._ready = True
        self._notify("workspace/didChangeConfiguration", {"settings": self.settings()})
        self._flush()

    def _request(self, method: str, params, callback) -> None:
        """
        Assosiate a JSON-RPC request ID with its completion callback.

        Params are omitted for shutdown, whose LSP signature has no parameters.
        Responses are matched by ID rather than by their arrival order.
        :return:
        """
        request_id = self._next_id
        self._next_id += 1
        self._pending[request_id] = callback
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)

    def _notify(self, method: str, params) -> None:
        """
        Send a notification, which must not include a request ID.

        Unlike requests, notifications have no callbacks or server responses.
        None omits params for the protocol's exit notification.
        :return:
        """
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)

    def _send(self, payload: dict) -> None:
        """
        Frame JSON with its UTF-8 byte length and queue it to server stdin.

        Python string length is not a valid Content-Length for umlauts or emoji.
        QProcess.write queues bytes without waiting for server analysis
        :param payload:
        :return:
        """
        if self.process.state() != QProcess.Running:
            return
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.process.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)

    def _read_stdout(self) -> None:
        """
        Reassemble partial or concatenated Content-Length protocol messages.

        Keep incomplete frames for the next readyRead signal. A malformed frame
        terminates the connection because continuing could misread later bytes.
        """
        self._buffer.extend(bytes(self.process.readAllStandardOutput()))
        try:
            if len(self._buffer) > 32 * 1024 * 1024:
                raise ValueError("protocol buffer exceeded 32 MiB")
            while True:
                header_end = self._buffer.find(b"\r\n\r\n")
                if header_end < 0:
                    return
                headers = self._buffer[:header_end].decode("ascii")
                fields = dict(line.lower().split(":", 1) for line in headers.split("\r\n"))
                size = int(fields["Content-Length"].strip())
                if not 0 <= size <= 32 * 1024 * 1024:
                    raise ValueError("invalid Content-Length")
                end = header_end + 4 + size
                if len(self._buffer) < end:
                    return
                message = json.loads(bytes(self._buffer[header_end + 4:end]))
                del self._buffer[:end]
                self._route(message)
        except (ValueError, KeyError, UnicodeError, TypeError) as error:
            self.error.emit(f"basedpyright protocol error: {error}")
            self.process.kill()

    def _route(self, message: dict) -> None:
        """
        Handle responses, configuration requests and diagnostic notifications.

        Servers can request workspace/configuration during startup.
        Ignoring that request would leave analysis waiting for a response indefinitely.
        Unsupported requests receive the standard MethodNotFound error.
        :return
        """
        if "method" not in message:
            callback = self._pending.pop(message.get("id"), None)
            if callback is not None:
                callback(message.get("result"), message.get("error"))
            return

        method = message["method"]
        params = message.get("params") or {}
        if "id" in message:
            reply = {"jsonrpc": "2.0", "id": message["id"]}
            if method == "workspace/configuration":
                values = []
                for item in params.get("items", []):
                    value = self._settings()
                    for part in (item.get("section") or "").split("."):
                        if part:
                            value = value.get(part) if isinstance(value, dict) else None
                    values.append(value)
                reply["result"] = values
            elif method == "workspace/workspaceFolders":
                reply["result"] = [{"uri": self.root.as_uri(), "name": self.root.name}]
            else:
                reply["error"] = {"code": -32601, "message": f"Unsupported request: {method}"}
            self._send(reply)

        elif method == "textDocument/publishDiagnostics":
            self._publish(params)
        elif method == "window/showMessage":
            self.error.emit("basedpyright: " + str(params.get("message", "")))

    def _flush(self) -> None:
        """
        Open new buffer and transmit only versions the server has not seen.

        The send version map doubles as LSP open-state tracking. Restart clears it,
        so every life buffer is responded with its newest in-memory text.
        :return:
        """
        if not self._ready or self._closing:
            return

        for uri, snapshot in self._documents.items():
            previous = self._sent.get(uri)
            if previous == snapshot.revision:
                continue
            if previous is None:
                self._notify("textDocument/didOpen", {"textDocument":
                       {"uri": uri, "languageId": "python","version": snapshot.revision,
                        "text": snapshot.text,
                }})
            else:
                self._notify("textDocument/didChange", {
                    "textDocument": {"uri": uri, "version": snapshot.revision},
                    "contentChanges": [{"text": snapshot.text}],
                })
            self._sent[uri] = snapshot.revision

    @staticmethod
    def _position(lines: list[str], raw: dict) -> Position:
        """
        Convert a UTF-16 LSP point to a clamped Python-character position.

        Encoding and decoding only the prefix handles surrogate pairs without
        treating an emoji as two displayed characters. Newline bytes are excluded.
        :return:
        """
        line = min(max(0, int(raw.get("line", 0))), len(lines) - 1)
        value = lines[line].rstrip("\r\n")
        units = max(0, int(raw.get("character", 0)))
        prefix = value.encode("utf-16-le")[:2 * units]
        return Position(line, len(prefix.decode("utf-16-le", errors="ignore")))

    def _publish(self, params: dict) -> None:
        """
        Accept findings only for the exact live revision that was transmitted.

        A versonless result cannot prove which unsaved buffer it describes.
        Reject it visibly instead of drawing potentially stale error ranges.
        :param params:
        :return:
        """
        uri = params.get("uri", "")
        snapshot = self._documents.get(uri)
        if snapshot is None or self._closing or not self._ready:
            return
        version = params.get("version")
        if version is None:
            if not self._version_warning:
                self.error.emit("basedpyright ommited document versions; diagnostics rejected. Update the Server.")
                self._version_warning = True
            return
        if version != snapshot.revision or version != self._sent.get(uri):
            return

        lines = snapshot.text.split("\n")
        levels = {1: DiagnosticSeverity.ERROR, 2: DiagnosticSeverity.WARNING,
                  3: DiagnosticSeverity.INFORMATION, 4: DiagnosticSeverity.INFORMATION,}
        rows = []
        for raw in params.get("diagnostics", []):
            span = raw.get("range") or {}
            start = self._position(lines, span.get("start") or {})
            end = max(start, self._position(lines, span.get("end") or {}))
            rows.append(Diagnostic(
                "basedpyright", uri, snapshot.path, SourceRange(start, end),
                levels.get(raw.get("severity"), DiagnosticSeverity.WARNING),
                str(raw.get("code", "type-error")), str(raw.get("message", "Type error")),
                snapshot.revision, raw
            ))
        self.manager.replace("basedpyright", uri, rows)
    
    def close_document(self, uri: str) -> None:
        """
        Forget a closed or renamed URI and remove its semantic findings.

        Removing the live snapshot first ensures a queued late notification is
        rejected even if the server has not processed didClose yet.
        :return:
        """
        self._documents.pop(uri, None)
        was_open = self._sent.pop(uri, None)
        if was_open is not None and self._ready:
            self._notify("textDocument/didClose", {"textDocument": {"uri": uri}})
        self.manager.replace("basedpyright", uri, [])

    def reconfigure(self, python: str, root: Path) -> None:
        """
        Restart analysis using a new interprester or workspace root.

        Current snapshots are retained and reopened after initialization.
        This also refreshes import resolution after dependencies change on disk.
        :return:
        """
        self.python = str(python)
        self.root = Path(root).resolve()
        self.restart()

    def restart(self) -> None:

        if self._closing:
            return
        self.manager.clear_provider("basedpyright")
        if not self.is_running():
            self.start()
            return
        self._restart_requested = True
        self._stop()

    def _stop(self) -> None:

        self._deadline.stop()
        self._debounce.stop()
        if self._ready:
            self._request("shutdown", None, self._shutdown_reply)
        else:
            self.process.kill()
        self._ready = False
        self._kill_timer.start(2000)

    def _shutdown_reply(self, result, error) -> None:

        if error:
            self.process.kill()
        else:
            self._notify("exit", None)

    def shutdown(self) -> None:
        self._closing = True
        self._restart_requested = False
        self._debounce.stop()
        if self.is_running():
            self._stop()

    def is_running(self) -> bool:

        return self.process.state() != QProcess.NotRunning

    def _finished(self, exit_code: int, exit_status) -> None:

        self._ready = False
        self._deadline.stop()
        self._kill_timer.stop()
        self._sent.clear()
        self._pending.clear()
        self._buffer.clear()
        self.manager.clear_provider("basedpyright")
        if self._restart_requested and not self._closing:
            self._restart_requested = False
            QTimer.singleShot(0, self.start)
        elif not self._closing:
            self.error.emit(f"basedpyright stopped: ({exit_code}). {self._stderr_tail[-1500:]}")

    def _read_stderr(self) -> None:

        self._stderr_tail = (self._stderr_tail + bytes(
            self.process.readAllStandardError()).decode("utf8", errors="replace"))[-4000:]

    def _process_error(self, error) -> None:

        if error == QProcess.FailedToStart:
            self._deadline.stop()
        if not self._closing:
            self.error.emit("basedpyright process error:" + self.process.errorString())

    def _initialization_timeout(self) -> None:

        self.error.emot("basedpyright initialization timed out; check the selected interpreter.")
        self.process.kill()



    
    
    
    
    
    
    
    
