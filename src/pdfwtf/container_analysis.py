"""Born-digital container analysis, reviewed plans, and unit HTML export."""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
from html import escape, unescape
import json
from pathlib import Path
import re
import shutil
from statistics import median
from typing import Any, Iterable
import unicodedata

import pymupdf as fitz

from .page_metadata import build_page_metadata
from .utils.common import _extract_doi_candidates

SCHEMA_VERSION = "1.2"
DOCUMENT_TYPES = {"unit", "journal-issue", "book", "proceedings", "unknown"}
PAGE_TYPES = {"normal", "full-page-advertisement", "unknown"}
UNIT_TYPES = {
    "preface",
    "editorial",
    "table-of-contents",
    "programme",
    "article",
    "chapter",
    "abstract-or-poster",
    "book-review",
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
_CAPTION = re.compile(r"^(?:fig(?:ure)?\.?\s*\d+|image\s*\d+)\b", re.IGNORECASE)
_CHAPTER = re.compile(r"^(?:chapter|part)\s+(?:\d+|[ivxlcdm]+)\b", re.IGNORECASE)
_ISSN = re.compile(r"\bISSN\s*:?[\s-]*(\d{4})[\s-]*(\d{3}[\dX])\b", re.IGNORECASE)
_ISBN = re.compile(
    r"\bISBN(?:-1[03])?\s*:?[\s-]*((?:97[89][\s-]*)?\d(?:[\s-]*\d){8,11}[\s-]*[\dX])\b",
    re.IGNORECASE,
)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


class PlanValidationError(ValueError):
    """A reviewed plan does not satisfy the processing contract."""


def _normalize_text(value: str) -> str:
    """Normalize extracted PDF text while preserving meaningful Unicode."""
    value = unescape(value)
    value = unicodedata.normalize("NFKC", value)
    characters = []
    for character in value:
        category = unicodedata.category(character)
        if character == "\ufffd" or category in {"Cs", "Co"}:
            characters.append(" ")
        elif category in {"Cc", "Cf"}:
            if character.isspace():
                characters.append(" ")
        else:
            characters.append(character)
    return " ".join("".join(characters).split())


def source_identity(pdf_path: Path) -> dict[str, Any]:
    """Return the source fingerprint and page count."""
    digest = sha256()
    with pdf_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    with fitz.open(pdf_path) as document:
        page_count = len(document)
    return {"sha256": digest.hexdigest(), "page_count": page_count}


def _rect(value: fitz.Rect | Iterable[float]) -> list[float]:
    rect = fitz.Rect(value)
    return [round(item, 3) for item in (rect.x0, rect.y0, rect.x1, rect.y1)]


def _line_text(line: dict[str, Any]) -> str:
    return _normalize_text(
        "".join(span.get("text", "") for span in line.get("spans", []))
    )


def _extract_page(page: fitz.Page, printed_number: str | None) -> dict[str, Any]:
    blocks = []
    text_flags = getattr(fitz, "TEXTFLAGS_TEXT", 0)
    for block in page.get_text("dict", flags=text_flags).get("blocks", []):
        if block.get("type") != 0:
            continue
        lines = []
        for line in block.get("lines", []):
            spans = [
                {
                    "text": span.get("text", ""),
                    "bbox": _rect(span["bbox"]),
                    "font": span.get("font", ""),
                    "size": round(float(span.get("size", 0)), 3),
                    "flags": int(span.get("flags", 0)),
                    "color": int(span.get("color", 0)),
                }
                for span in line.get("spans", [])
                if span.get("text")
            ]
            if spans:
                lines.append(
                    {
                        "text": _normalize_text(
                            "".join(span["text"] for span in spans)
                        ),
                        "bbox": _rect(line["bbox"]),
                        "spans": spans,
                    }
                )
        if lines:
            blocks.append(
                {"kind": "text", "bbox": _rect(block["bbox"]), "lines": lines}
            )
    images = []
    for occurrence, image in enumerate(page.get_image_info(xrefs=True), start=1):
        images.append(
            {
                "occurrence": occurrence,
                "xref": int(image.get("xref", 0)),
                "bbox": _rect(image["bbox"]),
                "width": int(image.get("width", 0)),
                "height": int(image.get("height", 0)),
                "colorspace": int(image.get("colorspace", 0)),
            }
        )
    return {
        "input_page": page.number + 1,
        "printed_page_number": printed_number,
        "width": round(page.rect.width, 3),
        "height": round(page.rect.height, 3),
        "text_blocks": blocks,
        "raster_images": images,
    }


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
    title_line = max(
        lines,
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
    return {
        "kind": "journal",
        "journal_title": (
            citation_title or expanded_page_title or page_title or metadata_title
        ),
        "issn": issn,
        "year": citation_year or year,
        "volume": volume,
        "issue": issue,
    }


def _page_figures(page: dict[str, Any]) -> list[dict[str, Any]]:
    """Describe raster figures and associate nearby explicit captions."""
    blocks = _ordered_blocks(page)
    caption_indices: set[int] = set()
    figures = []
    images = [image for image in page["raster_images"] if _is_figure_image(page, image)]
    for number, image in enumerate(images, start=1):
        caption = None
        caption_bbox = None
        for index, block in enumerate(blocks):
            if index in caption_indices:
                continue
            text = _block_text(block)
            if (
                _CAPTION.search(text)
                and block["bbox"][1] >= image["bbox"][3] - 5
                and block["bbox"][1] <= image["bbox"][3] + 72
            ):
                caption = text
                caption_bbox = block["bbox"]
                caption_indices.add(index)
                break
        figures.append(
            {
                "id": f"figure-{page['input_page']:03d}-{number:03d}",
                "bbox": image["bbox"],
                "xref": image["xref"],
                "occurrence": image["occurrence"],
                "width": image["width"],
                "height": image["height"],
                "colorspace": image["colorspace"],
                "caption": caption,
                "caption_bbox": caption_bbox,
            }
        )
    return figures


def _is_figure_image(page: dict[str, Any], image: dict[str, Any]) -> bool:
    rect = fitz.Rect(image["bbox"])
    page_area = page["width"] * page["height"]
    return (
        page_area > 0
        and rect.get_area() / page_area >= 0.005
        and rect.width >= 18
        and rect.height >= 18
        and image["width"] >= 40
        and image["height"] >= 40
    )


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
    if re.search(r"\b(poster|abstract)\b", title):
        return "abstract-or-poster"
    if document_type == "book" or _CHAPTER.search(title):
        return "chapter"
    if document_type in {"journal-issue", "proceedings"} or (
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
        signal["prominent_title"]
        and signal["author_pattern"]
        and (signal["abstract_pattern"] or signal["doi_pattern"])
        for signal in signals
    )
    if len(top_bookmarks) >= 2:
        return "book", ["Multiple top-level PDF bookmarks suggest chapters."]
    if article_pages >= 2:
        return "journal-issue", [
            "Multiple pages combine prominent titles, author evidence, and article evidence."
        ]
    if article_pages == 1:
        return "unit", [
            "One page combines a prominent title, author evidence, and article evidence."
        ]
    return "unknown", [
        "Automatic classification did not find enough independent evidence."
    ]


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
    elif document_type in {"journal-issue", "proceedings"}:
        for page, signal in zip(structure["pages"], signals):
            evidence = []
            if signal["prominent_title"]:
                evidence.append("prominent title typography")
            if signal["author_pattern"]:
                evidence.append("author pattern")
            if signal["abstract_pattern"]:
                evidence.append("abstract label")
            if signal["doi_pattern"]:
                evidence.append("DOI pattern")
            required = signal["prominent_title"] and signal["author_pattern"]
            if document_type == "journal-issue":
                required = required and (
                    signal["abstract_pattern"] or signal["doi_pattern"]
                )
            if required:
                candidates[page["input_page"]] = {
                    "input_page": page["input_page"],
                    "title": signal["title"],
                    "evidence": evidence,
                    "y": signal["title_y"],
                }
    if 1 not in candidates:
        candidates[1] = {
            "input_page": 1,
            "title": signals[0]["title"] if signals else "Document",
            "evidence": ["container start"],
            "y": 0,
        }
    return sorted(candidates.values(), key=lambda item: item["input_page"])


def analyze_container(pdf_path: Path, requested_type: str) -> dict[str, Any]:
    """Create conservative whole-page unit proposals with review evidence."""
    if requested_type not in {"unit", "journal-issue", "book", "proceedings", "auto"}:
        raise ValueError("Unsupported document type.")
    identity = source_identity(pdf_path)
    structure = extract_structure(pdf_path)
    signals = [_page_signals(page) for page in structure["pages"]]
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
    confirmed_starts = []
    suspected_shared = []
    for candidate in candidates:
        page = structure["pages"][candidate["input_page"] - 1]
        if candidate["input_page"] > 1 and candidate["y"] > page["height"] * 0.2:
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
                "figures": _page_figures(page),
                "unit_ids": page_unit_ids,
                "warnings": [],
            }
        )
    source = {
        **identity,
        "bibliographic": _bibliographic_source(document_type, structure, signals),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "analysis",
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
        and all(_is_number(item) for item in value)
        and 0 <= value[0] <= value[2]
        and 0 <= value[1] <= value[3]
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
            f"--doctype {cli_document_type} conflicts with plan document_type {document_type!r}."
        )
    actual_source = source_identity(pdf_path)
    source = plan.get("source")
    if not isinstance(source, dict):
        errors.append("source must be an object.")
    else:
        if source.get("sha256") != actual_source["sha256"]:
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
        elif unit_id in seen_ids:
            errors.append(f"{prefix}.id must be unique.")
        else:
            seen_ids.add(unit_id)
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
    for position, (start, end, unit_id) in enumerate(sorted(ranges)):
        if position == 0:
            continue
        previous_start, previous_end, previous_id = sorted(ranges)[position - 1]
        if start <= previous_end:
            errors.append(
                f"Units {previous_id!r} and {unit_id!r} overlap on input page "
                f"{start}. Whole-page processing requires disjoint ranges."
            )
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
    analysis = analyze_container(pdf_path, "unit")
    identity = analysis["source"]
    unit = analysis["units"][0]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "plan",
        "source": identity,
        "document_type": "unit",
        "pages": [{**page, "selected": True} for page in analysis["pages"]],
        "units": [
            {
                **unit,
                "selected": True,
                "boundary_status": "confirmed",
            }
        ],
    }


