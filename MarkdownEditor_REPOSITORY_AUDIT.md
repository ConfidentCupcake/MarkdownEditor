# MarkdownEditor Repository Audit

Audit target: `https://github.com/ConfidentCupcake/MarkdownEditor`  
Branch: `main`  
Commit: `b4f59b9b3cdd38cbbc171b07ab1f483f641a0a82` (`2026-09-10`, “Fixed Terminal Widget”)  
Audit date: `2026-09-10`  

## Executive summary

The repository contains 245 tracked files. Every tracked path was inventoried and reviewed according to its type: Python/Cython and configuration files were read individually; Markdown documents were checked for local-link integrity and implementation drift; JSON, XML/QRC, PNG, and SVG assets were parsed or decoded; the generated C file and compiled extension were inspected as generated/platform artifacts.

The audit found **49 actionable defects**: 4 critical, 12 high, 28 medium, and 5 low. It also found 4 release-engineering gaps. The most urgent problems are:

1. `code_inteligence/references_finder.py` is syntactically invalid, so importing it always fails.
2. `MainWindow.closeEvent()` can close the application without prompting for dirty documents and returns after shutting down the first Python editor, skipping all remaining cleanup.
3. `close_editors_for_path()` skips every open editor, so deleting an open file can leave an editor able to recreate the deleted file later.
4. File rename completion calls a method that does not exist.
5. The pure-Python lexer fallback corrupts quote state, affecting every platform/Python version that cannot load the committed CPython 3.14 Windows extension.

No source changes were made as part of this audit.

## Method and limitations

Checks performed:

- Enumerated all 245 tracked files with Git and inspected the complete repository tree.
- Parsed all Python files with `compileall`/AST and linted them with Ruff 0.16.6.
- Read all handwritten Python and Cython sources individually. The 844 KB C file was treated as generated output and checked against its `.pyx` provenance rather than reviewed as handwritten logic.
- Parsed all 5 JSON theme files and the QRC/SVG XML files.
- Decoded all 171 PNG files and checked sprite-sheet cell divisibility.
- Checked Markdown documents for broken relative links.
- Inspected the committed crash log for evidence, while separating old, already-fixed stack traces from defects still present at the audited commit.
- Ran `git fsck --full` successfully.

Runtime GUI testing was not possible in the audit environment because the repository has no dependency manifest and PyQt5, QScintilla, Jedi, and Markdown were not installed. Findings labeled “confirmed” follow directly from invalid syntax, impossible branches, missing symbols/files, API misuse, or deterministic state flow. Qt behavior that needs runtime confirmation is identified as a risk rather than asserted as a crash.

## Critical defects

### BUG-001 — References feature cannot be imported

- **File:** `code_inteligence/references_finder.py:34-44`
- **Evidence:** `run()` opens a `try:` block that reaches end-of-file without `except` or `finally`. Both AST parsing and `compileall` fail with `SyntaxError: expected 'except' or 'finally' block`.
- **Impact:** The module is unusable. Any future import or wiring of the references feature prevents that import path from loading.
- **Resolution:** Complete the worker implementation, add `except Exception as err`, emit either `references_found` or `references_empty`, and add a bounded `shutdown()`. Replace the non-existent/incorrect `get_references_all()` call with the supported Jedi `Script.get_references(...)` API. Add an import smoke test.

### BUG-002 — Closing the window can discard changes and skips cleanup

- **File:** `main.py:2446-2467`
- **Evidence:** If any `PythonEditor` exists, `closeEvent()` calls `shutdown()` on the first one and immediately `return`s. It never invokes `close_editor()` for dirty-document prompts. It also skips other editors, terminal/runner/git/Ruff shutdown, recent-file persistence, and `super().closeEvent()`. If no Python editor exists, it still performs no dirty-document prompt.
- **Impact:** Unsaved user work can be lost. QThreads/QProcesses may be destroyed while running, and session state is not saved.
- **Resolution:** Iterate over a snapshot and call `close_editor()` for every editor. If any call returns `False`, call `event.ignore()` and stop. Otherwise call `save_session()`, shut down each service exactly once, then call `event.accept()`/`super().closeEvent(event)`. Remove the in-loop `return`. Make shutdown idempotent.

### BUG-003 — Deleting an open file never closes its editor

- **File:** `main.py:1970-2002`
- **Evidence:** `if editor_path == Path(editor_path): continue` is true for every non-null path. Therefore `affected_editors` is always empty.
- **Impact:** The filesystem entry is deleted while its live editor remains open. A later save can silently recreate the deleted file, and unsaved changes are never prompted before deletion.
- **Resolution:** Change the guard to `if editor_path is None: continue` before converting to `Path`, then retain the file/directory containment checks. Add tests for deleting one open file, an open directory tree, dirty tabs, and Cancel.

### BUG-004 — File rename completion calls a missing method

- **Files:** `side_bar_widgets/file_manager.py:157-178`; `main.py:1921`
- **Evidence:** `FileManager.rename_file_with_index()` calls `self.main_window.on_file_renamed(...)`, but the only implementation is `MainWindow.on_file_rename(...)`.
- **Impact:** Completing a rename raises `AttributeError`; tab metadata and integrations remain pointed at the old path.
- **Resolution:** Use one method name consistently and add a rename integration test. The handler must also perform the LSP/autocomplete URI migration described in BUG-010.

## High-severity defects

### BUG-005 — Pure-Python lexer fallback corrupts string state

