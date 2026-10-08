"""Page metadata based on absolute input indices and margin text."""

from pathlib import Path
import re
from typing import Any

import pymupdf as fitz

_NUMBER = re.compile(
    r"(?:page\s+)?[-\u2013\u2014(\[]?\s*"
    r"(?P<number>\d{1,4}|[ivxlcdm]+)\s*"
    r"(?:of\s+\d{1,4})?\s*[-\u2013\u2014)\]]?",
    re.IGNORECASE,
)
_ROMAN = re.compile(
    r"M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})",
    re.IGNORECASE,
)


def _candidates(page: fitz.Page) -> set[str]:
    candidates = set()
    top = page.rect.y0 + page.rect.height * 0.15
    bottom = page.rect.y1 - page.rect.height * 0.15
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            if line["bbox"][3] > top and line["bbox"][1] < bottom:
                continue
            text = "".join(span["text"] for span in line["spans"]).strip()
            match = _NUMBER.fullmatch(text)
            if match is None:
                continue
            number = match["number"]
            if number.isdigit():
                if int(number) > 0:
                    candidates.add(str(int(number)))
            elif _ROMAN.fullmatch(number):
                candidates.add(number.lower())
    return candidates


def build_page_metadata(
    pdf_path: Path,
    pages_to_keep: list[int],
    pages_to_skip: list[int],
) -> dict[str, dict[str, Any]]:
    """Describe all input pages; never infer a missing printed number."""
    with fitz.open(pdf_path) as document:
        candidates = [_candidates(page) for page in document]
    keep = set(pages_to_keep)
    skip = set(pages_to_skip)
    pages = {}
    for offset, values in enumerate(candidates):
        pgn = next(iter(values)) if len(values) == 1 else None
        if len(values) > 1:
            supported = set()
            for value in values:
                if not value.isdigit():
                    continue
                for neighbor, delta in ((offset - 1, -1), (offset + 1, 1)):
                    if 0 <= neighbor < len(candidates):
                        expected = str(int(value) + delta)
                        if candidates[neighbor] == {expected}:
                            supported.add(value)
            if len(supported) == 1:
                pgn = next(iter(supported))
        index = offset + 1
        entry: dict[str, Any] = {"index": index, "pgn": pgn}
        if index in skip:
            entry["skip"] = True
        if index in keep:
            entry["keep"] = True
        pages[f"page_{index:03d}"] = entry
    return pages
