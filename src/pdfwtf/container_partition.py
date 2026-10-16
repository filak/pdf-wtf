"""Born-digital container partition, reviewed plans, and unit export."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, UTC
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import shutil
from statistics import median
from typing import Any

import pymupdf as fitz

from .html_extraction import reconstruct_pages, render_html
from .page_layout import (
    _CAPTION,
    _ISBN,
    _ISSN,
    _block_text,
    _extract_page,
    _normalize_text,
    _ordered_blocks,
    _page_figures,
    _rect,
)
from .page_metadata import build_page_metadata
from .utils.common import _extract_doi_candidates

SCHEMA_VERSION = "1.4"
DOCUMENT_TYPES = {
    "unit",
    "journal-issue",
    "magazine-issue",
    "book",
    "proceedings",
    "unknown",
}
PAGE_TYPES = {"normal", "full-page-advertisement", "unknown"}
UNIT_TYPES = {
    "preface",
    "editorial",
    "table-of-contents",
    "programme",
    "article",
    "chapter",
    "abstract",
    "poster",
    "book-review",
    "full-page-advertisement",
    "cover",
    "unknown",
}
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
_DOI = re.compile(r"(?:doi\s*:\s*|doi\.org/)?10\.\d{4,9}/\S+", re.IGNORECASE)
_AUTHOR = re.compile(
    r"(?:\b(?i:and|et al\.?|university|institute)\b|@|"
    r"[A-Z][a-z]+(?:[-'][A-Za-z]+)?\s+[A-Z][a-z]+(?:[-'][A-Za-z]+)?)"
)
_CHAPTER = re.compile(r"^(?:chapter|part)\s+(?:\d+|[ivxlcdm]+)\b", re.IGNORECASE)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


class PlanValidationError(ValueError):
    """A reviewed plan does not satisfy the processing contract."""


def source_identity(pdf_path: Path) -> dict[str, Any]:
    """Return the source fingerprint and page count."""
    digest = sha256()
    with pdf_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    with fitz.open(pdf_path) as document:
        page_count = len(document)
    return {"sha256": digest.hexdigest(), "page_count": page_count}


def extract_structure(pdf_path: Path) -> dict[str, Any]:
    """Extract reusable geometry and navigation without exporting image bytes."""
    page_metadata = build_page_metadata(pdf_path, [], [])
    with fitz.open(pdf_path) as document:
        pages = [
            _extract_page(
                page,
                page_metadata[f"page_{page.number + 1:03d}"]["pgn"],
            )
            for page in document
        ]
        bookmarks = [
            {
                "level": level,
                "title": _normalize_text(title),
                "input_page": input_page,
            }
            for level, title, input_page, *_ in document.get_toc(simple=False)
            if 1 <= input_page <= len(document)
        ]
        document_metadata = {
            name: _normalize_text(str(document.metadata.get(name) or ""))
            for name in ("title", "subject", "keywords")
        }
        links = []
        for page in document:
            for link in page.get_links():
                target = link.get("page")
                if target is None or target < 0:
                    continue
                links.append(
                    {
                        "source_input_page": page.number + 1,
                        "source_bbox": _rect(link.get("from", (0, 0, 0, 0))),
                        "target_input_page": target + 1,
                    }
                )
    return {
        "coordinate_system": {
            "origin": "top-left",
            "units": "PDF points",
            "x_direction": "right",
            "y_direction": "down",
        },
        "pages": pages,
        "bookmarks": bookmarks,
        "internal_links": links,
        "document_metadata": document_metadata,
    }


def _page_signals(page: dict[str, Any]) -> dict[str, Any]:
    lines = [
        line
        for block in page["text_blocks"]
        for line in block["lines"]
        if line["text"].strip()
    ]
    sizes = [
        span["size"] for line in lines for span in line["spans"] if span["text"].strip()
    ]
    body_size = median(sizes) if sizes else 0
    title_lines = [
        line
        for line in lines
        if not re.search(
            r"https?://|www\.|[\w.+-]+@[\w.-]+", line["text"], re.IGNORECASE
        )
        and not re.search(
            r"\b(?:prof|doc|mudr|rndr|phdr|drsc|csc|phd|frcp|fesc)\b",
            line["text"],
            re.IGNORECASE,
        )
        and not re.fullmatch(
            r"(?:references|bibliography|literat[uú]ra|correspondence|author contact)[:. ]*",
            line["text"].strip(),
            re.IGNORECASE,
        )
        and line["bbox"][1] < page["height"] * 0.85
    ]
    title_line = max(
        title_lines,
        key=lambda line: (
            max((span["size"] for span in line["spans"]), default=0),
            -line["bbox"][1],
        ),
        default=None,
    )
    title = title_line["text"] if title_line else f"Page {page['input_page']}"
    title_size = max(span["size"] for span in title_line["spans"]) if title_line else 0
    text = "\n".join(line["text"] for line in lines)
    return {
        "title": title,
        "title_bbox": title_line["bbox"] if title_line else None,
        "title_y": title_line["bbox"][1] if title_line else 0,
        "prominent_title": bool(
            title_line
            and len(title) >= 4
            and (body_size == 0 or title_size >= body_size * 1.18)
        ),
        "author_pattern": bool(_AUTHOR.search(text[:1500])),
        "abstract_pattern": bool(re.search(r"\babstract\b", text, re.IGNORECASE)),
        "doi_pattern": bool(_DOI.search(text)),
    }


# Opening labels are anchored: a mention in ordinary prose is not evidence.
_ABSTRACT_LABEL = re.compile(
    r"^(?:abstract|abstrakt|souhrn|súhrn|resumé|résumé|summary)\b", re.I
)
_KEYWORDS_LABEL = re.compile(
    r"^(?:key\s*words|klíčová slova|kľúčové slová|mots[- ]clés)\b", re.I
)
_INTERNAL_SECTION = re.compile(
    r"^(?:\d+(?:\.\d+)*[.)]?\s+)?(?:introduction|background|methods|"
    r"materials(?: and methods)?|results|discussion|conclusions?|references|"
    r"bibliography|acknowledg(?:e)?ments|literat[uú]ra|úvod|metodika|výsledky|"
    r"diskuse|diskusia|závěr|záver)\s*[:.]*$",
    re.I,
)
_CONTRIBUTION = re.compile(
    r"^(?:editorial|letter(?:s)?(?: to the editor)?|correspondence|"
    r"book review|recenze|úvodník)\b",
    re.I,
)
_AFFILIATION = re.compile(
    r"\b(?:university|universit[éy]|institute|department|hospital|univerzita|"
    r"univerzity|ústav|klinika|fakulta)\b|@",
    re.I,
)
_CONTENTS_LABEL = re.compile(r"\b(?:contents|obsah|sommaire)\b", re.I)


def _title_key(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", text.casefold()))


def _line_style(line: dict[str, Any]) -> tuple[float, bool]:
    spans = [span for span in line["spans"] if any(c.isalpha() for c in span["text"])]
    return (
        max((span["size"] for span in spans), default=0),
        any(span["flags"] & 16 or "bold" in span["font"].lower() for span in spans),
    )


def _journal_author(text: str) -> bool:
    """Recognize compact Unicode bylines without treating 'and' as an author."""
    words = re.findall(r"[^\W\d_]+(?:[-’'][^\W\d_]+)*", text)
    return (
        2 <= len(words) <= 16
        and len(text) <= 180
        and sum(word[0].isupper() for word in words) >= 2
        and not _AFFILIATION.search(text)
        and not _ABSTRACT_LABEL.match(text)
        and not _KEYWORDS_LABEL.match(text)
        and not _DOI.search(text)
    )


def _article_margin_dois(page: dict[str, Any]) -> set[str]:
    """Use explicit margin DOI labels as weak article identity evidence."""
    values = set()
    for block in page["text_blocks"]:
        for line in block["lines"]:
            if not (
                line["bbox"][1] < page["height"] * 0.2
                or line["bbox"][1] > page["height"] * 0.85
            ):
                continue
            if re.match(r"(?:doi\s*:|https?://(?:dx\.)?doi\.org/)", line["text"], re.I):
                values.update(
                    value.casefold() for value in _extract_doi_candidates(line["text"])
                )
    return values


def _journal_signals(structure: dict[str, Any]) -> list[dict[str, Any]]:
    """Score local article openings, excluding recurring margin material."""
    margins: Counter[str] = Counter()
    for page in structure["pages"]:
        margins.update(
            {
                _title_key(line["text"])
                for block in page["text_blocks"]
                for line in block["lines"]
                if line["bbox"][1] < page["height"] * 0.15
                or line["bbox"][1] > page["height"] * 0.85
            }
        )
    result = []
    previous_text = ""
    previous_dois: set[str] = set()
    for page in structure["pages"]:
        signal = _page_signals(page)
        lines = [line for block in _ordered_blocks(page) for line in block["lines"]]
        sizes = [
            span["size"]
            for line in lines
            for span in line["spans"]
            for char in span["text"]
            if char.isalpha()
        ]
        body_size = median(sizes) if sizes else 0
        page_dois = _article_margin_dois(page)
        candidates = []
        for index, line in enumerate(lines):
            text = line["text"].strip()
            size, bold = _line_style(line)
            if (
                len(text) < 4
                or len(text) > 220
                or not size
                or line["bbox"][1] > page["height"] * 0.75
                or _INTERNAL_SECTION.fullmatch(text)
                or _ABSTRACT_LABEL.match(text)
                or _KEYWORDS_LABEL.match(text)
                or _CAPTION.match(text)
                or _DOI.search(text)
                or _AFFILIATION.search(text)
                or re.search(r"https?://|www\.", text)
                or re.search(r"\b(?:prof|mudr|rndr|phdr|drsc|phd|csc)\b", text, re.I)
            ):
                continue
            repeated = margins[_title_key(text)] >= 2 and (
                line["bbox"][1] < page["height"] * 0.15
            )
            # A recurring heading can still introduce short contributions.
            if repeated and not _CONTRIBUTION.match(text):
                continue
            cluster = [line]
            for following in lines[index + 1 : index + 4]:
                next_size, next_bold = _line_style(following)
                gap = following["bbox"][1] - cluster[-1]["bbox"][3]
                if (
                    0 <= gap <= max(size, 6)
                    and abs(next_size - size) <= 0.5
                    and next_bold == bold
                    and (bold or size >= body_size * 1.15)
                    and not _ABSTRACT_LABEL.match(following["text"])
                    and not _INTERNAL_SECTION.fullmatch(following["text"])
                    and not _DOI.search(following["text"])
                    and not _AFFILIATION.search(following["text"])
                    and abs(following["bbox"][0] - line["bbox"][0]) < 30
                ):
                    cluster.append(following)
                else:
                    break
            title = " ".join(item["text"] for item in cluster)
            bottom = cluster[-1]["bbox"][3]
            nearby = [
                item
                for item in lines
                if bottom <= item["bbox"][1] <= bottom + min(230, page["height"] * 0.35)
            ]
            # Stop at references so bibliography entries cannot supply opening evidence.
            local = []
            for item in nearby:
                if re.match(
                    r"^(?:references|bibliography|literat[uú]ra)\b", item["text"], re.I
                ):
                    break
                local.append(item)
            author = any(
                _journal_author(item["text"]) and item["bbox"][1] - bottom <= 60
                for item in local[:3]
            )
            affiliation = any(_AFFILIATION.search(item["text"]) for item in local[:5])
            abstract = any(_ABSTRACT_LABEL.match(item["text"]) for item in local)
            keywords = any(_KEYWORDS_LABEL.match(item["text"]) for item in local)
            doi = any(_DOI.search(item["text"]) for item in local)
            styled = bold or size >= body_size * 1.15
            in_references = any(
                re.match(
                    r"^(?:references|bibliography|literat[uú]ra)\b", item["text"], re.I
                )
                for item in lines[:index]
            )
            if in_references and not (styled and (abstract or affiliation)):
                continue
            evidence = []
            score = 0
            for present, weight, label in (
                (styled, 2, "title typography"),
                (author, 2, "nearby author pattern"),
                (affiliation, 1, "nearby affiliation"),
                (abstract, 2, "nearby abstract label"),
                (keywords, 1, "nearby keywords label"),
                (doi, 1, "nearby DOI pattern"),
                (bool(_CONTRIBUTION.match(title)), 2, "short contribution label"),
            ):
                if present:
                    score += weight
                    evidence.append(label)
            if page_dois and previous_dois:
                if page_dois.isdisjoint(previous_dois):
                    score += 1
                    evidence.append("changed article margin DOI")
                elif not abstract:
                    score -= 3
                    evidence.append("continuing article margin DOI")
            if score >= 3 and re.search(
                r"(?:references|bibliography|literat[uú]ra)",
                previous_text[-2500:],
                re.I,
            ):
                score += 1
                evidence.append("previous page contains article-ending references")
            # Prefer the first title line over an author line or a later translation.
            candidates.append(
                (
                    score,
                    -line["bbox"][1],
                    {
                        "title": title,
                        "title_bbox": [
                            min(item["bbox"][0] for item in cluster),
                            min(item["bbox"][1] for item in cluster),
                            max(item["bbox"][2] for item in cluster),
                            bottom,
                        ],
                        "title_y": line["bbox"][1],
                        "prominent_title": styled,
                        "author_pattern": author,
                        "abstract_pattern": abstract,
                        "doi_pattern": doi,
                        "opening_score": score,
                        "opening_evidence": evidence,
                        "opening_accepted": score >= 4
                        and (styled or abstract or affiliation),
                    },
                )
            )
        if candidates:
            signal.update(
                max(
                    candidates,
                    key=lambda item: (item[0], item[2]["author_pattern"], item[1]),
                )[2]
            )
        else:
            signal.update(opening_score=0, opening_evidence=[], opening_accepted=False)
        result.append(signal)
        previous_text = "\n".join(line["text"] for line in lines)
        if page_dois:
            previous_dois = page_dois
    return result


def _journal_navigation(structure: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Match navigation titles to destination text; do not trust targets alone."""
    entries = [
        (item["input_page"], item["title"], "PDF bookmark title match")
        for item in structure["bookmarks"]
    ]
    for link in structure["internal_links"]:
        source = structure["pages"][link["source_input_page"] - 1]
        source_lines = [
            line["text"] for block in source["text_blocks"] for line in block["lines"]
        ]
        if not any(
            _CONTENTS_LABEL.fullmatch(text.strip()) for text in source_lines[:8]
        ):
            continue
        for block in source["text_blocks"]:
            for line in block["lines"]:
                if fitz.Rect(line["bbox"]).intersects(fitz.Rect(link["source_bbox"])):
                    entries.append(
                        (
                            link["target_input_page"],
                            line["text"],
                            "linked contents title match",
                        )
                    )
    printed: dict[str, list[int]] = {}
    for page in structure["pages"]:
        printed.setdefault(str(page["printed_page_number"]), []).append(
            page["input_page"]
        )
    for page in structure["pages"]:
        lines = [
            line["text"] for block in page["text_blocks"] for line in block["lines"]
        ]
        if not any(_CONTENTS_LABEL.fullmatch(text.strip()) for text in lines[:8]):
            continue
        for text in lines:
            match = re.fullmatch(r"(.{8,}?)\s*[.·… ]+\s+(\d+|[ivxlcdm]+)", text, re.I)
            if match and len(printed.get(match[2], [])) == 1:
                entries.append(
                    (printed[match[2]][0], match[1], "printed contents title match")
                )
    matches: dict[int, dict[str, Any]] = {}
    for number, title, evidence in entries:
        key = _title_key(title)
        if len(key) < 8 or _INTERNAL_SECTION.fullmatch(title.strip()):
            continue
        page = structure["pages"][number - 1]
        lines = [
            line
            for block in _ordered_blocks(page)
            for line in block["lines"]
            if line["bbox"][1] < page["height"] * 0.6
        ]
        # Contents page numbers and leader dots are not part of the title.
        key = re.sub(r"\s+\d+$", "", key)
        for index, line in enumerate(lines):
            group = []
            for item in lines[index : index + 4]:
                group.append(item)
                text = _title_key(" ".join(part["text"] for part in group))
                if text == key:
                    match = matches.setdefault(
                        number,
                        {
                            "title": " ".join(part["text"] for part in group),
                            "title_bbox": [
                                min(part["bbox"][0] for part in group),
                                min(part["bbox"][1] for part in group),
                                max(part["bbox"][2] for part in group),
                                max(part["bbox"][3] for part in group),
                            ],
                            "evidence": [],
                        },
                    )
                    if evidence not in match["evidence"]:
                        match["evidence"].append(evidence)
                    break
                if len(text) >= len(key):
                    break
    return matches