- **File:** `python_editor/custompythonlexer.py:47-78, 167-245, 557-590`
- **Evidence:** `_py_pack_state()` tests `string_delim == 34`, while the scanner stores delimiters as strings (`'"'` or `"'"`). `_py_unpack_state()` returns integer 34/39, which later gets compared to string characters. `_py_compute_state()` also assigns `text[i + 1]` (an integer) and compares decoded strings to it. The CRLF check compares an integer byte to `"\n"`.
- **Impact:** On systems where `lexer_fast.cp314-win_amd64.pyd` cannot load—Linux, macOS, non-x64 Windows, and non-CPython-3.14—multiline/chunked string styling can fail to close or be styled incorrectly.
- **Resolution:** Use one representation throughout, preferably integer byte values in state and scanning code. Add parity tests that feed identical chunks to the Python and Cython scanners for single, triple, raw, byte, and f-strings across line/chunk boundaries.

### BUG-006 — Terminal input boundary is updated from a stale cursor

- **File:** `con_term/terminal_widget.py:251-307`
- **Evidence:** Enter inserts a newline using a local `QTextCursor`, but does not call `self.output.setTextCursor(cursor)` before reading `self.output.textCursor().position()` into `_input_start_pos`. Regular characters, Delete, selection replacement, paste, and arrow navigation are not restricted to the editable input region.
- **Impact:** The boundary can point before the newline; subsequent commands can include old text. Users can modify terminal history/prompt text and send corrupted commands.
- **Resolution:** Set the widget cursor after newline insertion, derive `_input_start_pos` from that cursor, force edits to the document end, block every mutation before the prompt boundary, and handle selections/paste/Delete. For correct interactive shell behavior, replace the pipe-based emulation with a PTY/ConPTY-backed terminal.

### BUG-007 — Drag/drop can crash, duplicate operations, or target the wrong location

- **File:** `side_bar_widgets/file_manager.py:304-319`
- **Evidence:** All drops target the model root instead of the indicated directory. A folder copied over an existing name raises `FileExistsError` (also present in the committed crash log). A same-root file is moved onto itself. After manually copying/moving, the code calls `super().dropEvent(e)`, allowing Qt to attempt a second operation. No filesystem error is caught.
- **Impact:** Common drag/drop operations can crash the slot, duplicate work, overwrite unexpectedly, or ignore the displayed drop target.
- **Resolution:** Resolve and validate the drop target from `indexAt(e.position().toPoint())`; choose exactly one operation; reject source==destination and recursive directory moves; prompt on collisions; wrap errors; accept the event without calling the base implementation after a manual operation.

### BUG-008 — “Ruff on save” setting is entirely dead

- **Files:** `main.py:67, 897-924, 2092-2171`; `code_settings/settings_dialog.py:301-310`
- **Evidence:** The UI and comments refer to `_run_ruff_before_save()`, but no such method or call exists. `ruff_save_mode` is only loaded/stored. `save_file`, `save_as`, and `save_all` write directly.
- **Impact:** The default UI claims “Safe fixes + format on save,” but saving never runs Ruff or formatting.
- **Resolution:** Implement one save pipeline used by all three commands. When enabled, request/apply only safe Ruff fixes, format the resulting buffer, preserve cursor/selection/undo state, then atomically write. Surface errors and allow the user to save without formatting.

### BUG-009 — Running a file ignores save failure

- **File:** `main.py:1123-1144` (and the run-with-arguments path near `main.py:857-889`)
- **Evidence:** Existing files call `save_file()` but do not check its Boolean result before launching the process.
- **Impact:** If saving fails, the editor runs stale disk contents while the UI appears to run the current document.
- **Resolution:** Abort unless `save_file()` returns `True`; display an explicit “run cancelled because save failed” status. Apply the same gate to every run command.

### BUG-010 — Save As and rename break LSP/autocomplete document identity

- **Files:** `main.py:1921-1968, 2124-2171`; `ruff_implementation/ruff_lsp_controller.py:53-76`; `code_inteligence/autocompleter.py:9-20`
- **Evidence:** Paths are mutated in place. Ruff previously sent `didOpen` for the old URI, but future `didChange` uses the dynamically computed new URI without sending `didClose(old)`/`didOpen(new)`. `AutoCompleter.file_path` is never updated.
- **Impact:** Diagnostics and code intelligence can disappear, attach to a ghost document, or resolve imports relative to the old file.
- **Resolution:** Add an explicit `relocate(old_path, new_path)` lifecycle: flush/cancel pending work, `didClose` the old URI, update all helper paths, reset controller state/version, and `didOpen` the new URI. Recreate the editor mode when the extension changes, or warn/ask the user.

### BUG-011 — Update checker can never correctly detect this repository’s releases

- **File:** `main.py:2590-2664`
- **Evidence:** The API URL still contains `YOUR_USERNAME`. It strips `v` only from the remote version, while `APP_VERSION` includes `v`; it uses inequality instead of semantic ordering, and assumes the first release asset is the correct download. UI scheduling is initiated from a raw Python thread using `QTimer.singleShot`, which is not a reliable cross-thread dispatch mechanism without an event loop in that thread.
- **Impact:** Automatic checks silently fail; after fixing the URL they can report equal/older versions as updates or open an empty/wrong URL.
- **Resolution:** Use `ConfidentCupcake/MarkdownEditor`, parse both versions with `packaging.version.Version`, require `latest > current`, select an asset by platform/name with the release page as fallback, and emit a Qt signal connected to the dialog on the GUI thread. Log failures non-fatally.

### BUG-012 — Tracked crash log exposes local information and keeps growing

