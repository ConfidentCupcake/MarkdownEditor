# lexer_fast.pyx
# Cython implementation of the Python lexer - PyCharm "CustomDark" match.
#
# Extracted 1:1 from the user's PyCharm scheme (pixel-verified against
# screenshots of the real IDE):
#   keyword / True/False/None : #ff5700  (orange-red)
#   class definition name     : #ea5a5a  (soft red)
#   function/method def name  : #0082ff  (blue)
#   magic methods (__init__)  : #c857cd  (magenta)
#   self / cls                : #94558d  (dusty purple)
#   parameters / keyargs      : #ea5a5a
#   calls (func/method/ctor)  : #8621ff  (violet)
#   local variables           : #46b2ff
#   numbers                   : #11e4f6
#   strings                   : #00ff21  (italic)
#   docstrings                : #5f826b  (italic)
#   comments                  : #7a7e85
#   operators                 : #ffffff
#   class refs / builtins /
#   modules / punctuation     : #bcbec4  (default grey)
#
# Style IDs (must match custompythonlexer.py _init_theme_vars and theme.json):
cdef enum:
    STYLE_DEFAULT = 0
    STYLE_KEYWORD = 1
    STYLE_TYPES = 2          # type annotations (grey, like class refs)
    STYLE_STRING = 3
    STYLE_KEYARGS = 4         # CALL-SITE keyword-arg names (soft red)
    STYLE_BRACKETS = 5
    STYLE_COMMENTS = 6
    STYLE_CONSTANTS = 7       # True/False/None (keyword orange)
    STYLE_FUNCTIONS = 8       # calls after a non-self dot (violet)
    STYLE_CLASSES = 9         # class definition name (soft red)
    STYLE_FUNCTION_DEF = 10
    STYLE_DECORATOR = 11
    STYLE_OPERATORS = 12
    STYLE_MAGIC_METHODS = 13
    STYLE_NUMBERS = 14
    STYLE_SELF_CLS = 15       # self, cls
    STYLE_BUILTINS = 16       # builtins incl. super()/str() - grey, even when called
    STYLE_PARAMETERS = 17     # def-signature parameters (soft red)
    STYLE_CLASS_REFERENCE = 18  # class refs at usage sites (grey)
    STYLE_INSTANCE_FIELD = 19
    STYLE_INSTANCE_METHOD = 20
    STYLE_STATIC_FIELD = 21
    STYLE_STATIC_METHOD = 22
    STYLE_FUNCTION_CALL = 23
    STYLE_LOCAL_VARIABLE = 24
    STYLE_COMMA = 25
    STYLE_MODULE_NAME = 26
    STYLE_DOCSTRING = 27      # docstrings (dark green)


# --- Character classification helpers ---

cdef bint is_alpha(unsigned char c):
    return (c >= 65 and c <= 90) or (c >= 97 and c <= 122) or (c == 95)

cdef bint is_digit(unsigned char c):
    return c >= 48 and c <= 57

cdef bint is_alnum(unsigned char c):
    return is_alpha(c) or is_digit(c)

cdef bint is_space(unsigned char c):
    return c == 32 or c == 9

cdef bint is_operator(unsigned char c):
    return c in (43, 45, 42, 47, 37, 61, 60, 62, 33, 38, 124, 94, 126)

cdef bint is_bracket(unsigned char c):
    return c in (40, 41, 123, 125, 91, 93)


# --- State packing ---
# Bits:
#   0  in_string
#   1  in_comment
#   2  triple
#   3  string_delim == 34 (else 39)
#   4  in_fstring
#   5  escape_next
#   6  in_fexpr        (inside { ... } of an f-string)
#   7-9  fexpr_depth   (0..7)
#   10 is_docstring    (current triple string is a docstring -> STYLE_DOCSTRING)

