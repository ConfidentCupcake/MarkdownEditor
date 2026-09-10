# MarkdownEditor Code Fix Guide

Target repository: `https://github.com/ConfidentCupcake/MarkdownEditor`  
Target commit: `b4f59b9b3cdd38cbbc171b07ab1f483f641a0a82`  
Companion audit: `MarkdownEditor_REPOSITORY_AUDIT.md`  

## How to use this guide

Apply fixes in numeric order within each shared subsystem. In particular:

- Apply BUG-044's atomic save helper before BUG-008 and BUG-009.
- Apply BUG-010's stable LSP URI change before BUG-017 and BUG-018.
- Apply BUG-015's request-generation pattern to all Jedi helpers before BUG-016's shutdown changes.
- Regenerate `python_editor/lexer_fast.c`, the platform extension, and `resources_rc.py`; do not hand-edit generated files.

The snippets are written for the repository's current PyQt5/QScintilla design. Imports shown in a snippet belong at the top of that file unless stated otherwise.

## Critical fixes

### BUG-001 — Complete `ReferencesFinder`

**File:** `code_inteligence/references_finder.py`  
**Method:** replace `ReferencesFinder.run`; add `shutdown`

```python
def run(self):
    try:
        script = Script(code=self.code, path=self.file_path)
        names = script.get_references(
            line=self.line,
            column=self.column,
            include_builtins=False,
        )
        if self._shutting_down or self.isInterruptionRequested():
            return

        references = []
        for name in names:
            module_path = name.module_path
            if module_path is None:
                continue
            references.append(
                (str(module_path), name.line - 1, name.column, name.name)
            )

        if references:
            self.references_found.emit(references)
        else:
            self.references_empty.emit()
    except Exception as error:
        if not self._shutting_down:
            # Add `error = pyqtSignal(str)` to the class if the UI needs details.
            self.references_empty.emit()

def shutdown(self):
    self._shutting_down = True
    self.requestInterruption()
    if self.isRunning():
        self.wait(2000)
```

This closes the invalid `try` block, uses Jedi's supported `get_references` API, converts Jedi's one-based lines to editor coordinates, and gives the thread a bounded shutdown path.

### BUG-002 — Make window close safe and complete

**File:** `main.py`  
**Method:** replace `MainWindow.closeEvent`

```python
def closeEvent(self, event):
    # Save the still-intact tab layout before close_editor removes tabs.
    self.save_session()

    for editor in list(self.tab_view.all_editors()):
        if self.close_editor(editor) is False:
            event.ignore()
            return

    if hasattr(self, "terminal"):
        self.terminal.stop()
    if self.python_runner is not None:
        self.python_runner.stop()

    file_manager = getattr(self, "file_manager", None)
    if file_manager is not None:
        file_manager.git_checker.shutdown()

    self.settings.setValue("recent_files", self.recent_files)
    self.settings.sync()
    self.ruff_lsp_client.shutdown()
    event.accept()
    super().closeEvent(event)
```

This removes the early return, routes every dirty tab through the existing Save/Discard/Cancel prompt, honors Cancel, and always shuts down application-owned workers and processes.

### BUG-003 — Close editors before deleting their files

**File:** `main.py`  
**Method:** replace the loop in `MainWindow.close_editors_for_path`

```python
affected_editors = []
target_path = Path(target_path).resolve()

for editor in self.tab_view.all_editors():
    raw_path = getattr(editor, "path", None)
    if raw_path is None:
        continue

    editor_path = Path(raw_path).resolve()
    if is_directory:
        is_affected = (
            editor_path == target_path
            or target_path in editor_path.parents
        )
    else:
        is_affected = editor_path == target_path

    if is_affected:
        affected_editors.append(editor)

for editor in affected_editors:
    if self.close_editor(editor) is False:
        return False
return True
```

The old comparison was true for every `Path`, so all editors were skipped. Resolving both sides also prevents relative/absolute spelling differences from missing a match.

### BUG-004 — Call the real rename handler

**File:** `side_bar_widgets/file_manager.py`  
**Method:** `FileManager.rename_file_with_index`

```python
self.main_window.on_file_rename(
    old_path=old_path,
    new_path=new_path,
    is_directory=self._rename_is_directory,
)
```

This matches the method actually defined in `main.py`. After this minimal crash fix, also apply BUG-010 so renamed Python documents migrate their LSP and Jedi identity.

## High-severity fixes

### BUG-005 — Use one delimiter type in the Python lexer fallback

**File:** `python_editor/custompythonlexer.py`  
**Methods:** `_py_pack_state`, `_py_unpack_state`, and the prefixed-string/CRLF branches

```python
def _py_pack_state(in_string, in_comment, triple, string_delim, in_fstring,
                   escape, in_fexpr, fexpr_depth, is_docstring,
                   expect_docstring, at_class_body):
    state = 0
    if in_string: state |= 1
    if in_comment: state |= 2
    if triple: state |= 4
    if string_delim == '"': state |= 8
    if in_fstring: state |= 16
    if escape: state |= 32
    if in_fexpr: state |= 64
    state |= (fexpr_depth & 7) << 7
    if is_docstring: state |= 1024
    if expect_docstring: state |= 2048
    if at_class_body: state |= 4096
    return state

def _py_unpack_state(state):
    return (
        int(bool(state & 1)), int(bool(state & 2)), int(bool(state & 4)),
        '"' if state & 8 else "'",
        int(bool(state & 16)), int(bool(state & 32)), int(bool(state & 64)),
        (state >> 7) & 7, int(bool(state & 1024)),
        int(bool(state & 2048)), int(bool(state & 4096)),
    )
```

In `_py_compute_state`, replace the integer byte access:

```python
next_c = text[i + 1:i + 2].decode("latin-1")
if (i + 3 < length
        and text[i + 2:i + 3].decode("latin-1") == next_c
        and text[i + 3:i + 4].decode("latin-1") == next_c):
    in_string = triple_string = 1
    string_delim = next_c
    in_fstring = int(c == "f")
    if expect_docstring:
        is_docstring = 1
    expect_docstring = 0
    i += 4
    continue
```

In `_py_scan_tokens`, fix CRLF:

```python
if c == "\r" and i + 1 < end and text[i + 1:i + 2] == b"\n":
```

All persisted and live delimiters are now one-character strings. Add a shared corpus test that compares `_py_scan_tokens` to `lexer_fast.scan_tokens` chunk by chunk.

### BUG-006 — Protect the terminal prompt boundary

**File:** `con_term/terminal_widget.py`  
**Method:** replace the editing portion of `TerminalWidget.eventFilter`