def _journal_shared_start(page: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Distinguish modest branding above a title from preceding article content."""
    y = candidate["y"]
    if y <= page["height"] * 0.2:
        return False
    above = [
        line
        for block in page["text_blocks"]
        for line in block["lines"]
        if line["bbox"][3] < y and line["text"].strip()
    ]
    text = " ".join(line["text"] for line in above)
    # A very low title with no contextual explanation remains ambiguous.
    if not above:
        return y > page["height"] * 0.4
    return (
        len(text) > 300
        or len(above) > 6
        or any(len(line["text"]) > 100 for line in above)
        or any(
            len(line["text"]) > 60 and line["text"].endswith((".", ";"))
            for line in above
        )
        or bool(re.search(r"(?:references|bibliography|literat[uú]ra)\b", text, re.I))
    )


def _magazine_signals(page: dict[str, Any]) -> dict[str, Any]:
    """Use display typography, including lower-page titles, for magazines."""
    signal = _page_signals(page)
    lines = [
        line
        for block in page["text_blocks"]
        for line in block["lines"]
        if line["bbox"][1] < page["height"] * 0.85
    ]

    def letter_size(line: dict[str, Any]) -> float:
        return max(
            (
                span["size"]
                for span in line["spans"]
                if sum(character.isalpha() for character in span["text"]) >= 3
            ),
            default=0,
        )

    eligible = [
        line
        for line in lines
        if letter_size(line) > 0
        and not re.search(r"https?://|www\.|@", line["text"], re.IGNORECASE)
    ]
    anchor = max(
        eligible, key=lambda line: (letter_size(line), -line["bbox"][1]), default=None
    )
    if anchor is None:
        signal.update(title_bbox=None, prominent_title=False)
        return signal
    largest = letter_size(anchor)
    sizes = [
        span["size"]
        for line in eligible
        for span in line["spans"]
        for character in span["text"]
        if character.isalpha()
    ]
    body_size = median(sizes) if sizes else 0
    cluster = [anchor]
    nearby = sorted(
        (
            line
            for line in eligible
            if line is not anchor and letter_size(line) >= max(24, largest * 0.5)
        ),
        key=lambda line: line["bbox"][1],
    )
    for line in nearby:
        bounds = [
            min(item["bbox"][0] for item in cluster),
            min(item["bbox"][1] for item in cluster),
            max(item["bbox"][2] for item in cluster),
            max(item["bbox"][3] for item in cluster),
        ]
        gap = max(line["bbox"][1] - bounds[3], bounds[1] - line["bbox"][3], 0)
        if (
            gap <= largest * 0.65
            and line["bbox"][0] < bounds[2]
            and bounds[0] < line["bbox"][2]
        ):
            cluster.append(line)
    cluster.sort(key=lambda line: (line["bbox"][1], line["bbox"][0]))
    bbox = [
        min(line["bbox"][0] for line in cluster),
        min(line["bbox"][1] for line in cluster),
        max(line["bbox"][2] for line in cluster),
        max(line["bbox"][3] for line in cluster),
    ]
    signal.update(
        title=" ".join(line["text"] for line in cluster),
        title_bbox=bbox,
        title_y=bbox[1],
        prominent_title=largest >= 24 and largest >= body_size * 1.8,
    )
    return signal


def _page_lines(page: dict[str, Any]) -> list[str]:
    return [
        line["text"]
        for block in page["text_blocks"]
        for line in block["lines"]
        if line["text"]
    ]


def _unit_doi_candidates(structure: dict[str, Any], start: int, end: int) -> list[str]:
    """Extract DOI candidates from the pages assigned to one proposed unit."""
    page_texts = [
        "\n".join(_page_lines(structure["pages"][input_page - 1]))
        for input_page in range(start, end + 1)
    ]
    return _extract_doi_candidates("\n\n".join(page_texts))


def _compact_identifier(value: str) -> str | None:
    compact = "".join(
        character
        for character in value.upper()
        if character.isdigit() or character == "X"
    )
    return compact if len(compact) in {8, 10, 13} else None


def _footer_source(structure: dict[str, Any]) -> dict[str, Any] | None:
    """Prefer repeated journal citations in the bottom region of input pages."""
    candidates: Counter[tuple[str, str, str, str | None]] = Counter()
    footer_texts = []
    for page in structure["pages"]:
        texts = set()
        for block in page["text_blocks"]:
            lines = [
                line["text"].strip()
                for line in block["lines"]
                if line["bbox"][1] >= page["height"] * 0.85
            ]
            texts.update(lines)
            if lines:
                texts.add(" ".join(lines))
        footer_texts.extend(sorted(texts))
        page_candidates = set()
        for text in sorted(texts):
            text = re.sub(r"^\d+\s+", "", text)
            citation = re.match(
                r"^(?P<title>.{3,100}?)\s*[.,]?\s+"
                r"(?P<year>(?:19|20)\d{2})\s*;\s*"
                r"(?P<volume>\d+)\s*(?:\((?P<issue>\d+(?:[-–]\d+)?)\))?"
                r"(?:\s*[:;,]|\s*$)",
                text,
            )
            if citation is None:
                citation = re.match(
                    r"^(?P<title>.{3,100}?)\s*[,.:]\s*"
                    r"(?P<issue>\d{1,3}(?:\s*[-–]\s*\d{1,3})?)\s*/\s*"
                    r"(?P<year>(?:19|20)\d{2})\s*,?\s*vol(?:ume)?\.?\s*"
                    r"(?P<volume>[A-Za-z0-9.-]+)",
                    text,
                    re.IGNORECASE,
                )
            if citation:
                issue = citation.group("issue")
                page_candidates.add(
                    (
                        citation.group("title").strip(" .,:;-"),
                        citation.group("year"),
                        citation.group("volume"),
                        re.sub(r"\s*[-–]\s*", "-", issue) if issue else None,
                    )
                )
        candidates.update(sorted(page_candidates, key=repr))
    if not candidates:
        return None
    title, year, volume, issue = candidates.most_common(1)[0][0]
    issn = _ISSN.search("\n".join(footer_texts))
    return {
        "journal_title": title,
        "year": year,
        "volume": volume,
        "issue": issue,
        "issn": f"{issn.group(1)}-{issn.group(2).upper()}" if issn else None,
    }


def _bibliographic_source(
    document_type: str,
    structure: dict[str, Any],
    signals: list[dict[str, Any]],
) -> dict[str, Any]:
    """Propose compact journal or book source metadata for review."""
    lines = [line for page in structure["pages"][:5] for line in _page_lines(page)]
    blocks = [
        _block_text(block)
        for page in structure["pages"][:5]
        for block in page["text_blocks"]
    ]
    source_texts = [*lines, *blocks]
    text = "\n".join(source_texts)
    issn_match = _ISSN.search(text)
    issn = (
        f"{issn_match.group(1)}-{issn_match.group(2).upper()}" if issn_match else None
    )
    isbn = None
    for match in _ISBN.finditer(text):
        isbn = _compact_identifier(match.group(1))
        if isbn is not None:
            break

    year = None
    volume = None
    issue = None
    issue_match = re.search(
        r"\b(?P<issue>\d{1,3}(?:\s*[-–]\s*\d{1,3})?)\s*/\s*"
        r"(?P<year>(?:19|20)\d{2})\s*,?\s*vol(?:ume)?\.?\s*"
        r"(?P<volume>[A-Za-z0-9.-]+)",
        text,
        re.IGNORECASE,
    )
    if issue_match:
        issue = re.sub(r"\s*[-–]\s*", "-", issue_match.group("issue"))
        year = issue_match.group("year")
        volume = issue_match.group("volume")
    else:
        volume_match = re.search(
            r"\bvol(?:ume)?\.?\s*(?P<volume>[A-Za-z0-9.-]+)"
            r"(?:\s*[,;/]\s*(?:no\.?|issue)?\s*(?P<issue>[A-Za-z0-9.-]+))?",
            text,
            re.IGNORECASE,
        )
        if volume_match:
            volume = volume_match.group("volume")
            issue = volume_match.group("issue")
        year_match = _YEAR.search(text)
        year = year_match.group(0) if year_match else None

    metadata_title = structure["document_metadata"].get("title") or None
    page_title = signals[0]["title"] if signals else None
    expanded_page_title = None
    if page_title:
        title_candidates = [
            line.strip(" .,:;-")
            for line in source_texts
            if line.casefold().startswith(page_title.casefold())
            and len(page_title) <= len(line) <= 80
            and not _ISSN.search(line)
            and not re.search(r"https?://|\b(?:19|20)\d{2}\b", line, re.IGNORECASE)
        ]
        if title_candidates:
            expanded_page_title = max(title_candidates, key=len)
    citation_title = None
    citation_year = None
    for line in source_texts:
        match = re.match(r"^(.{3,80}?)\.\s*((?:19|20)\d{2})\s*;", line)
        if match:
            citation_title = match.group(1).strip(" .,:;-")
            citation_year = match.group(2)
            break

    if document_type == "book" or (isbn is not None and issn is None):
        return {
            "kind": "book",
            "book_title": metadata_title or page_title,
            "isbn": isbn,
            "volume": volume,
        }
    source = {
        "kind": "journal",
        "journal_title": (
            citation_title or expanded_page_title or page_title or metadata_title
        ),
        "issn": issn,
        "year": citation_year or year,
        "volume": volume,
        "issue": issue,
    }
    footer = _footer_source(structure)
    if footer:
        source.update(
            {key: value for key, value in footer.items() if value is not None}
        )
    return source


def _classify_page(page: dict[str, Any]) -> tuple[str, list[str]]:
    lines = [line for block in page["text_blocks"] for line in block["lines"]]
    text = " ".join(line["text"].strip() for line in lines).strip()
    page_area = page["width"] * page["height"]
    largest_image_fraction = max(
        (
            fitz.Rect(image["bbox"]).get_area() / page_area
            for image in page["raster_images"]
            if page_area > 0
        ),
        default=0,
    )
    if largest_image_fraction >= 0.72 and len(lines) <= 12 and len(text) <= 500:
        return "full-page-advertisement", [
            "A raster image covers most of the page and little text is present."
        ]
    if len(lines) >= 3 or len(text) >= 80:
        return "normal", ["The page contains substantial extractable text."]
    return "unknown", ["The page does not contain enough evidence for classification."]


def _page_sections(
    page: dict[str, Any], title_bbox: list[float] | None
) -> list[dict[str, Any]]:
    blocks = _ordered_blocks(page)
    sizes = [
        span["size"]
        for block in blocks
        for line in block["lines"]
        for span in line["spans"]
        if span["text"].strip()
    ]
    body_size = median(sizes) if sizes else 0
    sections = []
    seen: set[str] = set()
    for block in blocks:
        if (
            title_bbox is not None
            and block["bbox"][1] <= title_bbox[3] <= block["bbox"][3]
        ):
            continue
        level = _heading_level(block, body_size)
        if level is None:
            continue
        title = _block_text(block)
        normalized = title.casefold()
        if (
            not title
            or normalized in seen
            or title.isdigit()
            or _CAPTION.search(title)
            or _ISSN.search(title)
            or _ISBN.search(title)
        ):
            continue
        seen.add(normalized)
        sections.append({"title": title, "level": level, "bbox": block["bbox"]})
        if len(sections) == 30:
            break
    return sections


def _unit_type(
    document_type: str, candidate: dict[str, Any], signal: dict[str, Any]
) -> str:
    title = candidate["title"].strip().lower()
    if re.search(r"\b(preface|foreword)\b", title):
        return "preface"
    if re.search(r"\beditorial\b", title):
        return "editorial"
    if re.search(r"\b(table of contents|contents)\b", title):
        return "table-of-contents"
    if re.search(r"\b(programme|program|schedule)\b", title):
        return "programme"
    if re.search(r"\bbook reviews?\b", title):
        return "book-review"
    if re.search(r"\bposters?\b", title):
        return "poster"
    if re.search(r"\babstracts?\b", title):
        return "abstract"
    if document_type == "book" or _CHAPTER.search(title):
        return "chapter"
    if document_type in {"journal-issue", "magazine-issue", "proceedings"} or (
        signal["author_pattern"]
        and (signal["abstract_pattern"] or signal["doi_pattern"])
    ):
        return "article"
    return "unknown"


def _classify_auto(
    structure: dict[str, Any], signals: list[dict[str, Any]]
) -> tuple[str, list[str]]:
    top_bookmarks = [item for item in structure["bookmarks"] if item["level"] == 1]
    article_pages = sum(
        signal.get(
            "opening_accepted",
            signal["prominent_title"]
            and signal["author_pattern"]
            and (signal["abstract_pattern"] or signal["doi_pattern"]),
        )
        for signal in signals
    )
    if article_pages >= 2:
        return "journal-issue", ["Multiple pages contain supported article openings."]
    if len(top_bookmarks) >= 2:
        return "book", ["Multiple top-level PDF bookmarks suggest chapters."]
    if article_pages == 1:
        return "unit", [
            "One page combines a prominent title, author evidence, and article evidence."
        ]
    return "unknown", [
        "Automatic classification did not find enough independent evidence."
    ]


def _footer_unit_starts(structure: dict[str, Any]) -> list[int]:
    """Find article starts from repeated citation footer runs."""
    runs: list[tuple[str | None, int, int]] = []
    for page in structure["pages"]:
        citations = []
        for block in page["text_blocks"]:
            for line in block["lines"]:
                if line["bbox"][1] < page["height"] * 0.9:
                    continue
                match = re.fullmatch(
                    r"(.{3,100}?)\s*[.,]?\s+((?:19|20)\d{2})\s*;\s*"
                    r"(\d+)\s*(?:\(([^)]+)\))?\s*:\s*"
                    r"(\d+(?:\s*[-–—−]\s*\d+)?)\s*[.]?",
                    line["text"].strip(),
                )
                if match:
                    signature = re.sub(r"\s+", "", match.group(0)).casefold()
                    citations.append((line["bbox"][1], signature))
        signature = max(citations)[1] if citations else None
        number = page["input_page"]
        if runs and runs[-1][0] == signature:
            key, start, _end = runs[-1]
            runs[-1] = (key, start, number)
        else:
            runs.append((signature, number, number))
    starts = []
    for index, (signature, start, end) in enumerate(runs):
        if signature is None:
            continue
        previous_repeated = (
            index > 0
            and runs[index - 1][0] is not None
            and runs[index - 1][2] - runs[index - 1][1] >= 1
        )
        if end > start or previous_repeated:
            starts.append(start)
    return starts


def _candidate_starts(
    document_type: str,
    structure: dict[str, Any],
    signals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: dict[int, dict[str, Any]] = {}
    if document_type == "book":
        for bookmark in structure["bookmarks"]:
            if bookmark["level"] != 1:
                continue
            candidates[bookmark["input_page"]] = {
                "input_page": bookmark["input_page"],
                "title": bookmark["title"],
                "evidence": ["top-level PDF bookmark"],
                "y": 0,
            }
        for page, signal in zip(structure["pages"], signals):
            if (
                signal["prominent_title"]
                and _CHAPTER.search(signal["title"])
                and signal["title_y"] <= page["height"] * 0.2
            ):
                candidates.setdefault(
                    page["input_page"],
                    {
                        "input_page": page["input_page"],
                        "title": signal["title"],
                        "evidence": ["chapter heading typography"],
                        "y": signal["title_y"],
                    },
                )
        for link in structure["internal_links"]:
            source_page = structure["pages"][link["source_input_page"] - 1]
            link_rect = fitz.Rect(link["source_bbox"])
            for block in source_page["text_blocks"]:
                for line in block["lines"]:
                    if not fitz.Rect(line["bbox"]).intersects(link_rect):
                        continue
                    if not _CHAPTER.search(line["text"]):
                        continue
                    target = link["target_input_page"]
                    candidates.setdefault(
                        target,
                        {
                            "input_page": target,
                            "title": line["text"],
                            "evidence": ["linked contents entry"],
                            "y": 0,
                        },
                    )
    elif document_type == "magazine-issue":
        seen_titles = set()
        for page, signal in zip(structure["pages"], signals):
            title_key = signal["title"].casefold()
            if signal["prominent_title"] and title_key not in seen_titles:
                candidates[page["input_page"]] = {
                    "input_page": page["input_page"],
                    "title": signal["title"],
                    "evidence": ["large magazine article title typography"],
                    "y": 0,
                }
                seen_titles.add(title_key)
    elif document_type in {"journal-issue", "proceedings"}:
        navigation = _journal_navigation(structure)
        for page, signal in zip(structure["pages"], signals):
            number = page["input_page"]
            evidence = list(signal.get("opening_evidence", []))
            if number in navigation:
                match = navigation[number]
                signal.update(
                    title=match["title"],
                    title_bbox=match["title_bbox"],
                    title_y=match["title_bbox"][1],
                )
                evidence.extend(match["evidence"])
                signal["opening_score"] += 4
                signal["opening_evidence"] = evidence
                signal["opening_accepted"] = True
            if signal.get("opening_accepted") or number in navigation:
                candidates[number] = {
                    "input_page": number,
                    "title": signal["title"],
                    "evidence": evidence,
                    "y": signal["title_y"],
                }
    if document_type in {"journal-issue", "magazine-issue", "proceedings"}:
        for number in _footer_unit_starts(structure):
            if number in candidates:
                candidates[number]["evidence"].append(
                    "repeated article citation footer"
                )
            else:
                candidates[number] = {
                    "input_page": number,
                    "title": signals[number - 1]["title"],
                    "evidence": ["repeated article citation footer"],
                    "y": 0,
                }
    if 1 not in candidates:
        candidates[1] = {
            "input_page": 1,
            "title": signals[0]["title"] if signals else "Document",
            "evidence": ["container start"],
            "y": 0,
        }
    return sorted(candidates.values(), key=lambda item: item["input_page"])


def partition_container(pdf_path: Path, requested_type: str) -> dict[str, Any]:
    """Create conservative whole-page unit proposals with review evidence."""
    if requested_type not in {
        "unit",
        "journal-issue",
        "magazine-issue",
        "book",
        "proceedings",
        "auto",
    }:
        raise ValueError("Unsupported document type.")
    identity = source_identity(pdf_path)
    structure = extract_structure(pdf_path)
    signals = (
        _journal_signals(structure)
        if requested_type in {"journal-issue", "proceedings", "auto"}
        else [_page_signals(page) for page in structure["pages"]]
    )
    warnings: list[str] = []
    classification_evidence: list[str] = []
    document_type = requested_type
    if requested_type == "auto":
        document_type, classification_evidence = _classify_auto(structure, signals)
        if document_type == "unknown":
            warnings.append("Document type is uncertain and requires review.")

    if document_type in {"unit", "unknown"}:
        candidates = _candidate_starts("unit", structure, signals)[:1]
    else:
        candidates = _candidate_starts(document_type, structure, signals)
    if document_type == "magazine-issue":
        signals = [_magazine_signals(page) for page in structure["pages"]]
        candidates = _candidate_starts(document_type, structure, signals)
    confirmed_starts = []
    suspected_shared = []
    for candidate in candidates:
        page = structure["pages"][candidate["input_page"] - 1]
        shared = (
            _journal_shared_start(page, candidate)
            if document_type in {"journal-issue", "proceedings"}
            else candidate["y"] > page["height"] * 0.2
        )
        if candidate["input_page"] > 1 and shared:
            suspected_shared.append(
                {
                    "input_page": candidate["input_page"],
                    "title": candidate["title"],
                    "evidence": candidate["evidence"],
                    "reason": "Possible unit start occurs within the page.",
                }
            )
            continue
        confirmed_starts.append(candidate)
    if suspected_shared:
        warnings.append(
            "Possible within-page starts were not converted to whole-page boundaries."
        )

    units = []
    for offset, candidate in enumerate(confirmed_starts):
        start = candidate["input_page"]
        end = (
            confirmed_starts[offset + 1]["input_page"] - 1
            if offset + 1 < len(confirmed_starts)
            else identity["page_count"]
        )
        units.append(
            {
                "id": f"unit-{offset + 1:03d}",
                "title": candidate["title"],
                "type": _unit_type(
                    document_type,
                    candidate,
                    signals[candidate["input_page"] - 1],
                ),
                "input_pages": {"start": start, "end": end},
                "doi": _unit_doi_candidates(structure, start, end),
                "boundary_status": "proposed",
                "boundary_evidence": candidate["evidence"],
            }
        )
    pages = []
    for page, signal in zip(structure["pages"], signals):
        page_type, page_type_evidence = _classify_page(page)
        input_page = page["input_page"]
        page_unit_ids = [
            unit["id"]
            for unit in units
            if unit["input_pages"]["start"] <= input_page <= unit["input_pages"]["end"]
        ]
        pages.append(
            {
                "input_page": input_page,
                "printed_page_number": page["printed_page_number"],
                "page_type": page_type,
                "page_type_evidence": page_type_evidence,
                "main_title": (
                    {"text": signal["title"], "bbox": signal["title_bbox"]}
                    if signal["title_bbox"] is not None
                    else None
                ),
                "sections": _page_sections(page, signal["title_bbox"]),
                "figures": _page_figures(page, document_type),
                "unit_ids": page_unit_ids,
                "warnings": (
                    [
                        f"Article opening score {signal['opening_score']}: "
                        + (
                            "accepted"
                            if signal["opening_accepted"]
                            else "insufficient evidence"
                        )
                        + "; "
                        + ", ".join(signal["opening_evidence"])
                    ]
                    if "opening_score" in signal
                    else []
                ),
            }
        )
    source = {
        **identity,
        "bibliographic": _bibliographic_source(document_type, structure, signals),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "partition",
        "source": source,
        "document_type": document_type,
        "coordinate_system": structure["coordinate_system"],
        "classification_evidence": classification_evidence,
        "pages": pages,
        "units": units,
        "overlaps": [],
        "suspected_shared_page_boundaries": suspected_shared,
        "warnings": warnings,
    }


def write_json_document(document: dict[str, Any], path: Path) -> None:
    """Write a UTF-8 JSON document after its parent directory exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_plan(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PlanValidationError(f"Cannot read plan JSON: {error}") from error
    if not isinstance(value, dict):
        raise PlanValidationError("Plan root must be a JSON object.")
    return value


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _valid_bbox(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(_is_number(item) and math.isfinite(item) for item in value)
        and value[0] <= value[2]
        and value[1] <= value[3]
    )


def _validate_labeled_bbox(
    value: Any, prefix: str, errors: list[str], *, section: bool = False
) -> None:
    if not isinstance(value, dict):
        errors.append(f"{prefix} must be an object.")
        return
    key = "title" if section else "text"
    if not isinstance(value.get(key), str) or not value[key].strip():
        errors.append(f"{prefix}.{key} must be a non-empty string.")
    if not _valid_bbox(value.get("bbox")):
        errors.append(f"{prefix}.bbox must be a valid top-left coordinate rectangle.")
    if section:
        level = value.get("level")
        if not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= 6:
            errors.append(f"{prefix}.level must be an integer from 1 to 6.")


def validate_plan(
    plan: dict[str, Any], pdf_path: Path, cli_document_type: str | None = None
) -> list[dict[str, Any]]:
    """Validate the complete reviewed plan and return selected units."""
    errors = []
    if plan.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION!r}.")
    if plan.get("kind") != "plan":
        errors.append("kind must be 'plan'.")
    document_type = plan.get("document_type")
    if not isinstance(document_type, str) or document_type not in DOCUMENT_TYPES:
        errors.append("document_type is invalid.")
    if cli_document_type is not None and cli_document_type != document_type:
        errors.append(
            f"--doc_type {cli_document_type} conflicts with plan document_type {document_type!r}."
        )
    actual_source = source_identity(pdf_path)
    source = plan.get("source")
    if not isinstance(source, dict):
        errors.append("source must be an object.")
    else:
        if source.get("sha256") != actual_source["sha256"]:
            # Identity uses content, not paths, so a moved or renamed PDF passes.
            errors.append(
                "The plan was created for a different PDF. Partition the PDF "
                "that you export, normally the prepared PDF."
            )
            errors.append("source.sha256 does not match the input PDF.")
        if source.get("page_count") != actual_source["page_count"]:
            errors.append("source.page_count does not match the input PDF.")
        bibliographic = source.get("bibliographic")
        if not isinstance(bibliographic, dict):
            errors.append("source.bibliographic must be an object.")
        else:
            source_kind = bibliographic.get("kind")
            if source_kind == "journal":
                fields = ("journal_title", "issn", "year", "volume", "issue")
                title_field = "journal_title"
            elif source_kind == "book":
                fields = ("book_title", "isbn", "volume")
                title_field = "book_title"
            else:
                fields = ()
                title_field = None
                errors.append("source.bibliographic.kind must be 'journal' or 'book'.")
            for field in fields:
                value = bibliographic.get(field)
                if value is not None and not isinstance(value, str):
                    errors.append(
                        f"source.bibliographic.{field} must be a string or null."
                    )
            if title_field is not None and not bibliographic.get(title_field):
                errors.append(
                    f"source.bibliographic.{title_field} must be a non-empty string."
                )

    units = plan.get("units")
    selected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_filename_ids: set[str] = set()
    ranges: list[tuple[int, int, str]] = []
    if not isinstance(units, list) or not units:
        errors.append("units must be a non-empty array.")
        units = []
    for number, unit in enumerate(units, start=1):
        prefix = f"units[{number - 1}]"
        if not isinstance(unit, dict):
            errors.append(f"{prefix} must be an object.")
            continue
        unit_id = unit.get("id")
        if not isinstance(unit_id, str) or not _SAFE_ID.fullmatch(unit_id):
            errors.append(f"{prefix}.id must be a filesystem-safe identifier.")
        elif unit_id.lower().split(".")[0] in _WINDOWS_RESERVED:
            errors.append(f"{prefix}.id is reserved on Windows.")
        elif unit_id.casefold() in seen_filename_ids:
            errors.append(f"{prefix}.id must be unique, ignoring case.")
        else:
            seen_ids.add(unit_id)
            seen_filename_ids.add(unit_id.casefold())
        if not isinstance(unit.get("title"), str) or not unit["title"].strip():
            errors.append(f"{prefix}.title must be a non-empty string.")
        unit_type = unit.get("type")
        if not isinstance(unit_type, str) or unit_type not in UNIT_TYPES:
            errors.append(f"{prefix}.type is invalid.")
        if not isinstance(unit.get("selected"), bool):
            errors.append(f"{prefix}.selected must be true or false.")
        if unit.get("boundary_status") != "confirmed":
            errors.append(f"{prefix}.boundary_status must be 'confirmed'.")
        unsupported = {
            "start_offset",
            "end_offset",
            "start_bbox",
            "end_bbox",
            "shared_page",
        }.intersection(unit)
        if unsupported:
            errors.append(
                f"{prefix} contains unsupported within-page boundary fields: "
                + ", ".join(sorted(unsupported))
                + "."
            )
        pages = unit.get("input_pages")
        if not isinstance(pages, dict):
            errors.append(f"{prefix}.input_pages must be an object.")
            continue
        start, end = pages.get("start"), pages.get("end")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or not 1 <= start <= end <= actual_source["page_count"]
        ):
            errors.append(
                f"{prefix}.input_pages must contain an inclusive range within "
                f"1-{actual_source['page_count']}."
            )
            continue
        ranges.append((start, end, unit_id if isinstance(unit_id, str) else prefix))
        if unit.get("selected") is True:
            selected.append(unit)
    pages = plan.get("pages")
    selected_pages: set[int] = set()
    seen_figure_ids: set[str] = set()
    if not isinstance(pages, list) or len(pages) != actual_source["page_count"]:
        errors.append(
            f"pages must contain one entry for each of the {actual_source['page_count']} input pages."
        )
        pages = []
    valid_unit_ranges = [
        (start, end, unit_id) for start, end, unit_id in ranges if unit_id in seen_ids
    ]
    for position, page in enumerate(pages, start=1):
        prefix = f"pages[{position - 1}]"
        if not isinstance(page, dict):
            errors.append(f"{prefix} must be an object.")
            continue
        if page.get("input_page") != position:
            errors.append(f"{prefix}.input_page must be {position}.")
        if not isinstance(page.get("selected"), bool):
            errors.append(f"{prefix}.selected must be true or false.")
        elif page["selected"]:
            selected_pages.add(position)
        printed = page.get("printed_page_number")
        if printed is not None and (
            not isinstance(printed, str) or not printed.strip()
        ):
            errors.append(
                f"{prefix}.printed_page_number must be a non-empty string or null."
            )
        page_type = page.get("page_type")
        if not isinstance(page_type, str) or page_type not in PAGE_TYPES:
            errors.append(f"{prefix}.page_type is invalid.")
        main_title = page.get("main_title")
        if main_title is not None:
            _validate_labeled_bbox(main_title, f"{prefix}.main_title", errors)
        sections = page.get("sections")
        if not isinstance(sections, list):
            errors.append(f"{prefix}.sections must be an array.")
        else:
            for section_number, section in enumerate(sections):
                _validate_labeled_bbox(
                    section,
                    f"{prefix}.sections[{section_number}]",
                    errors,
                    section=True,
                )
        figures = page.get("figures")
        if not isinstance(figures, list):
            errors.append(f"{prefix}.figures must be an array.")
        else:
            for figure_number, figure in enumerate(figures):
                figure_prefix = f"{prefix}.figures[{figure_number}]"
                if not isinstance(figure, dict):
                    errors.append(f"{figure_prefix} must be an object.")
                    continue
                figure_id = figure.get("id")
                if not isinstance(figure_id, str) or not _SAFE_ID.fullmatch(figure_id):
                    errors.append(
                        f"{figure_prefix}.id must be a filesystem-safe identifier."
                    )
                elif figure_id in seen_figure_ids:
                    errors.append(f"{figure_prefix}.id must be unique.")
                else:
                    seen_figure_ids.add(figure_id)
                if not _valid_bbox(figure.get("bbox")):
                    errors.append(
                        f"{figure_prefix}.bbox must be a valid top-left coordinate rectangle."
                    )
                caption = figure.get("caption")
                if caption is not None and not isinstance(caption, str):
                    errors.append(f"{figure_prefix}.caption must be a string or null.")
                caption_bbox = figure.get("caption_bbox")
                if caption_bbox is not None and not _valid_bbox(caption_bbox):
                    errors.append(
                        f"{figure_prefix}.caption_bbox must be a valid rectangle or null."
                    )
        unit_ids = page.get("unit_ids")
        expected_unit_ids = [
            unit_id
            for start, end, unit_id in valid_unit_ranges
            if start <= position <= end
        ]
        if (
            not isinstance(unit_ids, list)
            or any(not isinstance(unit_id, str) for unit_id in unit_ids)
            or len(unit_ids) != len(set(unit_ids))
        ):
            errors.append(f"{prefix}.unit_ids must be an array of unique strings.")
        elif unit_ids != expected_unit_ids:
            errors.append(
                f"{prefix}.unit_ids must match the unit ranges: {expected_unit_ids!r}."
            )
        for name in ("page_type_evidence", "warnings"):
            values = page.get(name)
            if not isinstance(values, list) or any(
                not isinstance(value, str) for value in values
            ):
                errors.append(f"{prefix}.{name} must be an array of strings.")
    if not selected_pages:
        errors.append("Select at least one page for processing.")
    if not selected:
        errors.append("Select at least one unit for processing.")
    for unit in selected:
        pages_range = unit.get("input_pages", {})
        start, end = pages_range.get("start"), pages_range.get("end")
        if (
            isinstance(start, int)
            and isinstance(end, int)
            and not any(start <= page <= end for page in selected_pages)
        ):
            errors.append(
                f"Selected unit {unit.get('id')!r} does not contain a selected page."
            )
    if errors:
        raise PlanValidationError("Invalid plan:\n- " + "\n- ".join(errors))
    return selected