cdef int pack_state(int in_string, int in_comment, int triple,
                    int string_delim, int in_fstring, int escape,
                    int in_fexpr, int fexpr_depth, int is_docstring,
                    int expect_docstring, int at_class_body):
    cdef int state = 0
    if in_string:
        state |= 1
    if in_comment:
        state |= 2
    if triple:
        state |= 4
    if string_delim == 34:
        state |= 8
    if in_fstring:
        state |= 16
    if escape:
        state |= 32
    if in_fexpr:
        state |= 64
    state |= (fexpr_depth & 7) << 7
    if is_docstring:
        state |= 1024
    if expect_docstring:
        state |= 2048
    if at_class_body:
        state |= 4096
    return state


cdef tuple unpack_state(int state):
    cdef int in_string = 1 if (state & 1) else 0
    cdef int in_comment = 1 if (state & 2) else 0
    cdef int triple = 1 if (state & 4) else 0
    cdef int string_delim = 0
    if in_string:
        string_delim = 34 if (state & 8) else 39
    cdef int in_fstring = 1 if (state & 16) else 0
    cdef int escape = 1 if (state & 32) else 0
    cdef int in_fexpr = 1 if (state & 64) else 0
    cdef int fexpr_depth = (state >> 7) & 7
    cdef int is_docstring = 1 if (state & 1024) else 0
    cdef int expect_docstring = 1 if (state & 2048) else 0
    cdef int at_class_body = 1 if (state & 4096) else 0
    return (in_string, in_comment, triple, string_delim,
            in_fstring, escape, in_fexpr, fexpr_depth, is_docstring,
            expect_docstring, at_class_body)


# --- Helper: skip a nested string starting at i (i = first char after opener) ---
cdef void _skip_string(bytes text, int limit, int *i, int delim, int triple):
    cdef int length = len(text)
    cdef unsigned char c
    cdef int escape = 0
    while i[0] < limit:
        c = text[i[0]]
        if escape:
            escape = 0
            i[0] += 1
            continue
        if c == 92:
            escape = 1
            i[0] += 1
            continue
        if triple:
            if c == delim:
                if i[0] + 2 < length and text[i[0]+1] == delim and text[i[0]+2] == delim:
                    i[0] += 3
                    return
                i[0] += 1
                continue
            i[0] += 1
            continue
        if c == delim:
            i[0] += 1
            return
        if c == 10 or c == 13:
            return
        i[0] += 1


# --- State computation (scans from byte 0 to target_pos) ---
# Reconstructs ALL state that affects styling at target_pos, including whether
# we are inside a docstring or inside an f-string {expr}.