```python
if obj is not self.output or event.type() != QEvent.KeyPress:
    return super().eventFilter(obj, event)
if self.process.state() != QProcess.Running:
    return True

cursor = self.output.textCursor()

if event.key() in (Qt.Key_Return, Qt.Key_Enter):
    cursor.movePosition(QTextCursor.End)
    end_pos = cursor.position()
    cursor.setPosition(self._input_start_pos)
    cursor.setPosition(end_pos, QTextCursor.KeepAnchor)
    command = cursor.selectedText()
    cursor.clearSelection()
    cursor.movePosition(QTextCursor.End)
    cursor.insertText("\n")
    self.output.setTextCursor(cursor)
    self._input_start_pos = cursor.position()
    self.process.write((command + "\n").encode("utf-8"))
    return True

if event.matches(QKeySequence.Paste):
    text = QApplication.clipboard().text()
    cursor.movePosition(QTextCursor.End)
    cursor.insertText(text.replace("\r\n", "\n"))
    self.output.setTextCursor(cursor)
    return True

if event.key() == Qt.Key_Backspace:
    if cursor.hasSelection():
        if cursor.selectionStart() < self._input_start_pos:
            return True
        cursor.removeSelectedText()
    elif cursor.position() > self._input_start_pos:
        cursor.deletePreviousChar()
    self.output.setTextCursor(cursor)
    return True

if event.key() == Qt.Key_Delete:
    if cursor.selectionStart() < self._input_start_pos:
        return True
    cursor.deleteChar()
    self.output.setTextCursor(cursor)
    return True

if event.text():
    if cursor.hasSelection() and cursor.selectionStart() < self._input_start_pos:
        cursor.clearSelection()
    cursor.movePosition(QTextCursor.End)
    cursor.insertText(event.text())
    self.output.setTextCursor(cursor)
    return True

return False
```

Add `QApplication` and `QKeySequence` imports. This makes the displayed newline authoritative, updates the boundary from the same cursor, and prevents edits from reaching terminal history.

### BUG-007 — Implement one validated drag/drop operation

**File:** `side_bar_widgets/file_manager.py`  
**Method:** replace `FileManager.dropEvent`

```python
def dropEvent(self, event):
    if not event.mimeData().hasUrls():
        event.ignore()
        return

    index = self.indexAt(event.pos())
    if index.isValid():
        selected = Path(self.model.filePath(index))
        target_dir = selected if selected.is_dir() else selected.parent
    else:
        target_dir = Path(self.model.rootPath())

    try:
        for url in event.mimeData().urls():
            source = Path(url.toLocalFile()).resolve()
            destination = (target_dir / source.name).resolve()
            if source == destination:
                continue
            if source.is_dir() and source in destination.parents:
                raise OSError("Cannot copy a folder into itself")
            if destination.exists():
                raise FileExistsError(f"Destination already exists: {destination}")

            copy_requested = bool(event.keyboardModifiers() & Qt.ControlModifier)
            if copy_requested:
                shutil.copytree(source, destination) if source.is_dir() \
                    else shutil.copy2(source, destination)
            else:
                shutil.move(str(source), str(destination))
    except OSError as error:
        QMessageBox.critical(self, "File operation", str(error))
        event.ignore()
        return

    event.setDropAction(Qt.CopyAction if copy_requested else Qt.MoveAction)
    event.accept()
```

Do not call `super().dropEvent()` after manually performing the operation. This honors the visual target and eliminates duplicate/same-path operations.

### BUG-008 — Implement the advertised Ruff-on-save pipeline

**Files:** `main.py`, after applying BUG-044  
**Methods:** add `_format_python_bytes`; call it from `_save_editor_to_path`

```python
def _format_python_bytes(self, path: Path, source: bytes) -> bytes:
    if self.ruff_save_mode != "safe_format" or path.suffix.lower() != ".py":
        return source

    import subprocess
    import tempfile

    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, suffix=".py", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(source)

        commands = (
            [self.python_runner.interpreter, "-m", "ruff", "check", "--fix", str(temporary)],
            [self.python_runner.interpreter, "-m", "ruff", "format", str(temporary)],
        )
        for command in commands:
            completed = subprocess.run(
                command, cwd=path.parent, capture_output=True, text=True,
                timeout=15, check=False,
            )
            if completed.returncode not in (0, 1):
                raise RuntimeError(completed.stderr.strip() or "Ruff failed")
        return temporary.read_bytes()
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
```

Inside BUG-044's `_save_editor_to_path`, before writing the temporary output:

```python
data = editor.text().encode("utf-8")
try:
    data = self._format_python_bytes(path, data)
except (OSError, subprocess.SubprocessError, RuntimeError) as error:
    QMessageBox.warning(self, "Ruff on save", str(error))
    return False
```

After the atomic replace, reload formatted text with `setTextSafely(data.decode("utf-8"))` if it differs. Plain `ruff check --fix` applies safe fixes by default; do not add `--unsafe-fixes`.

### BUG-009 — Abort Run when Save fails

**File:** `main.py`  
**Methods:** `run_current_file` and the run-with-arguments handler

```python
if getattr(editor, "path", None) is None:
    if not self.save_as():
        return
else:
    if not self.save_file():
        self.statusBar().showMessage("Run cancelled: save failed", 4000)
        return

path = Path(editor.path)
self.console_dock.show()
self.python_runner.run_file(path, cwd=path.parent)
```

Use the same Boolean gate before `run_file_with_args`. The executed file is now guaranteed to be the version the user just saved.

### BUG-010 — Give Ruff a stable URI and relocate documents explicitly

**File:** `ruff_implementation/ruff_lsp_controller.py`  
**Methods:** `__init__`, `uri`, and new `relocate`

```python
from uuid import uuid4

# In __init__:
self._uri = self._make_uri(editor.full_path)

def _make_uri(self, path):
    if path is None:
        return f"untitled:markdown-editor/{uuid4().hex}.py"
    return Path(path).resolve().as_uri()

@property
def uri(self):
    return self._uri

def relocate(self, new_path: Path):
    old_uri = self._uri
    if self._opened and self.client.is_ready:
        self.client.close_document(old_uri)

    self._uri = Path(new_path).resolve().as_uri()
    self._opened = False
    self.document_version += 1
    self.open_document()
```

**File:** `main.py`  
**Methods:** `save_as` and `on_file_rename`, immediately after choosing each updated path

```python
old_path = getattr(editor, "path", None)
editor.path = path
editor.full_path = path.absolute()

if isinstance(editor, PythonEditor):
    if editor.ruff_lsp is not None:
        editor.ruff_lsp.relocate(path)
    editor.auto_completer.file_path = str(editor.full_path)
```

For a directory rename, call the same block for every affected Python editor. This sends `didClose` for the old identity and `didOpen` for the new one instead of sending changes for an unopened URI.

### BUG-011 — Make update checking repository-aware and thread-safe

