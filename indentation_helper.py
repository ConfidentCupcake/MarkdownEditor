"""
Pure, testable Python indentation for PythonEditor.

This module does not import PyQt or QScintilla.
It receives text before the caret and returns only an intdentation string. 
Keep the logic independent of GUI code makes it straightforward to test.
"""

from __future__ import annotations

import io
import tokenize


# These are Python compund-statement keywords whose final top-level colon opens and indented suite.
# 'async' is included because it occurs before 'def', 'for' or 'with' in async compound statements.

COMPOUND_KEYWORDS = frozenset(
    {
        "if",
        "elif",
        "else",
        "for",
        "while",
        "try",
        "except",
        "finally",
        "with",
        "class",
        "def",
        "match",
        "case",
        "async",
    }
)

def one_indent(use_tabs: bool, width: int) -> str:
    """Return ecaxtly one configured indentation level."""
    return "\t" if use_tabs else " " * max(1, width)

def should_indent_after_colon(prefix: str) -> bool:
    """
    Return whether 'prefix' ends with a Python suite-opening colon.
    
    'prefix' contains text from the beginning of the current source line up to the caret.
    It is intentionally not the whole source line: pressing Enter can split a line in its middle.
    """
    stripped = prefix.rstrip()
    if not stripped:
        return False
    
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(prefix).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # During typing, incomplete source can be untokenizable. 
        # Do not guess agressively in that state: only a plainly visible leading compound
        # keyword plus a final colon permits the extra indentation
        before_colon = stripped[:-1].strip() if stripped.endswith(":") else ""
        first_word = before_colon.split(maxsplit=1)[0] if before_colon else ""
        return first_word in COMPOUND_KEYWORDS
        
    
    # Comments are deliberately ignored. Thus 'if ok:  # explanation' is a 
    # suite opener while '# note:' is not.
    meaningful = [token for token in tokens if token.type not in {tokenize.COMMENT, tokenize.DEDENT, tokenize.ENDMARKER, tokenize.INDENT, tokenize.NL, tokenize.NEWLINE}]
    if not meaningful:
        return False
    
    last = meaningful[-1]
    if last.type != tokenize.OP or last.string != ":":
        return False
    
    bracket_depth = 0
    has_top_level_compound_keyword = False
    
    # Read every meaningful token before the final colon. 
    # The final colon must be at bracket depth zero and follow a top-level compound statement.
    for token in meaningful[:-1]:
        if token.type == tokenize.OP:
            if token.string in {"(", "[", "{"}:
                bracket_depth += 1
            elif token.string in {")", "]", "}"}:
                bracket_depth = max(0, bracket_depth - 1)
            continue
        
        if token.type == tokenize.NAME and bracket_depth == 0 and token.string in COMPOUND_KEYWORDS:
            has_top_level_compound_keyword = True
            
    return bracket_depth == 0 and has_top_level_compound_keyword
    
def indentation_for_new_line(prefix: str, use_tabs: bool, width: int) -> str:
    """Return the complete indentation that belongs on the next line."""
    # Preserve only leading spaces and tabs from the left side of the line.
    # Do not use lstrip() without arguments becuase it would treat unusual 
    # whitespace as indentation even if QScintilla does not.
    current_indent_length = len(prefix) - len(prefix.lstrip(" \t"))
    current_indent = prefix[:current_indent_length]
    
    if should_indent_after_colon(prefix):
        return current_indent + one_indent(use_tabs, width)
        
    return current_indent
    
    
    
    
    
    
    
    
    
    
    
    
    
    