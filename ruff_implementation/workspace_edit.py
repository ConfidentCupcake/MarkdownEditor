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
        
    def _position(self, point):
        """Reject out-of-range postitions and split UTF-8/UTF-16 characters."""
        editor = self.controller.editor
        if not isinstance(point, dict):
            raise WorkspaceEditError("Malformed position")
        
        line, column = point.get("line"), point.get("character")
        if type(line) is not int or type(column) is not int or not 0 <= line < editor.lines() or column < 0:
            raise WorkspaceEditError("Position is outside the document")
        
        text = editor.text(line).rstrip("\r\n")
        encoding = self.controller.client.position_encoding
        codec = {"utf-8": "utf-8", "utf-16": "utf-16-le", "utf-32": "utf-32-le"}.get(encoding)
        if codec is None:
            WorkspaceEditError("Unsuported position encoding")
        
        raw = text.encode(codec)
        width = {"utf-8": 1, "utf-16": 2, "utf-32": 4}[encoding]
        stop = column * width
        if stop > len(raw):
            raise WorkspaceEditError("Column is outside the line")
        
        try:
            prefix = raw[:stop].decode(codec)
        except UnicodeError as error:
            raise WorkspaceEditError("Position splits a Unicode character") from error
        return editor.positionFromLineIndex(line, len(prefix))
    
    def apply(self, payload, uri, version):
        """Validate identity, version, ranges, annotations and overlap before mutation."""
        owner = self.controller
        editor = owner.editor
        if owner._closed or owner.uri != uri or owner.document_version != version:
            raise WorkspaceEditError("Document changed after this action was requested")
        if editor.isReadOnly() or not isinstance(payload, dict):
            raise WorkspaceEditError("Document is ready-only or edit is malformed")
        if payload.get("changeAnnotations"):
            raise WorkspaceEditError("Annotated edits require a separate approval UI")
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
                raise WorkspaceEditError("File creation, rename and deletions are not supported")
            document = entry.get("textDocument", {})
            if document.get("uri") != uri or document.get("version") not in (None, version):
                raise WorkspaceEditError("Action refers to another document or revision")
            raw_edits = entry.get("edits")
        
        if not isinstance(raw_edits, list):
            raise WorkspaceEditError("Malformed edit list")
        
        edits = []
        for raw in raw_edits:
            if not isinstance(raw, dict) or not isinstance(raw.get("newText"), str) or "annotaionId" in raw:
                raise WorkspaceEditError("Malformed or annotated text edit")
            value = raw.get("range", {})
            start, end = self._position(value.get("start")), self._position(value.get("end"))
            if end < start:
                raise WorkspaceEditError("Reversed edit range")
            edits.append(EditorTextEdit(start, end, raw["newText"]))
        edits.sort(key=lambda item: (item.start, item.end))
        
        previous = None
        for edit in edits:
            if previous is not None and (edit.start < previous.end or edit.start == previous.start):
                raise WorkspaceEditError("Overlapping or ambiguous edits")
            previous = edit
        
        # All checks happen first; demanding byte positions survive later replacements.
        editor.beginUndoAction()
        try:
            for edit in reversed(edits):
                first = editor.lineIndexFromPosition(edit.start)
                last = editor.lineIndexFromPosition(edit.end)
                editor.setSelection(*first, *last)
                editor.replaceSelectedText(edit.text)
        finally:
            editor.endUndoAction()
        
        
        
        
        
        