**File:** `main.py`  
**Class/methods:** add a class signal, connect it in `__init__`, replace version comparison in `check_for_updates`

```python
from packaging.version import InvalidVersion, Version

class MainWindow(QMainWindow):
    update_available = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        self.update_available.connect(self._show_update_dialog)
        # existing initialization follows
```

```python
api_url = "https://api.github.com/repos/ConfidentCupcake/MarkdownEditor/releases/latest"

# Inside _check, after decoding `data`:
remote_tag = data.get("tag_name", "")
try:
    remote = Version(remote_tag.removeprefix("v"))
    current = Version(APP_VERSION.removeprefix("v"))
except InvalidVersion:
    return

if remote <= current:
    return

assets = data.get("assets") or []
download_url = next(
    (asset.get("browser_download_url", "") for asset in assets
     if asset.get("name", "").lower().endswith((".exe", ".msi"))),
    data.get("html_url", ""),
)
if download_url:
    self.update_available.emit(str(remote), download_url)
```

Qt queues a signal emitted from the worker thread to the GUI receiver. Semantic versions are normalized on both sides, older/equal releases are rejected, and the release page is a safe fallback.

### BUG-012 — Stop tracking runtime crash logs

**File:** `.gitignore`

```gitignore
# Runtime diagnostics
crash_log.txt
*.log
```

Then remove the tracked file once:

```bash
git rm --cached crash_log.txt
```

**File:** `main.py`  
**Method:** replace `_excepthook` storage path

```python
from PyQt5.QtCore import QStandardPaths

def _crash_log_path() -> Path:
    root = Path(QStandardPaths.writableLocation(
        QStandardPaths.AppLocalDataLocation
    ))
    root.mkdir(parents=True, exist_ok=True)
    return root / "crash_log.txt"

def _excepthook(exc_type, exc, tb):
    try:
        with _crash_log_path().open("a", encoding="utf-8") as handle:
            handle.write(f"\n[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}]\n")
            traceback.print_exception(exc_type, exc, tb, file=handle)
    except OSError:
        pass
    traceback.print_exception(exc_type, exc, tb)
```

This prevents machine-specific logs from entering Git and uses the operating system's writable application-data location.

### BUG-013 — Store user configuration outside the installation

**File:** `main.py`  
**Methods:** replace `_save_interpreter` and `_load_interpreter`; stop editing bundled theme JSON

```python
def _save_interpreter(self, path: str):
    self.settings.setValue("interpreter", path)
    self.settings.sync()

def _load_interpreter(self) -> str:
    value = self.settings.value("interpreter", sys.executable, type=str)
    return value if value and Path(value).is_file() else sys.executable
```

For theme customization, copy the selected built-in theme to a per-user directory first:

```python
def _user_theme_path(self, name: str) -> Path:
    root = Path(QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation))
    root.mkdir(parents=True, exist_ok=True)
    target = root / name
    if not target.exists():
        shutil.copy2(self._theme_path(name), target)
    return target
```

Make `_write_theme_editor` write this returned path and make `_active_theme_path` prefer the user copy. Bundled themes remain immutable defaults.

### BUG-014 — Recover from corrupt legacy settings JSON

**File:** `main.py`  
**Method:** if retaining JSON temporarily, replace `_load_interpreter`

```python
def _load_interpreter(self) -> str:
    settings_path = Path(__file__).resolve().parent / "settings.json"
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        interpreter = data.get("interpreter")
        if isinstance(interpreter, str) and Path(interpreter).is_file():
            return interpreter
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
        pass
    return sys.executable
```

BUG-013's QSettings replacement is preferred; this snippet is the safe migration bridge.

### BUG-015 — Return only the newest Jedi result

**Files:** `code_inteligence/autocompleter.py` and the other Jedi helpers  
**Methods:** add request generation/pending replay; update receiving slots

```python
class AutoCompleter(QThread):
    completions_ready = pyqtSignal(int, list)

    def __init__(self, file_path, api=None):
        super().__init__(None)
        self.file_path = file_path
        self.api = api
        self._generation = 0
        self._running_generation = 0
        self._pending = None
        self.finished.connect(self._start_pending)

    def get_completions(self, line, index, text):
        self._generation += 1
        request = (self._generation, line, index, text)
        if self.isRunning():
            self._pending = request
            return
        self._start_request(request)

    def _start_request(self, request):
        generation, line, index, text = request
        lines = text.splitlines() or [""]
        self._running_generation = generation
        self.line = max(1, min(line, len(lines)))
        self.index = max(0, min(index, len(lines[self.line - 1])))
        self.text = text
        self.start()

    def _start_pending(self):
        if self._pending is not None:
            request, self._pending = self._pending, None
            self._start_request(request)

    def run(self):
        try:
            names = [item.name for item in Script(
                code=self.text, path=self.file_path
            ).complete(self.line, self.index)]
            self.completions_ready.emit(self._running_generation, names)
        except Exception as error:
            self.error.emit(str(error))
```

**File:** `python_editor/pythoneditor.py`

```python
def _apply_completions(self, generation, names):
    if self._shutting_down or generation != self.auto_completer._generation:
        return
    self._api.clear()
    for name in names:
        self._api.add(name)
    self._api.prepare()
    self.autoCompleteFromAPIs()
```

Apply the same `(generation, queried_position, result)` contract to definition, hover, and signature helpers. Before displaying, compare the result's generation and queried cursor/mouse position with the latest request.

### BUG-016 — Shut down every helper with bounded waits

**File:** `python_editor/pythoneditor.py`  
**Method:** replace the helper portion of `shutdown`

```python
def shutdown(self):
    if self._shutting_down:
        return
    self._shutting_down = True
    self._loading_text = True

    if getattr(self, "ruff_lsp", None) is not None:
        self.ruff_lsp.shutdown()

    for name in (
        "auto_completer", "definition_finder",
        "hover_helper", "signature_helper",
    ):
        worker = getattr(self, name, None)
        if worker is None:
            continue
        if hasattr(worker, "shutdown"):
            worker.shutdown()
        else:
            worker.requestInterruption()
            if worker.isRunning() and not worker.wait(2000):
                print(f"Timed out stopping {name}")
```

Set `self.ruff_lsp = None` before the `if self.is_python_file` branch in `__init__`. Give every helper an idempotent `shutdown()` with a 2-second maximum and ensure worker slots suppress emissions after `_shutting_down`.

## Medium-severity fixes

### BUG-017 — Assign a unique URI to every untitled Python tab

**File:** `ruff_implementation/ruff_lsp_controller.py`  
**Method:** use BUG-010's `_make_uri`

```python
from uuid import uuid4

def _make_uri(self, path):
    if path is None:
        return f"untitled:markdown-editor/{uuid4().hex}.py"
    return Path(path).resolve().as_uri()
```