def compute_state_before(bytes text, int target_pos):
    cdef int length = len(text)
    if target_pos > length:
        target_pos = length

    cdef int i = 0
    cdef int start
    cdef unsigned char c
    cdef unsigned char next_c
    cdef int delim
    cdef int in_string = 0
    cdef int in_comment = 0
    cdef int triple_string = 0
    cdef int string_delim = 0
    cdef int in_fstring = 0
    cdef int escape_next = 0
    cdef int in_fexpr = 0
    cdef int fexpr_depth = 0
    cdef int is_docstring = 0

    cdef int bracket_depth = 0
    cdef int pending_def = 0
    cdef int pending_class = 0
    cdef int expect_docstring = 0
    cdef int at_class_body = 0

    while i < target_pos:
        c = text[i]

        # ---- inside f-string expression { ... } ----
        if in_fexpr:
            if c == 125:  # }
                fexpr_depth -= 1
                if fexpr_depth <= 0:
                    fexpr_depth = 0
                    in_fexpr = 0
                i += 1
                continue
            if c == 123:  # {
                fexpr_depth += 1
                i += 1
                continue
            # prefixed nested string
            if (c == 102 or c == 114 or c == 98) and i + 1 < target_pos:
                next_c = text[i + 1]
                if next_c == 34 or next_c == 39:
                    delim = next_c
                    i += 2
                    if i + 1 < length and text[i] == delim and text[i+1] == delim:
                        i += 2
                        _skip_string(text, target_pos, &i, delim, 1)
                    else:
                        _skip_string(text, target_pos, &i, delim, 0)
                    continue
            if c == 34 or c == 39:
                delim = c
                i += 1
                if i + 1 < length and text[i] == delim and text[i+1] == delim:
                    i += 2
                    _skip_string(text, target_pos, &i, delim, 1)
                else:
                    _skip_string(text, target_pos, &i, delim, 0)
                continue
            i += 1
            continue

        # ---- comment ----
        if in_comment:
            if c == 10 or c == 13:
                in_comment = 0
            i += 1
            continue

        # ---- inside string ----
        if in_string:
            if escape_next:
                escape_next = 0
                i += 1
                continue
            if c == 92:
                escape_next = 1
                i += 1
                continue
            if triple_string:
                if c == 34 or c == 39:
                    if i + 2 < length and text[i+1] == c and text[i+2] == c:
                        in_string = 0
                        triple_string = 0
                        string_delim = 0
                        in_fstring = 0
                        is_docstring = 0
                        i += 3
                        continue
                    i += 1
                    continue
                if in_fstring and c == 123:
                    if i + 1 < length and text[i+1] == 123:
                        i += 2
                        continue
                    in_fexpr = 1
                    fexpr_depth = 1
                    i += 1
                    continue
                i += 1
                continue
            # single-line string
            if c == string_delim:
                in_string = 0
                string_delim = 0
                in_fstring = 0
                is_docstring = 0
                i += 1
                continue
            if in_fstring and c == 123:
                if i + 1 < length and text[i+1] == 123:
                    i += 2
                    continue
                in_fexpr = 1
                fexpr_depth = 1
                i += 1
                continue
            i += 1
            continue

        # ---- not in string/comment ----

        # string prefix f/r/b
        if (c == 102 or c == 114 or c == 98) and i + 1 < target_pos:
            next_c = text[i + 1]
            if next_c == 34 or next_c == 39:
                if i + 3 < length and text[i+2] == next_c and text[i+3] == next_c:
                    in_string = 1
                    triple_string = 1
                    string_delim = next_c
                    in_fstring = 1 if (c == 102) else 0
                    if expect_docstring:
                        is_docstring = 1
                    expect_docstring = 0
                    i += 4
                    continue
                in_string = 1
                triple_string = 0
                string_delim = next_c
                in_fstring = 1 if (c == 102) else 0
                expect_docstring = 0
                i += 2
                continue

        # regular string
        if c == 34 or c == 39:
            if i + 2 < length and text[i+1] == c and text[i+2] == c:
                in_string = 1
                triple_string = 1
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

        # comment
        if c == 35:
            in_comment = 1
            i += 1
            continue

        # brackets
        if c == 40 or c == 91 or c == 123:
            bracket_depth += 1
            i += 1
            continue
        if c == 41 or c == 93 or c == 125:
            if bracket_depth > 0:
                bracket_depth -= 1
            i += 1
            continue

        # colon
        if c == 58:
            if bracket_depth == 0 and (pending_def or pending_class):
                expect_docstring = 1
                if pending_class:
                    at_class_body = 1
                pending_def = 0
                pending_class = 0
            i += 1
            continue

        # arrow ->
        if c == 45 and i + 1 < target_pos and text[i+1] == 62:
            i += 2
            continue

        # identifier
        if is_alpha(c):
            start = i
            i += 1
            while i < target_pos and is_alnum(text[i]):
                i += 1
            if text[start:i] == b"def":
                pending_def = 1
                pending_class = 0
                at_class_body = 0
            elif text[start:i] == b"class":
                pending_class = 1
                pending_def = 0
                at_class_body = 0
            else:
                expect_docstring = 0
            continue

        # newline (do NOT reset expect_docstring/pending_def here -
        # docstrings live on the line after the def/class header)
        if c == 10 or c == 13:
            i += 1
            continue

        i += 1

    return pack_state(in_string, in_comment, triple_string,
                      string_delim, in_fstring, escape_next,
                      in_fexpr, fexpr_depth, is_docstring,
                      expect_docstring, at_class_body)


# --- The full styler ---

