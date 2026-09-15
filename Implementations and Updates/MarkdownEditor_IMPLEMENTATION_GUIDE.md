# MarkdownEditor — consolidated implementation guide

**Repository:** <https://github.com/ConfidentCupcake/MarkdownEditor>  
**Code baseline:** `main` at `936ea5b632065d82d76557d57e059bba412e1613`  
**Source audit:** `MarkdownEditor_CURRENT_REPOSITORY_AUDIT.md`  

## How to use this guide

The line numbers below refer to the audited commit. They will move after each edit, so locate later changes by class/method name. Each numbered fix combines bugs that share state or must be implemented together. Do not apply half of a grouped fix.

For every replacement:

1. Create a branch and commit the unmodified baseline.
2. Delete the exact old method/class or stated line range.
3. Paste the complete replacement block shown here at the same indentation level.
4. Apply the listed imports/constants and dependency changes.
5. Run the verification commands at the end of that fix group.

The code blocks are complete replacements for the named section—not fragments to merge line by line.

## Fix order

| Order | Consolidated fix | Audit findings covered |
|---|---|---|
| 1 | Secure Markdown preview | ME-001 |
| 2 | Lossless and conflict-safe file lifecycle | ME-002, ME-003, ME-004, ME-005, ME-013 |
| 3 | Ruff protocol, Unicode, restart, and cat events | ME-007, ME-009, ME-010, ME-027 |
| 4 | Safe asynchronous worker retirement | ME-008 |
| 5 | Python runner and run-command correctness | ME-011, ME-012, ME-014 |
| 6 | Project search rewrite | ME-015, ME-016, ME-017, ME-018 |
| 7 | Terminal process/input lifecycle | ME-019, ME-020, ME-021 |
| 8 | Transaction-safe file-manager operations | ME-006, ME-022 |
| 9 | Consistent regex find/replace | ME-023 |
| 10 | Complete or remove the title bar | ME-024 |
| 11 | Correct Markdown comment styling | ME-025 |
| 12 | Reliable signature-help trigger | ME-026 |
| 13 | User-visible update-check results | ME-028 |
| 14 | Packaging, lint, and CI | QG-001, QG-002, QG-003 |

---

## 1. Secure Markdown preview

### Files and original lines

- `main.py:14-19` — imports.
- `main.py:133-151` — Markdown renderer and preview CSS initialization.
- `main.py:220-225` — preview page initialization.
- `main.py:2414-2426` — `render_preview` and `_on_preview_loaded`.
- `pyproject.toml` — dependencies are completed in Fix 14.

### Delete

- Delete the current `render_preview` and `_on_preview_loaded` methods in full.
- Keep the one-time `preview.setHtml(base)` call in `init_ui`; do **not** call `setHtml()` from `render_preview`, because `loadFinished → render_preview → setHtml → loadFinished` creates a loop.

### Add to the import section of `main.py`

```python
import bleach
```

### Add these constants below `APP_VERSION`

```python
# Markdown is untrusted document content. Only presentation-oriented HTML is
# allowed into the JavaScript-enabled preview. Event attributes, script/style,
# iframes, object/embed, and javascript:/data: URLs are intentionally excluded.
MARKDOWN_ALLOWED_TAGS = frozenset(
    bleach.sanitizer.ALLOWED_TAGS
    | {
        "p", "pre", "code", "h1", "h2", "h3", "h4", "h5", "h6",
        "br", "hr", "blockquote", "ul", "ol", "li", "table", "thead",
        "tbody", "tr", "th", "td", "img", "del", "sup", "sub",
    }
)
MARKDOWN_ALLOWED_ATTRIBUTES = {
    "a": ["href", "title"],
    "img": ["src", "alt", "title"],
    "code": ["class"],
    "th": ["align"],
    "td": ["align"],
}
MARKDOWN_ALLOWED_PROTOCOLS = frozenset({"http", "https", "mailto"})
```

### Replace the preview methods with this complete section

```python
    def _render_markdown_safely(self, source: str) -> str:
        """Convert Markdown and remove active/untrusted HTML.

        Python-Markdown deliberately preserves raw HTML. The preview is a
        QWebEngine page, so raw event handlers or script elements would be
        active content. Bleach applies a small, explicit allowlist before the
        result reaches innerHTML.
        """
        self.md.reset()
        rendered = self.md.convert(source)
        return bleach.clean(
            rendered,
            tags=MARKDOWN_ALLOWED_TAGS,
            attributes=MARKDOWN_ALLOWED_ATTRIBUTES,
            protocols=MARKDOWN_ALLOWED_PROTOCOLS,
            strip=True,
            strip_comments=True,
        )

    def render_preview(self):
        """Render only sanitized Markdown into the already-loaded shell."""
        editor = self.current_editor()
        if not isinstance(editor, MarkdownEditor) or not self._preview_ready:
            return

        safe_body = self._render_markdown_safely(editor.text())
        # json.dumps creates a valid JavaScript string literal. Never interpolate
        # document text directly into JavaScript source.
        payload = json.dumps(safe_body)
        script = f'document.getElementById("content").innerHTML = {payload};'
        self.preview.page().runJavaScript(script)

    def _on_preview_loaded(self, ok: bool):
        """Enable rendering only when the fixed preview shell loaded."""
        self._preview_ready = bool(ok)
        if self._preview_ready:
            self.render_preview()
        else:
            self.statusBar().showMessage("Markdown preview failed to load", 4000)
```

### What this does

The Markdown renderer still supports headings, tables, code, links, and images, but scripts, inline event handlers, unsafe URL schemes, and unknown elements are stripped. `json.dumps` remains necessary even after sanitization because the HTML is transported through a JavaScript string.

### Verify

Open a Markdown tab containing all of the following. None may execute or create a JavaScript link:

```markdown
<script>document.body.textContent = "unsafe"</script>
<img src=x onerror="document.body.textContent='unsafe'">
[unsafe](javascript:alert(1))
```

---

## 2. Lossless and conflict-safe file lifecycle

This is one fix because encoding, disk-conflict detection, symlink semantics, duplicate tabs, and Python-extension selection all depend on the same logical-path/write-target state.

### Files and original lines

- `main.py:1-10` — imports.
- `main.py:35` — application constants.
- `main.py:507-525` — `get_editor`.
- `main.py:1571-1631` — `set_new_tab`.
- `main.py:1973-2024` — `on_file_rename`.
- `main.py:2154-2246` — Ruff-on-save, encoding, and `_save_editor_to_path`.
- `main.py:2276-2324` — `save_as`.

### Add imports and shared constants in `main.py`

```python
import hashlib


PYTHON_SUFFIXES = frozenset({".py", ".pyw", ".pyi"})
UTF8_BOM = b"\xef\xbb\xbf"


def is_python_path(path: Path) -> bool:
    """Return one canonical answer for every editor-mode decision."""
    return Path(path).suffix.lower() in PYTHON_SUFFIXES


def disk_digest(path: Path) -> bytes:
    """Hash current bytes so timestamp-only changes do not cause conflicts."""
    return hashlib.sha256(path.read_bytes()).digest()


def save_target(path: Path) -> Path:
    """Preserve a symlink by atomically replacing its target, not the link."""
    path = Path(path)
    if path.is_symlink():
        # strict=True rejects broken links instead of replacing them silently.
        return path.resolve(strict=True)
    return path
```

### Replace `get_editor` completely

```python
    def get_editor(self, path: Path = None, is_python_file=None) -> QsciScintilla:
        """Create the correct editor using the shared extension policy."""
        if path is not None and is_python_file is None:
            path = Path(path)
            is_python_file = is_python_path(path)
        elif is_python_file is None:
            is_python_file = self.python_editor_active

        if is_python_file:
            editor = PythonEditor(
                path=path,
                is_python_file=True,
                ruff_lsp_client=self.ruff_lsp_client,
            )
        else:
            editor = MarkdownEditor(path=path, is_python_file=False)

        saved = getattr(self, "_current_settings", None)
        if saved:
            self._apply_editor_settings(editor, saved)
        return editor
```

### Replace `set_new_tab` completely

```python
    def set_new_tab(
        self,
        path: Path,
        is_new_file=False,
        target_group=None,
        is_python_file=None,
    ):
        """Open one UTF-8 document without silently changing its bytes."""
        path = Path(path) if path is not None else None
        if is_new_file:
            return self.new_file(target_group=target_group)
        if path is None or not path.is_file():
            return None
        if self.is_binary(path):
            self.statusBar().showMessage("Cannot open binary file", 2000)
            return None

        existing = self.tab_view.find_editor_by_path(path)
        if existing is not None:
            self.tab_view.focus_editor(existing)
            return existing

        editor = self.get_editor(path=path, is_python_file=is_python_file)
        try:
            target = save_target(path)
            raw = target.read_bytes()
            has_bom = raw.startswith(UTF8_BOM)
            text = raw.decode("utf-8-sig")  # strict: never replace bad bytes
        except (OSError, UnicodeDecodeError) as error:
            QMessageBox.critical(
                self,
                "Open File",
                f"Could not open '{path}':\n{error}",
            )
            editor.deleteLater()
            return None

        # File format state belongs to the tab and is refreshed after each save.
        editor._utf8_bom = has_bom
        editor._disk_digest = hashlib.sha256(raw).digest()
        editor._save_target = target

        crlf_count = raw.count(b"\r\n")
        lf_count = raw.count(b"\n") - crlf_count
        cr_count = raw.count(b"\r") - crlf_count
        if crlf_count >= max(lf_count, cr_count) and crlf_count:
            editor.setEolMode(QsciScintilla.EolWindows)
        elif cr_count > max(crlf_count, lf_count):
            editor.setEolMode(QsciScintilla.EolMac)
        else:
            editor.setEolMode(QsciScintilla.EolUnix)

        editor.setTextSafely(text)
        self._connect_editor(editor)
        self.tab_view.add_editor(editor, path.name, target_group)
        self.tab_view.set_editor_tooltip(editor, str(path.absolute()))

        self.current_file = path
        self._add_to_recent_files(str(path))
        if isinstance(editor, PythonEditor):
            self.outline_tree.update_outline(editor.text())
        else:
            self.outline_tree.clear()
        return editor
```