The URI is created once in `__init__`, not recomputed on each property access. Two unsaved tabs therefore cannot overwrite one another in Ruff. `relocate()` replaces it with a file URI after Save As.

### BUG-018 — Restart Ruff when its interpreter or lifecycle changes

**File:** `ruff_implementation/ruff_lsp_controller.py`  
**Method:** add `reset_server_lifecycle`

```python
def reset_server_lifecycle(self):
    if self._closed:
        return
    self.change_timer.stop()
    self._opened = False
    self.document_version += 1
    self.view.clear()
    self.open_document()
```

**File:** `main.py`  
**Method:** add `_restart_ruff`; call it after interpreter changes

```python
def _restart_ruff(self, interpreter: str):
    old_client = self.ruff_lsp_client
    old_client.shutdown()

    self.ruff_lsp_client = RuffLspClient(
        python_executable=interpreter,
        workspace_root=Path(self.file_manager.model.rootPath()),
        parent=self,
    )
    self.ruff_lsp_client.server_error.connect(self._on_ruff_lsp_error)

    for editor in self.tab_view.all_editors():
        if not isinstance(editor, PythonEditor):
            continue
        editor.ruff_lsp.client = self.ruff_lsp_client
        self.ruff_lsp_client.server_ready.connect(
            editor.ruff_lsp.open_document
        )
        self.ruff_lsp_client.diagnostics_published.connect(
            editor.ruff_lsp._on_diagnostics_published
        )
        editor.ruff_lsp.reset_server_lifecycle()

    self.ruff_lsp_client.start()
```

Prefer extracting controller signal wiring into `set_client(new_client)` so the old client is disconnected first. Call `_restart_ruff(path)` from `choose_interpreter` and Settings only when the value changed.

### BUG-019 — Make Ruff shutdown asynchronous

**File:** `ruff_implementation/ruff_lsp_client.py`  
**Methods:** `__init__`, `_on_finished`, and replace `shutdown`

```python
# In __init__:
self._shutting_down = False
self._shutdown_timer = QTimer(self)
self._shutdown_timer.setSingleShot(True)
self._shutdown_timer.timeout.connect(self._force_stop)

def shutdown(self):
    if self._shutting_down or self.process.state() == QProcess.NotRunning:
        return
    self._shutting_down = True
    self._shutdown_timer.start(2000)

    if self._initialized:
        self.request("shutdown", {}, self._after_shutdown)
    else:
        self._force_stop()

def _after_shutdown(self, _result, _error):
    if self.process.state() == QProcess.Running:
        self.notify("exit", {})

def _force_stop(self):
    if self.process.state() != QProcess.NotRunning:
        self.process.kill()

def _on_finished(self, exit_code, _exit_status):
    self._shutdown_timer.stop()
    self._initialized = False
    self._started = False
    if not self._shutting_down:
        self.server_error.emit(f"Ruff server stopped with exit code {exit_code}.")
```

Add `QTimer` to the import. The Qt event loop remains free to read Ruff's response and execute the callback that sends `exit`.

### BUG-020 — Harden Ruff framing and failed requests

**File:** `ruff_implementation/ruff_lsp_client.py`  
**Methods:** `request`, `_send`, `_read_stdout`, `_on_initialized`

```python
MAX_MESSAGE_BYTES = 16 * 1024 * 1024

def request(self, method, params, callback):
    request_id = self._next_request_id
    self._next_request_id += 1
    self._pending_requests[request_id] = callback
    if not self._send({
        "jsonrpc": "2.0", "id": request_id,
        "method": method, "params": params,
    }):
        self._pending_requests.pop(request_id, None)
        callback(None, {"message": "Ruff server is not running"})

def _send(self, payload):
    if self.process.state() != QProcess.Running:
        return False
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return self.process.write(header + body) >= 0
```

In `_read_stdout`, replace the length conversion:

```python
try:
    content_length = int(value.strip())
except ValueError:
    self.server_error.emit("Ruff sent an invalid Content-Length")
    self._read_buffer.clear()
    return
if not 0 <= content_length <= self.MAX_MESSAGE_BYTES:
    self.server_error.emit("Ruff message exceeded the size limit")
    self._read_buffer.clear()
    return
```

On initialization error:

```python
if error is not None:
    self.server_error.emit(f"Ruff initialize failed: {error}")
    self.process.kill()
    self._started = False
    self._initialized = False
    return
```

This prevents uncaught conversion failures, unbounded message allocation, stuck startup state, and leaked callbacks.

### BUG-021 — Put Ruff configuration at the workspace root

**File:** move `ruff_implementation/pyproject.toml` to root `pyproject.toml`

```toml
[tool.ruff]
target-version = "py310"
line-length = 100
extend-exclude = [
  "resources_rc.py",
  "python_editor/lexer_fast.c",
  "build",
  "dist",
  ".venv",
  "venv",
]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "RUF"]
```

Merge this with GAP-001's packaging metadata in one root `pyproject.toml`. Ruff's server starts at the repository/workspace root, so normal filesystem discovery will now find the configuration.

### BUG-022 — Fix Python outline type and column handling

**File:** `side_bar_widgets/code_outline.py`  
**Methods:** `update_outline`, `_on_item_clicked`

```python
elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
    function_item = QTreeWidgetItem(self)
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    function_item.setText(0, f"{prefix} {node.name}()")
    function_item.setData(
        0, Qt.UserRole, (node.lineno - 1, node.col_offset)
    )
    function_item.setForeground(0, self._color("#61afef"))

def _on_item_clicked(self, item, _column):
    position = item.data(0, Qt.UserRole)
    if position is not None:
        line, column = position
        self.symbol_clicked.emit(line, column)
```

The `isinstance` call now tests the AST node and navigation uses the stored source column rather than the tree widget's column.

### BUG-023 — Wire Enter and Shift+Enter correctly

**File:** `code_inteligence/find_replace.py`  
**Method:** replace the first part of `_connect_signals`

```python
self.find_input.returnPressed.connect(self._on_find_next)

previous_shortcut = QShortcut(QKeySequence("Shift+Return"), self.find_input)
previous_shortcut.setContext(Qt.WidgetShortcut)
previous_shortcut.activated.connect(self._on_find_prev)

previous_keypad_shortcut = QShortcut(
    QKeySequence("Shift+Enter"), self.find_input
)
previous_keypad_shortcut.setContext(Qt.WidgetShortcut)
previous_keypad_shortcut.activated.connect(self._on_find_prev)
```

Remove the Up/Down shortcuts. Arrow keys return to ordinary text-field navigation while the documented keys perform search.

### BUG-024 — Start backward search immediately before the caret

**File:** `main.py`  
**Method:** replace `_do_find_prev`