def style_chunk(bytes text, int start, int end, int prev_state,
                set keywords, set builtins, set magic_methods):
    cdef int length = len(text)
    if end > length:
        end = length
    if start < 0:
        start = 0

    cdef int in_string, in_comment, triple_string, string_delim
    cdef int in_fstring, escape_next, in_fexpr, fexpr_depth, is_docstring
    cdef int expect_docstring
    cdef int at_class_body
    (in_string, in_comment, triple_string, string_delim,
     in_fstring, escape_next, in_fexpr, fexpr_depth, is_docstring,
     expect_docstring, at_class_body) = unpack_state(prev_state)

    cdef list results = []
    cdef int i = start
    cdef int token_start
    cdef unsigned char c
    cdef bytes token_bytes
    cdef str token_str
    cdef unsigned char next_c
    cdef int string_style

    # Context tracking
    cdef int after_def = 0
    cdef int after_class = 0
    cdef int after_dot = 0
    cdef int after_at = 0
    cdef int in_def_params = 0
    cdef int param_depth = 0
    cdef int at_type_pos = 0
    cdef int after_arrow = 0
    cdef int bracket_depth = 0
    cdef int pending_def = 0
    cdef int pending_class = 0

    cdef int prev_id_type = 0
    cdef int dot_owner_type = 0

    cdef int after_from = 0
    cdef int after_import = 0
    cdef int in_from_import = 0
    cdef bint followed_by_paren
    cdef bint followed_by_eq
    cdef bint at_arg_pos = False
    cdef bint followed_by_assign
    cdef int _j

    if in_string and is_docstring:
        string_style = STYLE_DOCSTRING
    else:
        string_style = STYLE_STRING

    while i < end:
        c = text[i]

        # ============ COMMENT ============
        if in_comment:
            token_start = i
            while i < end and text[i] != 10 and text[i] != 13:
                i += 1
            if i > token_start:
                results.append((i - token_start, STYLE_COMMENTS))
            if i < end and (text[i] == 10 or text[i] == 13):
                in_comment = 0
                if text[i] == 13 and i + 1 < end and text[i+1] == 10:
                    results.append((2, STYLE_DEFAULT))
                    i += 2
                else:
                    results.append((1, STYLE_DEFAULT))
                    i += 1
            continue

        # ============ F-STRING EXPRESSION { ... } ============
        if in_fexpr:
            if c == 125:  # }
                fexpr_depth -= 1
                if fexpr_depth <= 0:
                    fexpr_depth = 0
                    in_fexpr = 0
                    results.append((1, string_style))
                else:
                    results.append((1, STYLE_BRACKETS))
                i += 1
                continue
            if c == 123:  # {
                fexpr_depth += 1
                results.append((1, STYLE_BRACKETS))
                i += 1
                continue
            if (c == 102 or c == 114 or c == 98) and i + 1 < end:
                next_c = text[i + 1]
                if next_c == 34 or next_c == 39:
                    token_start = i
                    # CHUNK-SAFETY: only consume 4 bytes when all 4 are
                    # inside THIS chunk (not `length` = full document).
                    if i + 3 < end and text[i+2] == next_c and text[i+3] == next_c:
                        results.append((4, STYLE_STRING))
                        i += 4
                    else:
                        results.append((2, STYLE_STRING))
                        i += 2
                    continue
            if c == 34 or c == 39:
                token_start = i
                i += 1
                while i < end:
                    if text[i] == 92:
                        # CHUNK-SAFETY: a backslash as the LAST byte of the
                        # chunk must not skip past `end` (would style one
                        # byte too many).
                        if i + 1 < end:
                            i += 2
                        else:
                            i += 1
                        continue
                    if text[i] == c:
                        i += 1
                        break
                    if text[i] == 10 or text[i] == 13:
                        break
                    i += 1
                results.append((i - token_start, STYLE_STRING))
                continue
            if c == 35:
                token_start = i
                while i < end and text[i] != 10 and text[i] != 13:
                    i += 1
                results.append((i - token_start, STYLE_COMMENTS))
                continue
            if is_alpha(c):
                token_start = i
                while i < end and is_alnum(text[i]):
                    i += 1
                token_bytes = text[token_start:i]
                token_str = token_bytes.decode('utf-8', errors='replace')
                followed_by_paren = (i < end and text[i] == 40)
                if token_str in ("True", "False", "None"):
                    results.append((i - token_start, STYLE_CONSTANTS))
                elif token_str in ("self", "cls"):
                    results.append((i - token_start, STYLE_SELF_CLS))
                elif token_str in keywords:
                    results.append((i - token_start, STYLE_KEYWORD))
                elif token_str in builtins:
                    results.append((i - token_start, STYLE_BUILTINS))
                elif followed_by_paren:
                    results.append((i - token_start, STYLE_FUNCTION_CALL))
                elif len(token_str) > 0 and token_str[0].isupper():
                    results.append((i - token_start, STYLE_CLASS_REFERENCE))
                else:
                    results.append((i - token_start, STYLE_LOCAL_VARIABLE))
                continue
            if is_digit(c):
                token_start = i
                while i < end and (is_alnum(text[i]) or text[i] == 46):
                    i += 1
                results.append((i - token_start, STYLE_NUMBERS))
                continue
            if is_operator(c):
                token_start = i
                i += 1
                if i < end and is_operator(text[i]):
                    i += 1
                results.append((i - token_start, STYLE_OPERATORS))
                continue
            if is_bracket(c):
                results.append((1, STYLE_BRACKETS))
                i += 1
                continue
            if is_space(c):
                token_start = i
                while i < end and is_space(text[i]):
                    i += 1
                results.append((i - token_start, STYLE_DEFAULT))
                continue
            if c == 10 or c == 13:
                results.append((1, STYLE_DEFAULT))
                i += 1
                continue
            results.append((1, STYLE_DEFAULT))
            i += 1
            continue

        # ============ STRING ============
        if in_string:
            token_start = i

            if escape_next:
                i += 1
                escape_next = 0
                results.append((i - token_start, string_style))
                continue

            if c == 92:
                i += 1
                escape_next = 1
                results.append((1, string_style))
                continue

            if triple_string:
                if c == 34 or c == 39:
                    # CHUNK-SAFETY: the closing '"""' may straddle the chunk
                    # end. Only consume 3 quote bytes when ALL of them are
                    # inside THIS chunk (i + 2 < end, NOT < length). Styling
                    # more bytes than the chunk contains corrupts
                    # Scintilla's style buffer and crashes the editor.
                    if i + 2 < end and text[i+1] == c and text[i+2] == c:
                        results.append((3, string_style))
                        i += 3
                        in_string = 0
                        triple_string = 0
                        string_delim = 0
                        in_fstring = 0
                        is_docstring = 0
                        string_style = STYLE_STRING
                        continue
                    results.append((1, string_style))
                    i += 1
                    continue
                if in_fstring and c == 123:
                    # CHUNK-SAFETY: '{{' needs both braces inside the chunk.
                    if i + 1 < end and text[i+1] == 123:
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
                    c = text[i]
                    if c == 34 or c == 39 or c == 92:
                        break
                    if in_fstring and c == 123:
                        break
                    i += 1
                if i > token_start:
                    results.append((i - token_start, string_style))
                continue

            # single-line string
            token_start = i
            while i < end:
                c = text[i]
                if c == string_delim:
                    results.append((i - token_start + 1, string_style))
                    i += 1
                    in_string = 0
                    string_delim = 0
                    in_fstring = 0
                    is_docstring = 0
                    string_style = STYLE_STRING
                    break
                if c == 92:
                    results.append((i - token_start, string_style))
                    results.append((1, string_style))
                    i += 1
                    escape_next = 1
                    break
                if in_fstring and c == 123:
                    # CHUNK-SAFETY: '{{' needs both braces inside the chunk.
                    if i + 1 < end and text[i+1] == 123:
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
                if c == 10 or c == 13:
                    results.append((i - token_start, string_style))
                    in_string = 0
                    string_delim = 0
                    in_fstring = 0
                    is_docstring = 0
                    string_style = STYLE_STRING
                    break
                i += 1
            else:
                results.append((i - token_start, string_style))
            continue

        # ============ NOT IN STRING OR COMMENT ============

        # Whitespace (preserves at_arg_pos / at_type_pos)
        if is_space(c):
            token_start = i
            while i < end and is_space(text[i]):
                i += 1
            results.append((i - token_start, STYLE_DEFAULT))
            continue

        # Newline
        if c == 10 or c == 13:
            if c == 13 and i + 1 < end and text[i+1] == 10:
                results.append((2, STYLE_DEFAULT))
                i += 2
            else:
                results.append((1, STYLE_DEFAULT))
                i += 1
            if bracket_depth == 0:
                after_def = 0
                after_class = 0
                after_at = 0
                after_dot = 0
                prev_id_type = 0
                dot_owner_type = 0
                after_from = 0
                after_import = 0
                in_from_import = 0
                at_arg_pos = False
                at_type_pos = 0
                after_arrow = 0
            continue

        # String with prefix: f"...", r'...', b"..."
        if (c == 102 or c == 114 or c == 98) and i + 1 < end:
            next_c = text[i + 1]
            if next_c == 34 or next_c == 39:
                token_start = i
                # CHUNK-SAFETY: prefixed triple strings need all 4 bytes
                # inside THIS chunk, not just inside the document.
                if i + 3 < end and text[i+2] == next_c and text[i+3] == next_c:
                    if expect_docstring:
                        string_style = STYLE_DOCSTRING
                        is_docstring = 1
                    else:
                        string_style = STYLE_STRING
                    results.append((4, string_style))
                    i += 4
                    in_string = 1
                    triple_string = 1
                    string_delim = next_c
                    in_fstring = 1 if (c == 102) else 0
                else:
                    results.append((2, STYLE_STRING))
                    i += 2
                    in_string = 1
                    triple_string = 0
                    string_delim = next_c
                    in_fstring = 1 if (c == 102) else 0
                expect_docstring = 0
                prev_id_type = 0
                continue

        # Regular string
        if c == 34 or c == 39:
            # CHUNK-SAFETY: the opening '"""' may straddle the chunk end.
            # Only consume 3 quote bytes when ALL of them are inside THIS
            # chunk; otherwise fall through to single-line-string handling
            # (the next chunk's state scan sees the full triple and styles
            # the remaining quotes correctly).
            if i + 2 < end and text[i+1] == c and text[i+2] == c:
                if expect_docstring:
                    string_style = STYLE_DOCSTRING
                    is_docstring = 1
                else:
                    string_style = STYLE_STRING
                results.append((3, string_style))
                i += 3
                in_string = 1
                triple_string = 1
                string_delim = c
                in_fstring = 0
            else:
                results.append((1, STYLE_STRING))
                i += 1
                in_string = 1
                triple_string = 0
                string_delim = c
                in_fstring = 0
            expect_docstring = 0
            prev_id_type = 0
            continue

        # Comment
        if c == 35:
            in_comment = 1
            token_start = i
            i += 1
            while i < end and text[i] != 10 and text[i] != 13:
                i += 1
            results.append((i - token_start, STYLE_COMMENTS))
            prev_id_type = 0
            continue

        # Decorator
        if c == 64:
            results.append((1, STYLE_DECORATOR))
            i += 1
            after_at = 1
            expect_docstring = 0
            prev_id_type = 0
            at_arg_pos = False
            continue

        # Number
        if is_digit(c):
            token_start = i
            while i < end and (is_alnum(text[i]) or text[i] == 46):
                i += 1
            results.append((i - token_start, STYLE_NUMBERS))
            expect_docstring = 0
            prev_id_type = 0
            at_arg_pos = False
            continue

        # Dot
        if c == 46:
            results.append((1, STYLE_DEFAULT))
            i += 1
            if after_from or after_import:
                pass
            else:
                after_dot = 1
                dot_owner_type = prev_id_type
            prev_id_type = 0
            at_arg_pos = False
            continue

        # Brackets
        if is_bracket(c):
            results.append((1, STYLE_BRACKETS))
            if c == 40 or c == 91 or c == 123:
                bracket_depth += 1
                if c == 40 and after_def:
                    in_def_params = 1
                    param_depth = 1
                    after_def = 0
                    at_type_pos = 0
                elif in_def_params:
                    param_depth += 1
                at_arg_pos = True
            elif c == 41 or c == 93 or c == 125:
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
            prev_id_type = 0
            continue

        # Operators
        if is_operator(c):
            token_start = i
            i += 1
            if i < end and is_operator(text[i]):
                i += 1
            results.append((i - token_start, STYLE_OPERATORS))
            expect_docstring = 0
            if text[token_start] == 45 and (token_start + 1 < end) and text[token_start+1] == 62:
                if pending_def and bracket_depth == 0:
                    after_arrow = 1
            prev_id_type = 0
            at_arg_pos = False
            continue

        # Comma
        if c == 44:
            results.append((1, STYLE_COMMA))
            i += 1
            if in_def_params:
                at_type_pos = 0
            if not after_import and not in_from_import and not after_from:
                prev_id_type = 0
            at_arg_pos = True
            continue

        # Colon
        if c == 58:
            results.append((1, STYLE_DEFAULT))
            if in_def_params and param_depth == 1:
                at_type_pos = 1
            elif bracket_depth == 0 and (pending_def or pending_class):
                expect_docstring = 1
                if pending_class:
                    at_class_body = 1
                pending_def = 0
                pending_class = 0
                after_arrow = 0
            i += 1
            prev_id_type = 0
            at_arg_pos = False
            continue

        # Semicolon
        if c == 59:
            results.append((1, STYLE_DEFAULT))
            i += 1
            expect_docstring = 0
            after_from = 0
            after_import = 0
            in_from_import = 0
            prev_id_type = 0
            at_arg_pos = False
            continue

        # ============ IDENTIFIER (word) ============
        if is_alpha(c):
            token_start = i
            while i < end and is_alnum(text[i]):
                i += 1

            token_bytes = text[token_start:i]
            token_str = token_bytes.decode('utf-8', errors='replace')

            followed_by_paren = (i < end and text[i] == 40)
            followed_by_eq = (i < end and text[i] == 61 and
                              (i + 1 >= end or text[i+1] != 61))
            _j = i
            while _j < end and (text[_j] == 32 or text[_j] == 9):
                _j += 1
            followed_by_assign = (_j < end and text[_j] == 61 and
                                  (_j + 1 >= end or text[_j+1] != 61))

            if not (token_str == "def" or token_str == "class"):
                expect_docstring = 0

            # --- Style decision tree (mirrors PyCharm's Python semantics) ---
            if after_at:
                results.append((i - token_start, STYLE_DECORATOR))
                after_at = 0
                prev_id_type = 0

            elif token_str in ("self", "cls"):
                results.append((i - token_start, STYLE_SELF_CLS))
                prev_id_type = 1

            elif token_str in ("True", "False", "None"):
                results.append((i - token_start, STYLE_CONSTANTS))
                prev_id_type = 0

            elif token_str == "def":
                results.append((i - token_start, STYLE_KEYWORD))
                after_def = 1
                pending_def = 1
                pending_class = 0
                at_class_body = 0
                prev_id_type = 0

            elif token_str == "class":
                results.append((i - token_start, STYLE_KEYWORD))
                after_class = 1
                pending_class = 1
                pending_def = 0
                prev_id_type = 0

            elif token_str == "import":
                results.append((i - token_start, STYLE_KEYWORD))
                if after_from:
                    after_from = 0
                    in_from_import = 1
                else:
                    after_import = 1
                prev_id_type = 0

            elif token_str == "from":
                results.append((i - token_start, STYLE_KEYWORD))
                after_from = 1
                prev_id_type = 0

            elif token_str == "as":
                results.append((i - token_start, STYLE_KEYWORD))
                after_import = 0
                in_from_import = 0
                prev_id_type = 0

            elif token_str in keywords:
                results.append((i - token_start, STYLE_KEYWORD))
                prev_id_type = 0

            elif after_def:
                # Method/function declaration name. Magic methods (dunders)
                # get their own magenta color in PyCharm.
                if token_str in magic_methods:
                    results.append((i - token_start, STYLE_MAGIC_METHODS))
                else:
                    results.append((i - token_start, STYLE_FUNCTION_DEF))
                prev_id_type = 3

            elif after_class:
                results.append((i - token_start, STYLE_CLASSES))
                after_class = 0
                prev_id_type = 2

            elif after_dot:
                # Anything CALLED after a dot is violet (method call),
                # anything merely referenced is grey (field).
                if followed_by_paren:
                    results.append((i - token_start, STYLE_INSTANCE_METHOD))
                else:
                    results.append((i - token_start, STYLE_INSTANCE_FIELD))
                after_dot = 0
                dot_owner_type = 0
                prev_id_type = 3

            elif in_def_params and param_depth == 1:
                if at_type_pos:
                    results.append((i - token_start, STYLE_TYPES))
                else:
                    results.append((i - token_start, STYLE_PARAMETERS))
                prev_id_type = 3

            elif after_arrow:
                results.append((i - token_start, STYLE_TYPES))
                prev_id_type = 2

            elif after_from:
                results.append((i - token_start, STYLE_MODULE_NAME))
                prev_id_type = 0

            elif after_import:
                results.append((i - token_start, STYLE_MODULE_NAME))
                prev_id_type = 0

            elif in_from_import:
                if len(token_str) > 0 and token_str[0].isupper():
                    results.append((i - token_start, STYLE_CLASS_REFERENCE))
                    prev_id_type = 2
                else:
                    results.append((i - token_start, STYLE_MODULE_NAME))
                    prev_id_type = 0

            elif (at_arg_pos and followed_by_eq and not in_def_params):
                results.append((i - token_start, STYLE_KEYARGS))
                prev_id_type = 3

            elif at_class_body and followed_by_assign:
                # class-body attribute assignment target -> grey (like PyCharm)
                results.append((i - token_start, STYLE_INSTANCE_FIELD))
                prev_id_type = 3

            elif token_str in builtins:
                # Builtins (super, print, str, os, ...) are grey in PyCharm,
                # even when called.
                results.append((i - token_start, STYLE_BUILTINS))
                prev_id_type = 3

            elif followed_by_paren:
                # A called identifier: function call / constructor call (violet).
                # Checked BEFORE the uppercase class-reference branch so that
                # QFont() / QLabel() render as calls, like in PyCharm.
                results.append((i - token_start, STYLE_FUNCTION_CALL))
                prev_id_type = 3

            elif (token_str.startswith("__") and token_str.endswith("__")
                    and len(token_str) > 4):
                # Dunder references (e.g. __file__, __name__) -> grey
                results.append((i - token_start, STYLE_BUILTINS))
                prev_id_type = 3

            elif len(token_str) > 0 and token_str[0].isupper():
                # Class reference at usage site -> grey (only the definition
                # site is colored soft red)
                results.append((i - token_start, STYLE_CLASS_REFERENCE))
                prev_id_type = 2

            else:
                results.append((i - token_start, STYLE_LOCAL_VARIABLE))
                prev_id_type = 3

            at_arg_pos = False
            continue

        # Unknown character
        results.append((1, STYLE_DEFAULT))
        i += 1
        prev_id_type = 0
        at_arg_pos = False

    cdef int final_state = pack_state(in_string, in_comment, triple_string,
                             string_delim, in_fstring, escape_next,
                             in_fexpr, fexpr_depth, is_docstring,
                             expect_docstring, at_class_body)
    return (final_state, results)
