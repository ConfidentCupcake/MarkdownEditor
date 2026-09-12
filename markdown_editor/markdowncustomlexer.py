import re
from PyQt5.QtGui import QColor, QFont
from python_editor.custompythonlexer import NeutronLexer


class MarkdownCustomLexer(NeutronLexer):
    """Custom visual lexer for Markdown, reusing the NeutronLexer base."""

    def __init__(self, editor, theme=None, paper=None):
        """
        :param theme: absolute path to a theme .json; None -> default
        :param paper: QColor paper override from the Settings color picker;
                      None -> the theme's editor.paper-color
        """
        super(MarkdownCustomLexer, self).__init__("Markdown", editor,
                                                  theme=theme, paper=paper)

        # Fallback colors ONLY for themes without an editor section —
        # _init_theme's theme-driven styles win; these just guarantee
        # unstyled regions never flash white when a legacy theme is loaded.
        if not self.theme_json.get("theme", {}).get("editor"):
            self.setDefaultColor(QColor("#abb2bf"))
            self.setDefaultPaper(QColor("#1e1f22"))

        self.editor.setColor(self.defaultColor())
        self.editor.setPaper(self.defaultPaper())

    # ------------------------------------------------------------------ #
    #  Style IDs + theme mapping
    # ------------------------------------------------------------------ #
    def _init_theme_vars(self):
        self.DEFAULT = 0

        self.HEADER = 1
        self.SETEXT_HEADER = 2
        self.HRULE = 3
        self.BLOCKQUOTE = 4
        self.LIST_MARKER = 5
        self.ORDERED_LIST_MARKER = 6
        self.TASK_MARKER = 7

        self.BOLD = 8
        self.ITALIC = 9
        self.BOLD_ITALIC = 10
        self.STRIKETHROUGH = 11

        self.INLINE_CODE = 12
        self.CODE_FENCE = 13
        self.CODE_BLOCK = 14
        self.CODE_LANGUAGE = 15

        self.LINK_TEXT = 16
        self.LINK_URL = 17
        self.LINK_TITLE = 18
        self.REFERENCE_ID = 19
        self.LINK_DEFINITION = 20
        self.AUTOLINK = 21
        self.IMAGE_MARKER = 22

        self.TABLE_PIPE = 23
        self.TABLE_SEPARATOR = 24
        self.TABLE_TEXT = 25

        self.HTML = 26
        self.ESCAPE = 27
        self.ENTITY = 28

        self.FOOTNOTE_MARKER = 29
        self.FOOTNOTE_TEXT = 30

        self.ALERT = 31
        self.MENTION = 32
        self.ISSUE_REF = 33
        self.EMOJI = 34

        self.COMMENTS = 35  # HTML comments <!-- ... -->

        self.default_names = [
            "default",
            "header",
            "setext_header",
            "hrule",
            "blockquote",
            "list_marker",
            "ordered_list_marker",
            "task_marker",
            "bold",
            "italic",
            "bold_italic",
            "strikethrough",
            "inline_code",
            "code_fence",
            "code_block",
            "code_language",
            "link_text",
            "link_url",
            "link_title",
            "reference_id",
            "link_definition",
            "autolink",
            "image_marker",
            "table_pipe",
            "table_separator",
            "table_text",
            "html",
            "escape",
            "entity",
            "footnote_marker",
            "footnote_text",
            "alert",
            "mention",
            "issue_ref",
            "emoji",
            "comments",
        ]

        self.font_weights = {
            "thin": QFont.Thin,
            "extralight": QFont.ExtraLight,
            "light": QFont.Light,
            "normal": QFont.Normal,
            "medium": QFont.Medium,
            "demibold": QFont.DemiBold,
            "bold": QFont.Bold,
            "extrabold": QFont.ExtraBold,
            "black": QFont.Black,
        }

    def description(self, style: int) -> str:
        mapping = {
            self.DEFAULT: "DEFAULT",
            self.HEADER: "HEADER",
            self.SETEXT_HEADER: "SETEXT_HEADER",
            self.HRULE: "HRULE",
            self.BLOCKQUOTE: "BLOCKQUOTE",
            self.LIST_MARKER: "LIST_MARKER",
            self.ORDERED_LIST_MARKER: "ORDERED_LIST_MARKER",
            self.TASK_MARKER: "TASK_MARKER",
            self.BOLD: "BOLD",
            self.ITALIC: "ITALIC",
            self.BOLD_ITALIC: "BOLD_ITALIC",
            self.STRIKETHROUGH: "STRIKETHROUGH",
            self.INLINE_CODE: "INLINE_CODE",
            self.CODE_FENCE: "CODE_FENCE",
            self.CODE_BLOCK: "CODE_BLOCK",
            self.CODE_LANGUAGE: "CODE_LANGUAGE",
            self.LINK_TEXT: "LINK_TEXT",
            self.LINK_URL: "LINK_URL",
            self.LINK_TITLE: "LINK_TITLE",
            self.REFERENCE_ID: "REFERENCE_ID",
            self.LINK_DEFINITION: "LINK_DEFINITION",
            self.AUTOLINK: "AUTOLINK",
            self.IMAGE_MARKER: "IMAGE_MARKER",
            self.TABLE_PIPE: "TABLE_PIPE",
            self.TABLE_SEPARATOR: "TABLE_SEPARATOR",
            self.TABLE_TEXT: "TABLE_TEXT",
            self.HTML: "HTML",
            self.ESCAPE: "ESCAPE",
            self.ENTITY: "ENTITY",
            self.FOOTNOTE_MARKER: "FOOTNOTE_MARKER",
            self.FOOTNOTE_TEXT: "FOOTNOTE_TEXT",
            self.ALERT: "ALERT",
            self.MENTION: "MENTION",
            self.ISSUE_REF: "ISSUE_REF",
            self.EMOJI: "EMOJI",
            self.COMMENTS: "COMMENTS",
        }
        return mapping.get(style, "")

    # ------------------------------------------------------------------ #
    #  Fence helpers
    # ------------------------------------------------------------------ #
    def _fence_line(self, line: str):
        """Return (fence_char, fence_len) if the line opens a code fence,
        otherwise (None, 0). A fence line begins (after indentation) with 3+
        identical backticks or tildes."""
        indent = len(line) - len(line.lstrip(" "))
        if indent > 3 or (line.startswith("\t") and not line.startswith("   ")):
            return None, 0
        stripped = line[indent:]
        if not stripped:
            return None, 0
        c = stripped[0]
        if c not in ("`", "~"):
            return None, 0
        n = 0
        for ch in stripped:
            if ch == c:
                n += 1
            else:
                break
        if n >= 3:
            return c, n
        return None, 0

    def _is_closing_fence(self, line: str, fence_char: str, fence_len: int) -> bool:
        """True if `line` closes the currently open fence: same char, at least
        as many, and only whitespace afterwards."""
        char, count = self._fence_line(line)
        if char != fence_char or count < fence_len:
            return False
        stripped = line.lstrip(" ")
        return stripped[count:].strip() == ""

    def _state_before(self, full_text: str, start_char: int):
        """Compute fence/comment state immediately before ``start_char``.

        Fenced code has precedence over HTML comment markers. Outside a fence,
        comment spans are removed only for the purpose of recognizing a fence;
        inside a fence, ``<!--`` and ``-->`` are ordinary code text.
        """
        prefix = full_text[:start_char]
        in_fence = False
        in_comment = False
        fence_char = None
        fence_len = 0

        for line in prefix.split("\n"):
            if in_fence:
                if self._is_closing_fence(line, fence_char, fence_len):
                    in_fence = False
                    fence_char = None
                    fence_len = 0
                continue

            cursor = 0
            visible_parts = []
            while cursor < len(line):
                if in_comment:
                    close = line.find("-->", cursor)
                    if close < 0:
                        cursor = len(line)
                        break
                    in_comment = False
                    cursor = close + 3
                    continue

                opening = line.find("<!--", cursor)
                if opening < 0:
                    visible_parts.append(line[cursor:])
                    break
                visible_parts.append(line[cursor:opening])
                close = line.find("-->", opening + 4)
                if close < 0:
                    in_comment = True
                    break
                cursor = close + 3

            visible = "".join(visible_parts)
            char, count = self._fence_line(visible)
            if char is not None:
                in_fence = True
                fence_char = char
                fence_len = count

        return in_fence, fence_char, fence_len, in_comment

    # ------------------------------------------------------------------ #
    #  Block classification helpers (operate on the stripped line string)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _is_atx_header(stripped: str) -> bool:
        if not stripped.startswith("#"):
            return False
        cnt = 0
        for ch in stripped:
            if ch == "#":
                cnt += 1
            else:
                break
        if not (1 <= cnt <= 6):
            return False
        rest = stripped[cnt:]
        return rest == "" or rest[0].isspace()

    @staticmethod
    def _is_setext_underline(stripped: str) -> bool:
        # Only the '===' form here; '---' would collide with thematic breaks.
        return bool(stripped) and set(stripped) == {"="}

    @staticmethod
    def _is_hrule(stripped: str) -> bool:
        if not stripped:
            return False
        chars = set(stripped.replace(" ", ""))
        if len(chars) != 1:
            return False
        c = chars.pop()
        if c not in ("-", "*", "_"):
            return False
        return stripped.count(c) >= 3

    @staticmethod
    def _is_unordered_list(stripped: str) -> bool:
        if not stripped or stripped[0] not in ("-", "*", "+"):
            return False
        return len(stripped) == 1 or stripped[1].isspace()

    @staticmethod
    def _ordered_list_prefix(stripped: str):
        """Return the matched prefix length (number+marker) if this is an
        ordered list item, else 0."""
        m = re.match(r"\d+[.)](?=\s|$)", stripped)
        return m.end() if m else 0

    @staticmethod
    def _is_table_row(stripped: str) -> bool:
        unescaped = re.findall(r"(?<!\\)\|", stripped)
        return len(unescaped) >= 2 or (
            bool(unescaped) and stripped.startswith("|") and stripped.endswith("|")
        )

    @staticmethod
    def _is_table_separator(stripped: str) -> bool:
        cleaned = stripped.replace(" ", "")
        if not cleaned or "-" not in cleaned:
            return False
        return set(cleaned) <= {"|", ":", "-"}

    @staticmethod
    def _is_footnote_def(stripped: str) -> bool:
        return bool(re.match(r"^\[\^[^\]]+\]:", stripped))

    # ------------------------------------------------------------------ #
    #  styleText entry point
    # ------------------------------------------------------------------ #
    @staticmethod
    def _byte_pos_to_char_index(text: str, byte_pos: int) -> int:
        """QScintilla hands us BYTE offsets, but editor.text() is a Python
        Unicode string. Convert a byte position to the matching character index
        so slicing and prefix scanning stay correct for multibyte content
        (umlauts, emoji, em-dashes, etc.)."""
        if byte_pos <= 0:
            return 0
        encoded = text.encode("utf-8")
        if byte_pos >= len(encoded):
            return len(text)
        return len(encoded[:byte_pos].decode("utf-8", errors="ignore"))

    def styleText(self, start: int, end: int) -> None:
        full_text = self.editor.text()
        start_char = self._byte_pos_to_char_index(full_text, start)
        end_char = self._byte_pos_to_char_index(full_text, end)
        in_fence, fence_char, fence_len, in_comment = self._state_before(full_text, start_char)
        partial_first = start_char > 0 and full_text[start_char - 1] != "\n"

        self.startStyling(start)
        lines = full_text[start_char:end_char].split("\n")
        for index, line in enumerate(lines):
            in_fence, fence_char, fence_len, in_comment = self._style_line_with_comments(
                line,
                in_fence,
                fence_char,
                fence_len,
                in_comment,
                force_inline=partial_first and index == 0,
            )
            if index != len(lines) - 1:
                self.setStyling(1, self.DEFAULT)

    # ------------------------------------------------------------------ #
    #  Per-line styling
    # ------------------------------------------------------------------ #
    def _style_line(self, line, in_fence, fence_char, fence_len, force_inline=False):
        """Style one line of text and return the updated fence state."""

        # --- Inside a fenced code block -------------------------------- #
        if in_fence:
            if not force_inline and self._is_closing_fence(line, fence_char, fence_len):
                self._style_plain_line(line, self.CODE_FENCE)
                return False, None, 0
            self._style_plain_line(line, self.CODE_BLOCK)
            return in_fence, fence_char, fence_len

        # A partial first segment: never opens a fence / header.
        if not force_inline:
            c, n = self._fence_line(line)
            if c is not None:
                self._style_fence_open_line(line, c, n)
                return True, c, n

        stripped = line.lstrip(" \t")

        if not force_inline:
            if self._is_atx_header(stripped):
                self._style_plain_line(line, self.HEADER)
                return in_fence, fence_char, fence_len

            if self._is_setext_underline(stripped):
                self._style_plain_line(line, self.SETEXT_HEADER)
                return in_fence, fence_char, fence_len

            if self._is_hrule(stripped):
                self._style_plain_line(line, self.HRULE)
                return in_fence, fence_char, fence_len

            if self._is_footnote_def(stripped):
                self._style_footnote_def_line(line)
                return in_fence, fence_char, fence_len

            if self._is_table_row(stripped):
                is_sep = self._is_table_separator(stripped)
                self._style_table_line(line, is_sep)
                return in_fence, fence_char, fence_len

            if stripped.startswith(">"):
                self._style_blockquote_line(line)
                return in_fence, fence_char, fence_len

            if self._is_unordered_list(stripped):
                self._style_unordered_list_line(line)
                return in_fence, fence_char, fence_len

            if self._ordered_list_prefix(stripped):
                self._style_ordered_list_line(line)
                return in_fence, fence_char, fence_len

        # Default: parse inline spans.
        self.generate_token(line)
        self._style_inline()
        return in_fence, fence_char, fence_len

    # ------------------------------------------------------------------ #
    #  Simple whole-line stylers
    # ------------------------------------------------------------------ #
    def _style_plain_line(self, line, style):
        self.generate_token(line)
        while True:
            curr = self.next_tok()
            if curr is None:
                break
            self.setStyling(curr[1], style)

    def _style_fence_open_line(self, line, fence_char, fence_len):
        """Opening fence: leading spaces -> DEFAULT, fence chars -> CODE_FENCE,
        trailing info string -> CODE_LANGUAGE."""
        self.generate_token(line)
        # leading whitespace
        while True:
            nxt = self.peek_tok(0)
            if nxt and nxt[0] and nxt[0].isspace():
                t = self.next_tok()
                self.setStyling(t[1], self.DEFAULT)
            else:
                break
        # fence chars (consume exactly fence_len of them)
        for _ in range(fence_len):
            nxt = self.peek_tok(0)
            if nxt and nxt[0] == fence_char:
                t = self.next_tok()
                self.setStyling(t[1], self.CODE_FENCE)
        # remainder = info string
        while True:
            curr = self.next_tok()
            if curr is None:
                break
            self.setStyling(curr[1], self.CODE_LANGUAGE)

    def _style_footnote_def_line(self, line):
        """`[^id]: text` -> label part FOOTNOTE_MARKER, rest FOOTNOTE_TEXT."""
        self.generate_token(line)
        # leading spaces
        while True:
            nxt = self.peek_tok(0)
            if nxt and nxt[0] and nxt[0].isspace():
                t = self.next_tok()
                self.setStyling(t[1], self.DEFAULT)
            else:
                break
        # consume the [..]: label as FOOTNOTE_MARKER
        seen_colon = False
        while True:
            curr = self.next_tok()
            if curr is None:
                break
            self.setStyling(curr[1], self.FOOTNOTE_MARKER)
            if curr[0] == ":":
                seen_colon = True
                break
        # rest of line as FOOTNOTE_TEXT (plain, no inline)
        if seen_colon:
            while True:
                curr = self.next_tok()
                if curr is None:
                    break
                self.setStyling(curr[1], self.FOOTNOTE_TEXT)

    def _style_blockquote_line(self, line):
        self.generate_token(line)
        # leading spaces -> BLOCKQUOTE
        while True:
            nxt = self.peek_tok(0)
            if nxt and nxt[0] and nxt[0].isspace():
                t = self.next_tok()
                self.setStyling(t[1], self.BLOCKQUOTE)
            else:
                break
        # the '>' marker
        nxt = self.peek_tok(0)
        if nxt and nxt[0] == ">":
            t = self.next_tok()
            self.setStyling(t[1], self.BLOCKQUOTE)
        # optional single space after '>'
        nxt = self.peek_tok(0)
        if nxt and nxt[0] and nxt[0].isspace():
            t = self.next_tok()
            self.setStyling(t[1], self.BLOCKQUOTE)
        # rest parsed inline
        self._style_inline()

    def _style_unordered_list_line(self, line):
        self.generate_token(line)
        # leading spaces -> DEFAULT
        while True:
            nxt = self.peek_tok(0)
            if nxt and nxt[0] and nxt[0].isspace():
                t = self.next_tok()
                self.setStyling(t[1], self.DEFAULT)
            else:
                break
        # marker -, * or +
        nxt = self.peek_tok(0)
        if nxt and nxt[0] in ("-", "*", "+"):
            t = self.next_tok()
            self.setStyling(t[1], self.LIST_MARKER)
        # optional space
        nxt = self.peek_tok(0)
        if nxt and nxt[0] and nxt[0].isspace():
            t = self.next_tok()
            self.setStyling(t[1], self.DEFAULT)
        # task list: [ ] or [x]/[X]
        self._maybe_style_task_marker()
        # rest inline
        self._style_inline()

    def _style_ordered_list_line(self, line):
        self.generate_token(line)
        # leading spaces -> DEFAULT
        while True:
            nxt = self.peek_tok(0)
            if nxt and nxt[0] and nxt[0].isspace():
                t = self.next_tok()
                self.setStyling(t[1], self.DEFAULT)
            else:
                break
        # digits -> ORDERED_LIST_MARKER
        while True:
            nxt = self.peek_tok(0)
            if nxt and nxt[0] and nxt[0].isdigit():
                t = self.next_tok()
                self.setStyling(t[1], self.ORDERED_LIST_MARKER)
            else:
                break
        # marker . or )
        nxt = self.peek_tok(0)
        if nxt and nxt[0] in (".", ")"):
            t = self.next_tok()
            self.setStyling(t[1], self.ORDERED_LIST_MARKER)
        # optional space
        nxt = self.peek_tok(0)
        if nxt and nxt[0] and nxt[0].isspace():
            t = self.next_tok()
            self.setStyling(t[1], self.DEFAULT)
        # task list: [ ] or [x]/[X]
        self._maybe_style_task_marker()
        # rest inline
        self._style_inline()

    def _style_line_with_comments(
            self,
            line,
            in_fence,
            fence_char,
            fence_len,
            in_comment,
            force_inline,
    ):
        """Style comments outside fences and code inside fences.

        Returning all four state values keeps incremental restyling consistent
        when QScintilla begins a styling request in the middle of a document.
        """
        if in_fence:
            in_fence, fence_char, fence_len = self._style_line(
                line,
                in_fence,
                fence_char,
                fence_len,
                force_inline=force_inline,
            )
            return in_fence, fence_char, fence_len, False

        # Recognize an opening fence from the complete line before looking for
        # comment markers in its info string. Everything after the opening
        # backticks/tildes is fence metadata, not a Markdown HTML comment.
        if not in_comment and not force_inline:
            opening_char, _opening_len = self._fence_line(line)
            if opening_char is not None:
                in_fence, fence_char, fence_len = self._style_line(
                    line,
                    in_fence,
                    fence_char,
                    fence_len,
                    force_inline=False,
                )
                return in_fence, fence_char, fence_len, False

        cursor = 0
        while cursor < len(line):
            if in_comment:
                close = line.find("-->", cursor)
                end = len(line) if close < 0 else close + 3
                self._style_plain_line(line[cursor:end], self.COMMENTS)
                cursor = end
                if close < 0:
                    return in_fence, fence_char, fence_len, True
                in_comment = False
                continue

            opening = line.find("<!--", cursor)
            end = len(line) if opening < 0 else opening
            if end > cursor:
                in_fence, fence_char, fence_len = self._style_line(
                    line[cursor:end],
                    in_fence,
                    fence_char,
                    fence_len,
                    force_inline=force_inline or cursor > 0,
                )
            if opening < 0:
                return in_fence, fence_char, fence_len, False
            cursor = opening
            in_comment = True

        return in_fence, fence_char, fence_len, in_comment
    
    def _maybe_style_task_marker(self):
        """If the upcoming tokens form `[ ]` or `[x]`/`[X]`, style them as
        TASK_MARKER. Tokens: '[' (' '|'x'|'X') ']'."""
        p0 = self.peek_tok(0)
        p1 = self.peek_tok(1)
        p2 = self.peek_tok(2)
        if (p0 and p0[0] == "[" and p1 and p1[0] in (" ", "x", "X")
                and p2 and p2[0] == "]"):
            for _ in range(3):
                t = self.next_tok()
                if t is None:
                    break
                self.setStyling(t[1], self.TASK_MARKER)
            # optional trailing space
            nxt = self.peek_tok(0)
            if nxt and nxt[0] and nxt[0].isspace():
                t = self.next_tok()
                self.setStyling(t[1], self.DEFAULT)

    def _style_table_line(self, line, is_separator):
        self.generate_token(line)
        sep_style = self.TABLE_SEPARATOR if is_separator else self.TABLE_TEXT
        while True:
            curr = self.next_tok()
            if curr is None:
                break
            tok = curr[0]
            if tok == "|":
                self.setStyling(curr[1], self.TABLE_PIPE)
            elif is_separator and tok in (":", "-"):
                self.setStyling(curr[1], self.TABLE_SEPARATOR)
            else:
                self.setStyling(curr[1], sep_style)

    # ------------------------------------------------------------------ #
    #  Inline span state machine
    # ------------------------------------------------------------------ #
    def _count_run(self, ch, first_len):
        """The current token (length first_len) equals `ch`. Consume any
        immediately following tokens that also equal `ch` and return
        (count, byte_total) covering all of them."""
        count = 1
        total = first_len
        while self.peek_tok(0)[0] == ch:
            t = self.next_tok()
            count += 1
            total += t[1]
        return count, total

    def _style_inline(self):
        """Tokenise-then-walk the current line's token stream for inline spans.
        Inline state is local to this call (one line)."""
        in_inline_code = False
        inline_code_run = 0
        in_link_text = False
        in_link_url = False
        in_strike = False
        in_bold = False
        in_italic = False

        while True:
            curr = self.next_tok()
            if curr is None:
                break
            tok, tok_len = curr

            # 1) Active inline code span ------------------------------ #
            if in_inline_code:
                if tok == "`":
                    count, total = self._count_run("`", tok_len)
                    if count >= inline_code_run:
                        in_inline_code = False
                    self.setStyling(total, self.INLINE_CODE)
                else:
                    self.setStyling(tok_len, self.INLINE_CODE)
                continue

            # 2) Active link URL -------------------------------------- #
            if in_link_url:
                if tok == ")":
                    self.setStyling(tok_len, self.LINK_URL)
                    in_link_url = False
                else:
                    self.setStyling(tok_len, self.LINK_URL)
                continue

            # 3) Active link text ------------------------------------- #
            if in_link_text:
                if tok == "]":
                    self.setStyling(tok_len, self.LINK_TEXT)
                    in_link_text = False
                    nxt = self.peek_tok(0)
                    if nxt[0] == "(":
                        t = self.next_tok()
                        self.setStyling(t[1], self.LINK_URL)
                        in_link_url = True
                    elif nxt[0] == "[":
                        t = self.next_tok()
                        self.setStyling(t[1], self.REFERENCE_ID)
                        while True:
                            c2 = self.peek_tok(0)
                            if not c2 or c2[0] == "":
                                break
                            t2 = self.next_tok()
                            self.setStyling(t2[1], self.REFERENCE_ID)
                            if t2[0] == "]":
                                break
                else:
                    self.setStyling(tok_len, self.LINK_TEXT)
                continue

            # 4) Backslash escape (works inside emphasis/strike) ------- #
            if tok == "\\":
                nxt = self.peek_tok(0)
                if nxt and nxt[0] and nxt[0] != "":
                    t = self.next_tok()
                    self.setStyling(tok_len + t[1], self.ESCAPE)
                else:
                    self.setStyling(tok_len, self.DEFAULT)
                continue

            # 5) Inline code opener (allowed inside emphasis) --------- #
            if tok == "`":
                count, total = self._count_run("`", tok_len)
                in_inline_code = True
                inline_code_run = count
                self.setStyling(total, self.INLINE_CODE)
                continue

            # 6) Active strikethrough --------------------------------- #
            if in_strike:
                if tok == "~":
                    count, total = self._count_run("~", tok_len)
                    if count >= 2:
                        in_strike = False
                    self.setStyling(total, self.STRIKETHROUGH)
                else:
                    self.setStyling(tok_len, self.STRIKETHROUGH)
                continue

            # 7) Active bold / italic --------------------------------- #
            if in_bold or in_italic:
                if tok in ("*", "_"):
                    count, total = self._count_run(tok, tok_len)
                    if in_bold and in_italic:
                        if count >= 3:
                            in_bold = False
                            in_italic = False
                            self.setStyling(total, self.BOLD_ITALIC)
                        else:
                            self.setStyling(total, self.BOLD_ITALIC)
                    elif in_bold:
                        if count == 2:
                            in_bold = False
                            self.setStyling(total, self.BOLD)
                        elif count == 1:
                            in_italic = True
                            self.setStyling(total, self.ITALIC)
                        else:
                            in_bold = False
                            in_italic = True
                            self.setStyling(total, self.BOLD_ITALIC)
                    else:  # in_italic only
                        if count == 1:
                            in_italic = False
                            self.setStyling(total, self.ITALIC)
                        elif count == 2:
                            in_bold = True
                            self.setStyling(total, self.BOLD)
                        else:
                            in_italic = False
                            in_bold = True
                            self.setStyling(total, self.BOLD_ITALIC)
                    continue
                style = self.BOLD_ITALIC if (in_bold and in_italic) else (
                    self.BOLD if in_bold else self.ITALIC)
                self.setStyling(tok_len, style)
                continue

            # 8) Opening markers (not currently inside any span) ------ #
            if tok == "~":
                count, total = self._count_run("~", tok_len)
                if count >= 2:
                    in_strike = True
                    self.setStyling(total, self.STRIKETHROUGH)
                else:
                    self.setStyling(total, self.DEFAULT)
                continue

            if tok in ("*", "_"):
                count, total = self._count_run(tok, tok_len)
                if count >= 3:
                    in_bold = True
                    in_italic = True
                    self.setStyling(total, self.BOLD_ITALIC)
                elif count == 2:
                    in_bold = True
                    self.setStyling(total, self.BOLD)
                else:
                    in_italic = True
                    self.setStyling(total, self.ITALIC)
                continue

            if tok == "!":
                if self.peek_tok(0)[0] == "[":
                    self.setStyling(tok_len, self.IMAGE_MARKER)
                else:
                    self.setStyling(tok_len, self.DEFAULT)
                continue

            if tok == "[":
                # Footnote marker: [^...]
                if self.peek_tok(0)[0] == "^":
                    self.setStyling(tok_len, self.FOOTNOTE_MARKER)
                    while True:
                        c2 = self.peek_tok(0)
                        if not c2 or c2[0] == "":
                            break
                        t2 = self.next_tok()
                        self.setStyling(t2[1], self.FOOTNOTE_MARKER)
                        if t2[0] == "]":
                            break
                    continue
                # Alert: [!NOTE] (only when followed by ! and an uppercase letter)
                if self.peek_tok(0)[0] == "!" and self.peek_tok(1)[0].isalpha() \
                        and self.peek_tok(1)[0].isupper():
                    self.setStyling(tok_len, self.ALERT)
                    while True:
                        c2 = self.peek_tok(0)
                        if not c2 or c2[0] == "":
                            break
                        t2 = self.next_tok()
                        self.setStyling(t2[1], self.ALERT)
                        if t2[0] == "]":
                            break
                    continue
                # Normal link text
                self.setStyling(tok_len, self.LINK_TEXT)
                in_link_text = True
                continue

            if tok == "<":
                # HTML comment <!-- ... -->
                if (self.peek_tok(0)[0] == "!" and self.peek_tok(1)[0] == "-"
                        and self.peek_tok(2)[0] == "-"):
                    total = tok_len
                    for _ in range(3):
                        t = self.next_tok()
                        if t is None:
                            break
                        total += t[1]
                    # consume until -->
                    while True:
                        c0 = self.peek_tok(0)
                        c1 = self.peek_tok(1)
                        c2 = self.peek_tok(2)
                        if c0[0] == "-" and c1[0] == "-" and c2[0] == ">":
                            for _ in range(3):
                                t = self.next_tok()
                                if t is None:
                                    break
                                total += t[1]
                            break
                        nxt = self.peek_tok(0)
                        if not nxt or nxt[0] == "":
                            break
                        t = self.next_tok()
                        total += t[1]
                    self.setStyling(total, self.COMMENTS)
                    continue
                # Autolink / inline HTML: collect up to '>'
                collected = [tok]
                total = tok_len
                closed = False
                while True:
                    nxt = self.peek_tok(0)
                    if not nxt or nxt[0] == "" or "\n" in nxt[0]:
                        break
                    t = self.next_tok()
                    collected.append(t[0])
                    total += t[1]
                    if t[0] == ">":
                        closed = True
                        break
                inner = "".join(collected)
                is_autolink = closed and (
                    "://" in inner or "@" in inner
                    or (len(inner) > 2 and inner[1].isalpha() and ":" in inner)
                )
                self.setStyling(total, self.AUTOLINK if is_autolink else self.HTML)
                continue

            if tok == "&":
                total = tok_len
                closed = False
                while True:
                    nxt = self.peek_tok(0)
                    if not nxt or nxt[0] == "" or nxt[0].isspace():
                        break
                    t = self.next_tok()
                    total += t[1]
                    if t[0] == ";":
                        closed = True
                        break
                self.setStyling(total, self.ENTITY if closed else self.DEFAULT)
                continue

            if tok == "@":
                nxt = self.peek_tok(0)
                if nxt and nxt[0] and nxt[0].isidentifier():
                    t = self.next_tok()
                    self.setStyling(tok_len + t[1], self.MENTION)
                else:
                    self.setStyling(tok_len, self.DEFAULT)
                continue

            if tok == "#":
                nxt = self.peek_tok(0)
                if nxt and nxt[0] and nxt[0].isdigit():
                    total = tok_len
                    while self.peek_tok(0)[0].isdigit():
                        t = self.next_tok()
                        total += t[1]
                    self.setStyling(total, self.ISSUE_REF)
                else:
                    self.setStyling(tok_len, self.DEFAULT)
                continue

            if tok == ":":
                nxt = self.peek_tok(0)
                if nxt and nxt[0] and nxt[0].isidentifier():
                    w = self.next_tok()
                    if self.peek_tok(0)[0] == ":":
                        c2 = self.next_tok()
                        self.setStyling(tok_len + w[1] + c2[1], self.EMOJI)
                    else:
                        self.setStyling(tok_len + w[1], self.DEFAULT)
                else:
                    self.setStyling(tok_len, self.DEFAULT)
                continue

            # 9) Everything else --------------------------------------- #
            self.setStyling(tok_len, self.DEFAULT)
