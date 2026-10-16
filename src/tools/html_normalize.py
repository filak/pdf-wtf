#!/usr/bin/env python3
"""Normalize PDF-extracted HTML for web publishing (Python 3.10+).

Usage:
    python html_normalize.py input.html -o cleaned.html
    python html_normalize.py input.html > cleaned.html

Input and output files use UTF-8. Without -o, HTML is written to stdout.
Single <br> elements remain intact with the default normalization options.
Dependency: beautifulsoup4 (uv add beautifulsoup4).
This is an editorial normalizer, NOT an HTML sanitizer. Only use trusted HTML
or sanitize separately before publishing. The parser may repair malformed HTML.

Short-paragraph merging is deliberately heuristic: mark captions, bylines,
addresses, poetry, etc. with data-no-merge, or supply protected_selector.
Page wrappers are not removed; merging is limited to sibling paragraphs.
Visible line-end hyphens are removed only when a caller's is_joined_word
callback confirms the combined word. Unknown cases retain the
hyphen and are reported. No dictionary, network service, or LLM is required.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

# Deliberately preserve NBSP and other meaningful Unicode spacing.
_SPACE = re.compile(r"[ \t\r\n\f]+")
_WORD_END = re.compile(r"([^\W\d_]+)-$", re.UNICODE)
_WORD_START = re.compile(r"^([^\W\d_]+)", re.UNICODE)
_LINE_HYPHEN = re.compile(r"([^\W\d_]+)-[ \t]*\r?\n[ \t]*([^\W\d_]+)", re.UNICODE)
_OPAQUE = {"script", "style", "pre", "code", "textarea", "svg", "math", "head"}
_STRUCTURAL = {
    "blockquote",
    "figure",
    "figcaption",
    "table",
    "ul",
    "ol",
    "dl",
    "address",
    "nav",
    "footer",
    "header",
    "aside",
}
_SPECIAL = re.compile(
    r"(?:^|[\s_-])(caption|byline|author|authors|credit|credits|"
    r"page-number|pagenum|footnote|label|verse|poetry|address)(?:$|[\s_-])",
    re.I,
)
_LABEL = re.compile(r"^(?:fig(?:ure)?\.?|table|plate|obr\.?|obrázek|tab\.?)\s*\d", re.I)
_DEFAULT_PROTECTED = '[data-no-merge], [data-preserve], [contenteditable="true"]'


@dataclass
class NormalizationResult:
    html: str
    changes: list[str] = field(default_factory=list)
    uncertain_cases: list[str] = field(default_factory=list)


def normalize_extracted_html(
    html: str,
    *,
    merge_short_paragraphs: bool = True,
    short_paragraph_chars: int = 150,
    max_paragraph_chars: int = 800,
    join_fragments: bool = True,
    unwrap_br: bool = False,
    is_joined_word: Callable[[str], bool] | None = None,
    protected_selector: str | None = None,
) -> NormalizationResult:
    """Return cleaned HTML, an edit log, and uncertain hyphenation decisions.

    ``merge_short_paragraphs`` allows editorial merging even after a full stop.
    Either sibling paragraph must be shorter than ``short_paragraph_chars``;
    the result must fit ``max_paragraph_chars``. Same parent and compatible
    attributes are required. Comments, page markers, and all other intervening
    elements are boundaries. IDs on the second paragraph are never discarded.

    ``join_fragments`` first joins unfinished paragraphs to lowercase-start
    continuations, without a length limit. Only after that does editorial
    short-paragraph merging run, subject to ``max_paragraph_chars``. This
    ordering prevents a short sentence fragment being merged backward before
    its continuation is considered. Sentence repair remains a heuristic.

    ``unwrap_br`` treats single, unmarked <br> elements in ordinary <p> prose
    as PDF wrapping. Default False protects intentional breaks. Double breaks,
    marked breaks, and breaks inside protected structures remain intact.

    The optional ``is_joined_word`` callback receives a casefolded joined
    spelling. Without it, visible hyphens are retained. A dictionary
    hit supports a join; it cannot prove that a printed hyphen was discretionary.

    Add ``data-no-merge`` to a paragraph or ancestor to preserve its contents
    and paragraph boundary. ``protected_selector`` extends those protections.
    Does not infer headings, remove headers/footers, or reorder text.
    """
    if short_paragraph_chars < 1 or max_paragraph_chars < 1:
        raise ValueError("Paragraph length limits must be positive")
    soup = BeautifulSoup(html, "html.parser")
    changes: list[str] = []
    uncertain: list[str] = []
    protected = {id(tag) for tag in soup.select(_DEFAULT_PROTECTED)}
    if protected_selector:
        protected.update(id(tag) for tag in soup.select(protected_selector))

    def blocked(tag: Tag | None, *, structural: bool = False) -> bool:
        while isinstance(tag, Tag):
            if id(tag) in protected or tag.name in _OPAQUE:
                return True
            if structural and tag.name in _STRUCTURAL:
                return True
            tag = tag.parent
        return False

    def approved(word: str) -> bool:
        folded = word.casefold()
        return bool(is_joined_word and is_joined_word(folded))

    def hyphen(left: str, right: str) -> tuple[str, str, str] | None:
        """Return edited edges and separator for a confirmed wrapping boundary."""
        if left.endswith("\u00ad"):
            changes.append("Removed a soft hyphen at a wrapping boundary")
            return left[:-1], right, ""
        a, b = _WORD_END.search(left), _WORD_START.match(right)
        if not a or not b or not b.group(1)[0].islower():
            return None
        joined = a.group(1) + b.group(1)
        if approved(joined):
            changes.append(f"Dehyphenated: {a.group(1)}- + {b.group(1)} → {joined}")
            return left[:-1], right, ""
        uncertain.append(f"Kept uncertain hyphen: {a.group(1)}- + {b.group(1)}")
        return left, right, ""

    # Heading levels are document-wide, excluding explicitly protected subtrees.
    first_heading = True
    for heading in soup.find_all(re.compile(r"^h[1-6]$")):
        if heading.name == "h1" and not first_heading and not blocked(heading):
            heading.name = "h2"
            changes.append("Converted a later h1 to h2")
        first_heading = False

    # Resolve line-end hyphens before collapsing source newlines.
    for node in list(soup.find_all(string=True)):
        if isinstance(node, Comment) or type(node) is not NavigableString:
            continue
        if blocked(node.parent):
            continue
        old = str(node)

        def replace_wrap(match: re.Match[str]) -> str:
            result = hyphen(match[1] + "-", match[2])
            return result[0] + result[2] + result[1] if result else match[0]

        new = _LINE_HYPHEN.sub(replace_wrap, old)
        # Soft hyphens explicitly identify discretionary word breaks.
        new = re.sub(r"\u00ad[ \t]*\r?\n[ \t]*", "", new)
        new = _SPACE.sub(" ", new)
        if old != new:
            node.replace_with(NavigableString(new))
            changes.append("Normalized whitespace or soft hyphens in a text node")

    def text_nodes(tag: Tag) -> list[NavigableString]:
        return [
            n for n in tag.descendants if type(n) is NavigableString and str(n).strip()
        ]

    def special_paragraph(p: Tag) -> bool:
        if blocked(p, structural=True):
            return True
        signature = " ".join([*p.get("class", []), str(p.get("role", ""))])
        if _SPECIAL.search(signature) or _LABEL.match(p.get_text().strip()):
            return True
        if p.find(["img", "picture", "svg", "math", "code", "pre", "input"]):
            return True
        if any(id(t) in protected for t in p.find_all(True)):
            return True
        # Standalone emphasized labels often represent headings or bylines.
        plain = p.get_text().strip()
        if not plain or plain.endswith(":"):
            return True
        for emphasis in p.find_all(["b", "strong", "i", "em"]):
            if emphasis.get_text().strip() == plain:
                return True
        return False

    if unwrap_br:
        for br in list(soup.find_all("br")):
            p = br.find_parent("p")
            if p is None or br.attrs or special_paragraph(p):
                continue

            # Whitespace siblings do not separate consecutive <br> elements.
            def neighbor(step: str):
                item = getattr(br, step)
                while type(item) is NavigableString and not str(item).strip():
                    item = getattr(item, step)
                return item

            if any(
                isinstance(n, Tag) and n.name == "br"
                for n in (neighbor("previous_sibling"), neighbor("next_sibling"))
            ):
                continue
            before, after = [], []
            seen = False
            for n in p.descendants:
                if n is br:
                    seen = True
                elif type(n) is NavigableString and str(n).strip():
                    (after if seen else before).append(n)
            if not before or not after:
                continue
            a, b = before[-1], after[0]
            left, right = str(a).rstrip(), str(b).lstrip()
            joined = hyphen(left, right)
            if joined:
                left, right, separator = joined
            else:
                separator = " "
            # All whitespace around the break is replaced by one separator.
            between = False
            for n in list(p.descendants):
                if n is a:
                    between = True
                elif n is b:
                    break
                elif between and type(n) is NavigableString and not str(n).strip():
                    n.extract()
            a.replace_with(NavigableString(left))
            b.replace_with(NavigableString(right))
            br.replace_with(NavigableString(separator))
            changes.append("Unwrapped a single br in prose")

    def next_paragraph(p: Tag) -> Tag | None:
        sibling = p.next_sibling
        while type(sibling) is NavigableString and not str(sibling).strip():
            sibling = sibling.next_sibling
        return sibling if isinstance(sibling, Tag) and sibling.name == "p" else None

    def compatible_pair(p: Tag, q: Tag | None) -> bool:
        if q is None or special_paragraph(q) or q.find("br"):
            return False
        # Preserve differing semantics/formatting and externally linked IDs.
        p_attrs = {k: v for k, v in p.attrs.items() if k != "id"}
        q_attrs = {k: v for k, v in q.attrs.items() if k != "id"}
        return not q.has_attr("id") and p_attrs == q_attrs

    def merge_pair(p: Tag, q: Tag, reason: str) -> None:
        a, b = text_nodes(p)[-1], text_nodes(q)[0]
        # Only merge plain edge text; embedded comments remain in place.
        edge_left, edge_right = str(a).rstrip(), str(b).lstrip()
        joined = hyphen(edge_left, edge_right)
        if joined:
            edge_left, edge_right, separator = joined
        else:
            separator = " "
        # Remove whitespace-only edge nodes, including inside inline tags.
        for node in list(p.find_all(string=True))[::-1]:
            if node is a:
                break
            if type(node) is NavigableString and not str(node).strip():
                node.extract()
        for node in list(q.find_all(string=True)):
            if node is b:
                break
            if type(node) is NavigableString and not str(node).strip():
                node.extract()
        a.replace_with(NavigableString(edge_left))
        b.replace_with(NavigableString(edge_right))
        p.append(NavigableString(separator))
        for child in list(q.contents):
            p.append(child.extract())
        # Remove interparagraph formatting whitespace to keep idempotence.
        while p.next_sibling is not q:
            p.next_sibling.extract()
        q.decompose()
        changes.append(reason)

    # Repair sentence continuations throughout the document BEFORE allowing
    # any editorial short-paragraph merges. Length is irrelevant to repair.
    for repair_pass in (True, False):
        if repair_pass and not join_fragments:
            continue
        if not repair_pass and not merge_short_paragraphs:
            continue
        for p in list(soup.find_all("p")):
            if p.parent is None or special_paragraph(p) or p.find("br"):
                continue
            while True:
                q = next_paragraph(p)
                if not compatible_pair(p, q):
                    break
                left, right = p.get_text().strip(), q.get_text().strip()
                if repair_pass:
                    # A trailing word hyphen also satisfies this condition;
                    # merge_pair separately decides whether to remove it.
                    if not right[0].islower() or re.search(
                        r'[.!?…:;]["\u201d\u2019\u00bb\)\]]*$', left
                    ):
                        break
                    reason = "Repaired a split sentence by merging forward"
                else:
                    if (
                        min(len(left), len(right)) >= short_paragraph_chars
                        or len(left) + len(right) + 1 > max_paragraph_chars
                    ):
                        break
                    reason = "Merged short prose paragraphs for web readability"
                merge_pair(p, q, reason)

    # Remove any remaining discretionary soft hyphens after boundary handling.
    for node in list(soup.find_all(string=True)):
        if type(node) is NavigableString and not blocked(node.parent):
            if "\u00ad" in str(node):
                node.replace_with(NavigableString(str(node).replace("\u00ad", "")))
                changes.append("Removed a discretionary soft hyphen")

    return NormalizationResult(str(soup), changes, list(dict.fromkeys(uncertain)))


def main(argv: list[str] | None = None) -> int:
    """Run normalization on a UTF-8 HTML file; diagnostics go to stderr."""
    parser = argparse.ArgumentParser(
        description="Normalize PDF-extracted HTML for web publishing. "
        "Merges short prose paragraphs and preserves uncertain visible hyphens.",
    )
    parser.add_argument("html_file", type=Path, help="Input HTML file (UTF-8)")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output HTML file (UTF-8); defaults to standard output",
    )
    args = parser.parse_args(argv)
    try:
        html = args.html_file.read_text(encoding="utf-8-sig")
        result = normalize_extracted_html(html)
        if args.output is None:
            sys.stdout.write(result.html)
        else:
            args.output.write_text(result.html, encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        parser.exit(1, f"{parser.prog}: {exc}\n")
    print(
        f"Normalized HTML: {len(result.changes)} changes; "
        f"{len(result.uncertain_cases)} uncertain hyphenation cases.",
        file=sys.stderr,
    )
    for case in result.uncertain_cases:
        print(f"CHECK: {case}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