def direct_unit_plan(pdf_path: Path) -> dict[str, Any]:
    """Build confirmed instructions for direct single-unit processing."""
    partition = partition_container(pdf_path, "unit")
    identity = partition["source"]
    unit = partition["units"][0]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "plan",
        "source": identity,
        "document_type": "unit",
        "pages": [{**page, "selected": True} for page in partition["pages"]],
        "units": [
            {
                **unit,
                "selected": True,
                "boundary_status": "confirmed",
            }
        ],
    }


def _heading_level(block: dict[str, Any], body_size: float) -> int | None:
    text = _block_text(block)
    if not text or len(text) > 180:
        return None
    sizes = [span["size"] for line in block["lines"] for span in line["spans"]]
    largest = max(sizes, default=0)
    bold = any(
        "bold" in span["font"].lower()
        for line in block["lines"]
        for span in line["spans"]
    )
    if body_size and largest >= body_size * 1.6:
        return 1
    if body_size and largest >= body_size * 1.35:
        return 2
    if body_size and (largest >= body_size * 1.15 or bold):
        return 3
    return None


def _write_unit(
    document: fitz.Document,
    plan_header: dict[str, Any],
    unit: dict[str, Any],
    selected_input_pages: list[int],
    reconstructed: tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]],
    destination: Path,
    include_html: bool,
    include_pdf: bool,
    extract_doi: bool,
) -> dict[str, Any]:
    """Write the files of one unit and return its manifest entry."""
    blocks, figures, warnings = reconstructed
    if extract_doi:
        doi = _extract_doi_candidates(
            "\n\n".join(document[page - 1].get_text() for page in selected_input_pages)
        )
    else:
        doi = unit.get("doi", [])
    pdf_name = f"{unit['id']}.pdf" if include_pdf else None
    if include_pdf:
        with fitz.open() as unit_pdf:
            for page in selected_input_pages:
                unit_pdf.insert_pdf(document, from_page=page - 1, to_page=page - 1)
            unit_pdf.save(destination / pdf_name)
    html_name = f"{unit['id']}.html" if include_html else None
    metadata_name = f"{unit['id']}.metadata.json"
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "kind": "unit-metadata",
        **plan_header,
        "unit": {
            "id": unit["id"],
            "title": unit["title"],
            "type": unit["type"],
            "input_pages": unit["input_pages"],
            "doi": doi,
            "selected_input_pages": selected_input_pages,
        },
        "figures": figures,
        "warnings": warnings,
        "semantic_blocks": blocks,
    }
    write_json_document(metadata, destination / metadata_name)
    if include_html:
        (destination / html_name).write_text(
            render_html(blocks, page_marker=False), encoding="utf-8"
        )
    return {
        "id": unit["id"],
        "title": unit["title"],
        "type": unit["type"],
        "input_pages": unit["input_pages"],
        "doi": doi,
        "selected_input_pages": selected_input_pages,
        "html": html_name,
        "metadata": metadata_name,
        **({"pdf": pdf_name} if include_pdf else {}),
    }