- **Files:** `crash_log.txt`; `.gitignore`; `main.py:30-34`
- **Evidence:** The repository commits an 880,301-byte runtime crash log containing timestamps, absolute user paths, environment details, and thousands of stack-trace lines. `.gitignore` does not exclude it.
- **Impact:** Privacy leakage, repository bloat, noisy diffs, and accidental disclosure of future document/module names or local setup.
- **Resolution:** Remove the log from Git history/current tracking, add `crash_log.txt`/`*.log` to `.gitignore`, rotate/cap logs, and write them to `QStandardPaths.AppLocalDataLocation` with best-effort error handling.

### BUG-013 — User configuration is stored beside/below application assets

- **File:** `main.py:1170-1189, 1398-1425`
- **Evidence:** Interpreter settings are written next to `main.py`, and font settings mutate the active theme JSON. Frozen one-file bundles extract assets to temporary `_MEIPASS`; installed source trees may be read-only.
- **Impact:** Settings can fail to save, disappear between bundled launches, or modify version-controlled files.
- **Resolution:** Store user settings and customized theme copies in `QStandardPaths.AppConfigLocation`/`QSettings`; treat bundled/repository themes as immutable defaults. Catch JSON decode and write errors.

### BUG-014 — Corrupt `settings.json` can crash startup

- **File:** `main.py:1181-1189` (also `1170-1179`)
- **Evidence:** `json.loads()`/`json.load()` has no decode or I/O guard. `_load_interpreter()` runs during `MainWindow.__init__` before the UI can recover.
- **Impact:** A truncated or manually edited settings file prevents the app from starting.
- **Resolution:** Catch `OSError`, `UnicodeError`, `JSONDecodeError`, and schema/type errors; quarantine the bad file and fall back to `sys.executable`. Write settings atomically.

### BUG-015 — Asynchronous code intelligence applies stale results and drops current requests

- **Files:** `code_inteligence/autocompleter.py:17-34`; `code_inteligence/definition_finder.py:21-30`; `code_inteligence/hover_helper.py`; `code_inteligence/signature_helper.py`; `python_editor/pythoneditor.py:225-301, 400-520`
- **Evidence:** Each worker refuses new work while running and has no request ID/text revision. Results are applied at the editor’s latest mouse/caret position rather than the position/revision queried. Manual Ctrl+Space opens the QScintilla list before the background API has been populated; `_apply_completions()` never opens it afterward.
- **Impact:** Fast typing/mouse movement shows stale docs/signatures/completions, while the newest request is lost; manual completion can appear empty.
- **Resolution:** Add generation IDs and immutable request snapshots, retain/debounce the latest pending request, discard results whose revision/position no longer matches, and call `autoCompleteFromAPIs()` only after the matching completion list is prepared.

### BUG-016 — Python editor shutdown omits a worker and can hang indefinitely

- **Files:** `python_editor/pythoneditor.py:526-554`; `code_inteligence/definition_finder.py:62-66`; `code_inteligence/hover_helper.py`; `code_inteligence/signature_helper.py`
- **Evidence:** `PythonEditor.shutdown()` stops autocomplete, definition, and hover but never shuts down `signature_helper`. Helper shutdowns use unbounded `QThread.wait()` even though Jedi work does not cooperatively check interruption.
- **Impact:** “QThread: Destroyed while thread is still running” or a permanently blocked GUI on tab/application close.
- **Resolution:** Shut down every worker, disconnect result signals, use bounded waits, and move analysis to cancellable tasks/processes if Jedi cannot stop promptly. Define `self.ruff_lsp = None` unconditionally so partial/non-Python construction is safe.

## Medium-severity defects

### BUG-017 — All untitled Python tabs share one LSP URI

- **File:** `ruff_implementation/ruff_lsp_controller.py:53-64`
- **Impact:** Two untitled tabs overwrite/cross-talk in Ruff because both are `cwd/untitled.py`.
- **Resolution:** Assign each editor a stable UUID-backed untitled URI or unique temporary path, then migrate it on Save As.

### BUG-018 — Ruff does not recover after server restart or interpreter change

- **Files:** `main.py:1160-1168, 1335-1338`; `ruff_implementation/ruff_lsp_controller.py:31-76`; `ruff_implementation/ruff_lsp_client.py:208-218, 387-415`
- **Evidence:** Changing interpreter updates only `PythonRunner`. Controllers retain `_opened=True`; a later `server_ready` therefore cannot reopen documents.
- **Resolution:** Restart the shared client when the interpreter/workspace changes and broadcast a lifecycle reset that clears `_opened`, then reopens every live document.

### BUG-019 — Ruff client shutdown blocks its own response callback

- **File:** `ruff_implementation/ruff_lsp_client.py:393-415`
- **Evidence:** It sends `shutdown` and immediately blocks the Qt thread in `waitForFinished()`. The same event loop must process stdout and invoke the callback that sends `exit`.
- **Resolution:** Make shutdown asynchronous: request shutdown, send `exit` in the response callback, and use a `QTimer` fallback to kill. Suppress expected `_on_finished` errors during intentional shutdown.

### BUG-020 — Malformed Ruff protocol data can crash the Qt slot and failed initialization cannot retry cleanly

- **File:** `ruff_implementation/ruff_lsp_client.py:208-218, 296-365`
- **Evidence:** `int(value.strip())` is outside an exception guard. Initialize error leaves the process alive and `_started=True`. Requests are registered before `_send`, so a failed send can leave callbacks pending forever.
- **Resolution:** Validate headers/length bounds, catch conversion errors, cap buffers, cleanly terminate/reset after initialize failure, and make `_send` return success so failed requests are removed/called with an error.

### BUG-021 — Ruff configuration is in the wrong discovery location

- **Files:** `ruff_implementation/pyproject.toml`; `main.py:97-99`
- **Evidence:** The server workspace root is the repository root, but its `pyproject.toml` is under `ruff_implementation/`; it will not configure root source files by normal upward discovery.
- **Resolution:** Move the Ruff config to the repository root or explicitly pass/configure its path for the server. Remove stale first-party module names.

