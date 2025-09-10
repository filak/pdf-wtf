import os
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
    os.makedirs(out_dir, exist_ok=True)
    doc = fitz.open(input_pdf)
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=dpi)
        out_path = os.path.join(out_dir, f"page_{i+1}.png")
        pix.save(out_path)
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
    tmp_pdf = output_pdf + ".tmp.pdf"
    input_pikepdf = pikepdf.open(input_pdf)
    total_pages = len(input_pikepdf.pages)

    # Step 1: Extract pages
    if extract_pages_str:
        pages_to_keep = parse_page_ranges(extract_pages_str, total_pages=total_pages)
        extract_pages(input_pikepdf, tmp_pdf, pages_to_keep)
    else:
        tmp_pdf = input_pdf

    input_pikepdf.close()

    # Step 2: OCR if scanned
    if is_scanned_pdf(tmp_pdf):
        run_ocr(tmp_pdf, output_pdf, lang=languages)
    else:
        if tmp_pdf != input_pdf:
            os.rename(tmp_pdf, output_pdf)
        else:
            with open(input_pdf, "rb") as f_in, open(output_pdf, "wb") as f_out:
                f_out.write(f_in.read())

    # Step 3: Export images
    if export_images_flag:
        export_images(output_pdf, image_dir, dpi=dpi)