def _normalized_margin_text(text: str) -> str:
    return re.sub(r"\d+", "#", " ".join(text.lower().split()))


def _running_margin_texts(pages: list[dict[str, Any]]) -> set[str]:
    counts: Counter[str] = Counter()
    for page in pages:
        on_page = set()
        for block in page["text_blocks"]:
            y0, y1 = block["bbox"][1], block["bbox"][3]
            if y0 <= page["height"] * 0.1 or y1 >= page["height"] * 0.9:
                text = " ".join(line["text"] for line in block["lines"]).strip()
                if text:
                    on_page.add(_normalized_margin_text(text))
        counts.update(on_page)
    return {text for text, count in counts.items() if count >= 2}


def _ordered_items(
    items: list[dict[str, Any]], page: dict[str, Any]
) -> list[dict[str, Any]]:
    width = page["width"]
    full = [item for item in items if item["bbox"][2] - item["bbox"][0] >= width * 0.62]
    columns = [item for item in items if item not in full]
    full.sort(key=lambda item: (item["bbox"][1], item["bbox"][0]))
    ordered = []
    band_top = 0.0
    for separator in [*full, None]:
        band_bottom = separator["bbox"][1] if separator else page["height"] + 1
        band = [
            item
            for item in columns
            if band_top <= (item["bbox"][1] + item["bbox"][3]) / 2 < band_bottom
        ]
        left = sorted(
            (
                item
                for item in band
                if (item["bbox"][0] + item["bbox"][2]) / 2 < width / 2
            ),
            key=lambda item: (item["bbox"][1], item["bbox"][0]),
        )
        right = sorted(
            (item for item in band if item not in left),
            key=lambda item: (item["bbox"][1], item["bbox"][0]),
        )
        ordered.extend(left)
        ordered.extend(right)
        if separator is not None:
            ordered.append(separator)
            band_top = separator["bbox"][3]
    return ordered