### Replace `_run_ruff_before_save`, `_encode_editor_text`, and `_save_editor_to_path` completely

```python
    def _run_ruff_before_save(self, path: Path, text: str) -> str:
        """Apply Ruff safe fixes/formatting to every supported Python suffix."""
        if self.ruff_save_mode != "safe_format" or not is_python_path(path):
            return text

        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.stem}.",
                suffix=path.suffix,
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(text)

            commands = (
                [self.python_runner.interpreter, "-m", "ruff", "check", "--fix", str(temporary)],
                [self.python_runner.interpreter, "-m", "ruff", "format", str(temporary)],
            )
            for index, command in enumerate(commands):
                result = subprocess.run(
                    command,
                    cwd=str(path.parent),
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )
                allowed = {0, 1} if index == 0 else {0}
                if result.returncode not in allowed:
                    message = result.stderr.strip() or result.stdout.strip() or "Ruff failed"
                    raise RuntimeError(message)
            return temporary.read_text(encoding="utf-8")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _encode_editor_text(editor, text: str) -> bytes:
        """Encode the editor buffer while preserving EOL mode and UTF-8 BOM."""
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        if editor.eolMode() == QsciScintilla.EolWindows:
            normalized = normalized.replace("\n", "\r\n")
        elif editor.eolMode() == QsciScintilla.EolMac:
            normalized = normalized.replace("\n", "\r")
        data = normalized.encode("utf-8")
        return UTF8_BOM + data if getattr(editor, "_utf8_bom", False) else data

    def _save_editor_to_path(
        self,
        editor,
        path: Path,
        *,
        check_external_change: bool = True,
    ) -> bool:
        """Format and atomically save without losing links or newer disk data."""
        logical_path = Path(path)
        try:
            target = save_target(logical_path)
        except OSError as error:
            QMessageBox.critical(self, "Save File", f"Broken symbolic link:\n{error}")
            return False

        if check_external_change and target.exists():
            expected = getattr(editor, "_disk_digest", None)
            try:
                actual = disk_digest(target)
            except OSError as error:
                QMessageBox.critical(self, "Save File", str(error))
                return False
            if expected is not None and actual != expected:
                reply = QMessageBox.warning(
                    self,
                    "File changed on disk",
                    f"'{logical_path.name}' changed outside the editor.\n"
                    "Overwrite the newer disk version?",
                    QMessageBox.Yes | QMessageBox.Cancel,
                    QMessageBox.Cancel,
                )
                if reply != QMessageBox.Yes:
                    return False

        original = editor.text()
        try:
            formatted = self._run_ruff_before_save(logical_path, original)
        except (OSError, subprocess.SubprocessError, RuntimeError) as error:
            reply = QMessageBox.warning(
                self,
                "Ruff on save",
                f"Ruff could not process this file:\n{error}\n\nSave without Ruff?",
                QMessageBox.Save | QMessageBox.Cancel,
                QMessageBox.Save,
            )
            if reply != QMessageBox.Save:
                return False
            formatted = original

        temporary = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            data = self._encode_editor_text(editor, formatted)
            with tempfile.NamedTemporaryFile(
                dir=target.parent,
                prefix=f".{target.name}.",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if target.exists():
                os.chmod(temporary, target.stat().st_mode)
            os.replace(temporary, target)
            temporary = None
        except OSError as error:
            QMessageBox.critical(
                self, "Save File", f"Could not save '{logical_path}':\n{error}"
            )
            return False
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

        if formatted != original:
            editor.setTextSafely(formatted)
        editor._save_target = target
        editor._disk_digest = hashlib.sha256(data).digest()
        self.mark_editor_clean(editor)
        return True
```

### Replace `save_as` completely

```python
    def save_as(self):
        """Save under a new unique logical path and convert editor mode."""
        editor = self.current_editor()
        if editor is None:
            return False

        file_path, _ = QFileDialog.getSaveFileName(self, "Save As", os.getcwd())
        if not file_path:
            self.statusBar().showMessage("Cancelled", 2000)
            return False

        path = Path(file_path)
        owner = self.tab_view.find_editor_by_path(path)
        if owner is not None and owner is not editor:
            QMessageBox.warning(
                self,
                "Save As",
                "That file is already open in another tab.",
            )
            self.tab_view.focus_editor(owner)
            return False

        # The file dialog already handled intentional replacement. The old
        # tab fingerprint belongs to the old path, so do not compare it here.
        if not self._save_editor_to_path(
            editor, path, check_external_change=False
        ):
            return False

        editor.path = path
        editor.full_path = path.absolute()
        self.current_file = path
        self.tab_view.set_editor_tooltip(editor, str(editor.full_path))

        desired_class = PythonEditor if is_python_path(path) else MarkdownEditor
        if not isinstance(editor, desired_class):
            editor = self._convert_editor(editor, desired_class)

        if isinstance(editor, PythonEditor):
            if editor.ruff_lsp is not None:
                editor.ruff_lsp.relocate(path)
            editor.auto_completer.file_path = str(editor.full_path)

        self.mark_editor_clean(editor)
        self._add_to_recent_files(str(path))
        self.statusBar().showMessage(f"Saved {path.name}", 2000)
        return True
```

### Replace `on_file_rename` completely

```python
    def on_file_rename(self, old_path: Path, new_path: Path, is_directory=False):
        """Relocate every affected tab while preserving one-path ownership."""
        old_path = Path(old_path)
        new_path = Path(new_path)

        for editor in list(self.tab_view.all_editors()):
            editor_path = getattr(editor, "path", None)
            if editor_path is None:
                continue
            editor_path = Path(editor_path)

            if is_directory:
                if editor_path == old_path:
                    updated_path = new_path
                elif old_path in editor_path.parents:
                    updated_path = new_path / editor_path.relative_to(old_path)
                else:
                    continue
            elif editor_path == old_path:
                updated_path = new_path
            else:
                continue

            editor.path = updated_path
            editor.full_path = updated_path.absolute()
            try:
                target = save_target(updated_path)
                editor._save_target = target
                editor._disk_digest = disk_digest(target)
            except OSError:
                editor._disk_digest = None

            desired_class = (
                PythonEditor if is_python_path(updated_path) else MarkdownEditor
            )
            converted = not isinstance(editor, desired_class)
            if converted:
                editor = self._convert_editor(editor, desired_class)

            if isinstance(editor, PythonEditor):
                # A newly converted controller was constructed with the new path;
                # an existing Python controller must migrate from its old URI.
                if not converted and editor.ruff_lsp is not None:
                    editor.ruff_lsp.relocate(updated_path)
                editor.auto_completer.file_path = str(editor.full_path)

            title = updated_path.name
            if editor in self._dirty_editors:
                title = f"● {title}"
            self.tab_view.set_editor_title(editor, title)
            self.tab_view.set_editor_tooltip(editor, str(editor.full_path))
            if editor is self.current_editor():
                self.current_file = updated_path
```

### Replace `_convert_editor` completely

```python
    def _convert_editor(self, old, EditorClass):
        """Replace one tab's editor class without losing document state."""
        if old is None or isinstance(old, EditorClass):
            return old
        group = self.tab_view.group_for_editor(old)
        if group is None:
            return None

        index = group.indexOf(old)
        was_current = old is self.current_editor()
        previous_current = group.currentWidget()
        title = group.tabText(index)
        tooltip = group.tabToolTip(index)
        icon = group.tabIcon(index)
        text = old.text()
        path = getattr(old, "path", None)
        was_dirty = old in self._dirty_editors
        line, column = old.getCursorPosition()
        selection = old.getSelection()
        first_visible = old.firstVisibleLine()

        new_editor = self.get_editor(
            path=path, is_python_file=(EditorClass is PythonEditor)
        )
        new_editor.setTextSafely(text)
        # Preserve the disk-format/conflict state introduced by this fix.
        new_editor._utf8_bom = getattr(old, "_utf8_bom", False)
        new_editor._disk_digest = getattr(old, "_disk_digest", None)
        new_editor._save_target = getattr(old, "_save_target", path)
        new_editor.setEolMode(old.eolMode())
        self._connect_editor(new_editor)

        group.blockSignals(True)
        group.removeTab(index)
        group.insertTab(index, new_editor, icon, title)
        group.setTabToolTip(index, tooltip)
        if was_current:
            group.setCurrentIndex(index)
        elif previous_current is not None and previous_current is not old:
            group.setCurrentWidget(previous_current)
        group.blockSignals(False)

        if was_dirty:
            self._dirty_editors.discard(old)
            self._dirty_editors.add(new_editor)
        if hasattr(old, "shutdown"):
            old.shutdown()
        old.setParent(None)
        old.deleteLater()

        new_editor.setCursorPosition(line, column)
        if selection[0] >= 0:
            new_editor.setSelection(*selection)
        new_editor.setFirstVisibleLine(first_visible)
        if was_current:
            new_editor.setFocus()
            self.tab_view.focus_editor(new_editor)
        return new_editor
```

### Verify

- Open/save a UTF-8 file with and without BOM; compare bytes.
- Open/save CRLF and classic-CR fixtures.
- Open a symlink, save it, and assert the link still exists and its target changed.
- Modify an open file externally and assert Save defaults to Cancel.
- Save As to a path already open in another tab and assert it is rejected.
- Open, rename, and Save As `.py`, `.pyw`, and `.pyi`; all must use `PythonEditor` and Ruff-on-save.

---

## 3. Ruff protocol, Unicode, restart, and cat events

### Files and original lines