### BUG-022 — Code outline omits top-level functions and ignores stored columns

- **File:** `side_bar_widgets/code_outline.py:46-77`
- **Evidence:** `isinstance(ast.FunctionDef, ast.AsyncFunctionDef)` tests the class object rather than `node`, so the branch is always false. Click handling emits Qt’s clicked-column argument instead of stored `col`.
- **Resolution:** Use `isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))` and emit `(line, col)`. Add AST unit tests for sync/async top-level functions, classes, and indented methods.

### BUG-023 — Find bar shortcuts contradict the UI and hijack arrow keys

- **File:** `code_inteligence/find_replace.py:190-209`
- **Evidence:** Comments/tooltips promise Enter and Shift+Enter, but shortcuts are Down and Up; `find_input.returnPressed` is never connected.
- **Resolution:** Connect Enter to next and Shift+Enter to previous (using appropriate `QShortcut` contexts), leaving arrow keys for cursor/history movement.

### BUG-024 — Find Previous starts from invalid/wrong coordinates

- **File:** `main.py:2381-2400`
- **Evidence:** It subtracts one from both line and column. At line 0 it passes negative coordinates; elsewhere it skips the preceding part of the current line.
- **Resolution:** Start backward search immediately before the current selection/caret using QScintilla’s expected coordinates, handling column zero by moving to the previous line’s end. Add boundary/wrap tests.

### BUG-025 — Replace can overwrite an unrelated selection

- **File:** `main.py:2402-2414`
- **Evidence:** Any selected text is replaced without checking it against `find_text` and the case/whole-word/regex options.
- **Resolution:** Verify the selected range is the active match; otherwise find the next match without replacing. Guard zero-length regexes in Replace All.

### BUG-026 — Project search excludes Markdown and text files

- **File:** `side_bar_widgets/fuzzy_searcher.py:43-64`
- **Impact:** A Markdown editor’s project search cannot search `.md` or `.txt`, its core document types.
- **Resolution:** Exclude only verified binary/generated types and make exclusions configurable.

### BUG-027 — Project-search flag includes `venv` instead of excluding it

- **File:** `side_bar_widgets/fuzzy_searcher.py:60-64`
- **Evidence:** `venv` starts excluded, then `if self.search_project: exclude_dirs.remove("venv")`.
- **Impact:** Project-wide searches can traverse huge virtual environments.
- **Resolution:** Keep common environment/build directories excluded for project searches; define clearly what the false branch should search.

### BUG-028 — Search drops the latest query and can terminate without a result signal

- **File:** `side_bar_widgets/fuzzy_searcher.py:36-47, 72-119`
- **Evidence:** `update()` drops every request while the thread is running. `is_binary()` and filesystem walk errors are not caught, so the worker can exit before `finished.emit()`.
- **Resolution:** Queue/debounce the most recent immutable request, add cancellation/generation IDs, catch per-file `OSError`/permission errors, and emit completion in `finally`. Display line numbers as 1-based.

### BUG-029 — Markdown link definitions are misclassified as footnotes

- **File:** `markdown_editor/markdowncustomlexer.py:294-296`
- **Evidence:** `^\[\^?[^\]]+\]:` makes `^` optional, so `[id]: url` matches.
- **Resolution:** Require `^\[\^[^\]]+\]:` for footnotes and implement ordinary link definitions separately.

### BUG-030 — Any pipe character is classified as a table row

- **File:** `markdown_editor/markdowncustomlexer.py:283-292`
- **Impact:** Prose such as “A | B” is styled as a table without a separator/header structure.
- **Resolution:** Recognize a table only with a valid adjacent delimiter row or a stricter cell grammar; exclude escaped/code-span pipes.

### BUG-031 — Markdown fence detection violates indentation rules

- **File:** `markdown_editor/markdowncustomlexer.py:173-233`
- **Evidence:** Unlimited spaces/tabs are stripped before recognizing a fence.
- **Impact:** Four-space indented code containing backticks/tildes can incorrectly open/close a fenced block.
- **Resolution:** Permit at most three leading spaces for CommonMark fences and treat tabs/4+ spaces appropriately. Add conformance cases.

### BUG-032 — Multiline HTML comments have no persistent lexer state

- **File:** `markdown_editor/markdowncustomlexer.py` HTML/comment styling branch
- **Impact:** Only the first line of `<!-- ... -->` is reliably styled; subsequent lines are parsed as normal Markdown.
- **Resolution:** Add comment state to `_state_before`/block scanning and close it only at `-->`.

### BUG-033 — Hacker mode cannot show scanlines or toggle off

- **Files:** `main.py:785-788, 1340-1362`; `cozy/overlays.py:12-28`
- **Evidence:** The attribute is created as `scanlines` but checked as `scnalines`; `_hacker` is never assigned, so the menu always requests `True`; the overlay implements `painterEvent` instead of Qt’s `paintEvent`.
- **Resolution:** Correct both spellings, implement `paintEvent`, assign/persist `_hacker`, initialize action checked state, synchronize overlay geometry/stacking, and test repeated on/off toggles.

### BUG-034 — “Reset to theme” keeps a permanent paper-color override

- **Files:** `code_settings/settings_dialog.py:347-415`; `main.py:1220-1320, 1461-1464`
- **Evidence:** Reset replaces the color with a concrete `theme_paper` value; `get_settings()` always returns a hex string; save always writes it. Theme switches therefore retain the prior theme’s background.
- **Resolution:** Represent “no override” as `None`/sentinel, remove the `paper_color` QSettings key on reset, and resolve the active theme paper at application time.

