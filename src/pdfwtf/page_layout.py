"""Born-digital page geometry, reading order, and figure detection.

The partition and HTML extraction components share these primitives.
"""

from __future__ import annotations

from html import unescape
import re
from typing import Any, Iterable
import unicodedata

import pymupdf as fitz

_CAPTION = re.compile(r"^(?:fig(?:ure)?\.?\s*\d+|image\s*\d+)\b", re.IGNORECASE)


_ISSN = re.compile(r"\bISSN\s*:?[\s-]*(\d{4})[\s-]*(\d{3}[\dX])\b", re.IGNORECASE)


_ISBN = re.compile(
    r"\bISBN(?:-1[03])?\s*:?[\s-]*((?:97[89][\s-]*)?\d(?:[\s-]*\d){8,11}[\s-]*[\dX])\b",
    re.IGNORECASE,
)


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
            # Native blocks can contain separate lines from several columns.
            for group in _projection_groups(
                lines, axis=0, min_gap=max(3.0, page.rect.width * 0.01)
            ):
                group.sort(key=lambda line: (line["bbox"][1], line["bbox"][0]))
                blocks.append(
                    {
                        "kind": "text",
                        "bbox": [
                            min(line["bbox"][0] for line in group),
                            min(line["bbox"][1] for line in group),
                            max(line["bbox"][2] for line in group),
                            max(line["bbox"][3] for line in group),
                        ],
                        "lines": group,
                    }
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
        "text_blocks": _ordered_items(
            blocks, {"width": page.rect.width, "height": page.rect.height}
        ),
        "raster_images": images,
    }


def _magazine_captions(
    page: dict[str, Any],
    images: list[dict[str, Any]],
) -> dict[int, tuple[str, list[float]]]:
    """Associate small bold lines below photos with the nearest image."""
    captions: dict[int, list[dict[str, Any]]] = {}
    for block in page["text_blocks"]:
        for line in block["lines"]:
            spans = [span for span in line["spans"] if span["text"].strip()]
            if not spans or not any(character.isalpha() for character in line["text"]):
                continue
            if line["bbox"][1] >= page["height"] * 0.94:
                continue
            if max(span["size"] for span in spans) > 14:
                continue
            total = sum(len(span["text"]) for span in spans)
            bold = sum(
                len(span["text"])
                for span in spans
                if span["flags"] & 16 or "bold" in span["font"].lower()
            )
            if bold < total * 0.7:
                continue
            candidates = []
            for index, image in enumerate(images):
                rect, bbox = image["bbox"], line["bbox"]
                gap = bbox[1] - rect[3]
                overlap = min(rect[2], bbox[2]) - max(rect[0], bbox[0])
                width = min(rect[2] - rect[0], bbox[2] - bbox[0])
                if 0 <= gap <= 18 and width > 0 and overlap / width >= 0.5:
                    candidates.append((gap, -overlap, index))
            if candidates:
                index = min(candidates)[2]
                captions.setdefault(index, []).append(line)
    result = {}
    for index, lines in captions.items():
        lines.sort(key=lambda line: (line["bbox"][1], line["bbox"][0]))
        result[index] = (
            " ".join(line["text"] for line in lines),
            [
                min(line["bbox"][0] for line in lines),
                min(line["bbox"][1] for line in lines),
                max(line["bbox"][2] for line in lines),
                max(line["bbox"][3] for line in lines),
            ],
        )
    return result


def _page_figures(
    page: dict[str, Any], document_type: str | None = None
) -> list[dict[str, Any]]:
    """Describe raster figures and associate nearby explicit captions."""
    blocks = _ordered_blocks(page)
    caption_indices: set[int] = set()
    figures = []
    images = [image for image in page["raster_images"] if _is_figure_image(page, image)]
    magazine_captions = (
        _magazine_captions(page, images) if document_type == "magazine-issue" else {}
    )
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
        if caption is None and number - 1 in magazine_captions:
            caption, caption_bbox = magazine_captions[number - 1]
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


def _projection_groups(
    items: list[dict[str, Any]], axis: int, min_gap: float
) -> list[list[dict[str, Any]]]:
    """Partition items only at clear whitespace gaps in one axis."""
    groups: list[list[dict[str, Any]]] = []
    edge = float("-inf")
    for item in sorted(items, key=lambda item: item["bbox"][axis]):
        start = item["bbox"][axis]
        end = item["bbox"][axis + 2]
        if not groups or start - edge >= min_gap:
            groups.append([])
        groups[-1].append(item)
        edge = max(edge, end)
    return groups


def _ordered_items(
    items: list[dict[str, Any]], page: dict[str, Any]
) -> list[dict[str, Any]]:
    """Read whitespace-separated columns and horizontal regions recursively."""
    if len(items) < 2:
        return list(items)
    groups = _projection_groups(items, 0, max(3.0, page["width"] * 0.01))
    if len(groups) > 1:
        return [item for group in groups for item in _ordered_items(group, page)]

    # Spanning headings and figures divide the page into column regions.
    # Keep each region together before looking for its column gutters.
    full = [
        item
        for item in items
        if item["bbox"][2] - item["bbox"][0] >= page["width"] * 0.62
    ]
    if full:
        full.sort(key=lambda item: (item["bbox"][1], item["bbox"][0]))
        columns = [item for item in items if item not in full]
        ordered = []
        band_top = float("-inf")
        for separator in [*full, None]:
            band_bottom = separator["bbox"][1] if separator else float("inf")
            band = [
                item
                for item in columns
                if band_top <= (item["bbox"][1] + item["bbox"][3]) / 2 < band_bottom
            ]
            ordered.extend(_ordered_items(band, page))
            if separator is not None:
                ordered.append(separator)
                # Retain items that overlap a separator in the following region.
                band_top = separator["bbox"][1]
        return ordered

    groups = _projection_groups(items, 1, max(2.0, page["height"] * 0.005))
    if len(groups) > 1:
        return [item for group in groups for item in _ordered_items(group, page)]
    # Ambiguous overlaps cannot establish a safe column boundary.
    # Retain every item in a deterministic top-to-bottom order.
    return sorted(items, key=lambda item: (item["bbox"][1], item["bbox"][0]))


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