- `ruff_implementation/ruff_lsp_client.py:42-90`, `210-224`, `273-293`, `406-440`.
- `ruff_implementation/ruff_lsp_controller.py:14-28`, `158-222`, `260-305`.
- `main.py:568-585` and Python-editor connection path.

### Update `RuffLspClient.__init__`

Add these fields after the existing lifecycle flags:

```python
        # LSP defaults to UTF-16 unless the server explicitly negotiates another
        # encoding. Ruff selects UTF-8 from this client's advertised preference.
        self.position_encoding = "utf-16"
        self._restart_attempts = 0
        self._max_restart_attempts = 3
```

### Replace `_on_initialized` completely

```python
    def _on_initialized(self, result, error):
        """Complete initialization and retain negotiated position encoding."""
        if error is not None:
            self.server_error.emit(f"Ruff initialize failed: {error}")
            self._suppress_restart_once = True
            self.process.kill()
            return

        capabilities = (result or {}).get("capabilities", {})
        self.position_encoding = capabilities.get("positionEncoding", "utf-16")
        # Do not reset retries immediately: a server that initializes and then
        # crashes would otherwise retry forever. Reset only after a stable run.
        QTimer.singleShot(60_000, self._reset_retries_if_stable)
        self.notify("initialized", {})
        self._initialized = True
        self.server_ready.emit()

    def _reset_retries_if_stable(self):
        if self.is_ready and not self._shutting_down:
            self._restart_attempts = 0
```

### Replace `request` completely

```python
    def request(self, method: str, params, callback):
        """Send one request; omit params when the method requires no value."""
        request_id = self._next_request_id
        self._next_request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        self._pending_requests[request_id] = callback
        if not self._send(payload):
            self._pending_requests.pop(request_id, None)
            callback(None, {"message": "Ruff server is not running"})
```

### Replace `_on_finished`, `shutdown`, and `_force_stop` completely

```python
    def _on_finished(self, exit_code, _exit_status):
        """Clear state and retry failed startup only a bounded number of times."""
        self._initialized = False
        self._started = False
        self._pending_requests.clear()
        self._read_buffer.clear()
        self.server_stopped.emit()

        if self._suppress_restart_once:
            self._suppress_restart_once = False
            return
        if self._shutting_down:
            return

        self._restart_attempts += 1
        if self._restart_attempts > self._max_restart_attempts:
            self.server_error.emit(
                "Ruff disabled after repeated startup failures. "
                "Check the selected interpreter in Settings."
            )
            return

        delay_ms = min(30_000, 1000 * 2 ** (self._restart_attempts - 1))
        self.server_error.emit(
            f"Ruff stopped with exit code {exit_code}; "
            f"retry {self._restart_attempts}/{self._max_restart_attempts}."
        )
        QTimer.singleShot(delay_ms, self.start)

    def shutdown(self):
        """Perform the LSP shutdown → exit sequence, then enforce a timeout."""
        self._shutting_down = True
        if self.process.state() == QProcess.NotRunning:
            return

        def after_shutdown(_result, error):
            if error is None and self.process.state() == QProcess.Running:
                self.notify("exit", {})
            else:
                self._force_stop()

        if self._initialized:
            # LSP shutdown has no params. Sending {} is invalid for Ruff.
            self.request("shutdown", None, after_shutdown)
            QTimer.singleShot(2000, self._force_stop)
        else:
            self._force_stop()

    def _force_stop(self):
        """Kill only when graceful shutdown did not finish."""
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()
```

### Add a signal and Unicode conversion to `RuffLspController`

Change the Qt import to include `pyqtSignal`:

```python
from PyQt5.QtCore import QObject, QTimer, pyqtSignal
```

Add this directly below the class docstring:

```python
    diagnostics_changed = pyqtSignal(object, list)
```

Replace `_on_diagnostics_published` and `_to_diagnostic` completely:

```python
    def _lsp_column_to_character(self, line: int, column: int) -> int:
        """Translate negotiated LSP units into QScintilla character columns."""
        if line < 0 or line >= self.editor.lines():
            return 0
        value = self.editor.text(line).rstrip("\r\n")
        column = max(0, int(column))
        encoding = getattr(self.client, "position_encoding", "utf-16")
        if encoding == "utf-8":
            raw = value.encode("utf-8")[:column]
            return len(raw.decode("utf-8", errors="ignore"))
        if encoding == "utf-16":
            raw = value.encode("utf-16-le")[: column * 2]
            return len(raw.decode("utf-16-le", errors="ignore"))
        return min(column, len(value))

    def _on_diagnostics_published(self, uri: str, raw_diagnostics, version):
        """Render only current diagnostics belonging to this document."""
        if self._closed or uri != self.uri:
            return
        if version is not None and version != self.document_version:
            return

        diagnostics = [self._to_diagnostic(raw) for raw in raw_diagnostics]
        self.view.render(diagnostics)
        # MainWindow owns the cat/XP state; the controller only reports data.
        self.diagnostics_changed.emit(self.editor, diagnostics)

    def _to_diagnostic(self, raw: dict) -> RuffDiagnostic:
        """Convert one LSP diagnostic, including negotiated column units."""
        raw_range = raw.get("range") or {}
        start = raw_range.get("start") or {}
        end = raw_range.get("end") or start
        start_line = max(0, int(start.get("line", 0)))
        last_line = max(0, self.editor.lines() - 1)
        start_line = min(start_line, last_line)
        end_line = min(
            last_line, max(start_line, int(end.get("line", start_line)))
        )

        severity_by_number = {
            1: RuffSeverity.ERROR,
            2: RuffSeverity.WARNING,
            3: RuffSeverity.INFO,
            4: RuffSeverity.INFO,
        }
        code = raw.get("code", "Ruff")
        if not isinstance(code, str):
            code = str(code)

        return RuffDiagnostic(
            code=code,
            message=str(raw.get("message", "Ruff diagnostic")),
            severity=severity_by_number.get(
                raw.get("severity"), RuffSeverity.WARNING
            ),
            start=RuffPosition(
                start_line,
                self._lsp_column_to_character(
                    start_line, start.get("character", 0)
                ),
            ),
            end=RuffPosition(
                end_line,
                self._lsp_column_to_character(
                    end_line, end.get("character", 0)
                ),
            ),
            revision=self.document_version,
            raw=raw,
        )
```

Delete the old `RuffLspController._on_ruff_diagnostics` method entirely. It is in the wrong class and references nonexistent `self.cat`.

### Add the owner-side reaction in `main.py`

Add this connection to `_connect_editor`, inside the `isinstance(editor, PythonEditor)` branch:

```python
            if editor.ruff_lsp is not None:
                editor.ruff_lsp.diagnostics_changed.connect(
                    self._on_ruff_diagnostics
                )
```

Add this complete method next to `_on_ruff_lsp_error`:

```python
    def _on_ruff_diagnostics(self, editor, diagnostics: list):
        """React once when a Python tab enters or leaves an error state."""
        had_errors = getattr(editor, "_had_ruff_errors", False)
        has_errors = bool(diagnostics)
        if has_errors and not had_errors:
            self.cat.set_state("alert", 2000)
        elif not has_errors and had_errors:
            self.cat.add_xp(5)
            self.cat.set_state("stretch", 1500)
        editor._had_ruff_errors = has_errors
```

### Verify

- Start with an interpreter without Ruff; confirm only three retries occur.
- Live-shutdown Ruff and confirm stderr has no invalid shutdown/early-exit error.
- Place an error after `é`, CJK, and emoji; assert the squiggle selects the exact token.
- Introduce and clear a diagnostic; assert the cat reacts once per transition.

---

## 4. Safe asynchronous worker retirement

The existing `requestInterruption()` calls cannot cancel a synchronous Jedi operation. The safe fix is to stop waiting on the GUI thread while keeping the editor and worker objects alive until the jobs finish.

### Files and original lines

- `code_inteligence/autocompleter.py:62-67`
- `code_inteligence/definition_finder.py:64-69`
- `code_inteligence/hover_helper.py:71-76`
- `code_inteligence/signature_helper.py:66-71`
- `code_inteligence/references_finder.py:59-64`
- `python_editor/git_integration.py:34-38`
- `python_editor/pythoneditor.py:537-565`
- `main.py:80-90`, `1937-1971`, `2637-2679`

### Replace every helper `shutdown` method

For all five Jedi helper classes, delete the `wait(2000)` logic and use this complete method:

```python
    def shutdown(self):
        """Reject future/results requests; completion remains asynchronous."""
        self._shutting_down = True
        self._pending = None
        self.requestInterruption()
```

Replace `GitStatusChecker.shutdown` with:

```python
    def shutdown(self):
        """Request shutdown without blocking the GUI thread."""
        self._shutting_down = True
        self.requestInterruption()
```

### Add lifecycle signals/state to `PythonEditor`

Add below the existing class signals:

```python
    shutdown_complete = pyqtSignal(object)
```

Replace `PythonEditor.shutdown` completely:

```python
    def _analysis_workers(self):
        """Return every per-editor QThread that must outlive the tab."""
        names = (
            "auto_completer",
            "definition_finder",
            "hover_helper",
            "signature_helper",
        )
        return [getattr(self, name) for name in names if hasattr(self, name)]

    def shutdown(self):
        """Stop producing results and emit when all workers naturally finish."""
        if self._shutting_down:
            return
        self._shutting_down = True
        self._loading_text = True
        self._autocomplete_timer.stop()
        for timer_name in ("_hover_timer", "_ruff_hover_timer"):
            timer = getattr(self, timer_name, None)
            if timer is not None:
                timer.stop()

        if self.ruff_lsp is not None:
            self.ruff_lsp.shutdown()

        workers = self._analysis_workers()
        for worker in workers:
            worker.shutdown()
            try:
                worker.finished.connect(self._check_shutdown_complete)
            except TypeError:
                pass
        self._check_shutdown_complete()

    def _check_shutdown_complete(self):
        """Signal exactly when no owned worker is still executing."""
        if self._shutting_down and not any(
            worker.isRunning() for worker in self._analysis_workers()
        ):
            self.shutdown_complete.emit(self)

    def closeEvent(self, event):
        self.shutdown()
        event.accept()
```

