import fitz
import re

PAGE_NUMBER_RE = re.compile(r"^\s*[\W_]*\d+[\W_]*\s*$")


def has_no_text(filepath):
    """Check if a PDF has been likely scanned (no embedded text)."""
    with fitz.open(filepath) as doc:
        for page in doc:
            if page.get_text().strip():
                return False
    return True


def is_meaningful_text(text, min_chars=30, min_words=5):
    text = text.strip()
    if len(text) < min_chars:
        return False
    if len(re.findall(r"\w+", text)) < min_words:
        return False
    return True


def page_has_large_image(page, min_area_ratio=0.4):
    page_area = page.rect.width * page.rect.height
    for img in page.get_images(full=True):
        xref = img[0]
        bbox = page.get_image_bbox(xref)
        img_area = bbox.width * bbox.height
        if img_area / page_area >= min_area_ratio:
            return True
    return False


def is_scanned_or_hybrid(filepath):
    """
    Returns True for scanned OR hybrid PDFs.
    Returns False only for truly born-digital PDFs.
    """
    with fitz.open(filepath) as doc:
        for page in doc:
            text = page.get_text("text")

            # Remove trivial page-number-only lines
            lines = [
                line for line in text.splitlines() if not PAGE_NUMBER_RE.match(line)
            ]
            cleaned = " ".join(lines)

            if is_meaningful_text(cleaned):
                if not page_has_large_image(page):
                    return False  # born-digital

    return True  # scanned or hybrid
