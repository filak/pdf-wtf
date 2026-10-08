"""PDF content classification."""

import re

import pymupdf as fitz

PAGE_NUMBER_RE = re.compile(r"^\s*[\W_]*\d+[\W_]*\s*$")


def has_no_text(filepath: str) -> bool:
    """Return whether the document has no embedded text."""
    with fitz.open(filepath) as doc:
        return not any(page.get_text().strip() for page in doc)


def is_meaningful_text(text: str, min_chars: int = 30, min_words: int = 5) -> bool:
    text = text.strip()
    return len(text) >= min_chars and len(re.findall(r"\w+", text)) >= min_words


def page_has_large_image(page: fitz.Page, min_area_ratio: float = 0.4) -> bool:
    """Inspect every displayed image rectangle, including reused images."""
    page_area = page.rect.width * page.rect.height
    if page_area <= 0:
        return False
    for image in page.get_images(full=True):
        for rectangle in page.get_image_rects(image[0]):
            visible = rectangle & page.rect
            if visible.width * visible.height / page_area >= min_area_ratio:
                return True
    return False


def is_scanned_or_hybrid(filepath: str) -> bool:
    """Return whether any nonblank page requires OCR."""
    with fitz.open(filepath) as doc:
        for page in doc:
            lines = [
                line
                for line in page.get_text("text").splitlines()
                if not PAGE_NUMBER_RE.match(line)
            ]
            cleaned = " ".join(lines)
            has_images = bool(page.get_images(full=True))
            # A blank page does not turn an otherwise digital PDF into a scan.
            if not cleaned.strip() and not has_images and not page.get_drawings():
                continue
            if not is_meaningful_text(cleaned) or page_has_large_image(page):
                return True
    return False