def _ordered_blocks(page: dict[str, Any]) -> list[dict[str, Any]]:
    return _ordered_items(list(page["text_blocks"]), page)


def _block_text(block: dict[str, Any]) -> str:
    result = ""
    for line in block["lines"]:
        value = line["text"].strip()
        if not value:
            continue
        if result.endswith("-") and value[0].islower():
            result = result[:-1] + value
        else:
            result = f"{result} {value}".strip()
    return result


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


def _overlap_fraction(first: list[float], second: list[float]) -> float:
    first_rect = fitz.Rect(first)
    intersection = first_rect & fitz.Rect(second)
    if first_rect.get_area() <= 0 or intersection.is_empty:
        return 0.0
    return intersection.get_area() / first_rect.get_area()


def _bbox_matches(first: list[float], second: list[float]) -> bool:
    first_rect = fitz.Rect(first)
    second_rect = fitz.Rect(second)
    intersection = first_rect & second_rect
    smaller = min(first_rect.get_area(), second_rect.get_area())
    return (
        smaller > 0
        and not intersection.is_empty
        and intersection.get_area() / smaller >= 0.8
    )


def _page_blocks_for_export(
    page: dict[str, Any],
    unit_id: str,
    figure_start: int,
    running: set[str],
    reviewed_page: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    semantic = [
        {
            "kind": "page_marker",
            "input_page": page["input_page"],
            "printed_page_number": (
                reviewed_page["printed_page_number"]
                if reviewed_page is not None
                else page["printed_page_number"]
            ),
        }
    ]
    figures = []
    warnings = []
    reviewed_figures = reviewed_page.get("figures", []) if reviewed_page else None
    page_figures = (
        reviewed_figures if reviewed_figures is not None else _page_figures(page)
    )
    blocks = []
    for block in _ordered_blocks(page):
        text = _block_text(block)
        y0, y1 = block["bbox"][1], block["bbox"][3]
        if (
            y0 <= page["height"] * 0.1 or y1 >= page["height"] * 0.9
        ) and _normalized_margin_text(text) in running:
            continue
        blocks.append(block)
    outside_figures = []
    for block in blocks:
        if any(
            _overlap_fraction(block["bbox"], figure["bbox"]) >= 0.8
            for figure in page_figures
        ):
            warnings.append(
                f"Input page {page['input_page']} has text inside a raster figure; review the figure content."
            )
            continue
        outside_figures.append(block)
    blocks = outside_figures
    caption_indices: set[int] = set()
    for image_number, described in enumerate(page_figures, start=figure_start):
        caption_bbox = described.get("caption_bbox")
        if caption_bbox is not None:
            for index, block in enumerate(blocks):
                if _bbox_matches(block["bbox"], caption_bbox):
                    caption_indices.add(index)
                    break
        figure = {
            **described,
            "kind": "figure",
            "id": described.get("id", f"{unit_id}-fig-{image_number:03d}"),
            "input_page": page["input_page"],
        }
        figures.append(figure)
    sizes = [
        span["size"]
        for index, block in enumerate(blocks)
        if index not in caption_indices
        for line in block["lines"]
        for span in line["spans"]
        if span["text"].strip()
    ]
    body_size = median(sizes) if sizes else 0
    reviewed_headings = []
    if reviewed_page is not None:
        main_title = reviewed_page.get("main_title")
        if main_title is not None:
            reviewed_headings.append(
                {"text": main_title["text"], "level": 1, "bbox": main_title["bbox"]}
            )
        reviewed_headings.extend(
            {
                "text": section["title"],
                "level": section["level"],
                "bbox": section["bbox"],
            }
            for section in reviewed_page.get("sections", [])
        )
    for item in _ordered_items([*blocks, *figures], page):
        if item.get("kind") == "figure":
            semantic.append(item)
            continue
        index = blocks.index(item)
        if index in caption_indices:
            continue
        reviewed_heading = next(
            (
                heading
                for heading in reviewed_headings
                if _bbox_matches(item["bbox"], heading["bbox"])
            ),
            None,
        )
        text = reviewed_heading["text"] if reviewed_heading else _block_text(item)
        if not text:
            continue
        level = (
            reviewed_heading["level"]
            if reviewed_heading is not None
            else _heading_level(item, body_size)
        )
        semantic.append(
            {
                "kind": "heading" if level else "paragraph",
                "level": level,
                "text": text,
                "input_page": page["input_page"],
                "bbox": item["bbox"],
            }
        )
    return semantic, figures, warnings


def reconstruct_unit(
    document: fitz.Document,
    unit: dict[str, Any],
    reviewed_pages: dict[int, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Build semantic blocks for one confirmed whole-page unit."""
    start = unit["input_pages"]["start"]
    end = unit["input_pages"]["end"]
    page_metadata = build_page_metadata(Path(document.name), [], [])
    page_numbers = [
        number
        for number in range(start, end + 1)
        if reviewed_pages is None or reviewed_pages[number]["selected"]
    ]
    pages = [
        _extract_page(document[number - 1], page_metadata[f"page_{number:03d}"]["pgn"])
        for number in page_numbers
    ]
    running = _running_margin_texts(pages)
    semantic = []
    figures = []
    warnings = []
    next_figure = 1
    for page in pages:
        page_blocks, page_figures, page_warnings = _page_blocks_for_export(
            page,
            unit["id"],
            next_figure,
            running,
            reviewed_pages.get(page["input_page"]) if reviewed_pages else None,
        )
        semantic.extend(page_blocks)
        figures.extend(page_figures)
        warnings.extend(page_warnings)
        next_figure += len(page_figures)
        try:
            drawings = document[page["input_page"] - 1].get_drawings()
        except (RuntimeError, ValueError):
            drawings = []
        if drawings:
            warnings.append(
                f"Input page {page['input_page']} contains vector graphics or table rules; review is required."
            )
    return semantic, figures, warnings


def render_html(blocks: list[dict[str, Any]]) -> str:
    """Render a safe UTF-8 HTML fragment without scripts or external resources."""
    output = []
    for block in blocks:
        if block["kind"] == "page_marker":
            printed = block["printed_page_number"]
            label = (
                f"Printed page {printed}"
                if printed is not None
                else f"Input page {block['input_page']}"
            )
            output.append(
                f'<p class="page-marker" data-input-page="{block["input_page"]}">{escape(label)}</p>'
            )
        elif block["kind"] == "heading":
            level = min(6, max(1, int(block["level"])))
            output.append(f"<h{level}>{escape(block['text'])}</h{level}>")
        elif block["kind"] == "paragraph":
            output.append(f"<p>{escape(block['text'])}</p>")
        elif block["kind"] == "figure":
            output.append(f'<figure data-figure-id="{escape(block["id"])}">')
            output.append(
                f'<div class="figure-placeholder">Figure {escape(block["id"])}</div>'
            )
            if block["caption"]:
                output.append(f"<figcaption>{escape(block['caption'])}</figcaption>")
            output.append("</figure>")
    return "\n".join(output) + "\n"


def export_units(
    pdf_path: Path,
    plan: dict[str, Any],
    selected_units: list[dict[str, Any]],
    destination: Path,
    include_html: bool,
) -> None:
    """Write selected units and a manifest into a separate export directory."""
    destination.mkdir(parents=True, exist_ok=True)
    manifest_units = []
    reviewed_pages = {page["input_page"]: page for page in plan["pages"]}
    with fitz.open(pdf_path) as document:
        for unit in selected_units:
            blocks, figures, warnings = reconstruct_unit(
                document, unit, reviewed_pages=reviewed_pages
            )
            selected_input_pages = [
                page
                for page in range(
                    unit["input_pages"]["start"], unit["input_pages"]["end"] + 1
                )
                if reviewed_pages[page]["selected"]
            ]
            html_name = f"{unit['id']}.html" if include_html else None
            metadata_name = f"{unit['id']}.metadata.json"
            metadata = {
                "schema_version": SCHEMA_VERSION,
                "kind": "unit-metadata",
                "source": plan["source"],
                "document_type": plan["document_type"],
                "unit": {
                    "id": unit["id"],
                    "title": unit["title"],
                    "type": unit["type"],
                    "input_pages": unit["input_pages"],
                    "selected_input_pages": selected_input_pages,
                },
                "figures": figures,
                "warnings": warnings,
                "semantic_blocks": blocks,
            }
            write_json_document(metadata, destination / metadata_name)
            if include_html:
                (destination / html_name).write_text(
                    render_html(blocks), encoding="utf-8"
                )
            manifest_units.append(
                {
                    "id": unit["id"],
                    "title": unit["title"],
                    "type": unit["type"],
                    "input_pages": unit["input_pages"],
                    "selected_input_pages": selected_input_pages,
                    "html": html_name,
                    "metadata": metadata_name,
                }
            )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": "container-manifest",
        "source": plan["source"],
        "document_type": plan["document_type"],
        "units": manifest_units,
    }
    write_json_document(manifest, destination / "manifest.json")


def publish_unit_directory(staged: Path, destination: Path) -> None:
    """Replace an earlier unit export only after the staged export succeeds."""
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(staged, destination)