```python
def _do_find_prev(self, text, case_sensitive, whole_word, regex):
    editor = self.current_editor()
    if editor is None:
        return

    line, index = editor.getCursorPosition()
    if editor.hasSelectedText():
        line, index, _line_to, _index_to = editor.getSelection()

    if index > 0:
        index -= 1
    elif line > 0:
        line -= 1
        index = max(0, editor.lineLength(line) - 1)
    else:
        line, index = -1, -1  # QScintilla wrap search from document end

    editor.findFirst(
        text, regex, case_sensitive, whole_word,
        True, False, line, index, True, False,
    )
```

Only the logical character before the current match/caret is skipped. Column zero moves to the previous line rather than producing mismatched negative coordinates.

### BUG-025 — Replace only the active matching selection

**File:** `main.py`  
**Method:** replace `_do_replace`

```python
def _selection_matches(self, selected, query, case_sensitive, whole_word, regex):
    import re
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = query if regex else re.escape(query)
    if whole_word:
        pattern = rf"\b(?:{pattern})\b"
    try:
        return re.fullmatch(pattern, selected, flags) is not None
    except re.error:
        return False

def _do_replace(self, find_text, replace_text,
                case_sensitive, whole_word, regex):
    editor = self.current_editor()
    if editor is None:
        return
    selected = editor.selectedText() if editor.hasSelectedText() else ""
    if self._selection_matches(
        selected, find_text, case_sensitive, whole_word, regex
    ):
        editor.replace(replace_text)
    self._do_find_next(find_text, case_sensitive, whole_word, regex)
```

For Replace All, reject a regex that matches an empty string before entering the loop. This prevents replacing unrelated selections and zero-width infinite loops.

### BUG-026 — Search Markdown and text files

**File:** `side_bar_widgets/fuzzy_searcher.py`  
**Method:** `SearchWorker.search`

```python
exclude_files = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico",
    ".exe", ".dll", ".pyd", ".so", ".pyc", ".qm",
}
```

Remove `.md` and `.txt`. The existing NUL-byte check still rejects unknown binary files.

### BUG-027 — Keep virtual environments excluded

**File:** `side_bar_widgets/fuzzy_searcher.py`  
**Method:** `SearchWorker.search`

```python
exclude_dirs = {
    ".git", ".svn", ".hg", ".bzr", ".idea", ".vscode",
    "__pycache__", "venv", ".venv", "env", "build", "dist",
}
# Do not remove `venv` when search_project is true.
```

If `search_project=False` is intended to mean “current file,” branch to a single-file search explicitly rather than changing directory exclusions.

### BUG-028 — Replay the newest search and always finish

**File:** `side_bar_widgets/fuzzy_searcher.py`  
**Class:** `SearchWorker`

```python
def __init__(self):
    super().__init__(None)
    self._pending = None
    self._generation = 0
    self.finished.connect(self._run_pending)

def update(self, pattern, path, search_project):
    self._generation += 1
    request = (self._generation, pattern, path, search_project)
    if self.isRunning():
        self._pending = request
        return
    self._start_request(request)

def _start_request(self, request):
    self.generation, self.search_text, self.search_path, \
        self.search_project = request
    self.start()

def _run_pending(self, *_args):
    if self._pending is not None:
        request, self._pending = self._pending, None
        self._start_request(request)
```

Wrap each file:

```python
try:
    if self.is_binary(full_path):
        continue
    with open(full_path, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle):
            match = reg.search(line)
            if match:
                self.items.append(SearchItem(
                    Path(full_path).name, full_path, line_number,
                    match.end(), line[match.start():].strip()[:50],
                ))
except (OSError, UnicodeError):
    continue
```

Emit `(generation, items)` from a distinct result signal and let the UI ignore older generations. Put final emission in `finally`, and display `i + 1` while retaining zero-based `i` separately for navigation.

### BUG-029 — Separate footnotes from link definitions

**File:** `markdown_editor/markdowncustomlexer.py`  
**Methods:** `_is_footnote_def`; add `_is_link_def`

```python
@staticmethod
def _is_footnote_def(stripped: str) -> bool:
    return bool(re.match(r"^\[\^[^\]]+\]:", stripped))

@staticmethod
def _is_link_def(stripped: str) -> bool:
    return bool(re.match(r"^\[(?!\^)[^\]]+\]:", stripped))
```

Style `_is_link_def` with the link/reference style rather than the footnote style. Requiring the caret prevents ordinary reference definitions from entering the footnote branch.

### BUG-030 — Require table structure, not merely a pipe

**File:** `markdown_editor/markdowncustomlexer.py`  
**Method:** replace `_is_table_row`

