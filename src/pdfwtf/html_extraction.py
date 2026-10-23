"""Semantic block reconstruction and HTML rendering for any PDF pages."""

from __future__ import annotations

from collections import Counter
from html import escape
from pathlib import Path
import re
from statistics import median
from typing import Any

import pymupdf as fitz

from .page_layout import (
    _CAPTION,
    _ISBN,
    _ISSN,
    _block_text,
    _extract_page,
    _ordered_blocks,
    _ordered_items,
    _page_figures,
)
from .page_metadata import build_page_metadata


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


def _text_style(block: dict[str, Any]) -> tuple[float, float]:
    """Return character-weighted dominant size and bold fraction."""
    sizes: Counter[float] = Counter()
    bold_chars = 0
    for line in block["lines"]:
        for span in line["spans"]:
            weight = len(span["text"].strip())
            sizes[round(span["size"], 1)] += weight
            if span.get("flags", 0) & 16 or "bold" in span["font"].lower():
                bold_chars += weight
    total = sum(sizes.values())
    return (
        sizes.most_common(1)[0][0] if total else 0,
        bold_chars / total if total else 0,
    )


def _body_size(pages: list[dict[str, Any]], running: set[str]) -> float:
    sizes: Counter[float] = Counter()
    for page in pages:
        for block in page["text_blocks"]:
            text = _block_text(block)
            margin = (
                block["bbox"][1] <= page["height"] * 0.1
                or block["bbox"][3] >= page["height"] * 0.9
            )
            if (margin and _normalized_margin_text(text) in running) or _CAPTION.search(
                text
            ):
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    sizes[round(span["size"], 1)] += len(span["text"].strip())
    # The most common size by character count is less sensitive to fragmented
    # title spans and small-font footnotes than an unweighted span median.
    return sizes.most_common(1)[0][0] if sizes else 0


_SECTION_NUMBER = re.compile(r"^\d+(?:\.\d+)*[.)]?\s+\S")


_SECTION_LABELS = {
    "abstract",
    "summary",
    "introduction",
    "methods",
    "materials and methods",
    "results",
    "discussion",
    "conclusion",
    "conclusions",
    "references",
    "acknowledgements",
    "acknowledgments",
    "abstrakt",
    "souhrn",
    "úvod",
    "metodika",
    "výsledky",
    "diskuse",
    "diskuze",
    "závěr",
    "literatura",
}


def _can_be_unit_title(text: str) -> bool:
    return (
        not _SECTION_NUMBER.match(text)
        and text.strip().casefold().rstrip(":.") not in _SECTION_LABELS
    )


def _export_heading_evidence(
    block: dict[str, Any], body_size: float, before_gap: float, after_gap: float
) -> list[str]:
    text = _block_text(block)
    size, bold = _text_style(block)
    if (
        not text
        or len(text) > 180
        or len(block["lines"]) > 3
        or not body_size
        or text.isdigit()
        or _CAPTION.search(text)
        or _ISSN.search(text)
        or _ISBN.search(text)
        or "@" in text
        or re.match(r"^(?:https?://|doi\s*:)", text, re.I)
    ):
        return []
    evidence = []
    if size >= body_size * 1.15:
        evidence.append("larger than unit body text")
    if bold >= 0.8:
        evidence.append("predominantly bold")
    if _SECTION_NUMBER.match(text):
        evidence.append("section numbering")
    if before_gap >= size * 0.5 and before_gap > after_gap * 1.2:
        evidence.append("spacing before exceeds spacing after")
    # Bold body sentences and isolated inline emphasis are weak evidence.
    typography = size >= body_size * 1.15 or bold >= 0.8
    if not typography or (
        text.endswith((".", "!", "?", ";")) and size < body_size * 1.35
    ):
        return []
    return evidence if len(evidence) >= 2 or size >= body_size * 1.35 else []


