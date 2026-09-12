import re
import json
from PyQt5.QtGui import QFont, QColor
from PyQt5.Qsci import QsciLexerCustom

import keyword
import types
import builtins
import os
import sys
from pathlib import Path
from markdowneditor_assets import asset_path

try:
    from lexer_fast import style_chunk as _cython_style
    from lexer_fast import compute_state_before as _cython_state
    _HAS_CYTHON = True
except ImportError:
    _HAS_CYTHON = False

def _resource_path(relative_path: str) -> str:
    """Compatibility wrapper around the shared packaged-asset resolver."""
    return asset_path(relative_path)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Common stdlib module names. They are rendered grey in PyCharm (module
# references), so we piggyback on the builtins set (which is also grey).
_STDLIB_MODULES = frozenset((
    "os", "sys", "re", "json", "math", "time", "datetime", "pathlib",
    "typing", "collections", "functools", "itertools", "subprocess",
    "threading", "shutil", "glob", "random", "logging", "io", "abc",
    "enum", "copy", "string", "textwrap", "uuid", "socket", "http",
    "urllib", "platform", "ctypes", "zipfile", "argparse", "signal",
    "queue", "tempfile", "traceback", "unittest", "sqlite3", "hashlib",
    "base64", "struct", "codecs", "contextlib", "dataclasses", "inspect",
    "importlib", "warnings", "statistics", "decimal", "fractions",
    "array", "bisect", "heapq", "operator", "pprint", "weakref",
))


# ---------------------------------------------------------------------------
# Pure-Python fallback for lexer_fast.pyx (identical styling logic).
# Used only when the Cython module is not compiled. Kept in sync with
# lexer_fast.pyx - if you change one, change the other.
# ---------------------------------------------------------------------------

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
        1 if (state & 1) else 0,
        1 if (state & 2) else 0,
        1 if (state & 4) else 0,
        '"' if (state & 8) else "'",
        1 if (state & 16) else 0,
        1 if (state & 32) else 0,
        1 if (state & 64) else 0,
        (state >> 7) & 7,
        1 if (state & 1024) else 0,
        1 if (state & 2048) else 0,
        1 if (state & 4096) else 0,
    )


def _py_skip_string(text, i, limit, delim, triple):
    """text is bytes; delim is a 1-char latin-1 string."""
    escape = False
    length = len(text)
    while i < limit:
        c = text[i:i + 1].decode("latin-1")
        if escape:
            escape = False
            i += 1
            continue
        if c == "\\":
            escape = True
            i += 1
            continue
        if triple:
            if c == delim:
                if i + 2 < length and text[i + 1:i + 2].decode("latin-1") == delim \
                        and text[i + 2:i + 3].decode("latin-1") == delim:
                    return i + 3
                i += 1
                continue
            i += 1
            continue
        if c == delim:
            return i + 1
        if c in "\n\r":
            return i
        i += 1
    return i


