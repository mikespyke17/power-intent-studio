"""Syntax highlighters for the "Inspect Source File" viewer
(source_viewer_dialog.py) -- one per file kind, selected by
create_highlighter_for_path() below based on file extension.

Verilog/SystemVerilog (.v/.sv) and VHDL (.vhd/.vhdl) use Pygments' own
HDL lexers (pygments.lexers.hdl.VerilogLexer/SystemVerilogLexer/VhdlLexer)
-- Pygments is a real, declared dependency now (requirements.txt),
not merely a transitive one pulled in by pytest, confirmed via
`pip show pygments` before adding it. Reusing a real, maintained lexer
for two full HDLs is a better tradeoff than hand-rolling regex rules for
each -- this is a readability aid, not a correctness-critical parser, but
"correct enough to be readable" for two real languages is exactly what a
maintained lexer already gives for free.

Liberty (.lib) has no standard Pygments lexer (confirmed -- not present
in pygments.lexers), so LibertyHighlighter below is a small, genuinely
hand-written QSyntaxHighlighter instead, using regex rules grounded in
the real Liberty syntax this project's own LIB parser
(backend/elaborator/lib/elaborate.py) and every real .lib golden fixture
already exercise: `name ( args ) { ... }` group declarations (cell, pin,
pg_pin, bus, type, library, ...) and `name : value ;` simple attributes
(pg_type, direction, related_power_pin, related_ground_pin,
related_bias_pin, voltage_name, bus_type, ...) -- rather than an
exhaustive, guessed keyword vocabulary, the STRUCTURAL patterns
(identifier-before-"("  /  identifier-before-":") generalize to any
Liberty construct, known or not, the same way the real grammar itself
does; a short explicit list of the group keywords this project's own
parser actually reads (library/cell/pin/pg_pin/bus/type) gets its own
distinct, bolder style on top of that generic pattern.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument

_VERILOG_EXTENSIONS = (".v", ".sv")
_VHDL_EXTENSIONS = (".vhd", ".vhdl")
_LIBERTY_EXTENSIONS = (".lib",)


def _format(color: str, *, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(color))
    if bold:
        fmt.setFontWeight(QFont.Weight.Bold)
    if italic:
        fmt.setFontItalic(True)
    return fmt


# Shared palette across both highlighters below -- same colors mean the
# same "kind of thing" regardless of which file type is open.
_KEYWORD_FORMAT = _format("#569cd6", bold=True)
_STRING_FORMAT = _format("#ce9178")
_COMMENT_FORMAT = _format("#6a9955", italic=True)
_NUMBER_FORMAT = _format("#b5cea8")
_TYPE_FORMAT = _format("#4ec9b0")


class PygmentsHighlighter(QSyntaxHighlighter):
    """Generic QSyntaxHighlighter driven by any Pygments lexer.

    Pygments has no block-oriented streaming API the way QSyntaxHighlighter
    itself expects (highlightBlock() is called once per line, and a naive
    per-line re-lex would misrender multi-line constructs like a `/* */`
    block comment spanning several lines) -- so the WHOLE document text is
    tokenized exactly ONCE, up front, into an absolute-offset token list;
    highlightBlock() then just looks up whichever tokens overlap the
    current block's own [start, start+len) range and applies formats.
    Safe to do once here (not on every keystroke) because this viewer is
    read-only -- the text is set once and never edited afterward."""

    def __init__(self, document: QTextDocument, lexer) -> None:
        super().__init__(document)
        self._tokens: list[tuple[int, int, QTextCharFormat]] = []
        self._tokenize(document.toPlainText(), lexer)

    def _tokenize(self, text: str, lexer) -> None:
        from pygments.token import Comment, Keyword, Literal, Name, Number, String

        pos = 0
        for token_type, value in lexer.get_tokens(text):
            length = len(value)
            fmt = self._format_for_token(token_type, Comment, Keyword, Literal, Name, Number, String)
            if fmt is not None and length:
                self._tokens.append((pos, length, fmt))
            pos += length

    @staticmethod
    def _format_for_token(token_type, Comment, Keyword, Literal, Name, Number, String) -> Optional[QTextCharFormat]:
        if token_type in Comment:
            return _COMMENT_FORMAT
        if token_type in Keyword:
            return _KEYWORD_FORMAT
        if token_type in String:
            return _STRING_FORMAT
        if token_type in Number or token_type in Literal.Number:
            return _NUMBER_FORMAT
        if token_type in Name.Builtin or token_type in Name.Class:
            return _TYPE_FORMAT
        return None

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt override)
        block_start = self.currentBlock().position()
        block_end = block_start + len(text)
        for start, length, fmt in self._tokens:
            end = start + length
            if end <= block_start or start >= block_end:
                continue
            rel_start = max(start, block_start) - block_start
            rel_end = min(end, block_end) - block_start
            self.setFormat(rel_start, rel_end - rel_start, fmt)


# Liberty group keywords this project's own parser actually reads (see
# backend/elaborator/lib/elaborate.py's own get_groups("cell"/"pin"/
# "bus"/"pg_pin"/"type") calls), plus "library" -- the outermost wrapper
# every real .lib file opens with (confirmed directly against every
# sram.lib golden fixture read this session), not read as a literal
# string anywhere in elaborate.py only because liberty-parser's own
# parse_liberty() consumes it before this project's code ever sees it.
_LIBERTY_GROUP_KEYWORDS = ("library", "cell", "pin", "pg_pin", "bus", "type")

_LIBERTY_GROUP_RE = QRegularExpression(r"\b(" + "|".join(_LIBERTY_GROUP_KEYWORDS) + r")\b(?=\s*\()")
# Generic "any identifier immediately before ( " -- covers every OTHER
# group name this project's own parser doesn't enumerate too (matches
# the real Liberty grammar shape itself, not a fixed vocabulary).
_LIBERTY_ANY_GROUP_RE = QRegularExpression(r"\b\w+\b(?=\s*\()")
# Generic "any identifier immediately before :" -- covers every simple
# attribute (pg_type, direction, related_power_pin, related_ground_pin,
# related_bias_pin, voltage_name, bus_type, area, capacitance, ...)
# without an exhaustive, guessed list.
_LIBERTY_ATTRIBUTE_RE = QRegularExpression(r"\b\w+\b(?=\s*:)")
_LIBERTY_STRING_RE = QRegularExpression(r'"[^"]*"')
_LIBERTY_NUMBER_RE = QRegularExpression(r"\b\d+\.?\d*\b")
_LIBERTY_LINE_COMMENT_RE = QRegularExpression(r"//[^\n]*")
_LIBERTY_BLOCK_COMMENT_START_RE = QRegularExpression(r"/\*")
_LIBERTY_BLOCK_COMMENT_END_RE = QRegularExpression(r"\*/")

_ATTRIBUTE_FORMAT = _format("#9cdcfe")


class LibertyHighlighter(QSyntaxHighlighter):
    """Hand-written, purely regex-based highlighter for Liberty (.lib)
    files -- no standard Pygments lexer exists for this format. Deliberately
    simple: structural patterns (group-name-before-"(", attribute-name-
    before-":") plus strings/numbers/comments, not a real parser. `/* */`
    block comments are the one construct that genuinely needs cross-line
    state (a comment can span many lines) -- handled the idiomatic
    QSyntaxHighlighter way via setCurrentBlockState()/previousBlockState(),
    not the whole-document pre-tokenize approach PygmentsHighlighter uses
    above (there's no external lexer here to run once; re-applying cheap
    regexes per block on a read-only, never-edited document is fine)."""

    _IN_BLOCK_COMMENT = 1

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt override)
        self._highlight_block_comments(text)
        # Explicit group keywords first (bold), then the generic
        # any-group-before-"(" pattern for everything else, then
        # attributes -- later passes only need to not clobber comments/
        # strings, which are re-applied last as the highest-priority
        # visual cue.
        self._apply_all(text, _LIBERTY_ANY_GROUP_RE, _KEYWORD_FORMAT)
        self._apply_all(text, _LIBERTY_GROUP_RE, _format("#569cd6", bold=True))
        self._apply_all(text, _LIBERTY_ATTRIBUTE_RE, _ATTRIBUTE_FORMAT)
        self._apply_all(text, _LIBERTY_NUMBER_RE, _NUMBER_FORMAT)
        self._apply_all(text, _LIBERTY_STRING_RE, _STRING_FORMAT)
        self._apply_all(text, _LIBERTY_LINE_COMMENT_RE, _COMMENT_FORMAT)

    def _apply_all(self, text: str, pattern: QRegularExpression, fmt: QTextCharFormat) -> None:
        it = pattern.globalMatch(text)
        while it.hasNext():
            m = it.next()
            self.setFormat(m.capturedStart(), m.capturedLength(), fmt)

    def _highlight_block_comments(self, text: str) -> None:
        start = 0 if self.previousBlockState() == self._IN_BLOCK_COMMENT else -1
        if start == -1:
            match = _LIBERTY_BLOCK_COMMENT_START_RE.match(text)
            start = match.capturedStart() if match.hasMatch() else -1
        while start != -1:
            end_match = _LIBERTY_BLOCK_COMMENT_END_RE.match(text, start)
            if not end_match.hasMatch():
                self.setFormat(start, len(text) - start, _COMMENT_FORMAT)
                self.setCurrentBlockState(self._IN_BLOCK_COMMENT)
                return
            comment_end = end_match.capturedEnd()
            self.setFormat(start, comment_end - start, _COMMENT_FORMAT)
            next_match = _LIBERTY_BLOCK_COMMENT_START_RE.match(text, comment_end)
            start = next_match.capturedStart() if next_match.hasMatch() else -1
        self.setCurrentBlockState(0)


def create_highlighter_for_path(path: str, document: QTextDocument) -> Optional[QSyntaxHighlighter]:
    """None for an extension with no highlighter defined -- the viewer
    still shows the file's plain text either way, just unstyled."""
    lower = path.lower()
    if lower.endswith(".sv"):
        from pygments.lexers.hdl import SystemVerilogLexer

        return PygmentsHighlighter(document, SystemVerilogLexer())
    if lower.endswith(_VERILOG_EXTENSIONS):
        from pygments.lexers.hdl import VerilogLexer

        return PygmentsHighlighter(document, VerilogLexer())
    if lower.endswith(_VHDL_EXTENSIONS):
        from pygments.lexers.hdl import VhdlLexer

        return PygmentsHighlighter(document, VhdlLexer())
    if lower.endswith(_LIBERTY_EXTENSIONS):
        return LibertyHighlighter(document)
    return None