def _write_manifest(
    plan_header: dict[str, Any],
    manifest_units: list[dict[str, Any]],
    destination: Path,
) -> None:
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": "container-manifest",
        "created": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        **plan_header,
        "units": manifest_units,
    }
    write_json_document(manifest, destination / "manifest.json")


def export_units(
    pdf_path: Path,
    plan: dict[str, Any],
    selected_units: list[dict[str, Any]],
    destination: Path,
    include_html: bool,
    include_pdf: bool = False,
    extract_doi: bool = False,
    *,
    use_reviewed_headings: bool = True,
) -> None:
    """Write selected units and a manifest into a separate export directory.

    With extract_doi, DOI candidates come from the text of the selected pages of
    each unit. Otherwise they come from the plan.
    """
    destination.mkdir(parents=True, exist_ok=True)
    plan_header = {"source": plan["source"], "document_type": plan["document_type"]}
    manifest_units = []
    reviewed_pages = {page["input_page"]: page for page in plan["pages"]}
    with fitz.open(pdf_path) as document:
        for unit in selected_units:
            selected_input_pages = [
                page
                for page in range(
                    unit["input_pages"]["start"], unit["input_pages"]["end"] + 1
                )
                if reviewed_pages[page]["selected"]
            ]
            reconstructed = reconstruct_pages(
                document,
                selected_input_pages,
                id_prefix=unit["id"],
                title=unit.get("title", ""),
                reviewed_pages=reviewed_pages,
                use_reviewed_headings=use_reviewed_headings,
            )
            manifest_units.append(
                _write_unit(
                    document,
                    plan_header,
                    unit,
                    selected_input_pages,
                    reconstructed,
                    destination,
                    include_html,
                    include_pdf,
                    extract_doi,
                )
            )
    _write_manifest(plan_header, manifest_units, destination)