### BUG-035 — Resource lookup depends on process working directory

- **Files:** `main.py:38-53`; `cozy/neko.py:30-35`; `cozy/cat_controller.py:43-56`
- **Impact:** Launching `python /path/to/main.py` from another directory breaks stylesheet, icons, themes, and cat sprites.
- **Resolution:** For source runs, resolve from `Path(__file__).resolve().parent` (or the package resource API); retain `_MEIPASS` only for frozen data.

### BUG-036 — Frozen Python lexer points outside the bundle

- **File:** `python_editor/custompythonlexer.py:19-25, 835-856`
- **Evidence:** `_resource_path(os.path.join("../themes", "theme.json"))` becomes `_MEIPASS/../themes/theme.json`.
- **Impact:** Packaged builds fall back or fail to load the intended theme.
- **Resolution:** Define a single application resource root and request `themes/theme.json` from it; do not embed `..` in resource-relative paths.

### BUG-037 — Qt resource bundle is not reproducible and is never registered

- **Files:** `icons/resources.qrc:2-5`; `resources_rc.py`; `side_bar_widgets/file_manager.py:145-155`
- **Evidence:** QRC references missing `icons/close-icon.png` while only `.svg` exists. No source imports `resources_rc`, so `:/icons/close-icon.png` is not registered at runtime.
- **Impact:** Rebuilding resources fails and the delete dialog icon is blank.
- **Resolution:** Point QRC/code to `close-icon.svg` (or add the intended PNG), regenerate `resources_rc.py`, and import it during application startup. Add a resource-existence smoke test.

### BUG-038 — Theme generator is working-directory-sensitive and creates duplicate names

- **File:** `themes/theme_factory.py:1-5, 70-113`
- **Evidence:** The documented `python theme_factory.py` from `themes/` looks for `themes/theme.json` below itself. Running from root works but writes `sunset.json`, `midnight.json`, and `bloodmoon.json`, while tracked files use trailing underscores; it also creates an untracked `forest.json`.
- **Resolution:** Anchor input/output to `Path(__file__).resolve().parent`, standardize filenames, and test generation into a temporary directory against a schema/golden set.

### BUG-039 — Cython build path is Windows-specific; binary supports only one ABI/platform

- **Files:** `python_editor/setup.py:1-32`; `lexer_fast.cp314-win_amd64.pyd`; `python_editor/lexer_fast.c`
- **Evidence:** Setup uses `"python_editor\lexer_fast.pyx"`, producing an invalid-escape warning and failing as a portable path. The committed `.pyd` is PE32+ x86-64 for CPython 3.14 only.
- **Resolution:** Use `Path(__file__).with_name("lexer_fast.pyx")`/an explicit `Extension`, build wheels for supported Python/OS/architectures in CI, or omit binaries and make the tested fallback correct. Regenerate C/binaries from the same tagged Cython version.

### BUG-040 — Python runner reports “started” before the process starts and parses Windows args incorrectly

- **File:** `python_editor/python_runner.py:7-35, 51-74, 100-154`
- **Evidence:** `process_started` is declared twice and emitted immediately after `QProcess.start()`, even if launch fails. `shlex.split()` defaults to POSIX parsing, which treats Windows backslashes/quotes differently.
- **Resolution:** Declare the signal once, connect QProcess `started` and `errorOccurred`, and use a platform-appropriate argument parser (`posix=False` on Windows) or structured argument input. Avoid synchronous two-second GUI blocking on stop.

### BUG-041 — Git status parser breaks on valid filenames

- **File:** `python_editor/git_integration.py:40-96`
- **Evidence:** It parses newline-delimited text, strips quotes without Git unescaping, and splits rename records on the literal ` -> `.
- **Impact:** Filenames with newlines, quotes, escape sequences, or ` -> ` are colored incorrectly.
- **Resolution:** Run `git status --porcelain=v1 -z` in bytes mode and parse NUL-delimited ordinary/rename records exactly. Log unexpected failures rather than swallowing all exceptions.

### BUG-042 — Editor conversion loses integrations and state

- **File:** `main.py:2290-2334`
- **Evidence:** `EditorClass(path=path)` omits the shared Ruff client when converting to Python. It does not apply current settings and discards cursor, selection, scroll, and undo state.
- **Impact:** A converted Python tab has no Ruff diagnostics and inconsistent UI state.
- **Resolution:** Construct via `get_editor(path, is_python_file=...)`, apply settings, migrate relevant view state, and establish/close LSP identity correctly. Decide explicitly whether conversion across file extensions is temporary or requires Save As.

### BUG-043 — Session persistence is not called and restore trusts malformed data

- **File:** `main.py:2470-2586`
- **Evidence:** `save_session()` has no call site. Its settings write is incorrectly inside the per-editor loop, so a zero-tab session never clears old data. Restore accesses `entry["group"]`, `entry["path"]`, and group indices without schema/range validation. The documented per-tab Python mode is ignored for saved paths because `get_editor()` always chooses by extension.
- **Resolution:** Call it from the successful close flow, write once after the loop, clear/record empty sessions, validate types/ranges/limits, and pass the saved editor mode through an API that does not override it from suffix.

### BUG-044 — Save rewrites line endings and is not atomic

- **File:** `main.py:897-924, 2092-2171`
- **Evidence:** Every save normalizes CRLF to LF regardless of the loaded file/configured editor EOL and writes directly with `Path.write_bytes()`.
- **Impact:** Large unrelated diffs on Windows and possible truncation/corruption if writing is interrupted.
- **Resolution:** Detect/preserve the file’s EOL mode unless the user opts to convert it. Write a temporary file in the same directory, flush/fsync as appropriate, and atomically replace while preserving permissions.

