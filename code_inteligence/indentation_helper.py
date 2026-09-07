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

# These statements normally finish the current-flow path. 
# The editor uses them as a optional UX signal to reduce indentation by one level after Enter.
# This is not Python syntax enforcement; it is editor behavior

TERMINAL_KEYWORDS = frozenset(
    {
        "return",
        "raise",
        "break",
        "continue",
        "pass",        
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
        
def is_terminal_statement(prefix: str) -> bool:
    """Return True when a requested terminal keyword starts this source line.

    The input contains only source text left of the caret. `tokenize` ensures
    that words in comments and quoted strings are not mistaken for Python
    statements.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(prefix).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # A conservative fallback for partially typed source. Split after
        # leading whitespace and inspect only the first word.
        first_word = prefix.lstrip(" \t").split(maxsplit=1)
        return bool(first_word and first_word[0] in TERMINAL_KEYWORDS)

    # Ignore layout/control tokens and comments. The first remaining token is
    # the first actual source token on this logical line.
    meaningful = [
        token
        for token in tokens
        if token.type
        not in {
            tokenize.COMMENT,
            tokenize.DEDENT,
            tokenize.ENDMARKER,
            tokenize.INDENT,
            tokenize.NL,
            tokenize.NEWLINE,
        }
    ]

    return bool(
        meaningful
        and meaningful[0].type == tokenize.NAME
        and meaningful[0].string in TERMINAL_KEYWORDS
    )

def dedent_one_level(indent: str, use_tabs: bool, width: int) -> str:
    """Remove exactly one configured indentation level from `indent`.

    Space mode removes up to `width` spaces. Tab mode removes one tab. The
    defensive mixed-indent branches avoid deleting non-indentation source text
    if a document contains legacy mixed whitespace.
    """
    if not indent:
        return ""

    if use_tabs:
        if indent.endswith("\t"):
            return indent[:-1]
        # A tab-mode file that ends in spaces is malformed/mixed indentation.
        # Removing trailing spaces is safer than removing source characters.
        return indent.rstrip(" ")

    if indent.endswith("\t"):
        # Space-mode file with a trailing tab: remove the one tab level.
        return indent[:-1]

    return indent[:-min(max(1, width), len(indent))]    
    
def indentation_for_new_line(prefix: str, use_tabs: bool, width: int) -> str:
    """Return the exact indentation to insert after Enter.

    Priority order matters:
    1. Terminal statement -> dedent exactly one level.
    2. Suite-opening colon -> add exactly one level.
    3. Ordinary statement -> preserve the current indentation.
    """
    # Keep only the line's leading spaces/tabs. `prefix` is text left of the
    # caret, so it is safe for Enter pressed at the end or in the middle of a
    # source line.
    current_indent_length = len(prefix) - len(prefix.lstrip(" \t"))
    current_indent = prefix[:current_indent_length]

    # This check comes first. A `return` line is not a suite opener and should
    # close one visual indentation level before normal preservation happens.
    if is_terminal_statement(prefix):
        return dedent_one_level(current_indent, use_tabs, width)

    if should_indent_after_colon(prefix):
        return current_indent + one_indent(use_tabs, width)

    return current_indent
    
    
    
    
    
    
    
    
    
    
    
    
    