def export_document_html(pdf_path: Path, destination: Path) -> None:
    """Write all pages of any PDF as one unit, without partition heuristics.

    The output layout is the same as export_units output for one unit. The unit
    title is the first level-1 heading. DOI candidates come from the page text.
    """
    destination.mkdir(parents=True, exist_ok=True)
    plan_header = {"source": source_identity(pdf_path), "document_type": "unit"}
    with fitz.open(pdf_path) as document:
        page_numbers = list(range(1, len(document) + 1))
        reconstructed = reconstruct_pages(
            document, page_numbers, id_prefix="unit-001", use_reviewed_headings=False
        )
        title = next(
            (
                block["text"]
                for block in reconstructed[0]
                if block["kind"] == "heading" and block["level"] == 1
            ),
            "",
        )
        unit = {
            "id": "unit-001",
            "title": title,
            "type": "unknown",
            "input_pages": {"start": 1, "end": len(document)},
        }
        manifest_unit = _write_unit(
            document,
            plan_header,
            unit,
            page_numbers,
            reconstructed,
            destination,
            include_html=True,
            include_pdf=False,
            extract_doi=True,
        )
    _write_manifest(plan_header, [manifest_unit], destination)


def publish_unit_directory(staged: Path, destination: Path) -> None:
    """Replace an earlier unit export only after the staged export succeeds."""
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(staged, destination)
