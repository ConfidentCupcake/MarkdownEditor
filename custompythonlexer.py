import re
import json
from PyQt5.QtGui import QFont, QColor
from PyQt5.Qsci import QsciLexerCustom

import keyword
import types
import builtins
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class NeutronLexer(QsciLexerCustom):
    def __init__(self, language_name, editor, theme=None):
        super(NeutronLexer, self).__init__(editor)
        self.editor = editor
        self.language_name = language_name
        self.theme_json = None
        self.theme = theme or os.path.join(BASE_DIR, "themes", "theme.json")

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

        self.default_names = [
            "default", "keyword", "types", "string", "keyargs",
            "brackets", "comments", "constants", "functions",
            "classes", "function_def",
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
        }
        return names.get(style, "")

    # ------------------------------------------------------------------ #
    #  Byte <-> character offset conversion
    # ------------------------------------------------------------------ #
    @staticmethod
    def _byte_pos_to_char_index(text: str, byte_pos: int) -> int:
        """QScintilla hands us BYTE offsets, but editor.text() is a Python
        Unicode string. Convert a byte position to the matching character
        index so slicing stays correct for multibyte content (umlauts,
        emoji, em-dashes, etc.)."""
        if byte_pos <= 0:
            return 0
        encoded = text.encode("utf-8")
        if byte_pos >= len(encoded):
            return len(text)
        return len(encoded[:byte_pos].decode("utf-8", errors="ignore"))

    def generate_token(self, text):
        p = re.compile(r"[*]\\/|\\/[*]|\s+|\w+|\W")
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
    def __init__(self, editor):
        super(PyCustomLexer, self).__init__("Python", editor)
        self.setKeywords(keyword.kwlist)
        self.setBuiltinNames([
            name for name, obj in vars(builtins).items()
            if isinstance(obj, types.BuiltinFunctionType)
        ])
        self.setDefaultPaper(QColor("#282c34"))

    def _state_before(self, text):
        self.generate_token(text)

        in_string = False
        in_comment = False
        string_delim = None
        triple_string = False
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
                    if tok == string_delim:
                        p1 = self.peek_tok(0)
                        p2 = self.peek_tok(1)
                        if p1 and p1[0] == string_delim and p2 and p2[0] == string_delim:
                            self.next_tok()
                            self.next_tok()
                            in_string = False
                            triple_string = False
                            string_delim = None
                    continue

                if tok == string_delim:
                    in_string = False
                    string_delim = None
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
                else:
                    in_string = True
                    triple_string = False
                    string_delim = tok
                continue

            if tok == "#":
                in_comment = True

        return in_string, in_comment, string_delim, triple_string, escape_next

    def styleText(self, start: int, end: int) -> None:
        full_text = self.editor.text()

        # `start` / `end` are Scintilla BYTE offsets. Convert to character
        # indices before touching the Unicode string.
        start_char = self._byte_pos_to_char_index(full_text, start)
        end_char = self._byte_pos_to_char_index(full_text, end)

        prefix = full_text[:start_char]
        chunk = full_text[start_char:end_char]

        in_string, in_comment, string_delim, triple_string, escape_next = self._state_before(prefix)

        # startStyling still takes the original BYTE offset for Scintilla.
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
                self.setStyling(tok_len, self.STRING)

                if escape_next:
                    escape_next = False
                    continue

                if tok == "\\" and not triple_string:
                    escape_next = True
                    continue

                if triple_string:
                    if tok == string_delim:
                        p1 = self.peek_tok(0)
                        p2 = self.peek_tok(1)
                        if p1 and p1[0] == string_delim and p2 and p2[0] == string_delim:
                            t = self.next_tok()
                            if t is not None:
                                self.setStyling(t[1], self.STRING)
                            t = self.next_tok()
                            if t is not None:
                                self.setStyling(t[1], self.STRING)
                            in_string = False
                            triple_string = False
                            string_delim = None
                    continue

                if tok == string_delim:
                    in_string = False
                    string_delim = None
                continue

            if tok in ("'", '"'):
                self.setStyling(tok_len, self.STRING)
                p1 = self.peek_tok(0)
                p2 = self.peek_tok(1)

                if p1 and p1[0] == tok and p2 and p2[0] == tok:
                    t = self.next_tok()
                    if t is not None:
                        self.setStyling(t[1], self.STRING)
                    t = self.next_tok()
                    if t is not None:
                        self.setStyling(t[1], self.STRING)
                    in_string = True
                    triple_string = True
                    string_delim = tok
                else:
                    in_string = True
                    triple_string = False
                    string_delim = tok
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
                    # Consume whitespace tokens AND the class name itself.
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
                    # Consume whitespace tokens AND the function name itself.
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
            elif tok.isnumeric() or tok == "self":
                self.setStyling(tok_len, self.CONSTANTS)
            elif tok in ["(", ")", "{", "}", "[", "]"]:
                self.setStyling(tok_len, self.BRACKETS)
            elif tok in self.builtin_names or tok in ['+', '-', '*', '/', '%', '=', '<', '>']:
                self.setStyling(tok_len, self.TYPES)
            else:
                self.setStyling(tok_len, self.DEFAULT)