## Low-severity defects

### BUG-045 — Markdown autocomplete is populated with Python symbols

- **File:** `markdown_editor/markdowneditor.py:41-67`
- **Impact:** Markdown completion is noisy and `dir(__builtins__)` may add dictionary attributes instead of Python builtins depending on execution context. Theme-wide styling is also applied before `setLexer()`, which may reset parts of it.
- **Resolution:** Use Markdown constructs/project words for the API, remove Python module enumeration, and apply editor-wide theme styling after lexer/margin setup.

### BUG-046 — File-manager commands are unreachable or ignore selection

- **File:** `side_bar_widgets/file_manager.py:117-143, 257-302`
- **Evidence:** Handler code for “Open In File Manager” exists but no such action is added. “New Folder” always uses the model root, unlike “New File.” The Windows directory reveal command uses fragile shell-style quoting.
- **Resolution:** Add the action and pass `ix`; create folders in the selected directory; use argument-list subprocess calls (`explorer`, path) and handle launch errors.

### BUG-047 — Signature tooltip uses the wrong Scintilla parameter slot

- **File:** `python_editor/pythoneditor.py:453-471`
- **Evidence:** `SCI_POINTX/YFROMPOSITION` expect the position in `lParam`; calls pass it as the second/wParam argument. The repository’s own `cozy/power_mode.py` documents and fixes the same defect.
- **Impact:** Signature tooltip appears at document position 0/top-left instead of the caret.
- **Resolution:** Call `SendScintilla(message, 0, caret)` for both coordinates and test with the caret far from line 1.

### BUG-048 — Close-all helper can continue after a cancelled close

- **File:** `code_inteligence/multi_tab_view.py` close-all group helper
- **Impact:** If this lower-level helper is wired directly, it emits requests for later tabs even after a user cancels an earlier unsaved prompt.
- **Resolution:** Return/propagate close success and stop at the first cancellation, matching `MainWindow._close_all_tabs()`.

### BUG-049 — Cat idle timing contradicts its contract

- **File:** `cozy/neko.py:23-25`
- **Evidence:** Comment/docstring says 30 seconds, but `IDLE_MS = 10 * 1000`.
- **Resolution:** Change the constant to 30,000 ms or update the UI/documentation to the intended 10-second behavior.

## Release-engineering and maintainability gaps

### GAP-001 — No installable dependency/project manifest

There is no root `pyproject.toml`, `requirements.txt`, package metadata, or installation README. A fresh clone does not state or constrain PyQt5, QScintilla, Markdown, Jedi, Ruff, Cython, and packaging/build requirements. Add a root `pyproject.toml` with runtime and optional build dependencies, supported Python versions, console/GUI entry point, and reproducible setup instructions.

### GAP-002 — No automated test suite or CI

There is no tracked test directory or GitHub Actions workflow. Add unit tests for pure lexers/search/parsers, Qt tests for save/close/rename/terminal flows, LSP framing tests with a fake server, and matrix CI for supported operating systems/Python versions. At minimum CI should compile all Python, lint, parse assets/config, and build the Cython extension.

### GAP-003 — Implementation documents describe code that does not exist

`Implementations and Updates/Tier 3 Features — Updated Sections (v1.9.0).md` says close prompts unsaved files and calls `_save_session()`, and multiple docs/settings comments claim `_run_ruff_before_save()` already exists. The audited code does neither. Update docs alongside fixes and distinguish proposals from implemented behavior.

### GAP-004 — Generated and platform artifacts lack provenance checks

`python_editor/lexer_fast.c`, `lexer_fast.cp314-win_amd64.pyd`, and `resources_rc.py` are committed without a build workflow that proves they match their sources. Add generated-file headers/tool versions and CI that regenerates and diffs them, or publish binaries as release artifacts rather than source-controlled files.

## Recommended remediation order

1. Fix BUG-001 through BUG-004 and add regression tests before another release.
2. Repair shutdown/save/delete/rename workflows (BUG-002, 003, 008, 009, 010, 043, 044) to protect user data.
3. Fix the Python lexer fallback and packaging path/build issues (BUG-005, 035-039), then test on Linux/macOS and multiple Python ABIs.
4. Stabilize Ruff/Jedi concurrency and lifecycle behavior (BUG-015-021, 047).
5. Repair terminal, search, outline, Markdown lexer, settings, and theme behavior.
6. Add the root project manifest and CI/test matrix before treating generated binaries as release-ready.

## Validation checklist after fixes

- `python -m compileall -q .` succeeds.
- Fresh-clone install works from documented commands on every supported OS/Python version.
- Closing with dirty tabs prompts once per tab; Cancel keeps the window open; services stop cleanly.
- Deleting/renaming open files and directories updates or closes every affected editor and LSP document.
- Python and Cython lexers produce identical spans/state for a shared corpus.
- Two untitled Python tabs receive independent Ruff diagnostics.
- Save, Save As, Save All, Run, and format-on-save share one tested, atomic pipeline.
- Project search finds `.md`/`.txt`, ignores virtual environments, and always returns the latest query.
- All QRC entries exist, `resources_rc` is imported, and resource icons load.
- All JSON/SVG/QRC/PNG validation checks remain green.

## Per-file coverage appendix

Legend: `FINDING` points to one or more defects above; `PASS` means no separate actionable defect was found in that file; `GENERATED` means provenance/output was reviewed rather than treating generated code as handwritten; `ASSET PASS` means the individual file decoded/parsed successfully.

### (repository root)

