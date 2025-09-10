import hashlib
import shutil
from pathlib import Path
import fitz  # PyMuPDF
import pikepdf
import ocrmypdf
from typing import List
from .utils import parse_page_ranges


def is_scanned_pdf(filepath):
    """Check if a PDF is likely scanned (no embedded text)."""
    with fitz.open(filepath) as doc:
        for page in doc:
            if page.get_text().strip():
                return False
    return True


def extract_pages(
    input_pikepdf: str,
    output_pdf: str,
    pages_to_keep: List[int],
    zero_based: bool = False,
):
    """
    Create a new PDF with specified pages.
    """
    # Convert to 0-based if needed
    if not zero_based:
        pages_to_keep = [p - 1 for p in pages_to_keep]

    try:
        new_pdf = pikepdf.Pdf.new()

        # Add only pages not in pages_to_remove
        for i, page in enumerate(input_pikepdf.pages):
            if i in pages_to_keep:
                new_pdf.pages.append(page)

        new_pdf.save(output_pdf)
        new_pdf.close()
    except Exception as e:
        raise RuntimeError(f"Failed to extract pages: {e}")


def run_ocr(input_pdf, output_pdf, lang="eng"):
    """Run OCR with Tesseract via OCRmyPDF."""
    ocrmypdf.ocr(input_pdf, output_pdf, language=lang, force_ocr=True)


def export_images(input_pdf, out_dir, dpi=200):
    """Export PDF pages as PNG images."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(input_pdf)
    try:
        for i, page in enumerate(doc, start=1):
            pix = page.get_pixmap(dpi=dpi)
            out_path = out_dir / f"page_{i}.png"
            pix.save(str(out_path))  # PyMuPDF expects a str path
    finally:
        doc.close()


def process_pdf(
    input_pdf,
    output_pdf,
    extract_pages_str=None,
    languages="eng",
    export_images_flag=False,
    image_dir="images",
    dpi=200,
):
    """Full PDF pipeline: remove pages, OCR if needed, export images."""

    # Convert to Path objects
    input_pdf = Path(input_pdf).resolve()
    output_pdf = Path(output_pdf).resolve()

    # Ensure temp dir exists relative to this script/module
    base_dir = Path(__file__).resolve().parent
    temp_dir = base_dir / "instance" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Create tmp filename with hash prefix
    base_hash = hashlib.md5(f"{input_pdf}{output_pdf}".encode("utf-8")).hexdigest()[:8]
    tmp_pdf = temp_dir / f"{base_hash}_{output_pdf.name}.tmp.pdf"

    try:
        input_pikepdf = pikepdf.open(input_pdf)
        total_pages = len(input_pikepdf.pages)

        # Step 1: Extract pages
        if extract_pages_str:
            pages_to_keep = parse_page_ranges(
                extract_pages_str, total_pages=total_pages
            )
            extract_pages(input_pikepdf, str(tmp_pdf), pages_to_keep)
        else:
            shutil.copy2(input_pdf, tmp_pdf)  # safe copy to tmp

        input_pikepdf.close()

        # Step 2: OCR if scanned
        if is_scanned_pdf(tmp_pdf):
            run_ocr(tmp_pdf, output_pdf, lang=languages)
        else:
            if tmp_pdf.resolve() != output_pdf:
                shutil.move(tmp_pdf, output_pdf)
            # else already correct location

    finally:
        # Cleanup tmp file if it still exists
        if tmp_pdf.exists():
            try:
                tmp_pdf.unlink()
            except Exception:
                pass

    # Step 3: Export images
    if export_images_flag:
        export_images(output_pdf, image_dir, dpi=dpi)