def _py_compute_state(text, target_pos):
    """Scan from byte 0 to target_pos and return the packed lexer state.
    `text` must be a bytes object; all positions/lengths are byte-based."""
    target_pos = min(target_pos, len(text))
    length = len(text)
    i = 0
    in_string = in_comment = triple_string = 0
    string_delim = ""
    in_fstring = escape_next = in_fexpr = fexpr_depth = is_docstring = 0
    bracket_depth = pending_def = pending_class = 0
    expect_docstring = at_class_body = 0

    while i < target_pos:
        c = text[i:i + 1].decode("latin-1")

        if in_fexpr:
            if c == "}":
                fexpr_depth -= 1
                if fexpr_depth <= 0:
                    fexpr_depth = 0
                    in_fexpr = 0
                i += 1
                continue
            if c == "{":
                fexpr_depth += 1
                i += 1
                continue
            if c in "frb" and i + 1 < target_pos and text[i + 1:i + 2].decode("latin-1") in "\"'":
                # decode to a 1-char string — bytes indexing gives an int here
                delim = text[i + 1:i + 2].decode("latin-1")
                i += 2
                if i + 1 < length and text[i:i + 1].decode("latin-1") == delim and text[i + 1:i + 2].decode("latin-1") == delim:
                    i += 2
                    i = _py_skip_string(text, i, target_pos, delim, True)
                else:
                    i = _py_skip_string(text, i, target_pos, delim, False)
                continue
            if c in "\"'":
                delim = c
                i += 1
                if i + 1 < length and text[i:i + 1].decode("latin-1") == delim and text[i + 1:i + 2].decode("latin-1") == delim:
                    i += 2
                    i = _py_skip_string(text, i, target_pos, delim, True)
                else:
                    i = _py_skip_string(text, i, target_pos, delim, False)
                continue
            i += 1
            continue

        if in_comment:
            if c in "\n\r":
                in_comment = 0
            i += 1
            continue

        if in_string:
            if escape_next:
                escape_next = 0
                i += 1
                continue
            if c == "\\":
                escape_next = 1
                i += 1
                continue
            if triple_string:
                if c in "\"'":
                    if i + 2 < length and text[i + 1:i + 2].decode("latin-1") == c and text[i + 2:i + 3].decode("latin-1") == c:
                        in_string = triple_string = string_delim = 0
                        in_fstring = is_docstring = 0
                        i += 3
                        continue
                    i += 1
                    continue
                if in_fstring and c == "{":
                    if i + 1 < length and text[i + 1:i + 2].decode("latin-1") == "{":
                        i += 2
                        continue
                    in_fexpr = 1
                    fexpr_depth = 1
                    i += 1
                    continue
                i += 1
                continue
            if c == string_delim:
                in_string = string_delim = in_fstring = is_docstring = 0
                i += 1
                continue
            if in_fstring and c == "{":
                if i + 1 < length and text[i + 1:i + 2].decode("latin-1") == "{":
                    i += 2
                    continue
                in_fexpr = 1
                fexpr_depth = 1
                i += 1
                continue
            i += 1
            continue

        # not in string/comment
        if c in "frb" and i + 1 < target_pos and text[i + 1:i + 2].decode("latin-1") in "\"'":
            next_c = text[i + 1:i + 2].decode("latin-1")
            if i + 3 < length and text[i + 2:i + 3].decode("latin-1") == next_c and text[i + 3:i + 4].decode("latin-1") == next_c:
                in_string = triple_string = 1
                string_delim = next_c
                in_fstring = 1 if c == "f" else 0
                if expect_docstring:
                    is_docstring = 1
                expect_docstring = 0
                i += 4
                continue
            in_string = 1
            triple_string = 0
            string_delim = next_c
            in_fstring = 1 if c == "f" else 0
            expect_docstring = 0
            i += 2
            continue

        if c in "\"'":
            if i + 2 < length and text[i + 1:i + 2].decode("latin-1") == c and text[i + 2:i + 3].decode("latin-1") == c:
                in_string = triple_string = 1
                string_delim = c
                in_fstring = 0
                if expect_docstring:
                    is_docstring = 1
                expect_docstring = 0
                i += 3
                continue
            in_string = 1
            triple_string = 0
            string_delim = c
            in_fstring = 0
            expect_docstring = 0
            i += 1
            continue

        if c == "#":
            in_comment = 1
            i += 1
            continue

        if c in "([{":
            bracket_depth += 1
            i += 1
            continue
        if c in ")]}":
            if bracket_depth > 0:
                bracket_depth -= 1
            i += 1
            continue

        if c == ":":
            if bracket_depth == 0 and (pending_def or pending_class):
                expect_docstring = 1
                if pending_class:
                    at_class_body = 1
                pending_def = pending_class = 0
            i += 1
            continue

        if c == "-" and i + 1 < target_pos and text[i + 1:i + 2].decode("latin-1") == ">":
            i += 2
            continue

        if c.isalpha() or c == "_":
            start = i
            i += 1
            while i < target_pos and (text[i:i + 1].decode("latin-1").isalnum() or text[i:i + 1].decode("latin-1") == "_"):
                i += 1
            word = text[start:i].decode("utf-8", errors="replace")
            if word == "def":
                pending_def = 1
                pending_class = 0
                at_class_body = 0
            elif word == "class":
                pending_class = 1
                pending_def = 0
                at_class_body = 0
            else:
                expect_docstring = 0
            continue

        i += 1

    return _py_pack_state(in_string, in_comment, triple_string, string_delim,
                          in_fstring, escape_next, in_fexpr, fexpr_depth,
                          is_docstring, expect_docstring, at_class_body)


