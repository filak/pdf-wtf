"""Indent HTML fragments without parsing them into or repairing a DOM."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "dd",
    "details",
    "dialog",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hgroup",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "script",
    "style",
    "summary",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "textarea",
    "ul",
}
_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_WHITESPACE_SENSITIVE_TAGS = {"pre", "textarea", "script", "style"}


@dataclass(frozen=True)
class _Token:
    kind: str
    raw: str
    tag: str | None = None


class _Tokenizer(HTMLParser):
    """Record source tokens without constructing an HTML tree."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self.source = source
        self.tokens: list[_Token] = []
        self._line_offsets = [0]
        for index, character in enumerate(source):
            if character == "\n":
                self._line_offsets.append(index + 1)

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_offsets[line - 1] + column

    def _through(self, marker: str) -> str:
        start = self._offset()
        end = self.source.find(marker, start)
        return (
            self.source[start:] if end < 0 else self.source[start : end + len(marker)]
        )

    def _reference(self, prefix: str, name: str) -> str:
        start = self._offset()
        end = start + len(prefix) + len(name)
        if end < len(self.source) and self.source[end] == ";":
            end += 1
        return self.source[start:end]

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        self.tokens.append(_Token("start", self.get_starttag_text(), tag))

    def handle_startendtag(
        self, tag: str, _attrs: list[tuple[str, str | None]]
    ) -> None:
        self.tokens.append(_Token("startend", self.get_starttag_text(), tag))

    def handle_endtag(self, tag: str) -> None:
        self.tokens.append(_Token("end", self._through(">"), tag))

    def handle_data(self, data: str) -> None:
        self.tokens.append(_Token("data", data))

    def handle_entityref(self, name: str) -> None:
        self.tokens.append(_Token("data", self._reference("&", name)))

    def handle_charref(self, name: str) -> None:
        self.tokens.append(_Token("data", self._reference("&#", name)))

    def handle_comment(self, _data: str) -> None:
        self.tokens.append(_Token("markup", self._through("-->")))

    def handle_decl(self, _decl: str) -> None:
        self.tokens.append(_Token("markup", self._through(">")))

    def unknown_decl(self, _data: str) -> None:
        self.tokens.append(_Token("markup", self._through(">")))

    def handle_pi(self, _data: str) -> None:
        self.tokens.append(_Token("markup", self._through(">")))


def _is_block_boundary(token: _Token) -> bool:
    return token.tag in _BLOCK_TAGS and token.kind in {"start", "startend", "end"}


def indent_html_fragment(html: str, *, indent: str = "  ") -> str:
    """Return an indented fragment without adding, removing, or repairing tags.

    Only layout whitespace next to block elements is replaced. Text between
    inline elements and content in whitespace-sensitive elements is preserved.
    """
    tokenizer = _Tokenizer(html)
    tokenizer.feed(html)
    tokenizer.close()
    tokens = tokenizer.tokens
    lines: list[str] = []
    current = ""
    current_depth = 0
    depth = 0
    sensitive_depth = 0

    def flush() -> None:
        nonlocal current
        if current:
            lines.append(f"{indent * current_depth}{current}")
            current = ""

    def append(raw: str) -> None:
        nonlocal current, current_depth
        if not current:
            current_depth = depth
        current += raw

    for index, token in enumerate(tokens):
        tag = token.tag or ""
        sensitive = sensitive_depth > 0
        if token.kind == "data" and not token.raw.strip() and not sensitive:
            previous = tokens[index - 1] if index else None
            following = tokens[index + 1] if index + 1 < len(tokens) else None
            if previous is None or following is None:
                continue
            if _is_block_boundary(previous) or _is_block_boundary(following):
                continue

        is_block = tag in _BLOCK_TAGS
        is_void = tag in _VOID_TAGS or token.kind == "startend"
        if token.kind in {"start", "startend"} and is_block and not sensitive:
            flush()
            append(token.raw)
            if not is_void:
                depth += 1
        elif (
            token.kind == "end"
            and is_block
            and (not sensitive or tag in _WHITESPACE_SENSITIVE_TAGS)
        ):
            depth = max(0, depth - 1)
            append(token.raw)
            flush()
        else:
            append(token.raw)

        if token.kind == "start" and tag in _WHITESPACE_SENSITIVE_TAGS:
            sensitive_depth += 1
        elif token.kind == "end" and tag in _WHITESPACE_SENSITIVE_TAGS:
            sensitive_depth = max(0, sensitive_depth - 1)

    flush()
    return "\n".join(lines) + ("\n" if lines else "")