- `.gitignore` — FINDING GAP-001, GAP-002; does not exclude crash_log.txt (BUG-012)
- `crash_log.txt` — FINDING BUG-012
- `lexer_fast.cp314-win_amd64.pyd` — GENERATED/platform binary; FINDING BUG-039, GAP-004
- `main.py` — FINDING BUG-002, 003, 008-014, 018, 024, 025, 033-035, 042-044
- `resources_rc.py` — GENERATED; FINDING BUG-037, GAP-004

### Implementations and Updates

- `Implementations and Updates/4-Implementation-Features.md` — PASS for links; reviewed for implementation drift (GAP-003)
- `Implementations and Updates/Auto_Indentation_Implementation_Guide.md` — PASS for links; reviewed for implementation drift (GAP-003)
- `Implementations and Updates/Code Intelligence Features — Implementation Guide.md` — PASS for links; reviewed for implementation drift (GAP-003)
- `Implementations and Updates/Code_Intelligence.png` — ASSET PASS
- `Implementations and Updates/Documentation.md` — PASS for links; reviewed for implementation drift (GAP-003)
- `Implementations and Updates/Editing_Features.png` — ASSET PASS
- `Implementations and Updates/File_Management_Features.png` — ASSET PASS
- `Implementations and Updates/Markdown_Features.png` — ASSET PASS
- `Implementations and Updates/Project_WorkspaceFeatures.png` — ASSET PASS
- `Implementations and Updates/Python_Executive_Features.png` — ASSET PASS
- `Implementations and Updates/Search_Improvements.png` — ASSET PASS
- `Implementations and Updates/Settings_Configurations.png` — ASSET PASS
- `Implementations and Updates/Tier 2 Features — Implementation Guide.md` — PASS for links; reviewed for implementation drift (GAP-003)
- `Implementations and Updates/Tier 3 Features — Implementation Guide (1).md` — PASS for links; reviewed for implementation drift (GAP-003)
- `Implementations and Updates/Tier 3 Features — Updated Sections (v1.9.0).md` — PASS for links; reviewed for implementation drift (GAP-003)
- `Implementations and Updates/UI_UX_Features.png` — ASSET PASS

### code_inteligence

- `code_inteligence/autocompleter.py` — FINDING BUG-015
- `code_inteligence/definition_finder.py` — FINDING BUG-015, BUG-016
- `code_inteligence/documentation_popup.py` — PASS
- `code_inteligence/find_replace.py` — FINDING BUG-023
- `code_inteligence/hover_helper.py` — FINDING BUG-015, BUG-016
- `code_inteligence/indentation_helper.py` — PASS
- `code_inteligence/multi_tab_view.py` — FINDING BUG-048
- `code_inteligence/references_finder.py` — FINDING BUG-001
- `code_inteligence/signature_helper.py` — FINDING BUG-015, BUG-016

### code_settings

- `code_settings/settings_dialog.py` — FINDING BUG-008, BUG-034

### con_term

- `con_term/console_widget.py` — PASS
- `con_term/terminal_widget.py` — FINDING BUG-006

### cozy

- `cozy/cat_controller.py` — FINDING BUG-035
- `cozy/neko.py` — FINDING BUG-035, BUG-049
- `cozy/overlays.py` — FINDING BUG-033
- `cozy/power_mode.py` — PASS

### css

- `css/style.qss` — PASS

### icons