### Add retirement state in `MainWindow.__init__`

```python
        self._retired_editors = set()
```

### Add these complete methods near `close_editor`

```python
    def _dispose_editor(self, editor):
        """Delete now, or retain a Python editor until its workers finish."""
        if isinstance(editor, PythonEditor):
            self._retired_editors.add(editor)
            editor.setParent(None)
            editor.shutdown_complete.connect(self._finalize_retired_editor)
            editor.shutdown()
            editor._check_shutdown_complete()
            return
        editor.shutdown()
        editor.setParent(None)
        editor.deleteLater()

    def _finalize_retired_editor(self, editor):
        """Release a retired editor only after all QThreads stopped."""
        if editor not in self._retired_editors:
            return
        self._retired_editors.remove(editor)
        editor.deleteLater()
```

In `close_editor`, delete this old disposal block:

```python
        if hasattr(editor, "shutdown"):
            editor.shutdown()

        editor.setParent(None)
        editor.deleteLater()
```

Replace it with:

```python
        self._dispose_editor(editor)
```

Make the same replacement in `_convert_editor`: delete its `shutdown`,
`setParent(None)`, and `deleteLater()` block, then call
`self._dispose_editor(old)`. This ensures mode conversion cannot destroy a
running Jedi worker either.

### Application shutdown rule

`MainWindow.closeEvent` must not destroy the window while `_retired_editors`, the project search worker, or the Git worker is running. Add a `_close_pending` state and ignore the first close until all workers have naturally finished. The complete control methods are:

```python
    def _background_work_running(self) -> bool:
        git_worker = getattr(getattr(self, "file_manager", None), "git_checker", None)
        search_worker = getattr(self, "search_worker", None)
        runner = getattr(self, "python_runner", None)
        ruff = getattr(self, "ruff_lsp_client", None)
        terminal = getattr(self, "terminal", None)
        return bool(
            self._retired_editors
            or (git_worker is not None and git_worker.isRunning())
            or (search_worker is not None and search_worker.isRunning())
            or (runner is not None and runner.is_running())
            or (
                ruff is not None
                and ruff.process.state() != QProcess.NotRunning
            )
            or (
                terminal is not None
                and terminal.process.state() != QProcess.NotRunning
            )
        )

    def _finish_pending_close(self):
        if self._background_work_running():
            QTimer.singleShot(100, self._finish_pending_close)
            return
        self._close_ready = True
        self.close()

    def closeEvent(self, event):
        if getattr(self, "_close_ready", False):
            event.accept()
            return super().closeEvent(event)

        if not getattr(self, "_close_prompts_complete", False):
            for editor in list(self.tab_view.all_editors()):
                if editor not in self._dirty_editors:
                    continue
                self.tab_view.focus_editor(editor)
                path = getattr(editor, "path", None)
                name = Path(path).name if path is not None else "Untitled"
                reply = QMessageBox.question(
                    self,
                    "Unsaved Changes",
                    f"Save changes to '{name}'?",
                    QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                    QMessageBox.Save,
                )
                if reply == QMessageBox.Cancel:
                    event.ignore()
                    return
                if reply == QMessageBox.Save and not self.save_file():
                    event.ignore()
                    return
            self._close_prompts_complete = True
            self.save_session()

        for editor in list(self.tab_view.all_editors()):
            self.tab_view.remove_editor(editor)
            self._dispose_editor(editor)

        self.terminal.stop()
        if hasattr(self.python_runner, "shutdown"):
            self.python_runner.shutdown()
        else:
            # Compatibility until Fix 5 replaces PythonRunner.
            self.python_runner.stop()
        self.ruff_lsp_client.shutdown()
        if hasattr(self.search_worker, "shutdown"):
            self.search_worker.shutdown()
        self.file_manager.git_checker.shutdown()
        self.settings.setValue("recent_files", self.recent_files)

        if self._background_work_running():
            event.ignore()
            QTimer.singleShot(100, self._finish_pending_close)
            return

        self._close_ready = True
        event.accept()
        super().closeEvent(event)
```

Add a nonblocking `SearchWorker.shutdown` in Fix 6. Ensure `GitStatusChecker.finished` and `SearchWorker.finished` call `_finish_pending_close` when the window is pending; polling above is the fallback.

### Verify

Start slow Jedi searches, close tabs, and immediately close the application. The UI must remain responsive, must not show `QThread: Destroyed while thread is still running`, and must exit after the workers finish.

---

## 5. Python runner and run-command correctness

### File 1: replace `python_editor/python_runner.py` completely

Original file: lines 1-154. Delete the whole file contents and use:

```python
import os
import shlex
import sys
from pathlib import Path

from PyQt5.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, pyqtSignal


def split_user_arguments(value: str) -> list[str]:
    """Parse the documented POSIX-style quoting into a QProcess argument list.

    QProcess receives an argv list directly on every platform, so quotes are
    grouping syntax and must not remain in the child arguments on Windows.
    """
    return shlex.split(value, posix=True)


class PythonRunner(QObject):
    output_ready = pyqtSignal(str)
    error_ready = pyqtSignal(str)
    process_finished = pyqtSignal(int)
    state_changed = pyqtSignal(str)
    process_started = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.interpreter = sys.executable
        self._pending_start = None
        self._shutting_down = False

        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._on_finished)
        self.process.stateChanged.connect(self._on_state_changed)
        self.process.started.connect(self.process_started.emit)
        self.process.errorOccurred.connect(self._on_process_error)

    def _start(self, arguments, cwd: Path, env: QProcessEnvironment):
        if self._shutting_down:
            return
        request = (list(arguments), str(cwd), env)
        if self.is_running():
            self._pending_start = request
            self._stop_process(clear_pending=False)
            return
        self._launch(request)

    def _launch(self, request):
        arguments, cwd, env = request
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(cwd)
        self.process.start(self.interpreter, arguments)

    def _build_env(self, project_root: Path) -> QProcessEnvironment:
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        old_path = env.value("PYTHONPATH", "")
        separator = os.pathsep
        root = str(project_root)
        env.insert("PYTHONPATH", root + (separator + old_path if old_path else ""))
        return env

    def run_file(self, path: Path, cwd: Path = None):
        work_dir = cwd or path.parent
        self._start(["-u", str(path)], work_dir, self._build_env(work_dir))

    def run_code(self, code: str, cwd: Path = None):
        work_dir = cwd or Path.cwd()
        self._start(["-u", "-c", code], work_dir, self._build_env(work_dir))

    def run_file_with_args(self, path: Path, args: str, cwd: Path = None):
        work_dir = cwd or path.parent
        try:
            arguments = ["-u", str(path), *split_user_arguments(args)]
        except ValueError as error:
            self.error_ready.emit(f"Invalid command-line arguments: {error}\n")
            return
        self._start(arguments, work_dir, self._build_env(work_dir))

    def run_pip(self, args: str):
        try:
            arguments = ["-m", "pip", *split_user_arguments(args)]
        except ValueError as error:
            self.error_ready.emit(f"Invalid pip arguments: {error}\n")
            return
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        self._start(arguments, Path.home(), env)

    def send_input(self, text: str):
        if self.process.state() == QProcess.Running:
            self.process.write((text + "\n").encode("utf-8"))

    def stop(self):
        """User-facing stop cancels both current and queued work."""
        self._stop_process(clear_pending=True)

    def _stop_process(self, *, clear_pending: bool):
        if clear_pending:
            self._pending_start = None
        if self.process.state() == QProcess.NotRunning:
            return
        self.process.terminate()
        QTimer.singleShot(2000, self._kill_if_running)

    def _kill_if_running(self):
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()

    def shutdown(self):
        """Application shutdown can never start a queued replacement."""
        self._shutting_down = True
        self._stop_process(clear_pending=True)

    def _read_stdout(self):
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
        self.output_ready.emit(data)

    def _read_stderr(self):
        data = bytes(self.process.readAllStandardError()).decode("utf-8", "replace")
        self.error_ready.emit(data)

    def _on_finished(self, exit_code, _exit_status):
        self.process_finished.emit(exit_code)
        if self._shutting_down or self._pending_start is None:
            return
        request, self._pending_start = self._pending_start, None
        self._launch(request)

    def _on_state_changed(self, state):
        self.state_changed.emit(
            "stopped" if state == QProcess.NotRunning else "running"
        )

    def _on_process_error(self, _error):
        if not self._shutting_down:
            self.error_ready.emit(self.process.errorString() + "\n")

    def set_interpreter(self, path: str):
        self.interpreter = path

    def is_running(self) -> bool:
        return self.process.state() != QProcess.NotRunning
```

### File 2: replace the run-command section in `main.py`

Delete `run_with_arguments`, `run_current_file`, and `run_selection`; add this shared validator and the complete replacements:

```python
    def _current_python_editor(self):
        """Return a runnable Python editor or show one consistent message."""
        editor = self.current_editor()
        if not isinstance(editor, PythonEditor):
            self.statusBar().showMessage(
                "Run commands are available only for Python files", 3000
            )
            return None
        return editor

    def run_with_arguments(self):
        editor = self._current_python_editor()
        if editor is None:
            return
        if editor.path is None:
            if not self.save_as():
                return
            editor = self.current_editor()
            if not isinstance(editor, PythonEditor) or editor.path is None:
                return
        elif not self.save_file():
            self.statusBar().showMessage("Run cancelled: save failed", 4000)
            return

        args, accepted = QInputDialog.getText(
            self,
            "Run with Arguments",
            "Command-line arguments",
            QLineEdit.Normal,
            "",
        )
        if not accepted:
            return
        self.console_dock.show()
        path = Path(editor.path)
        self.python_runner.run_file_with_args(path, args, cwd=path.parent)

    def run_current_file(self):
        editor = self._current_python_editor()
        if editor is None:
            return
        if editor.path is None:
            if not self.save_as():
                return
            editor = self.current_editor()
            if not isinstance(editor, PythonEditor) or editor.path is None:
                return
        elif not self.save_file():
            self.statusBar().showMessage("Run cancelled: save failed", 4000)
            return

        self.console_dock.show()
        path = Path(editor.path)
        self.python_runner.run_file(path, cwd=path.parent)

    def run_selection(self):
        editor = self._current_python_editor()
        if editor is None:
            return
        selected_text = editor.selectedText()
        if not selected_text:
            return
        cwd = Path(editor.path).parent if editor.path is not None else Path.cwd()
        self.console_dock.show()
        self.python_runner.run_code(selected_text, cwd=cwd)
```

