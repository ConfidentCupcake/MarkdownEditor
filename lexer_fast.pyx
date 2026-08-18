# lexer_fast.pyx
# Cython implementation of the Python lexer.

# --- Style IDs ---
cdef enum:
    STYLE_DEFAULT = 0
    STYLE_KEYWORD = 1
    STYLE_TYPES = 2
    STYLE_STRING = 3
    STYLE_KEYARGS = 4
    STYLE_BRACKETS = 5
    STYLE_COMMENTS = 6
    STYLE_CONSTANTS = 7
    STYLE_FUNCTIONS = 8
    STYLE_CLASSES = 9
    STYLE_FUNCTION_DEF = 10
    STYLE_DECORATOR = 11
    STYLE_OPERATORS = 12
    STYLE_MAGIC_METHODS = 13
    STYLE_NUMBERS = 14
    STYLE_SELF_CLS = 15
    STYLE_BUILTINS = 16
    STYLE_PARAMETERS = 17
    STYLE_CLASS_REFERENCE = 18
    STYLE_INSTANCE_FIELD = 19
    STYLE_INSTANCE_METHOD = 20
    STYLE_STATIC_FIELD = 21
    STYLE_STATIC_METHOD = 22
    STYLE_FUNCTION_CALL = 23
    STYLE_LOCAL_VARIABLE = 24
    STYLE_COMMA = 25
    STYLE_MODULE_NAME = 26


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

cdef int pack_state(int in_string, int in_comment, int triple,
                     int string_delim, int in_fstring, int escape):
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
    return (in_string, in_comment, triple, string_delim, in_fstring, escape)


# --- State computation ---

def compute_state_before(bytes text, int target_pos):
    cdef int length = len(text)
    if target_pos > length:
        target_pos = length

    cdef int i = 0
    cdef unsigned char c
    cdef unsigned char next_c
    cdef int in_string = 0
    cdef int in_comment = 0
    cdef int triple_string = 0
    cdef int string_delim = 0
    cdef int in_fstring = 0
    cdef int escape_next = 0

    while i < target_pos:
        c = text[i]

        if in_comment:
            if c == 10 or c == 13:
                in_comment = 0
            i += 1
            continue

        if in_string:
            if escape_next:
                escape_next = 0
                i += 1
                continue

            if c == 92 and not triple_string:
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
                        i += 3
                        continue
                    i += 1
                    continue
                i += 1
                continue

            if c == string_delim:
                in_string = 0
                string_delim = 0
                in_fstring = 0
                i += 1
                continue
            i += 1
            continue

        # Check for string prefix: f, r, b
        if (c == 102 or c == 114 or c == 98) and i + 1 < target_pos:
            next_c = text[i + 1]
            if next_c == 34 or next_c == 39:
                if i + 3 < length and text[i+2] == next_c and text[i+3] == next_c:
                    in_string = 1
                    triple_string = 1
                    string_delim = next_c
                    in_fstring = 1 if (c == 102) else 0
                    i += 4
                    continue
                in_string = 1
                triple_string = 0
                string_delim = next_c
                in_fstring = 1 if (c == 102) else 0
                i += 2
                continue

        # Regular string
        if c == 34 or c == 39:
            if i + 2 < length and text[i+1] == c and text[i+2] == c:
                in_string = 1
                triple_string = 1
                string_delim = c
                in_fstring = 0
                i += 3
                continue
            in_string = 1
            triple_string = 0
            string_delim = c
            in_fstring = 0
            i += 1
            continue

        # Comment
        if c == 35:
            in_comment = 1
            i += 1
            continue

        i += 1

    return pack_state(in_string, in_comment, triple_string,
                      string_delim, in_fstring, escape_next)


# --- The full styler ---