def _py_style_chunk(text, start, end, prev_state, keywords, builtins, magic_methods):
    """Pure-Python twin of lexer_fast.style_chunk. Returns (final_state, [(len, style), ...])."""
    length = len(text)
    end = min(end, length)
    start = max(0, start)

    (in_string, in_comment, triple_string, string_delim,
     in_fstring, escape_next, in_fexpr, fexpr_depth, is_docstring,
     expect_docstring, at_class_body) = _py_unpack_state(prev_state)

    S_DEFAULT, S_KEYWORD, S_TYPES, S_STRING, S_KEYARGS, S_BRACKETS = 0, 1, 2, 3, 4, 5
    S_COMMENTS, S_CONSTANTS, S_FUNCTIONS, S_CLASSES, S_FUNCTION_DEF = 6, 7, 8, 9, 10
    S_DECORATOR, S_OPERATORS, S_MAGIC, S_NUMBERS, S_SELF, S_BUILTINS = 11, 12, 13, 14, 15, 16
    S_PARAMS, S_CLASSREF, S_IFIELD, S_IMETHOD, S_SFIELD, S_SMETHOD = 17, 18, 19, 20, 21, 22
    S_FCALL, S_LOCAL, S_COMMA, S_MODULE, S_DOCSTRING = 23, 24, 25, 26, 27

    results = []
    i = start

    after_def = after_class = after_dot = after_at = 0
    after_from = after_import = in_from_import = 0
    in_def_params = param_depth = at_type_pos = after_arrow = 0
    bracket_depth = pending_def = pending_class = 0
    at_arg_pos = False

    string_style = S_DOCSTRING if (in_string and is_docstring) else S_STRING

    while i < end:
        c = text[i:i + 1].decode("latin-1")

        # ---- comment ----
        if in_comment:
            token_start = i
            while i < end and text[i:i + 1].decode("latin-1") not in "\n\r":
                i += 1
            if i > token_start:
                results.append((i - token_start, S_COMMENTS))
            if i < end:
                in_comment = 0
                if text[i:i + 1].decode("latin-1") == "\r" and i + 1 < end and text[i + 1:i + 2].decode("latin-1") == "\n":
                    results.append((2, S_DEFAULT)); i += 2
                else:
                    results.append((1, S_DEFAULT)); i += 1
            continue

        # ---- f-string expression ----
        if in_fexpr:
            if c == "}":
                fexpr_depth -= 1
                if fexpr_depth <= 0:
                    fexpr_depth = 0
                    in_fexpr = 0
                    results.append((1, string_style))
                else:
                    results.append((1, S_BRACKETS))
                i += 1
                continue
            if c == "{":
                fexpr_depth += 1
                results.append((1, S_BRACKETS))
                i += 1
                continue
            if c in "frb" and i + 1 < end and text[i + 1:i + 2].decode("latin-1") in "\"'":
                token_start = i
                # CHUNK-SAFETY: only consume 4 bytes when all 4 are inside
                # THIS chunk (not `length` = the full document).
                if i + 3 < end and text[i + 2:i + 3].decode("latin-1") == text[i + 1:i + 2].decode("latin-1") and text[i + 3:i + 4].decode("latin-1") == text[i + 1:i + 2].decode("latin-1"):
                    results.append((4, S_STRING)); i += 4
                else:
                    results.append((2, S_STRING)); i += 2
                continue
            if c in "\"'":
                token_start = i
                i += 1
                while i < end:
                    if text[i:i + 1].decode("latin-1") == "\\":
                        # CHUNK-SAFETY: a backslash as the LAST chunk byte
                        # must not skip past `end` (would over-style by 1).
                        if i + 1 < end:
                            i += 2
                        else:
                            i += 1
                        continue
                    if text[i:i + 1].decode("latin-1") == c:
                        i += 1
                        break
                    if text[i:i + 1].decode("latin-1") in "\n\r":
                        break
                    i += 1
                results.append((i - token_start, S_STRING))
                continue
            if c == "#":
                token_start = i
                while i < end and text[i:i + 1].decode("latin-1") not in "\n\r":
                    i += 1
                results.append((i - token_start, S_COMMENTS))
                continue
            if c.isalpha() or c == "_":
                token_start = i
                while i < end and (text[i:i + 1].decode("latin-1").isalnum() or text[i:i + 1].decode("latin-1") == "_"):
                    i += 1
                token = text[token_start:i].decode("utf-8", errors="replace")
                followed_by_paren = i < end and text[i:i + 1].decode("latin-1") == "("
                if token in ("True", "False", "None"):
                    results.append((i - token_start, S_CONSTANTS))
                elif token in ("self", "cls"):
                    results.append((i - token_start, S_SELF))
                elif token in keywords:
                    results.append((i - token_start, S_KEYWORD))
                elif token in builtins:
                    results.append((i - token_start, S_BUILTINS))
                elif followed_by_paren:
                    results.append((i - token_start, S_FCALL))
                elif token[:1].isupper():
                    results.append((i - token_start, S_CLASSREF))
                else:
                    results.append((i - token_start, S_LOCAL))
                continue
            if c.isdigit():
                token_start = i
                while i < end and (text[i:i + 1].decode("latin-1").isalnum() or text[i:i + 1].decode("latin-1") == "."):
                    i += 1
                results.append((i - token_start, S_NUMBERS))
                continue
            if c in "+-*/%=<>!&|^~":
                token_start = i
                i += 1
                if i < end and text[i:i + 1].decode("latin-1") in "+-*/%=<>!&|^~":
                    i += 1
                results.append((i - token_start, S_OPERATORS))
                continue
            if c in "(){}[]":
                results.append((1, S_BRACKETS))
                i += 1
                continue
            if c in " \t":
                token_start = i
                while i < end and text[i:i + 1].decode("latin-1") in " \t":
                    i += 1
                results.append((i - token_start, S_DEFAULT))
                continue
            if c in "\n\r":
                results.append((1, S_DEFAULT))
                i += 1
                continue
            results.append((1, S_DEFAULT))
            i += 1
            continue

        # ---- string ----
        if in_string:
            token_start = i
            if escape_next:
                i += 1
                escape_next = 0
                results.append((i - token_start, string_style))
                continue
            if c == "\\":
                i += 1
                escape_next = 1
                results.append((1, string_style))
                continue
            if triple_string:
                if c in "\"'":
                    # CHUNK-SAFETY: the closing '"""' may straddle the chunk
                    # end. Only consume 3 quote bytes when ALL of them are
                    # inside THIS chunk (i + 2 < end, NOT < length). Styling
                    # more bytes than the chunk contains corrupts
                    # Scintilla's style buffer and crashes the editor.
                    if i + 2 < end and text[i + 1:i + 2].decode("latin-1") == c and text[i + 2:i + 3].decode("latin-1") == c:
                        results.append((3, string_style))
                        i += 3
                        in_string = triple_string = string_delim = 0
                        in_fstring = is_docstring = 0
                        string_style = S_STRING
                        continue
                    results.append((1, string_style))
                    i += 1
                    continue
                if in_fstring and c == "{":
                    # CHUNK-SAFETY: '{{' needs both braces inside the chunk.
                    if i + 1 < end and text[i + 1:i + 2].decode("latin-1") == "{":
                        if i > token_start:
                            results.append((i - token_start, string_style))
                        results.append((2, string_style))
                        i += 2
                        continue
                    if i > token_start:
                        results.append((i - token_start, string_style))
                    results.append((1, string_style))
                    i += 1
                    in_fexpr = 1
                    fexpr_depth = 1
                    continue
                token_start = i
                while i < end:
                    c2 = text[i:i + 1].decode("latin-1")
                    if c2 in "\"'\\":
                        break
                    if in_fstring and c2 == "{":
                        break
                    i += 1
                if i > token_start:
                    results.append((i - token_start, string_style))
                continue

            # single-line string
            token_start = i
            while i < end:
                c2 = text[i:i + 1].decode("latin-1")
                if c2 == string_delim:
                    results.append((i - token_start + 1, string_style))
                    i += 1
                    in_string = string_delim = in_fstring = is_docstring = 0
                    string_style = S_STRING
                    break
                if c2 == "\\":
                    results.append((i - token_start, string_style))
                    results.append((1, string_style))
                    i += 1
                    escape_next = 1
                    break
                if in_fstring and c2 == "{":
                    # CHUNK-SAFETY: '{{' needs both braces inside the chunk.
                    if i + 1 < end and text[i + 1:i + 2].decode("latin-1") == "{":
                        if i > token_start:
                            results.append((i - token_start, string_style))
                        results.append((2, string_style))
                        i += 2
                        token_start = i
                        continue
                    if i > token_start:
                        results.append((i - token_start, string_style))
                    results.append((1, string_style))
                    i += 1
                    in_fexpr = 1
                    fexpr_depth = 1
                    break
                if c2 in "\n\r":
                    results.append((i - token_start, string_style))
                    in_string = string_delim = in_fstring = is_docstring = 0
                    string_style = S_STRING
                    break
                i += 1
            else:
                results.append((i - token_start, string_style))
            continue

        # ---- not in string or comment ----
        if c in " \t":
            token_start = i
            while i < end and text[i:i + 1].decode("latin-1") in " \t":
                i += 1
            results.append((i - token_start, S_DEFAULT))
            continue

        if c in "\n\r":
            if c == "\r" and i + 1 < end and text[i + 1:i + 2] == b"\n":
                results.append((2, S_DEFAULT)); i += 2
            else:
                results.append((1, S_DEFAULT)); i += 1
            if bracket_depth == 0:
                after_def = after_class = after_at = after_dot = 0
                after_from = after_import = in_from_import = 0
                at_arg_pos = False
                at_type_pos = after_arrow = 0
            continue

        if c in "frb" and i + 1 < end and text[i + 1:i + 2].decode("latin-1") in "\"'":
            # decode to a 1-char string — bytes indexing would give an int
            # here and break every comparison below.
            next_c = text[i + 1:i + 2].decode("latin-1")
            token_start = i
            # CHUNK-SAFETY: prefixed triple strings need all 4 bytes
            # inside THIS chunk, not just inside the document.
            if i + 3 < end and text[i + 2:i + 3].decode("latin-1") == next_c and text[i + 3:i + 4].decode("latin-1") == next_c:
                if expect_docstring:
                    string_style = S_DOCSTRING
                    is_docstring = 1
                else:
                    string_style = S_STRING
                results.append((4, string_style))
                i += 4
                in_string = triple_string = 1
                string_delim = next_c
                in_fstring = 1 if c == "f" else 0
            else:
                results.append((2, S_STRING))
                i += 2
                in_string = 1
                triple_string = 0
                string_delim = next_c
                in_fstring = 1 if c == "f" else 0
            expect_docstring = 0
            continue

        if c in "\"'":
            # CHUNK-SAFETY: the opening '"""' may straddle the chunk end.
            # Only consume 3 quote bytes when ALL of them are inside THIS
            # chunk; otherwise fall through to single-line-string handling
            # (the next chunk's state scan sees the full triple and styles
            # the remaining quotes correctly).
            if i + 2 < end and text[i + 1:i + 2].decode("latin-1") == c and text[i + 2:i + 3].decode("latin-1") == c:
                if expect_docstring:
                    string_style = S_DOCSTRING
                    is_docstring = 1
                else:
                    string_style = S_STRING
                results.append((3, string_style))
                i += 3
                in_string = triple_string = 1
                string_delim = c
                in_fstring = 0
            else:
                results.append((1, S_STRING))
                i += 1
                in_string = 1
                triple_string = 0
                string_delim = c
                in_fstring = 0
            expect_docstring = 0
            continue

        if c == "#":
            in_comment = 1
            token_start = i
            i += 1
            while i < end and text[i:i + 1].decode("latin-1") not in "\n\r":
                i += 1
            results.append((i - token_start, S_COMMENTS))
            continue

        if c == "@":
            results.append((1, S_DECORATOR))
            i += 1
            after_at = 1
            expect_docstring = 0
            at_arg_pos = False
            continue

        if c.isdigit():
            token_start = i
            while i < end and (text[i:i + 1].decode("latin-1").isalnum() or text[i:i + 1].decode("latin-1") == "."):
                i += 1
            results.append((i - token_start, S_NUMBERS))
            expect_docstring = 0
            at_arg_pos = False
            continue

        if c == ".":
            results.append((1, S_DEFAULT))
            i += 1
            if not (after_from or after_import):
                after_dot = 1
            at_arg_pos = False
            continue

        if c in "(){}[]":
            results.append((1, S_BRACKETS))
            if c in "([{":
                bracket_depth += 1
                if c == "(" and after_def:
                    in_def_params = 1
                    param_depth = 1
                    after_def = 0
                    at_type_pos = 0
                elif in_def_params:
                    param_depth += 1
                at_arg_pos = True
            else:
                if bracket_depth > 0:
                    bracket_depth -= 1
                if in_def_params:
                    param_depth -= 1
                    if param_depth <= 0:
                        in_def_params = 0
                        param_depth = 0
                        at_type_pos = 0
                at_arg_pos = False
            i += 1
            after_dot = 0
            continue

        if c in "+-*/%=<>!&|^~":
            token_start = i
            i += 1
            if i < end and text[i:i + 1].decode("latin-1") in "+-*/%=<>!&|^~":
                i += 1
            results.append((i - token_start, S_OPERATORS))
            expect_docstring = 0
            if text[token_start:token_start + 1].decode("latin-1") == "-" and token_start + 1 < end and text[token_start + 1:token_start + 2].decode("latin-1") == ">":
                if pending_def and bracket_depth == 0:
                    after_arrow = 1
            at_arg_pos = False
            continue

        if c == ",":
            results.append((1, S_COMMA))
            i += 1
            if in_def_params:
                at_type_pos = 0
            if not (after_import or in_from_import or after_from):
                pass
            at_arg_pos = True
            continue

        if c == ":":
            results.append((1, S_DEFAULT))
            if in_def_params and param_depth == 1:
                at_type_pos = 1
            elif bracket_depth == 0 and (pending_def or pending_class):
                expect_docstring = 1
                if pending_class:
                    at_class_body = 1
                pending_def = pending_class = 0
                after_arrow = 0
            i += 1
            at_arg_pos = False
            continue

        if c == ";":
            results.append((1, S_DEFAULT))
            i += 1
            expect_docstring = after_from = after_import = in_from_import = 0
            at_arg_pos = False
            continue

        # ---- identifier ----
        if c.isalpha() or c == "_":
            token_start = i
            while i < end and (text[i:i + 1].decode("latin-1").isalnum() or text[i:i + 1].decode("latin-1") == "_"):
                i += 1
            token = text[token_start:i].decode("utf-8", errors="replace")

            followed_by_paren = i < end and text[i:i + 1].decode("latin-1") == "("
            followed_by_eq = (i < end and text[i:i + 1].decode("latin-1") == "=" and
                              (i + 1 >= end or text[i + 1:i + 2].decode("latin-1") != "="))
            _j = i
            while _j < end and text[_j:_j + 1].decode("latin-1") in " \t":
                _j += 1
            followed_by_assign = (_j < end and text[_j:_j + 1].decode("latin-1") == "=" and
                                  (_j + 1 >= end or text[_j + 1:_j + 2].decode("latin-1") != "="))

            if token not in ("def", "class"):
                expect_docstring = 0

            # --- decision tree (mirrors lexer_fast.pyx / PyCharm) ---
            if after_at:
                results.append((i - token_start, S_DECORATOR))
                after_at = 0
            elif token in ("self", "cls"):
                results.append((i - token_start, S_SELF))
            elif token in ("True", "False", "None"):
                results.append((i - token_start, S_CONSTANTS))
            elif token == "def":
                results.append((i - token_start, S_KEYWORD))
                after_def = pending_def = 1
                pending_class = at_class_body = 0
            elif token == "class":
                results.append((i - token_start, S_KEYWORD))
                after_class = pending_class = 1
                pending_def = 0
            elif token == "import":
                results.append((i - token_start, S_KEYWORD))
                if after_from:
                    after_from = 0
                    in_from_import = 1
                else:
                    after_import = 1
            elif token == "from":
                results.append((i - token_start, S_KEYWORD))
                after_from = 1
            elif token == "as":
                results.append((i - token_start, S_KEYWORD))
                after_import = in_from_import = 0
            elif token in keywords:
                results.append((i - token_start, S_KEYWORD))
            elif after_def:
                if token in magic_methods:
                    results.append((i - token_start, S_MAGIC))
                else:
                    results.append((i - token_start, S_FUNCTION_DEF))
            elif after_class:
                results.append((i - token_start, S_CLASSES))
                after_class = 0
            elif after_dot:
                if followed_by_paren:
                    results.append((i - token_start, S_IMETHOD))
                else:
                    results.append((i - token_start, S_IFIELD))
                after_dot = 0
            elif in_def_params and param_depth == 1:
                if at_type_pos:
                    results.append((i - token_start, S_TYPES))
                else:
                    results.append((i - token_start, S_PARAMS))
            elif after_arrow:
                results.append((i - token_start, S_TYPES))
            elif after_from or after_import:
                results.append((i - token_start, S_MODULE))
            elif in_from_import:
                if token[:1].isupper():
                    results.append((i - token_start, S_CLASSREF))
                else:
                    results.append((i - token_start, S_MODULE))
            elif at_arg_pos and followed_by_eq and not in_def_params:
                results.append((i - token_start, S_KEYARGS))
            elif at_class_body and followed_by_assign:
                results.append((i - token_start, S_IFIELD))
            elif token in builtins:
                results.append((i - token_start, S_BUILTINS))
            elif followed_by_paren:
                results.append((i - token_start, S_FCALL))
            elif token.startswith("__") and token.endswith("__") and len(token) > 4:
                results.append((i - token_start, S_BUILTINS))
            elif token[:1].isupper():
                results.append((i - token_start, S_CLASSREF))
            else:
                results.append((i - token_start, S_LOCAL))

            at_arg_pos = False
            continue

        results.append((1, S_DEFAULT))
        i += 1
        at_arg_pos = False

    final_state = _py_pack_state(in_string, in_comment, triple_string,
                                 string_delim, in_fstring, escape_next,
                                 in_fexpr, fexpr_depth, is_docstring,
                                 expect_docstring, at_class_body)
    return (final_state, results)