### Verify

- On Windows, `--output "my file.txt"` must reach the child as two arguments.
- `--name="A B"` must remain one argument.
- Start run A, request run B, then press Stop; B must not launch.
- Repeat and close the window; B must not launch.
- Run actions in a Markdown tab must be rejected.
- Selected Python code must resolve relative files from its document directory.

---

## 6. Project search rewrite

### Replace `side_bar_widgets/fuzzy_searcher.py` completely

Delete lines 1-131 and use:

```python
import os
import re
from dataclasses import dataclass
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import QListWidgetItem


MAX_RESULTS = 5_000
EXCLUDED_DIRS = {
    ".git", ".svn", ".hg", ".bzr", ".idea", ".vscode",
    "__pycache__", "venv", ".venv", "env", "build", "dist",
}
EXCLUDED_SUFFIXES = {
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico",
    ".exe", ".dll", ".pyd", ".so", ".pyc", ".qm",
}


class SearchItem(QListWidgetItem):
    """One exact match, with start/end columns for correct navigation."""

    def __init__(self, name, full_path, lineno, start, end, line):
        self.name = name
        self.full_path = full_path
        self.lineno = lineno
        self.start = start
        self.end = end
        self.line = line
        self.formatted = f"{name}:{lineno + 1}:{start + 1} - {line}"
        super().__init__(self.formatted)

    def __str__(self):
        return self.formatted

    def __repr__(self):
        return self.formatted


class SearchWorker(QThread):
    results_ready = pyqtSignal(int, list, bool)
    search_error = pyqtSignal(int, str)

    def __init__(self):
        super().__init__(None)
        self.generation = 0
        self._request = None
        self._pending = None
        self._shutting_down = False
        self.finished.connect(self._start_pending)

    @staticmethod
    def _is_binary(path):
        with open(path, "rb") as handle:
            return b"\0" in handle.read(1024)

    @staticmethod
    def _walk(path, include_modules):
        """Walk project files; optional module mode includes hidden module dirs.

        The old checkbox had no semantics. Here unchecked search omits all
        hidden directories; checked search includes hidden directories except
        explicit VCS/cache/environment exclusions.
        """
        for root, dirs, files in os.walk(path, topdown=True):
            dirs[:] = [
                directory
                for directory in dirs
                if directory not in EXCLUDED_DIRS
                and (include_modules or not directory.startswith("."))
            ]
            yield root, files

    def update(self, text, path, include_modules, regex=False, case_sensitive=False):
        if self._shutting_down:
            return
        self.generation += 1
        request = (
            self.generation,
            text,
            str(path),
            bool(include_modules),
            bool(regex),
            bool(case_sensitive),
        )
        if self.isRunning():
            self._pending = request
        else:
            self._start_request(request)

    def _start_request(self, request):
        self._request = request
        self.start()

    def _start_pending(self):
        if not self._shutting_down and self._pending is not None:
            request, self._pending = self._pending, None
            self._start_request(request)

    def shutdown(self):
        self._shutting_down = True
        self._pending = None
        self.requestInterruption()

    def run(self):
        generation, text, path, include_modules, regex_mode, case_sensitive = self._request
        if not text or not text.strip():
            self.results_ready.emit(generation, [], False)
            return

        expression = text if regex_mode else re.escape(text)
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(expression, flags)
        except re.error as error:
            self.search_error.emit(generation, str(error))
            return

        items = []
        for root, files in self._walk(path, include_modules):
            if self._shutting_down or self.isInterruptionRequested():
                return
            for file_name in files:
                file_path = Path(root) / file_name
                if file_path.suffix.lower() in EXCLUDED_SUFFIXES:
                    continue
                try:
                    if self._is_binary(file_path):
                        continue
                    with file_path.open("r", encoding="utf-8") as handle:
                        for line_number, line in enumerate(handle):
                            if self._shutting_down or self.isInterruptionRequested():
                                return
                            for match in pattern.finditer(line):
                                # Zero-length matches are not useful navigation
                                # targets and can create enormous result sets.
                                if match.start() == match.end():
                                    continue
                                items.append(
                                    SearchItem(
                                        file_name,
                                        str(file_path),
                                        line_number,
                                        match.start(),
                                        match.end(),
                                        line.strip()[:80],
                                    )
                                )
                                if len(items) >= MAX_RESULTS:
                                    self.results_ready.emit(generation, items, True)
                                    return
                except (OSError, UnicodeError):
                    continue
        self.results_ready.emit(generation, items, False)
```

### Replace the search UI wiring in `main.py:1747-1770`

```python
        search_input = QLineEdit()
        search_input.setPlaceholderText("Search")
        search_input.setFont(self.window_font)

        self.search_checkbox = QCheckBox("Include hidden module folders")
        self.search_regex_checkbox = QCheckBox("Regex")
        self.search_case_checkbox = QCheckBox("Case sensitive")
        for checkbox in (
            self.search_checkbox,
            self.search_regex_checkbox,
            self.search_case_checkbox,
        ):
            checkbox.setFont(self.window_font)

        self.search_worker = SearchWorker()
        self.search_worker.results_ready.connect(self.search_finished)
        self.search_worker.search_error.connect(self.search_failed)

        def request_search():
            self.search_worker.update(
                search_input.text(),
                self.file_manager.model.rootPath(),
                self.search_checkbox.isChecked(),
                self.search_regex_checkbox.isChecked(),
                self.search_case_checkbox.isChecked(),
            )

        search_input.textChanged.connect(lambda _text: request_search())
        self.search_checkbox.toggled.connect(lambda _on: request_search())
        self.search_regex_checkbox.toggled.connect(lambda _on: request_search())
        self.search_case_checkbox.toggled.connect(lambda _on: request_search())
```

Add all three checkboxes to `search_layout` before the list.

### Replace search result handlers in `main.py`

```python
    def search_finished(self, generation, items, truncated):
        if generation != self.search_worker.generation:
            return
        self.search_list_view.clear()
        for item in items:
            self.search_list_view.addItem(item)
        if truncated:
            self.statusBar().showMessage(
                f"Search limited to {len(items)} results", 4000
            )

    def search_failed(self, generation, message):
        if generation == self.search_worker.generation:
            self.search_list_view.clear()
            self.statusBar().showMessage(f"Invalid search: {message}", 4000)

    def search_list_view_clicked(self, item: SearchItem):
        editor = self.set_new_tab(Path(item.full_path))
        if editor is None:
            return
        editor.setSelection(item.lineno, item.start, item.lineno, item.end)
        editor.setCursorPosition(item.lineno, item.start)
        editor.ensureLineVisible(item.lineno)
        editor.setFocus()
```

### Verify

- Literal `[` must search for `[` rather than report an invalid regex.
- Regex mode must report invalid expressions visibly.
- Search `import` in the audited repository and assert all 644 occurrences appear unless capped.
- Multiple matches on one line must create multiple entries.
- Clicking a result must select the match from start to end.
- Toggle every checkbox without changing text and assert a new generation starts.
- A generated file with 10,000 matches must return exactly 5,000 and `truncated=True`.

---

## 7. Terminal process/input lifecycle

The reliable short-term implementation is a shell console: read-only output plus a separate input line. A true terminal needs PTY/ConPTY and a VT emulator; ordinary `QProcess` pipes cannot provide that contract.

### File and original lines

`con_term/terminal_widget.py:37-353`

### Imports

Add `QLineEdit` to the QtWidgets import. Remove `QEvent`, `QKeySequence`, and the event filter after applying this fix. Keep `QTextCursor` because `_append_text` uses its `End` enum.

### Replace `__init__` completely

```python
    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self._shells = []
        self._next_shell = None
        self._stopping = False
        self._init_ui()
        self._connect_signals()

        # Adding the first combo item changes currentIndex. Block signals so
        # construction starts exactly one shell.
        self.shell_combo.blockSignals(True)
        self._populate_shell_combo_method()
        self.shell_combo.blockSignals(False)
        self._detect_and_start_default()
```

### Replace the output/input part of `_init_ui`

Delete the current editable output creation and `_input_start_pos`. Use:

```python
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFont("Consolas", 12))
        self.output.setStyleSheet(
            "QPlainTextEdit { background:#1e2127; color:#abb2bf; "
            "border:none; padding:4px; }"
        )
        layout.addWidget(self.output)

        self.input_line = QLineEdit()
        self.input_line.setPlaceholderText("Enter a shell command")
        self.input_line.setFont(QFont("Consolas", 12))
        layout.addWidget(self.input_line)
```

### Replace `_connect_signals` completely

```python
    def _connect_signals(self):
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._on_finished)
        self.process.stateChanged.connect(self._on_state_changed)
        self.clear_btn.clicked.connect(self.output.clear)
        self.restart_btn.clicked.connect(self._restart_shell)
        self.shell_combo.currentIndexChanged.connect(self._on_shell_changed)
        self.input_line.returnPressed.connect(self._submit_input)
```

### Replace shell lifecycle, append, input, clear, and stop methods

Delete `_start_shell`, `_append_text`, the entire `eventFilter`, `_clear`, and `stop`. Replace them with:

```python
    def _start_shell(self, shell_path: str, args: list = None):
        """Queue a shell replacement without blocking the GUI thread."""
        self._next_shell = (shell_path, list(args or []))
        if self.process.state() != QProcess.NotRunning:
            self._stopping = True
            self.process.kill()
            return
        self._launch_next_shell()

    def _launch_next_shell(self):
        if self._next_shell is None:
            return
        shell_path, args = self._next_shell
        self._next_shell = None
        self._stopping = False

        env = QProcessEnvironment.systemEnvironment()
        # TERM describes output capabilities; it does not create a PTY.
        # Use a conservative value for this pipe-based shell console.
        if sys.platform != "win32":
            env.insert("TERM", "dumb")
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(Path.home()))
        self.output.clear()
        self.process.start(shell_path, args)

    def _append_text(self, text: str):
        """Append process output; user input lives in a different widget."""
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()

    def _submit_input(self):
        if self.process.state() != QProcess.Running:
            return
        text = self.input_line.text()
        self.input_line.clear()
        self._append_text(f"> {text}\n")
        self.process.write((text + "\n").encode("utf-8"))

    def _clear(self):
        self.output.clear()

    def stop(self):
        """Stop without waitForFinished; no replacement is launched."""
        self._next_shell = None
        self._stopping = True
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()
```

Replace `_on_finished` completely so queued shell changes launch once:

```python
    def _on_finished(self, exit_code, _exit_status):
        if not self._stopping:
            self._append_text(f"\n[Process exited with code {exit_code}]\n")
        self.status_label.setText("● Shell stopped")
        self.status_label.setStyleSheet("color:#e06c75; font-size:12px;")
        if self._next_shell is not None:
            self._launch_next_shell()
```

### User-facing naming

Rename the dock and class description from “Terminal” to “Shell Console” unless you implement a real PTY/ConPTY backend. If full terminal semantics are required, use a dedicated terminal-emulator library and delete this pipe-based implementation rather than layering more cursor handling onto it.

### Verify

- Construction starts one shell process, not two.
- Shell switching never calls `waitForFinished` and never freezes the UI.
- Output arriving while text is being entered does not alter the input line.
- Stop does not restart a queued shell.
- The UI does not claim PTY features in pipe-console mode.

---

## 8. Transaction-safe file-manager operations

### File and original lines

`side_bar_widgets/file_manager.py:1-15`, `215-275`, `325-353`

### Add imports

```python
from PyQt5.QtCore import QDir, QItemSelectionModel, QModelIndex, QPoint, Qt
from uuid import uuid4
```

### Replace `action_delete` completely

```python
    def action_delete(self, index: QModelIndex):
        """Confirm the exact selection, then close all editors before deletion."""
        if not index.isValid():
            return

        rows = list(self.selectionModel().selectedRows())
        if index not in rows:
            self.selectionModel().clearSelection()
            self.selectionModel().select(
                index,
                QItemSelectionModel.Select | QItemSelectionModel.Rows,
            )
            rows = [index]

        selected = [
            (Path(self.model.filePath(row)), self.model.isDir(row))
            for row in rows
        ]
        # A selected parent already contains its selected descendants.
        targets = [
            (path, is_dir)
            for path, is_dir in selected
            if not any(
                other != path and other in path.parents
                for other, _other_is_dir in selected
            )
        ]
        label = (
            f"'{targets[0][0].name}'"
            if len(targets) == 1
            else f"{len(targets)} selected items"
        )
        answer = self.show_dialog(
            "Permanent Delete",
            f"Permanently delete {label}? This cannot be undone.",
        )
        if answer != QMessageBox.Yes:
            return

        # Phase 1: finish every editor prompt before touching the filesystem.
        for path, is_directory in targets:
            if not self.main_window.can_close_editors_for_path(
                path, is_directory=is_directory
            ):
                return

        # Phase 2: close the already-approved editors.
        for path, is_directory in targets:
            self.main_window.close_editors_for_path(
                path, is_directory=is_directory, prompt=False
            )

        # Phase 3: atomically rename every target out of view. If any rename
        # fails, restore all earlier names; the requested set stays intact.
        staged = []
        try:
            for path, _is_directory in targets:
                temporary = path.with_name(
                    f".{path.name}.markdowneditor-delete-{uuid4().hex}"
                )
                path.rename(temporary)
                staged.append((path, temporary))
        except OSError as error:
            rollback_errors = []
            for original, temporary in reversed(staged):
                try:
                    temporary.rename(original)
                except OSError as rollback_error:
                    rollback_errors.append(str(rollback_error))
            details = "\n".join(rollback_errors)
            QMessageBox.critical(
                self,
                "Delete",
                f"Nothing was deleted because staging failed:\n{error}"
                + (f"\nRollback errors:\n{details}" if details else ""),
            )
            return

        # Cleanup begins only when the whole set is staged. If cleanup fails,
        # the hidden staging name remains available for recovery.
        cleanup_errors = []
        for original, temporary in staged:
            try:
                self.delete_file(temporary)
            except OSError as error:
                cleanup_errors.append(f"{original.name}: {error}")
        if cleanup_errors:
            QMessageBox.critical(
                self,
                "Delete cleanup",
                "Some staged recovery items could not be removed:\n"
                + "\n".join(cleanup_errors),
            )
```

This requires splitting `MainWindow.close_editors_for_path` into a prompt-only validation and a non-prompt close operation. Do not close any editor during validation. A suitable contract is:

```python
    def editors_for_path(self, target_path: Path, is_directory=False):
        target = Path(target_path).resolve()
        result = []
        for editor in self.tab_view.all_editors():
            path = getattr(editor, "path", None)
            if path is None:
                continue
            editor_path = Path(path).resolve()
            affected = (
                editor_path == target or target in editor_path.parents
                if is_directory
                else editor_path == target
            )
            if affected:
                result.append(editor)
        return result

    def can_close_editors_for_path(self, target_path, is_directory=False):
        """Collect save/discard decisions without removing tabs."""
        for editor in self.editors_for_path(target_path, is_directory):
            if editor not in self._dirty_editors:
                continue
            self.tab_view.focus_editor(editor)
            name = Path(editor.path).name
            reply = QMessageBox.question(
                self, "Unsaved Changes", f"Save changes to '{name}'?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )
            if reply == QMessageBox.Cancel:
                return False
            if reply == QMessageBox.Save and not self.save_file():
                return False
        return True

    def close_editors_for_path(self, target_path, is_directory=False, prompt=True):
        editors = self.editors_for_path(target_path, is_directory)
        if prompt and not self.can_close_editors_for_path(target_path, is_directory):
            return False
        for editor in editors:
            self._dirty_editors.discard(editor)
            self.tab_view.remove_editor(editor)
            self._dispose_editor(editor)
        return True
```

### Replace `dropEvent` completely

```python
    def dropEvent(self, event: QDropEvent) -> None:
        """Validate the complete batch before moving or copying anything."""
        if not event.mimeData().hasUrls():
            event.ignore()
            return

        index = self.indexAt(event.pos())
        if index.isValid():
            selected = Path(self.model.filePath(index))
            target_dir = selected if selected.is_dir() else selected.parent
        else:
            target_dir = Path(self.model.rootPath())

        copy_requested = bool(event.keyboardModifiers() & Qt.ControlModifier)
        operations = []
        try:
            for url in event.mimeData().urls():
                source = Path(url.toLocalFile()).resolve(strict=True)
                destination = (target_dir / source.name).resolve(strict=False)
                if source == destination:
                    continue
                if source.is_dir() and source in destination.parents:
                    raise OSError("Cannot copy a folder into itself")
                if destination.exists():
                    raise FileExistsError(f"Destination already exists: {destination}")
                operations.append((source, destination))

            completed = []
            for source, destination in operations:
                if copy_requested:
                    if source.is_dir():
                        shutil.copytree(source, destination)
                    else:
                        shutil.copy2(source, destination)
                else:
                    shutil.move(str(source), str(destination))
                completed.append((source, destination))
        except OSError as error:
            rollback_errors = []
            for source, destination in reversed(locals().get("completed", [])):
                try:
                    if copy_requested:
                        self.delete_file(destination)
                    elif destination.exists() and not source.exists():
                        shutil.move(str(destination), str(source))
                except OSError as rollback_error:
                    rollback_errors.append(str(rollback_error))
            details = "\n".join(rollback_errors)
            QMessageBox.critical(
                self,
                "File operation",
                str(error) + (f"\nRollback errors:\n{details}" if details else ""),
            )
            event.ignore()
            return

        event.setDropAction(Qt.CopyAction if copy_requested else Qt.MoveAction)
        event.accept()
```

For stronger guarantees across process crashes or power loss, use a journaled
transaction or the operating system trash instead of permanent deletion.

---

## 9. Consistent regex find/replace

### Files and original lines

- `main.py:2523-2635`
- `code_inteligence/find_replace.py` supplies UI only and can remain unchanged.

### Replace all four find/replace methods and add the helpers below

