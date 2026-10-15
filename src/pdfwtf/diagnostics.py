"""Read-only PDF diagnostics for the ``analyze`` command.

The service inspects observable PDF properties with PyMuPDF. It does not
render pages, run cleanup or OCR, call an LLM, partition, or export. All
classifications are conservative heuristics and record their evidence.
"""

from __future__ import annotations

from datetime import datetime, UTC
from hashlib import sha256
import logging
from pathlib import Path
from typing import Any, Iterable
import unicodedata

import pymupdf as fitz

from .utils.analyze import PAGE_NUMBER_RE, is_meaningful_text

log = logging.getLogger(__name__)

ANALYSIS_SCHEMA_VERSION = "1.0"
ANALYSIS_KIND = "pdf-analysis"

# Classification thresholds. specs/ANALYSIS.md documents them.
SCAN_COVERAGE = 0.85
IMAGE_COVERAGE = 0.05
BAD_CHARACTER_RATIO = 0.01
OCR_FONT_MARKERS = ("glyphlessfont",)
INVISIBLE_RENDER_MODE = 3

COMPOSITIONS = ("born_digital", "scanned", "mixed", "blank", "uncertain")
DOCUMENT_COMPOSITIONS = COMPOSITIONS + ("heterogeneous",)
OCR_NEEDS = ("likely", "unlikely", "uncertain")
TEXT_LAYERS = ("absent", "present", "suspected_ocr")
TEXT_QUALITIES = ("apparently_usable", "suspect", "unknown", "not_applicable")
ACTIONS = ("run_ocr", "review_ocr_quality", "check_page_content", "review_blank_page")

_PAGE_SIZES = {
    "A3": (841.89, 1190.55),
    "A4": (595.28, 841.89),
    "A5": (419.53, 595.28),
    "Letter": (612.0, 792.0),
    "Legal": (612.0, 1008.0),
}
_SIZE_TOLERANCE = 2.0
_LIMITATIONS = [
    "Classifications and OCR assessments are heuristics. Extractable text does "
    "not prove complete or accurate OCR, and an image does not prove that a page "
    "contains text.",
    "Image coverage uses image placement rectangles clipped to the page. It "
    "ignores clip paths, transparency, and image content.",
    "Declared page rotation is reported as is. Physical skew and visual "
    "orientation are not inspected.",
]
_ACTION_REASONS = {
    "run_ocr": "These pages appear scanned and have no usable text layer.",
    "review_ocr_quality": "These pages have text with suspect quality indicators.",
    "check_page_content": "The content type of these pages could not be "
    "classified with confidence.",
    "review_blank_page": "These pages have no text, images, or drawings.",
}
_ENHANCE_NOTE = (
    " Note: enhance rasterizes and runs OCR on all selected pages when it detects "
    "a scan. Other pages have usable digital text. Use --extract to limit the "
    "enhanced pages if necessary."
)