def _resolve_unit_headings(
    blocks: list[dict[str, Any]],
    unit_title: str,
    body_size: float,
    *,
    reviewed_unit_title: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Merge wrapped automatic headings and assign hierarchy across pages."""
    merged: list[dict[str, Any]] = []
    warnings = []
    for block in blocks:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and previous["kind"] == block["kind"] == "heading"
            and not previous.get("_reviewed")
            and not block.get("_reviewed")
            and previous["input_page"] == block["input_page"]
            and previous["_style"] == block["_style"]
            and abs(previous["bbox"][0] - block["bbox"][0]) <= body_size * 0.5
            and 0 <= block["bbox"][1] - previous["bbox"][3] <= body_size * 0.35
            and len(previous["text"]) + len(block["text"]) < 180
            and not previous["text"].endswith((".", ":", "!", "?"))
            and not _SECTION_NUMBER.match(block["text"])
        ):
            previous["text"] += " " + block["text"]
            previous["bbox"] = list(
                fitz.Rect(previous["bbox"]) | fitz.Rect(block["bbox"])
            )
            previous["heading_evidence"].append("merged adjacent wrapped heading")
            continue
        merged.append(block)
    headings = [b for b in merged if b["kind"] == "heading"]
    automatic = [b for b in headings if not b.get("_reviewed")]
    title = None
    normalized_title = " ".join(unit_title.casefold().split())
    approved_title = next(
        (
            block
            for block in headings
            if " ".join(block["text"].casefold().split()) == normalized_title
            and (reviewed_unit_title or _can_be_unit_title(block["text"]))
        ),
        None,
    )
    if reviewed_unit_title and approved_title is not None:
        if not approved_title.get("_reviewed"):
            approved_title["level"] = 1
        title = approved_title
    elif reviewed_unit_title and normalized_title:
        first_page = next(b["input_page"] for b in merged if "input_page" in b)
        insert_at = next(
            (
                index
                for index, block in enumerate(merged)
                if block["kind"] != "page_marker"
            ),
            len(merged),
        )
        merged.insert(
            insert_at,
            {
                "kind": "heading",
                "level": 1,
                "text": unit_title,
                "input_page": first_page,
                "bbox": [0.0, 0.0, 0.0, 0.0],
                "heading_evidence": ["approved plan unit title"],
                "_style": (float("inf"), True),
                "_reviewed": True,
            },
        )
    elif not any(b.get("_reviewed") and b["level"] == 1 for b in headings):
        title = next(
            (
                b
                for b in automatic
                if " ".join(b["text"].casefold().split()) == normalized_title
                and _can_be_unit_title(b["text"])
            ),
            None,
        )
        if title is None and automatic:
            first_page = next(b["input_page"] for b in merged if "input_page" in b)
            first_content = next(
                (b for b in merged if b["kind"] != "page_marker"), None
            )
            if (
                first_content is automatic[0]
                and first_content["input_page"] == first_page
                and first_content["_style"][0] >= body_size * 1.35
                and _can_be_unit_title(first_content["text"])
            ):
                title = first_content
    styles = sorted({b["_style"] for b in automatic if b is not title}, reverse=True)
    for block in automatic:
        block["level"] = (
            1 if block is title else min(6, 2 + styles.index(block["_style"]))
        )
    previous = None
    for block in merged:
        if block["kind"] == "page_marker":
            continue
        if (
            previous is not None
            and previous["kind"] == block["kind"] == "heading"
            and block["level"] <= previous["level"]
        ):
            warnings.append(
                f"Input page {block['input_page']} has consecutive headings without "
                "a subordinate hierarchy; review their classification."
            )
        previous = block
    for block in merged:
        block.pop("_style", None)
        block.pop("_reviewed", None)
    return merged, warnings


def _page_blocks_for_export(
    page: dict[str, Any],
    id_prefix: str,
    figure_start: int,
    running: set[str],
    reviewed_page: dict[str, Any] | None = None,
    *,
    unit_body_size: float | None = None,
    use_reviewed_headings: bool = True,
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
            "id": described.get("id", f"{id_prefix}-fig-{image_number:03d}"),
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
    body_size = (
        unit_body_size
        if unit_body_size is not None
        else (median(sizes) if sizes else 0)
    )
    reviewed_headings = []
    if reviewed_page is not None and use_reviewed_headings:
        main_title = reviewed_page.get("main_title")
        if main_title is not None:
            reviewed_headings.append(
                {"text": main_title["text"], "level": 1, "bbox": main_title["bbox"]}
            )
        reviewed_headings.extend(
            {
                "text": section["title"],
                "level": max(2, section["level"]),
                "bbox": section["bbox"],
            }
            for section in reviewed_page.get("sections", [])
        )
    items = [*blocks, *figures]
    if any(
        (fitz.Rect(first["bbox"]) & fitz.Rect(second["bbox"])).get_area() > 0
        for index, first in enumerate(items)
        for second in items[index + 1 :]
    ):
        warnings.append(
            "Input page {} has overlapping blocks; review the reading order.".format(
                page["input_page"]
            )
        )
    heading_by_block: dict[int, dict[str, Any]] = {}
    suppressed_heading_blocks: set[int] = set()
    unmatched_reviewed_headings = []
    for heading in reviewed_headings:
        matching_indices = [
            index
            for index, block in enumerate(blocks)
            if _bbox_matches(block["bbox"], heading["bbox"])
            or _overlap_fraction(block["bbox"], heading["bbox"]) >= 0.8
        ]
        if matching_indices:
            heading_by_block[matching_indices[0]] = heading
            suppressed_heading_blocks.update(matching_indices[1:])
        else:
            unmatched_reviewed_headings.append(heading)
    unmatched_reviewed_headings.sort(key=lambda heading: heading["bbox"][1])

    def append_reviewed_heading(heading: dict[str, Any]) -> None:
        semantic.append(
            {
                "kind": "heading",
                "level": heading["level"],
                "text": heading["text"],
                "input_page": page["input_page"],
                "bbox": heading["bbox"],
                "heading_evidence": ["reviewed plan heading"],
                "_style": (float("inf"), True),
                "_reviewed": True,
            }
        )

    for item in _ordered_items(items, page):
        while (
            unmatched_reviewed_headings
            and unmatched_reviewed_headings[0]["bbox"][1] <= item["bbox"][1]
        ):
            append_reviewed_heading(unmatched_reviewed_headings.pop(0))
        if item.get("kind") == "figure":
            semantic.append(item)
            continue
        index = blocks.index(item)
        if index in caption_indices or index in suppressed_heading_blocks:
            continue
        reviewed_heading = heading_by_block.get(index)
        text = reviewed_heading["text"] if reviewed_heading else _block_text(item)
        if not text:
            continue
        before = blocks[index - 1] if index else None
        after = blocks[index + 1] if index + 1 < len(blocks) else None
        before_gap = item["bbox"][1] - before["bbox"][3] if before else float("inf")
        after_gap = after["bbox"][1] - item["bbox"][3] if after else float("inf")
        evidence = (
            ["reviewed plan heading"]
            if reviewed_heading
            else _export_heading_evidence(item, body_size, before_gap, after_gap)
        )
        level = (
            reviewed_heading["level"] if reviewed_heading else (2 if evidence else None)
        )
        semantic.append(
            {
                "kind": "heading" if level else "paragraph",
                "level": level,
                "text": text,
                "input_page": page["input_page"],
                "bbox": item["bbox"],
                **({"heading_evidence": evidence} if level else {}),
                "_style": (_text_style(item)[0], _text_style(item)[1] >= 0.8),
                "_reviewed": reviewed_heading is not None,
            }
        )
    for heading in unmatched_reviewed_headings:
        append_reviewed_heading(heading)
    return semantic, figures, warnings


def reconstruct_pages(
    document: fitz.Document,
    page_numbers: list[int],
    *,
    id_prefix: str,
    title: str = "",
    reviewed_pages: dict[int, dict[str, Any]] | None = None,
    use_reviewed_headings: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Build semantic blocks for the given 1-based input pages of any PDF.

    The pages form one reading flow. Figure identifiers use id_prefix. A title
    helps to select the level-1 heading. Reviewed pages from a plan can supply
    figures and headings.
    """
    page_metadata = build_page_metadata(Path(document.name), [], [])
    pages = [
        _extract_page(document[number - 1], page_metadata[f"page_{number:03d}"]["pgn"])
        for number in page_numbers
    ]
    running = _running_margin_texts(pages)
    body_size = _body_size(pages, running)
    semantic = []
    figures = []
    warnings = []
    next_figure = 1
    for page in pages:
        page_blocks, page_figures, page_warnings = _page_blocks_for_export(
            page,
            id_prefix,
            next_figure,
            running,
            reviewed_pages.get(page["input_page"]) if reviewed_pages else None,
            unit_body_size=body_size,
            use_reviewed_headings=use_reviewed_headings,
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
    semantic, heading_warnings = _resolve_unit_headings(
        semantic,
        title,
        body_size,
        reviewed_unit_title=reviewed_pages is not None and use_reviewed_headings,
    )
    warnings.extend(heading_warnings)
    return semantic, figures, warnings


def render_html(
    blocks: list[dict[str, Any]], page_marker: bool = False, figure_list: bool = False
) -> str:
    """Render a safe UTF-8 HTML fragment, optionally with a linked figure list."""
    output = []
    figures = []
    for block in blocks:
        if block["kind"] == "page_marker":
            printed = block["printed_page_number"]
            label = (
                f"Printed page {printed}"
                if printed is not None
                else f"Input page {block['input_page']}"
            )
            if page_marker is True:
                output.append(
                    f'<p class="page-marker" data-input-page="{block["input_page"]}">{escape(label)}</p>'
                )
        elif block["kind"] == "heading":
            level = min(6, max(1, int(block["level"])))
            output.append(f"<h{level}>{escape(block['text'])}</h{level}>")
        elif block["kind"] == "paragraph":
            output.append(f"<p>{escape(block['text'])}</p>")
        elif block["kind"] == "figure":
            anchor = escape(block["id"])
            figures.append((anchor, block["id"], block.get("caption") or ""))
            output.append(f'<figure data-figure-id="{escape(block["id"])}">')
            output.append(
                f'<div class="figure-placeholder" id="{anchor}">Figure {escape(block["id"])}</div>'
            )
            if block.get("caption"):
                output.append(f"<figcaption>{escape(block['caption'])}</figcaption>")
            output.append("</figure>")
    if figure_list:
        output.extend(['<section class="figures">', "<h2>Figures</h2>", "<ul>"])
        for anchor, identifier, caption in figures:
            output.append(
                f'<li><a href="#{anchor}">{escape(identifier)}</a> -- {escape(caption)}</li>'
            )
        output.extend(["</ul>", "</section>"])
    return "\n".join(output) + "\n"