```python
    @staticmethod
    def _compile_find_pattern(text, case_sensitive, whole_word, regex):
        expression = text if regex else re.escape(text)
        if whole_word:
            expression = rf"\b(?:{expression})\b"
        flags = 0 if case_sensitive else re.IGNORECASE
        return re.compile(expression, flags)

    @staticmethod
    def _offset_to_line_column(source: str, offset: int):
        prefix = source[:offset]
        line = prefix.count("\n")
        previous_newline = prefix.rfind("\n")
        column = offset if previous_newline < 0 else offset - previous_newline - 1
        return line, column

    @staticmethod
    def _line_column_to_offset(source: str, line: int, column: int) -> int:
        lines = source.splitlines(True)
        line = max(0, min(line, max(0, len(lines) - 1)))
        return sum(len(part) for part in lines[:line]) + max(0, column)

    def _select_python_match(self, editor, match):
        source = editor.text()
        start_line, start_column = self._offset_to_line_column(source, match.start())
        end_line, end_column = self._offset_to_line_column(source, match.end())
        editor.setSelection(start_line, start_column, end_line, end_column)
        editor.ensureLineVisible(start_line)

    def _do_find_next(self, text, case_sensitive, whole_word, regex):
        editor = self.current_editor()
        if editor is None:
            return
        try:
            pattern = self._compile_find_pattern(
                text, case_sensitive, whole_word, regex
            )
        except re.error as error:
            self.statusBar().showMessage(f"Invalid regex: {error}", 4000)
            return

        source = editor.text()
        if editor.hasSelectedText():
            _line_from, _column_from, line, column = editor.getSelection()
        else:
            line, column = editor.getCursorPosition()
        cursor_offset = self._line_column_to_offset(source, line, column)
        match = pattern.search(source, cursor_offset) or pattern.search(source, 0, cursor_offset)
        if match:
            self._select_python_match(editor, match)

    def _do_find_prev(self, text, case_sensitive, whole_word, regex):
        editor = self.current_editor()
        if editor is None:
            return
        try:
            pattern = self._compile_find_pattern(
                text, case_sensitive, whole_word, regex
            )
        except re.error as error:
            self.statusBar().showMessage(f"Invalid regex: {error}", 4000)
            return

        source = editor.text()
        if editor.hasSelectedText():
            line, column, _line_to, _column_to = editor.getSelection()
        else:
            line, column = editor.getCursorPosition()
        cursor_offset = self._line_column_to_offset(source, line, column)
        matches = list(pattern.finditer(source, 0, cursor_offset))
        if not matches:
            matches = list(pattern.finditer(source, cursor_offset))
        if matches:
            self._select_python_match(editor, matches[-1])

    def _do_replace(self, find_text, replace_text, case_sensitive, whole_word, regex):
        editor = self.current_editor()
        if editor is None:
            return
        try:
            pattern = self._compile_find_pattern(
                find_text, case_sensitive, whole_word, regex
            )
        except re.error as error:
            self.statusBar().showMessage(f"Invalid regex: {error}", 4000)
            return

        selected = editor.selectedText() if editor.hasSelectedText() else ""
        match = pattern.fullmatch(selected) if selected else None
        if match:
            replacement = match.expand(replace_text) if regex else replace_text
            editor.replace(replacement)
        self._do_find_next(
            find_text, case_sensitive, whole_word, regex
        )

    def _do_replace_all(self, find_text, replace_text, case_sensitive, whole_word, regex):
        editor = self.current_editor()
        if editor is None:
            return
        try:
            pattern = self._compile_find_pattern(
                find_text, case_sensitive, whole_word, regex
            )
        except re.error as error:
            self.statusBar().showMessage(f"Invalid regex: {error}", 4000)
            return
        if pattern.match("") is not None:
            self.statusBar().showMessage(
                "Zero-length regex cannot be replaced", 4000
            )
            return

        source = editor.text()
        matches = list(pattern.finditer(source))
        count = len(matches)
        if matches:
            # Replace from the end so earlier offsets remain valid. Using
            # QScintilla selection/replace keeps the whole operation undoable.
            editor.beginUndoAction()
            try:
                for match in reversed(matches):
                    start_line, start_column = self._offset_to_line_column(
                        source, match.start()
                    )
                    end_line, end_column = self._offset_to_line_column(
                        source, match.end()
                    )
                    editor.setSelection(
                        start_line, start_column, end_line, end_column
                    )
                    replacement = (
                        match.expand(replace_text) if regex else replace_text
                    )
                    editor.replace(replacement)
            finally:
                editor.endUndoAction()
        self.statusBar().showMessage(f"Replaced {count} occurrences", 3000)
```

### Notes

Using Python `re` everywhere removes the previous disagreement between Scintilla regex syntax and Python validation. For literal Replace All, a callable replacement prevents backslashes in user text from being interpreted as group escapes.

### Verify

Test literal backslashes, `(abc)(123)` with `\2-\1`, case-insensitive matching, whole words, multiline text, previous-search wrapping, invalid regex, and a zero-length regex.

---

## 10. Complete or remove the custom title bar

`title_bar.py` is currently unused. The safest choice is to delete it until custom window chrome is intentionally integrated. If it must remain, replace the entire file (original lines 1-73) with:

```python
from PyQt5.QtCore import QPoint, QSize, Qt
from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QStyle, QToolButton, QWidget


class CustomTitleBar(QWidget):
    """Small movable title bar for a frameless top-level window."""

    def __init__(self, parent):
        super().__init__(parent)
        self._window = parent.window()
        self._drag_origin = None
        self.setAutoFillBackground(True)
        self.setBackgroundRole(QPalette.Highlight)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.title = QLabel(parent.windowTitle() or "MarkdownEditor", self)
        self.title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.title, 1)

        self.min_btn = self._button(QStyle.SP_TitleBarMinButton)
        self.norm_btn = self._button(QStyle.SP_TitleBarNormalButton)
        self.max_btn = self._button(QStyle.SP_TitleBarMaxButton)
        self.close_btn = self._button(QStyle.SP_TitleBarCloseButton)

        self.min_btn.clicked.connect(self._window.showMinimized)
        self.norm_btn.clicked.connect(self._window.showNormal)
        self.max_btn.clicked.connect(self._window.showMaximized)
        self.close_btn.clicked.connect(self._window.close)
        self.norm_btn.hide()

        for button in (self.min_btn, self.norm_btn, self.max_btn, self.close_btn):
            layout.addWidget(button)

    def _button(self, standard_icon):
        button = QToolButton(self)
        button.setIcon(self.style().standardIcon(standard_icon))
        button.setFocusPolicy(Qt.NoFocus)
        button.setFixedSize(QSize(28, 28))
        return button

    def sync_window_state(self):
        maximized = self._window.isMaximized()
        self.max_btn.setVisible(not maximized)
        self.norm_btn.setVisible(maximized)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self._window.isMaximized():
                self._window.showNormal()
            else:
                self._window.showMaximized()
            self.sync_window_state()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self._window.isMaximized():
            self._drag_origin = event.globalPos() - self._window.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_origin is not None and event.buttons() & Qt.LeftButton:
            self._window.move(event.globalPos() - self._drag_origin)

    def mouseReleaseEvent(self, _event):
        self._drag_origin = None
```

Do not set `Qt.FramelessWindowHint` until resize behavior, accessibility, DPI, and platform-native snapping have been tested.

---

## 11. Correct Markdown comment styling

### File and original lines

`markdown_editor/markdowncustomlexer.py:174-208`, `274-319`

### Replace `_state_before` completely

```python
    def _state_before(self, full_text: str, start_char: int):
        """Compute fence/comment state while continuing after closed comments."""
        prefix = full_text[:start_char]
        in_fence = False
        in_comment = False
        fence_char = None
        fence_len = 0

        for line in prefix.split("\n"):
            cursor = 0
            visible_parts = []
            while cursor < len(line):
                if in_comment:
                    close = line.find("-->", cursor)
                    if close < 0:
                        cursor = len(line)
                        break
                    in_comment = False
                    cursor = close + 3
                    continue
                opening = line.find("<!--", cursor)
                if opening < 0:
                    visible_parts.append(line[cursor:])
                    break
                visible_parts.append(line[cursor:opening])
                close = line.find("-->", opening + 4)
                if close < 0:
                    in_comment = True
                    break
                cursor = close + 3

            visible = "".join(visible_parts)
            char, count = self._fence_line(visible)
            if char is None:
                continue
            if not in_fence:
                in_fence, fence_char, fence_len = True, char, count
            elif char == fence_char and self._is_closing_fence(
                visible, fence_char, fence_len
            ):
                in_fence, fence_char, fence_len = False, None, 0
        return in_fence, fence_char, fence_len, in_comment
```

### Add a complete line-segment helper

```python
    def _style_line_with_comments(
        self, line, in_fence, fence_char, fence_len, in_comment, force_inline
    ):
        """Style only comment spans, then resume Markdown for surrounding text."""
        cursor = 0
        while cursor < len(line):
            if in_comment:
                close = line.find("-->", cursor)
                end = len(line) if close < 0 else close + 3
                self._style_plain_line(line[cursor:end], self.COMMENTS)
                cursor = end
                if close < 0:
                    return in_fence, fence_char, fence_len, True
                in_comment = False
                continue

            opening = line.find("<!--", cursor)
            end = len(line) if opening < 0 else opening
            if end > cursor:
                in_fence, fence_char, fence_len = self._style_line(
                    line[cursor:end],
                    in_fence,
                    fence_char,
                    fence_len,
                    force_inline=force_inline or cursor > 0,
                )
            if opening < 0:
                return in_fence, fence_char, fence_len, False
            cursor = opening
            in_comment = True
        return in_fence, fence_char, fence_len, in_comment
```

### Replace `styleText` completely

```python
    def styleText(self, start: int, end: int) -> None:
        full_text = self.editor.text()
        start_char = self._byte_pos_to_char_index(full_text, start)
        end_char = self._byte_pos_to_char_index(full_text, end)
        in_fence, fence_char, fence_len, in_comment = self._state_before(
            full_text, start_char
        )
        partial_first = start_char > 0 and full_text[start_char - 1] != "\n"

        self.startStyling(start)
        lines = full_text[start_char:end_char].split("\n")
        for index, line in enumerate(lines):
            in_fence, fence_char, fence_len, in_comment = (
                self._style_line_with_comments(
                    line,
                    in_fence,
                    fence_char,
                    fence_len,
                    in_comment,
                    force_inline=partial_first and index == 0,
                )
            )
            if index != len(lines) - 1:
                self.setStyling(1, self.DEFAULT)
```

### Verify

Test text before/after same-line comments, multiline comments, comments containing fence markers, and real fences immediately after a closing comment. Verify byte alignment with emoji before the comment.

---

## 12. Reliable signature-help trigger

### File and original lines

