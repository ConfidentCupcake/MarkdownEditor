"""Validate complete single-document LSP edits before touching the buffer."""
from dataclasses import dataclass


class WorkspaceEditError(ValueError):
    """An action cannot be applied completely to the requested revision."""
    
@dataclass(frozen=True)
class EditorTextEdit:
    """One checked replacement using absolute UTF-8 bytes positions."""
    start: int
    end: int
    text: str
    

class WorkspaceEditApplier:
    """Apply a single-document edit as one undo group after complete validation."""
    def __init__(self, controller):
        """Use the requesting Ruff controller's URI, version, and position encoding."""
        self.controller = controller

    def _position(self, point) -> int:
        """Validate an LSP point and convert it to an absolute UTF-8 byte offset.

        Reject malformed fields, positions past a line, and boundaries inside
        a UTF-8 sequence or UTF-16 surrogate pair. The controller's negotiated
        encoding determines units; Scintilla's document storage remains UTF-8.
        """
        editor = self.controller.editor
        if not isinstance(point, dict):
            raise WorkspaceEditError("Malformed position")
        line, column = point.get("line"), point.get("character")
        if (type(line) is not int or type(column) is not int
                or not 0 <= line < editor.lines() or column < 0):
            raise WorkspaceEditError("Position is outside the document")
        text = editor.text(line).rstrip("\r\n")
        encoding = self.controller.client.position_encoding
        codec = {"utf-8": "utf-8", "utf-16": "utf-16-le", "utf-32": "utf-32-le"}.get(encoding)
        if codec is None:
            raise WorkspaceEditError("Unsupported position encoding")
        width = {"utf-8": 1, "utf-16": 2, "utf-32": 4}[encoding]
        raw = text.encode(codec)
        stop = column * width
        if stop > len(raw):
            raise WorkspaceEditError("Column is outside the line")
        try:
            prefix = raw[:stop].decode(codec)
        except UnicodeError as error:
            raise WorkspaceEditError("Position splits a Unicode character") from error
        return editor.SendScintilla(editor.SCI_POSITIONFROMLINE, line) + len(prefix.encode("utf-8"))
    
    def apply(self, payload, uri, version):
        """Apply a validated single-document WorkspaceEdit as one undo operation.

        Args:
            payload: WorkspaceEdit containing changes or one textDocument change.
            uri: URI captured when the user requested the action.
            version: Captured editor revision; different live text is rejected.

        Validate every entry, Unicode boundary, annotation and overlap before
        the first mutation. Only direct edits to this open document are supported;
        resource operations and cross-file actions are rejected in their entirety.
        Apply in descending byte order so earlier positions remain valid.
        """
        owner = self.controller
        editor = owner.editor
        if owner._closed or owner.uri != uri or owner.document_version != version:
            raise WorkspaceEditError("Document changed after this action was requested")
        if editor.isReadOnly() or not isinstance(payload, dict):
            raise WorkspaceEditError("Document is read-only or edit is malformed")
        if payload.get("changeAnnotations"):
            raise WorkspaceEditError("Annotated edits are not supported")
        if "changes" in payload and "documentChanges" in payload:
            raise WorkspaceEditError("Ambiguous workspace edit")
        if "changes" in payload:
            changes = payload["changes"]
            if not isinstance(changes, dict) or set(changes) != {uri}:
                raise WorkspaceEditError("Only complete single-document edits are supported")
            raw_edits = changes[uri]
        else:
            entries = payload.get("documentChanges")
            if not isinstance(entries, list) or len(entries) != 1:
                raise WorkspaceEditError("Only one text-document change is supported")
            entry = entries[0]
            if not isinstance(entry, dict) or "kind" in entry:
                raise WorkspaceEditError("File creation, rename and deletion are not supported")
            document = entry.get("textDocument")
            if not isinstance(document, dict) or document.get("uri") != uri:
                raise WorkspaceEditError("Action refers to another or malformed document")
            requested_version = document.get("version")
            if requested_version is not None and (type(requested_version) is not int or requested_version != version):
                raise WorkspaceEditError("Action refers to another revision")
            raw_edits = entry.get("edits")
        if not isinstance(raw_edits, list):
            raise WorkspaceEditError("Malformed edit list")
        edits = []
        for raw in raw_edits:
            if (not isinstance(raw, dict) or not isinstance(raw.get("newText"), str)
                    or "annotationId" in raw):
                raise WorkspaceEditError("Malformed or annotated text edit")
            value = raw.get("range")
            if not isinstance(value, dict):
                raise WorkspaceEditError("Malformed edit range")
            start, end = self._position(value.get("start")), self._position(value.get("end"))
            if end < start:
                raise WorkspaceEditError("Reversed edit range")
            try:
                raw["newText"].encode("utf-8")
            except UnicodeError as error:
                raise WorkspaceEditError("Replacement contains invalid Unicode") from error
            edits.append(EditorTextEdit(start, end, raw["newText"]))
        edits.sort(key=lambda item: (item.start, item.end))
        previous = None
        for edit in edits:
            if previous is not None and (edit.start < previous.end or edit.start == previous.start):
                raise WorkspaceEditError("Overlapping or ambiguous edits")
            previous = edit
        if not edits:
            return
        editor.beginUndoAction()
        try:
            for edit in reversed(edits):
                replacement = edit.text.encode("utf-8")
                # Byte targets avoid mixing LSP units with QScintilla selections.
                editor.SendScintilla(editor.SCI_SETTARGETSTART, edit.start)
                editor.SendScintilla(editor.SCI_SETTARGETEND, edit.end)
                editor.SendScintilla(editor.SCI_REPLACETARGET, len(replacement), replacement)
        finally:
            editor.endUndoAction()
        
        
        
        
        
        