```python
@staticmethod
def _is_table_row(stripped: str) -> bool:
    if "|" not in stripped:
        return False
    # Remove escaped pipes and inline-code spans before counting cells.
    visible = re.sub(r"\\\|", "", stripped)
    visible = re.sub(r"`[^`]*`", "", visible)
    cells = [cell.strip() for cell in visible.strip("|").split("|")]
    return len(cells) >= 2 and any(cells)
```

In block classification, only mark a normal row as a table when the next/previous logical row is a valid `_is_table_separator`. The helper alone filters obvious prose, while adjacency provides actual Markdown table semantics.

### BUG-031 — Enforce CommonMark fence indentation

**File:** `markdown_editor/markdowncustomlexer.py`  
**Methods:** `_fence_line`, `_is_closing_fence`

```python
@staticmethod
def _fence_prefix(line: str):
    spaces = len(line) - len(line.lstrip(" "))
    if spaces > 3 or line.startswith("\t"):
        return None
    return line[spaces:]

def _fence_line(self, line):
    stripped = self._fence_prefix(line)
    if not stripped or stripped[0] not in ("`", "~"):
        return None, 0
    char = stripped[0]
    length = len(stripped) - len(stripped.lstrip(char))
    return (char, length) if length >= 3 else (None, 0)
```

Use `_fence_prefix` in `_is_closing_fence` too. Four-space indented code and tab-indented content will no longer open or close a fenced block.

### BUG-032 — Persist multiline HTML-comment state

**File:** `markdown_editor/markdowncustomlexer.py`  
**Methods:** extend `_state_before` and the per-line styling loop

```python
def _html_comment_before(self, full_text: str, start_char: int) -> bool:
    prefix = full_text[:start_char]
    opened = prefix.rfind("<!--")
    closed = prefix.rfind("-->")
    return opened > closed
```

At the beginning of `styleText`:

```python
in_html_comment = self._html_comment_before(full_text, start_char)
```

In the line scanner, before other Markdown rules:

```python
if in_html_comment:
    close = line.find("-->")
    if close < 0:
        self._style_span(line, self.HTML_COMMENT)  # use the existing span helper/style
        continue
    self._style_span(line[:close + 3], self.HTML_COMMENT)
    line = line[close + 3:]
    in_html_comment = False

while "<!--" in line:
    open_at = line.find("<!--")
    close_at = line.find("-->", open_at + 4)
    if close_at < 0:
        # Style from open_at through end and carry state to the next line.
        in_html_comment = True
        break
    # Style open_at:close_at + 3 with the existing HTML-comment style.
    line = line[close_at + 3:]
```

Adapt `_style_span` to the lexer's existing `startStyling`/`setStyling` helpers. The essential change is computing and carrying comment state before classifying subsequent lines.

### BUG-033 — Repair Hacker Mode and scanline painting

**File:** `cozy/overlays.py`  
**Method:** rename `painterEvent`

```python
def paintEvent(self, _event):
    painter = QPainter(self)
    line = QColor(0, 0, 0, 28)
    for y in range(0, self.height(), 3):
        painter.fillRect(0, y, self.width(), 1, line)
```

**File:** `main.py`  
**Method:** replace `set_hacker_mode`

```python
def set_hacker_mode(self, on: bool):
    from cozy.overlays import ScanlineOverlay

    if not hasattr(self, "scanlines"):
        self.scanlines = ScanlineOverlay(self)
        self.scanlines.setGeometry(self.rect())

    self._hacker = bool(on)
    self.scanlines.setVisible(self._hacker)
    if self._hacker:
        self.scanlines.raise_()

    settings = self._load_settings()
    settings["theme"] = "hacker.json" if self._hacker else "theme.json"
    self._save_settings(settings)
    self._apply_settings(settings)
    self.statusBar().showMessage(
        "HACK THE PLANET" if self._hacker else "Back to reality", 2500
    )
```

Also resize the overlay from `MainWindow.resizeEvent` or an event filter. The corrected Qt method is invoked, the attribute name is consistent, and `_hacker` makes the next toggle invert correctly.

### BUG-034 — Represent “theme paper” as no override

**File:** `code_settings/settings_dialog.py`  
**Methods:** `_reset_paper_color`, `get_settings`

```python
# In __init__ / appearance setup:
self._paper_override = self.settings.get("paper_color") or None
self._paper_color = QColor(
    self._paper_override or self.settings.get("theme_paper", "#1e1f22")
)

def _pick_paper_color(self):
    color = QColorDialog.getColor(self._paper_color, self, "Editor background color")
    if color.isValid():
        self._paper_color = color
        self._paper_override = color.name()
        self._refresh_paper_swatch()

def _reset_paper_color(self):
    self._paper_override = None
    self._paper_color = QColor(self.settings.get("theme_paper", "#1e1f22"))
    self._refresh_paper_swatch()

# In get_settings:
"paper_color": self._paper_override,
```

**File:** `main.py`, `_save_settings`

```python
paper = new_settings.get("paper_color")
if paper is None:
    self.settings.remove("paper_color")
else:
    self.settings.setValue("paper_color", paper)
```

`None` now means “resolve the current theme's paper,” so changing themes after Reset changes the background too.

### BUG-035 — Anchor resources to source, not the current directory

**Files:** `main.py`, `cozy/neko.py`, `cozy/cat_controller.py`  
**Function:** replace each `_resource_path`/`resource_path`

```python
def resource_path(relative_path: str) -> str:
    if hasattr(sys, "_MEIPASS"):
        root = Path(sys._MEIPASS)
    else:
        # In cozy modules use `.parent.parent`; in main.py use `.parent`.
        root = Path(__file__).resolve().parent
    return str(root / relative_path)
```

For `cozy/neko.py` and `cozy/cat_controller.py`, set source `root = Path(__file__).resolve().parent.parent`. Starting the app from another directory no longer changes asset resolution.

### BUG-036 — Remove `..` from frozen lexer resource paths

**File:** `python_editor/custompythonlexer.py`  
**Function/constructor:** replace `_resource_path` and the default theme expression

```python
def _resource_path(relative_path: str) -> str:
    root = (Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS")
            else Path(__file__).resolve().parent.parent)
    return str(root / relative_path)

# NeutronLexer.__init__
self.theme = theme or _resource_path("themes/theme.json")
```

The frozen path stays inside `_MEIPASS`, while source execution resolves from the repository root.

### BUG-037 — Make and register the Qt resource bundle

**File:** `icons/resources.qrc`

```xml
<!DOCTYPE RCC>
<RCC version="1.0">
  <qresource prefix="/icons">
    <file>folder-icon-blue.svg</file>
    <file>close-icon.svg</file>
    <file>search-icon.svg</file>
  </qresource>
</RCC>
```

**File:** `side_bar_widgets/file_manager.py`

```python
dialog.setWindowIcon(QIcon(":/icons/close-icon.svg"))
```

Regenerate from the repository root:

```bash
pyrcc5 icons/resources.qrc -o resources_rc.py
```

**File:** `main.py`, imports

```python
import resources_rc  # noqa: F401 -- registers :/icons resources
```

Every QRC source now exists and importing the generated module registers its resource initializer before dialogs are built.

### BUG-038 — Make theme generation location-independent

**File:** `themes/theme_factory.py`  
**Constants/function:** add `THEMES_DIR`; replace path creation

```python
THEMES_DIR = Path(__file__).resolve().parent

def make_theme(name: str, palette: dict, src: Path | None = None):
    source = src or THEMES_DIR / "theme.json"
    theme = json.loads(source.read_text(encoding="utf-8"))
    # existing recoloring loop
    output = THEMES_DIR / f"{name}.json"
    output.write_text(json.dumps(theme, indent=2) + "\n", encoding="utf-8")
    return output
```

Rename the tracked files to `sunset.json`, `midnight.json`, and `bloodmoon.json`, or change `PALETTES` keys to the underscore names—but use one convention. Add/commit `forest.json` only if it is an intended built-in theme.

### BUG-039 — Make Cython builds portable and reproducible

**File:** `python_editor/setup.py`  
**Replace file body:**

```python
from pathlib import Path

from Cython.Build import cythonize
from setuptools import Extension, setup

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "python_editor" / "lexer_fast.pyx"

extensions = [
    Extension("lexer_fast", [str(SOURCE)])
]

setup(
    name="markdown-editor-lexer",
    ext_modules=cythonize(
        extensions,
        compiler_directives={
            "boundscheck": False,
            "wraparound": False,
            "cdivision": True,
            "language_level": "3",
        },
    ),
)
```

Build per target ABI in CI with `python python_editor/setup.py build_ext --inplace`. Do not treat the CPython 3.14 Windows `.pyd` as portable; publish wheel/release artifacts or rely on the corrected fallback.

### BUG-040 — Emit runner state from real QProcess signals

**File:** `python_editor/python_runner.py`  
**Class initialization and argument parsing**

```python
class PythonRunner(QObject):
    output_ready = pyqtSignal(str)
    error_ready = pyqtSignal(str)
    process_started = pyqtSignal()
    process_finished = pyqtSignal(int)
    state_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.started.connect(self.process_started)
        self.process.errorOccurred.connect(self._on_process_error)
        # keep existing stdout/stderr/finished connections

    def _on_process_error(self, _error):
        self.error_ready.emit(self.process.errorString())
```

Remove all manual `self.process_started.emit()` calls. Parse user argument strings with:

```python
arg_list = ["-u", str(path)] + shlex.split(
    args, posix=(sys.platform != "win32")
)
```

Use `terminate()` plus an asynchronous kill timer instead of blocking the GUI in `waitForFinished()`.

### BUG-041 — Parse Git porcelain as NUL-delimited bytes

**File:** `python_editor/git_integration.py`  
**Methods:** allow binary `_git`; replace status parsing

```python
def _git(self, *args, text=True):
    result = subprocess.run(
        ["git", *args], cwd=self.repo_path, capture_output=True,
        text=text, encoding="utf-8" if text else None,
        errors="surrogateescape" if text else None,
        timeout=5, check=False,
    )
    return result.stdout if result.returncode == 0 else None

output = self._git("status", "--porcelain=v1", "-z", text=False)
if output is None:
    self.status_ready.emit({})
    return

records = output.split(b"\0")
statuses = {}
i = 0
while i < len(records) and records[i]:
    record = records[i]
    code = record[:2].decode("ascii", "replace")
    path_bytes = record[3:]
    if code[0] in {"R", "C"}:
        i += 1
        if i >= len(records):
            break
        path_bytes = records[i]  # destination in -z porcelain v1
    relative = path_bytes.decode("utf-8", "surrogateescape")
    statuses[str((repo_root / relative).resolve())] = code
    i += 1
```

NUL framing handles quotes, newlines, escapes, and literal ` -> ` safely.

### BUG-042 — Convert tabs through the normal editor factory

**File:** `main.py`  
**Method:** in `_convert_current_tab`, replace construction/state setup

```python
line, column = old.getCursorPosition()
first_visible = old.firstVisibleLine()

new_editor = self.get_editor(
    path=path,
    is_python_file=(EditorClass is PythonEditor),
)
new_editor.setTextSafely(text)
self._connect_editor(new_editor)

# Existing tab replacement code follows.
new_editor.setCursorPosition(line, column)
new_editor.setFirstVisibleLine(first_visible)
```

Change `get_editor` so an explicit `is_python_file` is not overwritten by the suffix:

```python
if is_python_file is None and path is not None:
    is_python_file = Path(path).suffix.lower() in {".py", ".pyw", ".pyi"}
elif is_python_file is None:
    is_python_file = self.python_editor_active
```

The factory injects the shared Ruff client and applies current settings. Cursor/scroll are restored; undo history cannot safely be migrated across widgets and should be documented as reset.

### BUG-043 — Save and validate session data

**File:** `main.py`  
**Method:** replace `save_session` body after the preference guard

```python
groups = list(self.tab_view.groups())
tabs = []
for editor in self.tab_view.all_editors():
    path = getattr(editor, "path", None)
    if path is None:
        continue
    group = self.tab_view.group_for_editor(editor)
    tabs.append({
        "path": str(Path(path).resolve()),
        "python": isinstance(editor, PythonEditor),
        "group": groups.index(group) if group in groups else 0,
    })

active = self.current_editor()
active_path = getattr(active, "path", None)
self.settings.setValue("session", json.dumps({
    "tabs": tabs,
    "active": str(Path(active_path).resolve()) if active_path else None,
    "python_mode": bool(self.python_editor_active),
}))
```

Validate before restore:

```python
tabs = session.get("tabs")
if not isinstance(tabs, list) or len(tabs) > 100:
    return
valid_tabs = []
for entry in tabs:
    if not isinstance(entry, dict):
        continue
    path = entry.get("path")
    group = entry.get("group", 0)
    python_mode = entry.get("python", False)
    if (isinstance(path, str) and isinstance(group, int)
            and 0 <= group < 20 and isinstance(python_mode, bool)):
        valid_tabs.append({"path": path, "group": group,
                           "python": python_mode})
```

Use `valid_tabs`, pass its explicit mode through the corrected `get_editor` logic from BUG-042, write once even for zero tabs, and call `save_session()` from BUG-002's successful close path.

### BUG-044 — Preserve EOLs and save atomically

**File:** `main.py`  
**Methods:** add `_save_editor_to_path`; call it from Save, Save As, and Save All

```python
import os
import tempfile

def _editor_bytes(self, editor) -> bytes:
    text = editor.text()
    mode = editor.eolMode()
    if mode == QsciScintilla.EolWindows:
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    elif mode == QsciScintilla.EolMac:
        text = text.replace("\r\n", "\n").replace("\n", "\r")
    else:
        text = text.replace("\r\n", "\n")
    return text.encode("utf-8")

def _save_editor_to_path(self, editor, path: Path) -> bool:
    path = Path(path)
    temporary = None
    try:
        data = self._editor_bytes(editor)
        data = self._format_python_bytes(path, data)  # BUG-008
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            os.chmod(temporary, path.stat().st_mode)
        os.replace(temporary, path)
        temporary = None
    except (OSError, RuntimeError) as error:
        QMessageBox.critical(self, "Save File", f"Could not save {path}:\n{error}")
        return False
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    self.mark_editor_clean(editor)
    return True
```

When loading a file, detect its original newline bytes and set the editor's EOL mode accordingly. Replace every direct `path.write_bytes(...)` with this one helper.

## Low-severity fixes

### BUG-045 — Use Markdown-relevant completions and apply theme last

**File:** `markdown_editor/markdowneditor.py`  
**Method:** `MarkdownEditor.__init__`

Replace the Python keyword/module API population:

```python
self.api = QsciAPIs(self.md_lexer)
fence = "`" * 3
for token in (
    "# ", "## ", "### ", "- ", "1. ", "> ",
    fence, "[text](url)", "![alt](url)", "**bold**", "*italic*",
):
    self.api.add(token)
self.api.prepare()
self.md_lexer.setAPIs(self.api)

self.setLexer(self.md_lexer)
self.setMarginType(0, QsciScintilla.NumberMargin)
self.setMarginWidth(0, "000")
self._apply_theme_editor_style()
```

Remove `keyword`, `pkgutil`, and `dir(__builtins__)`. Markdown completion becomes relevant to the document type, and applying the theme after `setLexer`/margin setup prevents those operations from resetting editor-wide colors.

### BUG-046 — Add the missing file-manager action and honor selection

**File:** `side_bar_widgets/file_manager.py`  
**Methods:** `show_context_menu`, `action_new_folder`, `action_open_in_file_manager`

```python
# In show_context_menu:
open_in_manager = menu.addAction("Open In File Manager")
# After `action = menu.exec_(...)` and the existing Rename/Delete branches:
elif action is open_in_manager:
    self.action_open_in_file_manager(ix)
```

```python
def action_new_folder(self, index=None):
    parent = Path(self.model.rootPath())
    if index is not None and index.isValid():
        selected = Path(self.model.filePath(index))
        parent = selected if selected.is_dir() else selected.parent

    folder = parent / "New Folder"
    count = 1
    while folder.exists():
        folder = parent / f"New Folder{count}"
        count += 1
    folder.mkdir()
    self.edit(self.model.index(str(folder)))
```

Call `self.action_new_folder(ix)`. For reveal on Windows:

```python
if sys.platform == "win32":
    args = ["explorer", str(path)] if is_dir \
        else ["explorer", f"/select,{path}"]
    subprocess.Popen(args)
```

The menu action becomes reachable, folder creation uses the clicked directory, and subprocess arguments avoid shell-string quoting.

### BUG-047 — Pass the caret as Scintilla `lParam`

**File:** `python_editor/pythoneditor.py`  
**Method:** `_on_signature_ready`

```python
caret = self.SendScintilla(2008)
x = self.SendScintilla(2164, 0, caret)
y = self.SendScintilla(2165, 0, caret)
point = self.mapToGlobal(QPoint(x, y - 20))
QToolTip.showText(point, signature, self)
```

Scintilla ignores the position when it is passed as `wParam`; the three-argument overload places it in `lParam`, so the tooltip follows the actual caret.

### BUG-048 — Stop lower-level Close All after cancellation

**File:** `code_inteligence/multi_tab_view.py`  
**Method:** change the close-all helper to use a Boolean callback

```python
def close_all_groups(self, close_editor):
    """Return False when the caller cancels closing any editor."""
    for editor in list(self.all_editors()):
        if close_editor(editor) is False:
            return False
    return True
```

Call it as `self.tab_view.close_all_groups(self.close_editor)`. A synchronous Boolean result is required; emitting every close request first cannot honor a later Cancel.

### BUG-049 — Make the cat idle timing match its documentation

**File:** `cozy/neko.py`  
**Constant:** `IDLE_MS`

```python
IDLE_MS = 30_000
```

The wake timer now matches the module and method documentation. If 10 seconds is the intended product behavior, keep `10_000` and update every “30 seconds” label/comment instead.

## Release-engineering gap implementations

These were not assigned BUG numbers in the audit, but closing them is necessary to keep the fixes reproducible.

### GAP-001 — Add a root project manifest

**File:** new root `pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=75", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "markdown-editor"
version = "1.9.2"
requires-python = ">=3.10"
dependencies = [
  "PyQt5>=5.15,<6",
  "QScintilla>=2.14,<3",
  "Markdown>=3.6,<4",
  "jedi>=0.19,<1",
  "ruff>=0.16,<1",
  "packaging>=24",
]

[project.optional-dependencies]
build = ["Cython>=3.0,<4", "pyinstaller>=6,<7"]
test = ["pytest>=8,<9", "pytest-qt>=4.4,<5"]

[project.gui-scripts]
markdown-editor = "main:main"
```

Refactor the current bottom-of-file startup into `def main(): ...` so the GUI entry point can call it. Pin exact dependency versions in a lock file for releases.

### GAP-002 — Add compilation, tests, and asset checks to CI

**File:** new `.github/workflows/ci.yml`

```yaml
name: CI
on: [push, pull_request]

jobs:
  test:
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
        python: ["3.10", "3.12", "3.14"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
      - run: python -m pip install -e ".[test,build]"
      - run: python -m compileall -q .
      - run: python -m ruff check .
      - run: python python_editor/setup.py build_ext --inplace
      - run: python -m pytest -q
```

Add a small test that parses every JSON/SVG/QRC file and decodes every PNG. Run Qt tests headlessly with `QT_QPA_PLATFORM=offscreen` where supported.

### GAP-003 — Mark proposals and implementation status in documentation

**Files:** documents under `Implementations and Updates/`

Add a standard status block to each guide:

```markdown
> Status: Proposal / Partially implemented / Implemented
> Verified against: `<commit>`
> Known deviations: link to issue IDs or the relevant BUG section
```

After implementing this guide, replace references to `_save_session()` with `save_session()` (or rename the method consistently), and only state that Ruff-on-save/dirty-close exists once regression tests prove it.

### GAP-004 — Verify generated files against their sources

**File:** add `scripts/check_generated.py`

```python
from pathlib import Path
import filecmp
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory() as temporary:
    temp = Path(temporary)
    subprocess.run(
        ["pyrcc5", str(ROOT / "icons/resources.qrc"),
         "-o", str(temp / "resources_rc.py")],
        check=True,
    )
    if not filecmp.cmp(temp / "resources_rc.py", ROOT / "resources_rc.py"):
        raise SystemExit("resources_rc.py is stale; regenerate it")
```

For Cython, generate C into a temporary build tree with the pinned Cython version and compare normalized output, or remove generated C/binaries from Git and build them only as release artifacts.

## Minimum regression test matrix

| Area | Required regression |
| --- | --- |
| Compilation | Every `.py` imports/parses; specifically import `ReferencesFinder`. |
| Close/save | Dirty Markdown and Python tabs exercise Save, Discard, and Cancel; no worker remains running. |
| File operations | Rename/delete open files and directories; cancel deletion; drag/drop collision and same-path cases. |
| Lexers | Python fallback and Cython output match across chunk boundaries; Markdown fences/comments/tables/link definitions. |
| Ruff/LSP | Two untitled tabs; Save As; rename; interpreter restart; malformed/partial LSP frames; graceful shutdown. |
| Search | Rapid query replacement returns only the latest result; `.md` and `.txt` included; venv excluded. |
| Terminal | Enter, Backspace, Delete, paste, selection, cursor navigation, and prompt-history protection. |
| Packaging | Source launch from another CWD; frozen resource/theme lookup; build on Windows, Linux, and macOS. |

## Completion checklist

- [ ] BUG-001 through BUG-004 fixed and covered before other release work.
- [ ] One atomic save pipeline handles Save, Save As, Save All, Ruff-on-save, and Run.
- [ ] LSP documents have stable identity and explicit relocation/restart lifecycle.
- [ ] Every worker rejects stale results and shuts down within a bounded time.
- [ ] Resource and theme paths are independent of the process working directory.
- [ ] Generated artifacts are regenerated, not manually edited.
- [ ] `python -m compileall -q .`, Ruff, tests, and the platform build matrix pass.