class PdfInspectionError(ValueError):
    """The PDF cannot be opened or inspected."""


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _fingerprint(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text_quality(text: str) -> dict[str, int | float]:
    """Count characters that indicate damaged or unusable extracted text."""
    characters = 0
    replacement = control = private_use = 0
    for character in text:
        if character.isspace():
            continue
        characters += 1
        category = unicodedata.category(character)
        if character == "�":
            replacement += 1
        elif category == "Cc":
            control += 1
        elif category == "Co":
            private_use += 1
    # Control characters that are whitespace (tab, line breaks) are not counted.
    bad = replacement + control + private_use
    return {
        "characters": characters,
        "replacement_characters": replacement,
        "control_characters": control,
        "private_use_characters": private_use,
        "suspect_character_ratio": round(bad / characters, 4) if characters else 0.0,
    }


def format_page_ranges(numbers: Iterable[int]) -> str:
    """Format page numbers as compact ranges, for example "1-3, 7"."""
    ranges: list[list[int]] = []
    for number in numbers:
        if ranges and ranges[-1][1] == number - 1:
            ranges[-1][1] = number
        else:
            ranges.append([number, number])
    return ", ".join(
        str(start) if start == end else f"{start}-{end}" for start, end in ranges
    )


def union_area(rects: Iterable[fitz.Rect]) -> float:
    """Return the area covered by rectangles, without counting overlaps twice."""
    boxes = [
        (rect.x0, rect.y0, rect.x1, rect.y1)
        for rect in rects
        if rect.x1 > rect.x0 and rect.y1 > rect.y0
    ]
    if not boxes:
        return 0.0
    xs = sorted({x for box in boxes for x in (box[0], box[2])})
    area = 0.0
    for left, right in zip(xs, xs[1:]):
        intervals = sorted(
            (box[1], box[3]) for box in boxes if box[0] <= left and box[2] >= right
        )
        covered = 0.0
        start = end = None
        for top, bottom in intervals:
            if end is None or top > end:
                if end is not None:
                    covered += end - start
                start, end = top, bottom
            else:
                end = max(end, bottom)
        if end is not None:
            covered += end - start
        area += covered * (right - left)
    return area


def _size_label(width: float, height: float) -> str | None:
    for label, (short, long) in _PAGE_SIZES.items():
        for w, h in ((short, long), (long, short)):
            if abs(width - w) <= _SIZE_TOLERANCE and abs(height - h) <= _SIZE_TOLERANCE:
                return label
    return None


def _finding(code: str, severity: str, message: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "message": message}


def _inspect_page(page: fitz.Page) -> dict[str, Any]:
    """Inspect one page. Raise on unexpected errors; the caller records them."""
    box = page.cropbox
    width, height = round(box.width, 2), round(box.height, 2)
    # Extraction coordinates are relative to the unrotated cropbox origin.
    bounds = fitz.Rect(0, 0, box.width, box.height)
    page_area = bounds.width * bounds.height

    text = page.get_text("text")
    quality = text_quality(text)
    words = len(page.get_text("words"))
    lines = [line for line in text.splitlines() if not PAGE_NUMBER_RE.match(line)]
    meaningful = is_meaningful_text(" ".join(lines))

    visible = invisible = 0
    fonts: set[str] = set()
    for span in page.get_texttrace():
        count = sum(1 for item in span.get("chars", ()) if not chr(item[0]).isspace())
        if span.get("type") == INVISIBLE_RENDER_MODE:
            invisible += count
        else:
            visible += count
        if span.get("font"):
            fonts.add(str(span["font"]))
    ocr_font = any(
        marker in font.lower() for font in fonts for marker in OCR_FONT_MARKERS
    )

    infos = page.get_image_info()
    clipped = [fitz.Rect(info["bbox"]) & bounds for info in infos]
    coverage = (
        round(min(1.0, union_area(clipped) / page_area), 4) if page_area > 0 else 0.0
    )
    drawings = len(page.get_cdrawings())

    characters = int(quality["characters"])
    ocr_like = (invisible > 0 and invisible >= visible) or ocr_font
    digital_text = meaningful and not ocr_like
    reasons: list[str] = [f"image_coverage_{coverage:.2f}"]
    findings: list[dict[str, str]] = []

    # Composition: what the page is made of.
    if characters == 0 and not infos and drawings == 0:
        composition = "blank"
        findings.append(
            _finding("blank_page", "info", "The page has no text, images, or drawings.")
        )
    elif coverage >= SCAN_COVERAGE:
        composition = "mixed" if digital_text else "scanned"
        findings.append(
            _finding(
                "full_page_image",
                "info",
                f"Images cover {coverage:.0%} of the page.",
            )
        )
    elif coverage >= IMAGE_COVERAGE:
        composition = "mixed" if digital_text else "uncertain"
        findings.append(
            _finding(
                "partial_image", "info", f"Images cover {coverage:.0%} of the page."
            )
        )
    else:
        composition = "born_digital"
        if characters == 0 and drawings and not infos:
            findings.append(
                _finding(
                    "vector_only",
                    "info",
                    "The page has vector drawings but no text or images. "
                    "Text drawn as outlines cannot be detected.",
                )
            )

    # Text layer and quality, separate from composition.
    if characters == 0:
        text_layer = "absent"
        text_quality_status = "not_applicable"
        reasons.append("no_extractable_text")
        if composition != "blank":
            findings.append(
                _finding(
                    "no_extractable_text", "info", "The page has no extractable text."
                )
            )
    else:
        text_layer = "suspected_ocr" if ocr_like else "present"
        if invisible:
            reasons.append("invisible_text_layer")
            findings.append(
                _finding(
                    "invisible_text_layer",
                    "info",
                    "The page has invisible text, which OCR tools typically add.",
                )
            )
        if ocr_font:
            reasons.append("ocr_font")
        if quality["suspect_character_ratio"] > BAD_CHARACTER_RATIO:
            text_quality_status = "suspect"
            reasons.append("suspect_characters")
            findings.append(
                _finding(
                    "suspect_characters",
                    "warning",
                    "The text contains replacement, control, or private-use "
                    "characters.",
                )
            )
        elif not meaningful and composition in {"scanned", "uncertain", "mixed"}:
            text_quality_status = "suspect"
            reasons.append("insufficient_text")
            findings.append(
                _finding(
                    "insufficient_text",
                    "warning",
                    "The page has images but too little meaningful text.",
                )
            )
        elif not meaningful:
            text_quality_status = "unknown"
            reasons.append("little_text")
        else:
            text_quality_status = "apparently_usable"
            reasons.append("meaningful_text")

    # OCR need: an advisory assessment based on the evidence above.
    if composition == "blank":
        need = "unlikely"
    elif composition == "scanned":
        need = "unlikely" if text_quality_status == "apparently_usable" else "likely"
    elif composition == "born_digital" and text_quality_status == "apparently_usable":
        need = "unlikely"
    else:
        need = "uncertain"
        if composition == "mixed":
            reasons.append("images_may_contain_text")

    recommendations = []
    if need == "likely":
        recommendations.append(
            {"action": "run_ocr", "reason": _ACTION_REASONS["run_ocr"]}
        )
    if text_quality_status == "suspect" and need != "likely":
        recommendations.append(
            {
                "action": "review_ocr_quality",
                "reason": _ACTION_REASONS["review_ocr_quality"],
            }
        )
    if composition == "uncertain":
        recommendations.append(
            {
                "action": "check_page_content",
                "reason": _ACTION_REASONS["check_page_content"],
            }
        )
    if composition == "blank":
        recommendations.append(
            {
                "action": "review_blank_page",
                "reason": _ACTION_REASONS["review_blank_page"],
            }
        )
    if page.rotation:
        findings.append(
            _finding(
                "declared_rotation",
                "info",
                f"The page declares a rotation of {page.rotation} degrees.",
            )
        )

    return {
        "status": "ok",
        "size": {"width": width, "height": height, "label": _size_label(width, height)},
        "rotation": page.rotation,
        "composition": composition,
        "text": {
            **quality,
            "words": words,
            "visible_characters": visible,
            "invisible_characters": invisible,
            "meaningful": meaningful,
            "fonts": sorted(fonts),
        },
        "images": {"count": len(infos), "coverage": coverage},
        "drawings": drawings,
        "ocr": {
            "need": need,
            "text_layer": text_layer,
            "quality": text_quality_status,
            "reasons": reasons,
        },
        "findings": findings,
        "recommendations": recommendations,
    }


def _error_page(error: Exception) -> dict[str, Any]:
    return {
        "status": "error",
        "size": None,
        "rotation": None,
        "composition": None,
        "text": None,
        "images": None,
        "drawings": None,
        "ocr": None,
        "findings": [
            _finding(
                "inspection_error",
                "error",
                f"The page could not be inspected ({type(error).__name__}).",
            )
        ],
        "recommendations": [],
    }


def _document_info(document: fitz.Document) -> dict[str, Any]:
    metadata = document.metadata or {}

    def value(key: str) -> str | None:
        item = metadata.get(key)
        return item if isinstance(item, str) and item.strip() else None

    encrypted = bool(document.is_encrypted or metadata.get("encryption"))
    permissions = None
    if encrypted:
        bits = document.permissions
        permissions = {
            "print": bool(bits & fitz.PDF_PERM_PRINT),
            "copy": bool(bits & fitz.PDF_PERM_COPY),
            "modify": bool(bits & fitz.PDF_PERM_MODIFY),
            "annotate": bool(bits & fitz.PDF_PERM_ANNOTATE),
        }
    try:
        outline_entries: int | None = len(document.get_toc(simple=True))
    except Exception:
        outline_entries = None
    return {
        "page_count": document.page_count,
        "pdf_version": value("format"),
        "metadata": {
            "title": value("title"),
            "author": value("author"),
            "subject": value("subject"),
            "keywords": value("keywords"),
            "creator": value("creator"),
            "producer": value("producer"),
            "creation_date": value("creationDate"),
            "modification_date": value("modDate"),
        },
        "outline_entries": outline_entries,
        "encryption": {
            "encrypted": encrypted,
            "method": value("encryption"),
            "permissions": permissions,
        },
    }


def _summary(pages: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [page for page in pages if page["status"] == "ok"]
    by_composition = {name: 0 for name in COMPOSITIONS}
    by_need = {name: 0 for name in OCR_NEEDS}
    sizes: dict[tuple[float, float], dict[str, Any]] = {}
    rotations: dict[str, list[int]] = {}
    for page in ok:
        by_composition[page["composition"]] += 1
        by_need[page["ocr"]["need"]] += 1
        size = page["size"]
        entry = sizes.setdefault((size["width"], size["height"]), {**size, "pages": []})
        entry["pages"].append(page["number"])
        rotations.setdefault(str(page["rotation"]), []).append(page["number"])

    content = {page["composition"] for page in ok if page["composition"] != "blank"}
    if not ok:
        document_composition = "uncertain"
    elif not content:
        document_composition = "blank"
    elif len(content) == 1:
        document_composition = content.pop()
    else:
        # Pages of different types. A single "mixed" page has mixed content.
        document_composition = "heterogeneous"

    actions: dict[str, list[int]] = {}
    for page in ok:
        for item in page["recommendations"]:
            actions.setdefault(item["action"], []).append(page["number"])
    usable_digital = any(
        page["ocr"]["need"] == "unlikely" and page["ocr"]["text_layer"] == "present"
        for page in ok
    )
    recommendations = []
    for action in ACTIONS:
        if action not in actions:
            continue
        reason = _ACTION_REASONS[action]
        item: dict[str, Any] = {"action": action, "pages": actions[action]}
        if action == "run_ocr":
            item["suggested_command"] = "pdfwtf enhance INPUT_PDF --pdf_type scanned"
            if usable_digital:
                reason += _ENHANCE_NOTE
        item["reason"] = reason
        recommendations.append(item)

    return {
        "inspection": "complete" if len(ok) == len(pages) else "incomplete",
        "document_composition": document_composition,
        "pages_by_composition": by_composition,
        "pages_by_ocr_need": by_need,
        "pages_without_text": [
            page["number"] for page in ok if page["text"]["characters"] == 0
        ],
        "pages_with_suspect_text": [
            page["number"] for page in ok if page["ocr"]["quality"] == "suspect"
        ],
        "blank_pages": [p["number"] for p in ok if p["composition"] == "blank"],
        "uncertain_pages": [p["number"] for p in ok if p["composition"] == "uncertain"],
        "error_pages": [p["number"] for p in pages if p["status"] == "error"],
        "page_sizes": list(sizes.values()),
        "rotations": rotations,
        "recommendations_are_advisory": True,
        "recommendations": recommendations,
    }


def analyze_document(path: str | Path) -> dict[str, Any]:
    """Inspect a PDF and return the diagnostic report. Never modify the file."""
    path = Path(path)
    try:
        size = path.stat().st_size
        fingerprint = _fingerprint(path)
    except OSError as error:
        raise PdfInspectionError(f"The PDF cannot be read: {error}") from error
    try:
        document = fitz.open(path, filetype="pdf")
    except Exception as error:
        raise PdfInspectionError(
            "The file is not a readable PDF or is damaged."
        ) from error
    with document:
        if not document.is_pdf:
            raise PdfInspectionError("The file is not a PDF.")
        if document.needs_pass:
            raise PdfInspectionError(
                "The PDF is encrypted and requires a password. Analysis cannot "
                "inspect it."
            )
        if document.page_count == 0:
            raise PdfInspectionError("The PDF contains no pages.")
        info = _document_info(document)
        pages = []
        warnings = list(_LIMITATIONS)
        for index in range(document.page_count):
            number = index + 1
            try:
                entry = _inspect_page(document[index])
            except Exception as error:
                log.warning(
                    "Page inspection failed. Page=%d Error=%s",
                    number,
                    type(error).__name__,
                )
                entry = _error_page(error)
                warnings.append(f"Page {number} could not be inspected.")
            pages.append({"number": number, **entry})
    summary = _summary(pages)
    if info["encryption"]["encrypted"]:
        warnings.append(
            "The PDF is encrypted. It was opened without a password, and its "
            "permissions are reported."
        )
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "kind": ANALYSIS_KIND,
        "created_at": _timestamp(),
        "source": {"filename": path.name, "size_bytes": size, "sha256": fingerprint},
        "document": info,
        "summary": summary,
        "pages": pages,
        "warnings": warnings,
    }