def style_chunk(bytes text, int start, int end, int prev_state,
                set keywords, set builtins, set magic_methods):
    cdef int length = len(text)
    if end > length:
        end = length

    cdef int in_string, in_comment, triple_string, string_delim, in_fstring, escape_next
    in_string, in_comment, triple_string, string_delim, in_fstring, escape_next = unpack_state(prev_state)

    cdef list results = []
    cdef int i = start
    cdef int token_start
    cdef unsigned char c
    cdef bytes token_bytes
    cdef str token_str
    cdef unsigned char next_c
    
    # Context Tracking
    cdef int after_def = 0
    cdef int after_class = 0
    cdef int after_dot = 0
    cdef int after_at = 0
    cdef int in_def_params = 0
    cdef int param_depth = 0
    
    # --- New context tracking (v2) ---
    # prev_id_type: 0=none, 1=self/cls, 2=class_ref, 3=other identifier
    # Preserved through whitespice only; consumed by '.'; cleared by everything else
    cdef int prev_id_type = 0
    # dot_owner_type: set when '.' is consumed, used for the next identifier
    cdef int dot_owner_type = 0
    
    # Import context
    cdef int after_from = 0
    cdef int after_import = 0
    cdef int in_from_import = 0
    cdef bint followed_by_paren

    while i < end:
        c = text[i]

        # ============ COMMENT ============
        if in_comment:
            token_start = i
            while i < end and text[i] != 10 and text[i] != 13:
                i += 1
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

        # ============ STRING ============
        if in_string:
            token_start = i

            if escape_next:
                i += 1
                escape_next = 0
                results.append((i - token_start, STYLE_STRING))
                continue

            if c == 92 and not triple_string:
                i += 1
                escape_next = 1
                results.append((1, STYLE_STRING))
                continue

            if triple_string:
                if c == 34 or c == 39:
                    if i + 2 < length and text[i+1] == c and text[i+2] == c:
                        # Closing triple quote
                        results.append((3, STYLE_STRING))
                        i += 3
                        in_string = 0
                        triple_string = 0
                        string_delim = 0
                        in_fstring = 0
                        continue
                    results.append((1, STYLE_STRING))
                    i += 1
                    continue
                token_start = i
                while i < end:
                    c = text[i]
                    if c == 34 or c == 39 or c == 92:
                        break
                    i += 1
                if i > token_start:
                    results.append((i - token_start, STYLE_STRING))
                continue

            # Single-line string
            token_start = i
            while i < end:
                c = text[i]
                if c == string_delim:
                    results.append((i - token_start + 1, STYLE_STRING))
                    i += 1
                    in_string = 0
                    string_delim = 0
                    in_fstring = 0
                    break
                if c == 92:
                    results.append((i - token_start, STYLE_STRING))
                    results.append((1, STYLE_STRING))
                    i += 1
                    escape_next = 1
                    break
                if c == 10 or c == 13:
                    results.append((i - token_start, STYLE_STRING))
                    in_string = 0
                    string_delim = 0
                    in_fstring = 0
                    break
                i += 1
            else:
                results.append((i - token_start, STYLE_STRING))
            continue

        # ============ NOT IN STRING OR COMMENT ============

        # Whitespace
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
            after_def = 0
            after_class = 0
            after_at = 0
            after_dot = 0
            prev_id_type = 0
            dot_owner_type = 0
            after_from = 0
            after_import = 0
            in_from_import = 0
            continue

        # String with prefix: f"...", r'...', b"..."
        if (c == 102 or c == 114 or c == 98) and i + 1 < end:
            next_c = text[i + 1]
            if next_c == 34 or next_c == 39:
                token_start = i
                if i + 3 < length and text[i+2] == next_c and text[i+3] == next_c:
                    results.append((4, STYLE_STRING))
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
                prev_id_type = 0
                continue

        # Regular string
        if c == 34 or c == 39:
            if i + 2 < length and text[i+1] == c and text[i+2] == c:
                results.append((3, STYLE_STRING))
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
            prev_id_type = 0
            continue

        # Number
        if is_digit(c):
            token_start = i
            while i < end and (is_alnum(text[i]) or text[i] == 46):
                i += 1
            results.append((i - token_start, STYLE_NUMBERS))
            prev_id_type = 0
            continue

        # Dot
        if c == 46:
            results.append((1, STYLE_DEFAULT))
            i += 1
            if after_from or after_import:
                # In import context, dots are part of module paths
                # Dont't set after_dot - keep the import context
                pass
            else:
                after_dot = 1
                dot_owner_type = prev_id_type
            prev_id_type = 0
            continue

        # Brackets
        if is_bracket(c):
            results.append((1, STYLE_BRACKETS))
            if c == 40:
                if after_def:
                    in_def_params = 1
                    param_depth = 1
                    after_def = 0
                elif in_def_params:
                    param_depth += 1
            elif c == 91 or c == 123:
                if in_def_params:
                    param_depth += 1
            elif c == 41:
                if in_def_params:
                    param_depth -= 1
                    if param_depth <= 0:
                        in_def_params = 0
                        param_depth = 0
            elif c == 93 or c == 125:
                if in_def_params:
                    param_depth -= 1
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
            prev_id_type = 0
            continue

        # Comma
        if c == 44:
            results.append((1, STYLE_COMMA))
            i += 1
            # Import context survives comas
            if not after_import and not in_from_import and not after_from:
                prev_id_type = 0
            continue
            
        # Colon
        if c == 58:
            results.append((1, STYLE_DEFAULT))
            in_def_params = 0
            param_depth = 0
            i += 1
            prev_id_type = 0
            continue
            
        # Semicolon
        if c == 59:
            results.append((1, STYLE_DEFAULT))
            i += 1
            after_from = 0
            after_import = 0
            in_from_import = 0
            prev_id_type = 0
            continue

        # ============ IDENTIFIER (word) ============
        if is_alpha(c):
            token_start = i
            while i < end and is_alnum(text[i]):
                i += 1

            token_bytes = text[token_start:i]
            token_str = token_bytes.decode('utf-8', errors='replace')
            
            followed_by_paren = (i < end and text[i] == 40)
            
            # --- Style decision tree ---
            if after_at:
                results.append((i - token_start, STYLE_DECORATOR))
                after_at = 0
                prev_id_type = 0
                
            elif token_str in magic_methods:
                results.append((i - token_start, STYLE_MAGIC_METHODS))
                prev_id_type = 3
                
            elif token_str in ("self", "cls"):
                results.append((i - token_start, STYLE_SELF_CLS))
                prev_id_type = 1
                
            elif token_str in ("True", "False", "None"):
                results.append((i - token_start, STYLE_CONSTANTS))
                prev_id_type = 0
                
            elif token_str == "def":
                results.append((i - token_start, STYLE_KEYWORD))
                after_def = 1
                prev_id_type = 0
                
            elif token_str == "class":
                results.append((i - token_start, STYLE_KEYWORD))
                after_class = 1
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
                # 'as' in import: next name is alias
                after_import = 0
                in_from_import = 0
                prev_id_type = 0
                
            elif token_str in keywords:
                results.append((i - token_start, STYLE_KEYWORD))
                prev_id_type = 0
                
            elif after_def:
            # Function definition name
                results.append((i - token_start, STYLE_FUNCTION_DEF))
                prev_id_type = 3
                
            elif after_class:
                # Class name definition
                results.append((i - token_start, STYLE_CLASSES))
                after_class = 0
                prev_id_type = 2
                
            elif after_dot:
                # After dot - check dot_owner_type
                if dot_owner_type == 1:
                    # self.xxx or cls.xxx
                    if followed_by_paren:
                        results.append((i - token_start, STYLE_INSTANCE_METHOD))
                    else:
                        results.append((i - token_start, STYLE_INSTANCE_FIELD))
                elif dot_owner_type == 2:
                    # ClassName.xxx
                    if followed_by_paren:
                        results.append((i - token_start, STYLE_STATIC_METHOD))
                    else:
                        results.append((i - token_start, STYLE_STATIC_FIELD))
                else:
                    # obj.xxx
                    if followed_by_paren:
                        results.append((i - token_start, STYLE_FUNCTIONS))
                    else:
                        results.append((i - token_start, STYLE_DEFAULT))
                after_dot = 0
                dot_owner_type = 0
                prev_id_type = 3
            
            elif in_def_params and param_depth == 1:
                # Parameter inside function definition
                if i < end and text[i] == 61:
                    results.append((i - token_start, STYLE_KEYARGS))
                else:
                    results.append((i - token_start, STYLE_PARAMETERS))
                prev_id_type = 3

            elif after_from:
                # Module name in from-import
                results.append((i - token_start, STYLE_MODULE_NAME))
                prev_id_type = 0

            elif after_import:
                # Module name in standalone import
                results.append((i - token_start, STYLE_MODULE_NAME))
                prev_id_type = 0

            elif in_from_import:
                # Imported name from 'from X import Y'
                if len(token_str) > 0 and token_str[0].isupper():
                    results.append((i - token_start, STYLE_CLASS_REFERENCE))
                    prev_id_type = 2
                else:
                    results.append((i - token_start, STYLE_LOCAL_VARIABLE))
                    prev_id_type = 3
            
            elif token_str in builtins:
                # Builtin function/class
                if followed_by_paren:
                    results.append((i - token_start, STYLE_FUNCTION_CALL))
                else:
                    results.append((i - token_start, STYLE_BUILTINS))
                prev_id_type = 3
                
            elif len(token_str) > 0 and token_str[0].isupper():
                # Capitalized name -> class reference
                results.append((i - token_start, STYLE_CLASS_REFERENCE))
                prev_id_type = 2
                
            elif followed_by_paren:
                # Bare function call
                results.append((i - token_start, STYLE_FUNCTION_CALL))
                prev_id_type = 3
            
            else:
                # Local Variable
                results.append((i - token_start, STYLE_LOCAL_VARIABLE))
                prev_id_type = 3
                
            continue

        # Unknown character
        results.append((1, STYLE_DEFAULT))
        i += 1
        prev_id_type = 0

    cdef int final_state = pack_state(in_string, in_comment, triple_string,
                             string_delim, in_fstring, escape_next)
    return (final_state, results)