class NeutronLexer(QsciLexerCustom):
    def __init__(self, language_name, editor, theme=None, paper=None):
        """
        Base lexer for both custom lexers.

        Font/styling model (single source of truth: the THEME):
          - family + size come from the theme's GLOBAL editor.font block
            (one place — the Settings dialog edits exactly that block)
          - per-style font blocks contribute ONLY font-weight and italic
          - per-style paper-color is optional; styles without one inherit
            the editor paper (which is also what the Settings color picker
            overrides at runtime)

        :param theme: absolute path to a theme .json; None -> themes/theme.json
        :param paper: QColor paper override from the Settings dialog
                     (None = use the theme's editor.paper-color)
        """
        super(NeutronLexer, self).__init__(editor)
        self.editor = editor
        self.language_name = language_name
        self.theme_json = None
        self.theme = theme or _resource_path(os.path.join("themes", "theme.json"))
        self.paper_override = paper          # QColor or None

        self.token_list = []
        self.keyword_list = []
        self.builtin_names = []

        self._init_theme_vars()
        self._init_theme()

    def setKeywords(self, keywords):
        self.keyword_list = keywords

    def setBuiltinNames(self, builtin_names):
        self.builtin_names = builtin_names

    def _init_theme_vars(self):
        self.DEFAULT = 0
        self.KEYWORD = 1
        self.TYPES = 2
        self.STRING = 3
        self.KEYARGS = 4
        self.BRACKETS = 5
        self.COMMENTS = 6
        self.CONSTANTS = 7
        self.FUNCTIONS = 8
        self.CLASSES = 9
        self.FUNCTION_DEF = 10
        self.DECORATOR = 11
        # --- Semantic styles (mirror PyCharm's Python color scheme) ---
        self.OPERATORS = 12
        self.MAGIC_METHODS = 13
        self.NUMBERS = 14
        self.SELF_CLS = 15
        self.BUILTINS = 16
        self.PARAMETERS = 17
        self.CLASS_REFERENCE = 18
        self.INSTANCE_FIELD = 19
        self.INSTANCE_METHOD = 20
        self.STATIC_FIELD = 21
        self.STATIC_METHOD = 22
        self.FUNCTION_CALL = 23
        self.LOCAL_VARIABLE = 24
        self.COMMA = 25
        self.MODULE_NAME = 26
        self.DOCSTRING = 27

        self.default_names = [
            "default", "keyword", "types", "string", "keyargs",
            "brackets", "comments", "constants", "functions",
            "classes", "function_def", "decorator",
            "operators", "magic_methods", "numbers",
            "self_cls", "builtins", "parameters",
            "class_reference", "instance_field", "instance_method",
            "static_field", "static_method", "function_call",
            "local_variable", "comma", "module_name", "docstring",
        ]

        self.font_weights = {
            "thin": QFont.Thin,
            "extralight": getattr(QFont, 'ExtraLight', QFont.Light),
            "light": QFont.Light,
            "normal": QFont.Normal,
            "medium": QFont.Medium,
            "demibold": QFont.DemiBold,
            "bold": QFont.Bold,
            "extrabold": getattr(QFont, 'ExtraBold', QFont.Bold),
            "black": QFont.Black,
        }
        self._prev_state = 0

    def _init_theme(self):
        """
        Build the whole style table from the theme file.

        Lookup order per style:
          color        <- style block (required)
          paper-color  <- style block, else editor.paper-color,
                         else the `paper` override from Settings (wins over
                         everything when set)
          font         <- GLOBAL family+size (editor.font) combined with the
                         style's font-weight / italic only. Per-style family
                         and font-size are deliberately IGNORED so one font
                         choice in Settings really changes every style —
                         that inconsistency is exactly why PythonEditor
                         ignored the settings dialog before.
        """
        with open(self.theme, "r", encoding="utf-8") as f:
            self.theme_json = json.load(f)

        data = self.theme_json["theme"]

        # --- editor-wide defaults (the section the Settings dialog edits) -- #
        editor_section = data.get("editor", {})
        gfont = editor_section.get("font", {})
        self.editor_font_family = gfont.get("family", "JetBrains Mono")
        self.editor_font_size = int(gfont.get("font-size", 13))

        theme_paper = editor_section.get("paper-color", "#1e1f22")
        default_paper = (self.paper_override.name()
                         if self.paper_override else theme_paper)

        # The default style carries the editor-wide look; unstyled bytes
        # and the margins inherit from it.
        self.setDefaultColor(QColor(
            data["syntax"][0]["default"].get("color", "#bcbec4")
            if "default" in data["syntax"][0] else "#bcbec4"))
        self.setDefaultPaper(QColor(default_paper))

        colors = data["syntax"]
        for clr in colors:
            name = list(clr.keys())[0]
            if name not in self.default_names:
                # Styles of the OTHER language live in the same theme file
                # (one merged 62-style list) — skip silently, they are not
                # errors, just not ours.
                continue
            body = clr[name]

            if "color" in body:
                self.setColor(QColor(body["color"]), getattr(self, name.upper()))

            paper_hex = body.get("paper-color", default_paper)
            self.setPaper(QColor(paper_hex), getattr(self, name.upper()))

            f = body.get("font", {})
            weight = self.font_weights.get(f.get("font-weight", "normal"),
                                           QFont.Normal)
            fnt = QFont(self.editor_font_family, self.editor_font_size, weight)
            fnt.setItalic(bool(f.get("italic", False)))
            self.setFont(fnt, getattr(self, name.upper()))

    def language(self):
        return self.language_name

    def editor_font(self) -> QFont:
        """
        The editor-wide font (family + size) from the theme's global
        editor.font block, with per-style weight/italic stripped — this is
        the font for margins, popups and anything editor-wide.

        Replaces the old editors' self.window_font. Falls back to
        JetBrains Mono 13 when the theme has no editor section.
        """
        f = QFont(self.editor_font_family, self.editor_font_size)
        return f

    def editor_color(self, key: str, fallback: str) -> QColor:
        """
        A color from the theme's editor section (caret, margins, ...).

        :param key: editor-section key, e.g. "caret-color"
        :param fallback: hex used when the theme lacks the key (older
                        theme files without an editor section still work)
        """
        section = self.theme_json.get("theme", {}).get("editor", {})
        return QColor(section.get(key, fallback))

    def description(self, style):
        names = {
            self.DEFAULT: "DEFAULT",
            self.KEYWORD: "KEYWORD",
            self.TYPES: "TYPES",
            self.STRING: "STRING",
            self.KEYARGS: "KEYARGS",
            self.BRACKETS: "BRACKETS",
            self.COMMENTS: "COMMENTS",
            self.CONSTANTS: "CONSTANTS",
            self.FUNCTIONS: "FUNCTIONS",
            self.CLASSES: "CLASSES",
            self.FUNCTION_DEF: "FUNCTION_DEF",
            self.DECORATOR: "DECORATOR",
            self.OPERATORS: "OPERATORS",
            self.MAGIC_METHODS: "MAGIC_METHODS",
            self.NUMBERS: "NUMBERS",
            self.SELF_CLS: "SELF_CLS",
            self.BUILTINS: "BUILTINS",
            self.PARAMETERS: "PARAMETERS",
            self.CLASS_REFERENCE: "CLASS_REFERENCE",
            self.INSTANCE_FIELD: "INSTANCE_FIELD",
            self.INSTANCE_METHOD: "INSTANCE_METHOD",
            self.STATIC_FIELD: "STATIC_FIELD",
            self.STATIC_METHOD: "STATIC_METHOD",
            self.FUNCTION_CALL: "FUNCTION_CALL",
            self.LOCAL_VARIABLE: "LOCAL_VARIABLE",
            self.COMMA: "COMMA",
            self.MODULE_NAME: "MODULE_NAME",
            self.DOCSTRING: "DOCSTRING",
        }
        return names.get(style, "")

    @staticmethod
    def _byte_pos_to_char_index(text: str, byte_pos: int) -> int:
        if byte_pos <= 0:
            return 0
        encoded = text.encode("utf-8")
        if byte_pos >= len(encoded):
            return len(text)
        return len(encoded[:byte_pos].decode("utf-8", errors="ignore"))

    # ------------------------------------------------------------------ #
    #  Token stream helpers.
    #  Used by subclasses that style text token-by-token (e.g.
    #  MarkdownCustomLexer). PyCustomLexer itself no longer needs them -
    #  its styling runs through lexer_fast / the pure-Python fallback.
    # ------------------------------------------------------------------ #
    def generate_token(self, text):
        p = re.compile(r"\s+|\w+|\W")
        self.token_list = [(token, len(bytearray(token, "utf-8"))) for token in p.findall(text)]

    def next_tok(self):
        if self.token_list:
            return self.token_list.pop(0)
        return None

    def peek_tok(self, n=0):
        try:
            return self.token_list[n]
        except IndexError:
            return ("", 0)

    def skip_space_peek(self):
        i = 0
        while True:
            tok = self.peek_tok(i)
            if not tok[0]:
                return tok, i
            if not tok[0].isspace():
                return tok, i
            i += 1


