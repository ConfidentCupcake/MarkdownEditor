import re
import json
from PyQt5.QtGui import QFont, QColor
from PyQt5.Qsci import QsciLexerCustom

import keyword
import types
import builtins
import os
import sys

try:
    from lexer_fast import style_chunk as _cython_style
    from lexer_fast import compute_state_before as _cython_state
    _HAS_CYTHON = True
except ImportError:
    _HAS_CYTHON = False

def _resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class NeutronLexer(QsciLexerCustom):
    def __init__(self, language_name, editor, theme=None):
        super(NeutronLexer, self).__init__(editor)
        self.editor = editor
        self.language_name = language_name
        self.theme_json = None
        self.theme = theme or _resource_path(os.path.join("themes", "theme.json"))

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
        # --- New styles for Cython lexer 
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


        self.default_names = [
            "default", "keyword", "types", "string", "keyargs",
            "brackets", "comments", "constants", "functions",
            "classes", "function_def", "decorator", 
            "operators", "magic_methods", "numbers", 
            "self_cls", "builtins", "parameters",
            "class_reference", "instance_field", "instance_method",
            "static_field", "static_method", "function_call",
            "local_variable", "comma", "module_name",
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
        self._prev_state=0

    def _init_theme(self):
        with open(self.theme, "r", encoding="utf-8") as f:
            self.theme_json = json.load(f)

        colors = self.theme_json["theme"]["syntax"]
        for clr in colors:
            name = list(clr.keys())[0]
            if name not in self.default_names:
                print(f"Theme error: {name} is not a valid style name!")
                continue
            for k, v in clr[name].items():
                if k == "color":
                    self.setColor(QColor(v), getattr(self, name.upper()))
                elif k == "paper-color":
                    self.setPaper(QColor(v), getattr(self, name.upper()))
                elif k == "font":
                    weight = self.font_weights.get(v.get("font-weight", "light"), QFont.Light)
                    self.setFont(
                        QFont(
                            v.get("family", "sans-serif"),
                            v.get("font-size", 13),
                            weight,
                            v.get("italic", False),
                        ),
                        getattr(self, name.upper())
                    )

    def language(self):
        return self.language_name

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

    def _compute_state_before(self, full_text, target_char_pos):
        """
        Scan from the beginning of the document to target_char_pos
        and return the lexer state at that position.

        This is the GUARANTEED CORRECT approach — it doesn't rely on
        any cached state. It re-parses the prefix every time.

        Returns: (in_string, in_comment, triple_string, string_delim,
                  in_fstring, escape_next)
        """
        prefix = full_text[:target_char_pos]
        self.generate_token(prefix)

        in_string = False
        in_comment = False
        triple_string = False
        string_delim = None
        in_fstring = False
        escape_next = False

        while True:
            curr = self.next_tok()
            if curr is None:
                break
            tok, _ = curr

            if in_comment:
                if "\n" in tok or "\r" in tok:
                    in_comment = False
                continue

            if in_string:
                if escape_next:
                    escape_next = False
                    continue

                if tok == "\\" and not triple_string:
                    escape_next = True
                    continue

                if triple_string:
                    if tok in ("'", '"'):
                        p1 = self.peek_tok(0)
                        p2 = self.peek_tok(1)
                        if p1 and p1[0] == tok and p2 and p2[0] == tok:
                            self.next_tok()
                            self.next_tok()
                            in_string = False
                            triple_string = False
                            string_delim = None
                            in_fstring = False
                    continue

                if tok == string_delim:
                    in_string = False
                    string_delim = None
                    in_fstring = False
                continue

            # f-string prefix detection
            if tok in ("f", "fr", "rf", "r", "b", "rb", "br", "fb", "bf") and not tok.isnumeric():
                nt = self.peek_tok(0)
                if nt[0] in ("'", '"'):
                    p1 = self.peek_tok(1)
                    p2 = self.peek_tok(2)
                    if p1 and p1[0] == nt[0] and p2 and p2[0] == nt[0]:
                        self.next_tok()
                        self.next_tok()
                        in_string = True
                        triple_string = True
                        string_delim = nt[0]
                        in_fstring = "f" in tok
                    else:
                        self.next_tok()
                        in_string = True
                        triple_string = False
                        string_delim = nt[0]
                        in_fstring = "f" in tok
                    continue

            if tok in ("'", '"'):
                p1 = self.peek_tok(0)
                p2 = self.peek_tok(1)
                if p1 and p1[0] == tok and p2 and p2[0] == tok:
                    self.next_tok()
                    self.next_tok()
                    in_string = True
                    triple_string = True
                    string_delim = tok
                    in_fstring = False
                else:
                    in_string = True
                    triple_string = False
                    string_delim = tok
                    in_fstring = False
                continue

            if tok == "#":
                in_comment = True
                continue

        return in_string, in_comment, triple_string, string_delim, in_fstring, escape_next


class PyCustomLexer(NeutronLexer):
    def __init__(self, editor):
        super(PyCustomLexer, self).__init__("Python", editor)
        self.setKeywords(keyword.kwlist)
        self.setBuiltinNames([
            name for name, obj in vars(builtins).items()
            if isinstance(obj, (types.BuiltinFunctionType, type))
        ])
        self._keyword_set = set(keyword.kwlist)
        self._builtin_set = set(self.builtin_names)
        self.setDefaultPaper(QColor("#282c34"))
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
        
        if _HAS_CYTHON:
            # --- Cython path (50 - 100x faster) ---
            # Call the Cython style_chunk() function. It scans the text at C speed and returns:
            #   final_state: packed integer state for the next call
            #   styled_tokens: list of (byte_length, style_id) tuples
            final_state, styled_tokens = _cython_style(
                text_bytes,
                start_byte,
                end_byte,
                self._prev_state,
                self._keyword_set,
                self._builtin_set,
                self._magic_set,
            )
            
            # Apply the styles to QScintilla
            # startStyling() sets the starting byte pyosition
            # setStyling(length, style_id) styles 'length' bytes
            self.startStyling(start)
            for byte_len, style_id in styled_tokens:
                self.setStyling(byte_len, style_id)
                
            # Save the state for the next styleText call
            self._prev_state = final_state
            
        else:
            # --- Pure Python fallback (your existing code) ---
            # This runs when the Cython module is not compiled.
            # It's the same tokenization and styling that was here before
            in_string, in_comment, triple_string, string_delim, in_fstring, escape_next = self._compute_state_before(full_text, start_char)
            
            chunk = full_text[start_char:end_char]
            self.startStyling(start)
            self.generate_token(chunk)
            
            
        
        while True:
            curr_token = self.next_tok()
            if curr_token is None:
                break

            tok, tok_len = curr_token

            if in_comment:
                self.setStyling(tok_len, self.COMMENTS)
                if "\n" in tok or "\r" in tok:
                    in_comment = False
                continue

            if in_string:
                # f-string expression handling
                if in_fstring and not triple_string:
                    if tok == "{":
                        self.setStyling(tok_len, self.BRACKETS)
                        depth = 1
                        while depth > 0:
                            expr_tok = self.next_tok()
                            if expr_tok is None:
                                break
                            et, el = expr_tok
                            if et == "{":
                                depth += 1
                                self.setStyling(el, self.BRACKETS)
                            elif et == "}":
                                depth -= 1
                                self.setStyling(el, self.BRACKETS)
                            else:
                                self.setStyling(el, self.DEFAULT)
                        continue
                    self.setStyling(tok_len, self.STRING)
                else:
                    self.setStyling(tok_len, self.STRING)

                if escape_next:
                    escape_next = False
                    continue

                if tok == "\\" and not triple_string:
                    escape_next = True
                    continue

                if triple_string:
                    if tok in ("'", '"'):
                        p1 = self.peek_tok(0)
                        p2 = self.peek_tok(1)
                        if p1 and p1[0] == tok and p2 and p2[0] == tok:
                            t1 = self.next_tok()
                            self.setStyling(t1[1], self.STRING)
                            t2 = self.next_tok()
                            self.setStyling(t2[1], self.STRING)
                            in_string = False
                            triple_string = False
                            string_delim = None
                            in_fstring = False
                    continue

                if tok == string_delim:
                    in_string = False
                    string_delim = None
                    in_fstring = False
                continue

            # --- Not in string or comment ---

            # f-string prefix detection
            if tok in ("f", "fr", "rf", "r", "b", "rb", "br", "fb", "bf") and not tok.isnumeric():
                next_tok = self.peek_tok(0)
                if next_tok[0] in ("'", '"'):
                    p1 = self.peek_tok(1)
                    p2 = self.peek_tok(2)
                    if p1 and p1[0] == next_tok[0] and p2 and p2[0] == next_tok[0]:
                        # Triple-quoted f-string
                        self.setStyling(tok_len, self.STRING)
                        t1 = self.next_tok()
                        self.setStyling(t1[1], self.STRING)
                        t2 = self.next_tok()
                        self.setStyling(t2[1], self.STRING)
                        in_string = True
                        triple_string = True
                        string_delim = next_tok[0]
                        in_fstring = "f" in tok
                    else:
                        # Single-quoted f-string
                        self.setStyling(tok_len, self.STRING)
                        qt = self.next_tok()
                        self.setStyling(qt[1], self.STRING)
                        in_string = True
                        triple_string = False
                        string_delim = qt[0]
                        in_fstring = "f" in tok
                    continue

            if tok in ("'", '"'):
                self.setStyling(tok_len, self.STRING)
                p1 = self.peek_tok(0)
                p2 = self.peek_tok(1)

                if p1 and p1[0] == tok and p2 and p2[0] == tok:
                    t1 = self.next_tok()
                    self.setStyling(t1[1], self.STRING)
                    t2 = self.next_tok()
                    self.setStyling(t2[1], self.STRING)
                    in_string = True
                    triple_string = True
                    string_delim = tok
                    in_fstring = False
                else:
                    in_string = True
                    triple_string = False
                    string_delim = tok
                    in_fstring = False
                continue

            # Decorators
            if tok == "@":
                self.setStyling(tok_len, self.DECORATOR)
                name, name_index = self.skip_space_peek()
                if name and name[0].isidentifier():
                    for _ in range(name_index + 1):
                        t = self.next_tok()
                        if t is None:
                            break
                        if t[0].isspace():
                            self.setStyling(t[1], self.DEFAULT)
                        else:
                            self.setStyling(t[1], self.DECORATOR)
                    while self.peek_tok()[0] == ".":
                        dot = self.next_tok()
                        self.setStyling(dot[1], self.DECORATOR)
                        attr = self.next_tok()
                        if attr is None:
                            break
                        self.setStyling(attr[1], self.DECORATOR)
                continue

            if tok == "#":
                self.setStyling(tok_len, self.COMMENTS)
                in_comment = True
                continue

            if tok == "class":
                name, name_index = self.skip_space_peek()
                after_name = self.peek_tok(name_index + 1) if name and name[0] else ("", 0)
                if name[0].isidentifier() and after_name[0] in (":", "("):
                    self.setStyling(tok_len, self.KEYWORD)
                    for _ in range(name_index + 1):
                        t = self.next_tok()
                        if t is None:
                            break
                        if t[0].isspace():
                            self.setStyling(t[1], self.DEFAULT)
                        else:
                            self.setStyling(t[1], self.CLASSES)
                    continue
                self.setStyling(tok_len, self.KEYWORD)
                continue

            if tok == "def":
                name, name_index = self.skip_space_peek()
                if name[0].isidentifier():
                    self.setStyling(tok_len, self.KEYWORD)
                    for _ in range(name_index + 1):
                        t = self.next_tok()
                        if t is None:
                            break
                        style = self.DEFAULT if t[0].isspace() else self.FUNCTION_DEF
                        self.setStyling(t[1], style)
                    continue
                self.setStyling(tok_len, self.KEYWORD)
                continue

            if tok in self.keyword_list:
                self.setStyling(tok_len, self.KEYWORD)
            elif tok == "." and self.peek_tok()[0].isidentifier():
                self.setStyling(tok_len, self.DEFAULT)
                nxt = self.next_tok()
                if nxt is None:
                    break
                name_tok, name_len = nxt
                if self.peek_tok()[0] == "(":
                    self.setStyling(name_len, self.FUNCTIONS)
                else:
                    self.setStyling(name_len, self.DEFAULT)
            elif tok.isnumeric() or tok in ("self", "cls"):
                self.setStyling(tok_len, self.CONSTANTS)
            elif tok in ["(", ")", "{", "}", "[", "]"]:
                self.setStyling(tok_len, self.BRACKETS)
            elif tok in self.builtin_names or tok in ['+', '-', '*', '/', '%', '=', '<', '>', '!', '&', '|', '^', '~']:
                self.setStyling(tok_len, self.TYPES)
            else:
                self.setStyling(tok_len, self.DEFAULT)