`python_editor/pythoneditor.py:397-435` inside `keyPressEvent`.

### Add import

```python
import re
```

### Replace only the complete `if e.text() == "("` block

```python
        if e.text() == "(" and self.is_python_file and not self._shutting_down:
            line, index = self.getCursorPosition()
            before = self.text(line)[:index].rstrip()
            # Validate the complete trailing identifier, not only its last
            # character. Digits are legal after the first identifier character.
            if re.search(r"(?:^|\.)[A-Za-z_]\w*$", before):
                QTimer.singleShot(50, self._trigger_signature_help)
```

Keep the final `return super().keyPressEvent(e)` so `(` is inserted before the delayed Jedi query.

---

## 13. User-visible update-check results

### File and original lines

`main.py:277`, `870-871`, `2819-2881`

### Add a result signal to `MainWindow`

Add this module import near the top of `main.py` so the exception type always
exists even if the background request fails before version parsing:

```python
from packaging.version import InvalidVersion, Version
```

```python
    update_check_finished = pyqtSignal(bool, str)
```

Connect it in `__init__`:

```python
        self.update_check_finished.connect(self._show_update_check_result)
```

Change the startup call to remain quiet:

```python
        QTimer.singleShot(2000, lambda: self.check_for_updates(manual=False))
```

Change the menu connection:

```python
        check_updates_action.triggered.connect(
            lambda: self.check_for_updates(manual=True)
        )
```

### Replace `check_for_updates` completely and add the result handler

```python
    def check_for_updates(self, manual=False):
        """Check releases off-thread and report every manual outcome."""
        import json
        import threading
        import urllib.error
        import urllib.request

        api_url = "https://api.github.com/repos/ConfidentCupcake/MarkdownEditor/releases/latest"

        def finish(message):
            self.update_check_finished.emit(bool(manual), message)

        def check():
            try:
                request = urllib.request.Request(
                    api_url, headers={"User-Agent": "MarkdownEditor"}
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    data = json.loads(response.read().decode("utf-8"))

                latest_tag = str(data.get("tag_name", ""))
                latest = Version(latest_tag.removeprefix("v"))
                current = Version(APP_VERSION.removeprefix("v"))
                if latest <= current:
                    finish(f"MarkdownEditor {APP_VERSION} is up to date.")
                    return

                assets = data.get("assets") or []
                suffixes = (
                    (".exe", ".msi") if sys.platform == "win32"
                    else (".dmg", ".pkg", ".zip") if sys.platform == "darwin"
                    else (".appimage", ".deb", ".rpm", ".tar.gz")
                )
                url = next(
                    (
                        str(asset.get("browser_download_url", ""))
                        for asset in assets
                        if str(asset.get("name", "")).lower().endswith(suffixes)
                    ),
                    str(data.get("html_url", "")),
                )
                if url:
                    self.update_available.emit(str(latest), url)
                else:
                    finish("An update exists, but no download URL was provided.")
            except (
                OSError,
                ValueError,
                json.JSONDecodeError,
                urllib.error.URLError,
                InvalidVersion,
            ) as error:
                finish(f"Could not check for updates: {error}")

        threading.Thread(target=check, daemon=True).start()

    def _show_update_check_result(self, manual: bool, message: str):
        if manual:
            QMessageBox.information(self, "Check for Updates", message)
```

The update-available dialog remains unchanged. Automated startup failures remain silent; manual checks always answer.

---

## 14. Packaging, lint, and CI

### Replace `pyproject.toml` completely

The current file is only a Ruff configuration. Replace it with a complete application definition while preserving the Ruff sections:

```toml
[build-system]
requires = ["setuptools>=75", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "confident-cupcake-markdown-editor"
version = "1.9.2"
description = "A PyQt Markdown and Python editor"
requires-python = ">=3.11"
dependencies = [
    "bleach>=6.2,<7",
    "jedi>=0.19,<1",
    "Markdown>=3.7,<4",
    "packaging>=24,<27",
    "PyQt5>=5.15,<6",
    "PyQtWebEngine>=5.15,<6",
    "QScintilla>=2.14,<3",
    "ruff>=0.12,<1",
]

[project.gui-scripts]
markdown-editor = "main:main"

[tool.setuptools]
py-modules = ["main", "resources_rc", "title_bar"]
include-package-data = true

[tool.setuptools.packages.find]
where = ["."]
include = [
    "code_inteligence*",
    "code_settings*",
    "con_term*",
    "cozy*",
    "markdown_editor*",
    "python_editor*",
    "ruff_implementation*",
    "side_bar_widgets*",
    "markdowneditor_assets*",
]

[tool.setuptools.package-data]
markdowneditor_assets = [
    "css/*.qss",
    "icons/*.png",
    "icons/*.svg",
    "icons/*.qrc",
    "icons/cat/*.png",
    "icons/cat_sheets/*.png",
    "themes/*.json",
]

[tool.ruff]
target-version = "py311"
line-length = 100
extend-exclude = ["resources_rc.py", "build", "dist", ".venv", "venv", "__pycache__"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "C4", "RUF"]
ignore = ["E501"]
fixable = ["ALL"]
unfixable = []

[tool.ruff.lint.per-file-ignores]
"main.py" = ["F403", "F405"]
"resources_rc.py" = ["ALL"]

[tool.ruff.lint.isort]
combine-as-imports = true
known-first-party = [
    "code_inteligence",
    "code_settings",
    "con_term",
    "cozy",
    "markdown_editor",
    "python_editor",
    "ruff_implementation",
    "side_bar_widgets",
]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
line-ending = "auto"
docstring-code-format = true
```

### Add a real entry point to `main.py`

Replace the bottom `if __name__ == "__main__"` block with:

```python
def main() -> int:
    """Installed GUI entry point."""
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
```

### Move runtime assets into an importable package

The package-data declaration works only for files inside a package. Create this
layout; move the existing runtime asset directories rather than keeping two
copies:

```text
markdowneditor_assets/
    __init__.py
    paths.py
    css/
        style.qss
    icons/
        ...all current icon files and cat folders...
    themes/
        ...the six JSON files...
```

Keep `themes/theme_factory.py` as a developer tool or move it into
`markdowneditor_assets/themes/` and add an `__init__.py` there. If it is moved,
change `THEMES_DIR` to `Path(asset_path("themes"))`.

Create `markdowneditor_assets/paths.py` with this complete content:

```python
import sys
from importlib.resources import files
from pathlib import Path


def asset_path(relative_path: str) -> str:
    """Return a filesystem path in source, wheel, and PyInstaller builds."""
    if hasattr(sys, "_MEIPASS"):
        return str(Path(sys._MEIPASS) / relative_path)
    return str(files("markdowneditor_assets").joinpath(relative_path))
```

Then replace each duplicate resource helper with the shared function:

- `main.py:58-72`: delete `resource_path`, import
  `asset_path as resource_path` from `markdowneditor_assets.paths`.
- `cozy/cat_controller.py:42-53`: delete `_resource_path`, import
  `asset_path as _resource_path`.
- `cozy/neko.py`: delete its `_resource_path`, import the same alias.
- `python_editor/custompythonlexer.py:19-22`: delete `_resource_path`, import
  the same alias.
- `code_settings/settings_dialog.py` theme discovery: replace its frozen/source
  branch with `themes_dir = Path(asset_path("themes"))` and import `asset_path`.
- `themes/theme_factory.py`, if retained outside the package: import `asset_path`
  and use `THEMES_DIR = Path(asset_path("themes"))`.

After moving files, update `icons/resources.qrc` paths only if you regenerate
`resources_rc.py` from its new directory. Do not commit a second, stale icon
tree merely to make source execution appear to work.

### Add `.github/workflows/quality.yml`

```yaml
name: quality

on:
  push:
  pull_request:

jobs:
  test:
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
        python-version: ["3.11", "3.12"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: python -m pip install --upgrade pip
      - run: python -m pip install -e . pytest pytest-qt pillow
      - run: python -m compileall -q -f .
      - run: python -m ruff check .
      - run: python -m pytest -q
        env:
          QT_QPA_PLATFORM: offscreen
```

### Lint cleanup

Run `python -m ruff check . --fix`, inspect the diff, then fix remaining warnings manually. Do this as its own commit after functional changes so mechanical formatting cannot hide behavior changes.

---

## Final regression checklist

Run these after all fix groups:

```bash
python -m compileall -q -f .
python -m ruff check .
python -m pytest -q
python -m pip install --dry-run .
```

Manual acceptance checks:

- Untrusted Markdown cannot execute script, event handlers, or unsafe URLs.
- UTF-8 BOM, newline mode, symlinks, and external edits survive the expected workflows.
- No two tabs can own the same logical path.
- `.py`, `.pyw`, and `.pyi` behave identically as Python documents.
- Ruff starts, stops, retries, and positions Unicode diagnostics correctly.
- Closing tabs/the app during Jedi/Git/search work produces no QThread warning.
- Stop and close never launch queued Python work.
- Project search returns every occurrence, navigates to its start, and caps exactly.
- Shell-console output cannot corrupt text being entered and does not double-start.
- Delete/move/copy confirmation and mutations refer to the exact same target set.
- Find/replace uses one regex dialect and supports capture expansion.
- Manual update checks always return a visible result.

## Commit strategy

Use one commit per consolidated group. That keeps connected code together while avoiding the cross-linked, out-of-order implementation problem from the earlier document. A suggested sequence is:

```text
security: sanitize markdown preview
files: preserve bytes links conflicts and unique tab ownership
ruff: fix protocol unicode retries and diagnostic events
lifecycle: retire background workers safely
runner: make process replacement and arguments deterministic
search: replace project search pipeline
terminal: use nonblocking shell-console lifecycle
files: make batch operations transactional
editing: unify regex find and replace
ui: complete or remove custom title bar
lexer: scope markdown comments correctly
python: fix signature trigger
updates: report manual outcomes
build: add packaging tests and CI
```