class PyCustomLexer(NeutronLexer):
    def __init__(self, editor, theme=None, paper=None):
        """
        :param theme: absolute path to a theme .json chosen in the
                      Settings dialog; None -> built-in default (themes/theme.json)
        :param paper: QColor paper override from the Settings color picker;
                      None -> the theme's editor.paper-color
        """
        super(PyCustomLexer, self).__init__("Python", editor, theme=theme,
                                            paper=paper)
        self.setKeywords(keyword.kwlist)
        self.setBuiltinNames([
            name for name, obj in vars(builtins).items()
            if isinstance(obj, (types.BuiltinFunctionType, type))
        ] + list(_STDLIB_MODULES))
        self._keyword_set = set(keyword.kwlist)
        self._builtin_set = set(self.builtin_names)
        # Paper comes from the theme's editor section (or the Settings
        # paper override via super().__init__) — no longer hardcoded here.
        self.setDefaultPaper(QColor(
            self.theme_json["theme"].get("editor", {}).get(
                "paper-color", "#1e1f22"))
            if self.paper_override is None else self.paper_override)
        self._magic_set = {
            "__init__", "__str__", "__repr__", "__len__", "__iter__",
            "__next__", "__enter__", "__exit__", "__call__", "__getattr__",
            "__setattr__", "__delattr__", "__getitem__", "__setitem__",
            "__delitem__", "__contains__", "__add__", "__sub__", "__mul__",
            "__div__", "__truediv__", "__floordiv__", "__mod__", "__pow__",
            "__eq__", "__ne__", "__lt__", "__le__", "__gt__", "__ge__",
            "__hash__", "__bool__", "__new__", "__class__", "__del__",
            "__name__", "__doc__", "__dict__", "__module__",
        }
        self._prev_state = 0

    def styleText(self, start: int, end: int) -> None:
        full_text = self.editor.text()
        text_bytes = full_text.encode('utf-8')

        start_char = self._byte_pos_to_char_index(full_text, start)
        end_char = self._byte_pos_to_char_index(full_text, end)

        start_byte = len(full_text[:start_char].encode('utf-8'))
        end_byte = len(full_text[:end_char].encode('utf-8'))

        # Compute the correct lexer state at start_byte by scanning from byte 0.
        # This is necessary because QScintilla may call styleText for
        # non-contiguous regions (e.g. when the user scrolls).
        if _HAS_CYTHON:
            prev_state = _cython_state(text_bytes, start_byte)
            final_state, styled_tokens = _cython_style(
                text_bytes,
                start_byte,
                end_byte,
                prev_state,
                self._keyword_set,
                self._builtin_set,
                self._magic_set,
            )
        else:
            # Pure-Python fallback - identical logic to lexer_fast.pyx
            prev_state = _py_compute_state(text_bytes, start_byte)
            final_state, styled_tokens = _py_style_chunk(
                text_bytes,
                start_byte,
                end_byte,
                prev_state,
                self._keyword_set,
                self._builtin_set,
                self._magic_set,
            )

        self.startStyling(start)
        for byte_len, style_id in styled_tokens:
            self.setStyling(byte_len, style_id)

        self._prev_state = final_state