- `icons/app-icon-256.png` — ASSET PASS (decoded; dimensions checked)
- `icons/app-icon.png` — ASSET PASS (decoded; dimensions checked)
- `icons/app-icon.svg` — ASSET PASS (XML parsed)
- `icons/cat/alert_0.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/alert_1.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/alert_2.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/alert_3.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/attack_0.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/attack_1.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/attack_2.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/attack_3.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/attack_4.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/attack_5.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/attack_6.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_0.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_1.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_10.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_11.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_2.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_3.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_4.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_5.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_6.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_7.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_8.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_9.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_idle_0.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_idle_1.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_idle_10.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_idle_11.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_idle_2.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_idle_3.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_idle_4.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_idle_5.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_idle_6.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_idle_7.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_idle_8.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_idle_9.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_pop_0.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_pop_1.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_pop_2.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_pop_3.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_pop_4.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_pop_5.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_pop_6.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_pop_7.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_pop_8.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/box_pop_9.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/cry_0.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/cry_1.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/cry_2.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/cry_3.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/dead_0.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/dead_1.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/dead_10.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/dead_11.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/dead_2.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/dead_3.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/dead_4.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/dead_5.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/dead_6.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/dead_7.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/dead_8.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/dead_9.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/hurt_0.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/hurt_1.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/hurt_2.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/hurt_3.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/hurt_4.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/hurt_5.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/hurt_6.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/hurt_7.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/idle_0.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/idle_1.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/idle_2.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/idle_3.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/idle_4.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/idle_5.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/jump_0.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/jump_1.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/jump_10.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/jump_11.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/jump_2.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/jump_3.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/jump_4.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/jump_5.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/jump_6.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/jump_7.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/jump_8.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/jump_9.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/lay_down_0.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/lay_down_1.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/lay_down_2.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/lay_down_3.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/lay_down_4.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/lay_down_5.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/lay_down_6.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/lay_down_7.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/party_0.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/party_1.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/party_2.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/party_3.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/sleep_0.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/sleep_1.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/sleep_2.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/sleep_3.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/stretch_0.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/stretch_1.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/stretch_2.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/stretch_3.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/stretch_4.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/stretch_5.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/stretch_6.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/stretch_7.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/stretch_8.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/stretch_9.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/type_excited_0.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/type_excited_1.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/type_excited_2.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/walk_0.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/walk_1.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/walk_2.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/walk_3.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/walk_4.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/walk_5.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/wiggle_0.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/wiggle_1.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/wiggle_2.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat/wiggle_3.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat_sheets/alert.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat_sheets/attack.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat_sheets/box.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat_sheets/box_idle.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat_sheets/box_pop.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat_sheets/cry.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat_sheets/hurt.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat_sheets/idle.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat_sheets/jump.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat_sheets/lay_down.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat_sheets/party.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat_sheets/run_lay.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat_sheets/sleep.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat_sheets/stretch.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat_sheets/type_excited.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat_sheets/walk.png` — ASSET PASS (decoded; dimensions checked)
- `icons/cat_sheets/wiggle.png` — ASSET PASS (decoded; dimensions checked)
- `icons/checklist-active.png` — ASSET PASS (decoded; dimensions checked)
- `icons/checklist-active.svg` — ASSET PASS (XML parsed)
- `icons/checklist.png` — ASSET PASS (decoded; dimensions checked)
- `icons/checklist.svg` — ASSET PASS (XML parsed)
- `icons/close-icon.svg` — ASSET PASS (XML parsed)
- `icons/code-active.png` — ASSET PASS (decoded; dimensions checked)
- `icons/code-active.svg` — ASSET PASS (XML parsed)
- `icons/code.png` — ASSET PASS (decoded; dimensions checked)
- `icons/code.svg` — ASSET PASS (XML parsed)
- `icons/console-active.png` — ASSET PASS (decoded; dimensions checked)
- `icons/console-active.svg` — ASSET PASS (XML parsed)
- `icons/console.png` — ASSET PASS (decoded; dimensions checked)
- `icons/console.svg` — ASSET PASS (XML parsed)
- `icons/file.png` — ASSET PASS (decoded; dimensions checked)
- `icons/file.svg` — ASSET PASS (XML parsed)
- `icons/folder-active.png` — ASSET PASS (decoded; dimensions checked)
- `icons/folder-active.svg` — ASSET PASS (XML parsed)
- `icons/folder-icon-blue.svg` — ASSET PASS (XML parsed)
- `icons/folder.png` — ASSET PASS (decoded; dimensions checked)
- `icons/folder.svg` — ASSET PASS (XML parsed)
- `icons/outline-active.png` — ASSET PASS (decoded; dimensions checked)
- `icons/outline-active.svg` — ASSET PASS (XML parsed)
- `icons/outline.png` — ASSET PASS (decoded; dimensions checked)
- `icons/outline.svg` — ASSET PASS (XML parsed)
- `icons/resources.qrc` — FINDING BUG-037
- `icons/search-active.png` — ASSET PASS (decoded; dimensions checked)
- `icons/search-active.svg` — ASSET PASS (XML parsed)
- `icons/search-icon.svg` — ASSET PASS (XML parsed)
- `icons/search.png` — ASSET PASS (decoded; dimensions checked)
- `icons/search.svg` — ASSET PASS (XML parsed)
- `icons/settings-active.png` — ASSET PASS (decoded; dimensions checked)
- `icons/settings-active.svg` — ASSET PASS (XML parsed)
- `icons/settings.png` — ASSET PASS (decoded; dimensions checked)
- `icons/settings.svg` — ASSET PASS (XML parsed)
- `icons/terminal-active.png` — ASSET PASS (decoded; dimensions checked)
- `icons/terminal-active.svg` — ASSET PASS (XML parsed)
- `icons/terminal.png` — ASSET PASS (decoded; dimensions checked)
- `icons/terminal.svg` — ASSET PASS (XML parsed)

### markdown_editor

- `markdown_editor/markdowncustomlexer.py` — FINDING BUG-029-032
- `markdown_editor/markdowneditor.py` — FINDING BUG-045

### python_editor

- `python_editor/custompythonlexer.py` — FINDING BUG-005, BUG-036
- `python_editor/git_integration.py` — FINDING BUG-041
- `python_editor/lexer_fast.c` — GENERATED; FINDING BUG-039, GAP-004
- `python_editor/lexer_fast.pyx` — PASS (handwritten Cython scanner reviewed; parity tests still required by BUG-005/GAP-002)
- `python_editor/python_runner.py` — FINDING BUG-040
- `python_editor/pythoneditor.py` — FINDING BUG-015, 016, 047
- `python_editor/setup.py` — FINDING BUG-039

### ruff_implementation

- `ruff_implementation/pyproject.toml` — FINDING BUG-021
- `ruff_implementation/ruff_diagnostics_model.py` — PASS
- `ruff_implementation/ruff_diagnostics_view.py` — PASS (quick-fix UI remains unwired through controller)
- `ruff_implementation/ruff_lsp_client.py` — FINDING BUG-018-020
- `ruff_implementation/ruff_lsp_controller.py` — FINDING BUG-010, BUG-017-020

### side_bar_widgets

- `side_bar_widgets/code_outline.py` — FINDING BUG-022
- `side_bar_widgets/file_manager.py` — FINDING BUG-004, 007, 037, 046
- `side_bar_widgets/fuzzy_searcher.py` — FINDING BUG-026-028

### themes

- `themes/bloodmoon_.json` — ASSET PASS (JSON parsed; theme structure inspected)
- `themes/hacker.json` — ASSET PASS (JSON parsed; theme structure inspected)
- `themes/midnight_.json` — ASSET PASS (JSON parsed; theme structure inspected)
- `themes/sunset_.json` — ASSET PASS (JSON parsed; theme structure inspected)
- `themes/theme.json` — ASSET PASS (JSON parsed; theme structure inspected)
- `themes/theme_factory.py` — FINDING BUG-038

Coverage total: **245 tracked files**.
