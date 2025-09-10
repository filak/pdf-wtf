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


def remove_pages(
    input_pdf: str,
    output_pdf: str,
    pages_to_remove: List[int],
    zero_based: bool = False,
):
    """
    Create a new PDF with specified pages removed.
    """
    # Convert to 0-based if needed
    if not zero_based:
        pages_to_remove = [p - 1 for p in pages_to_remove]

    pdf = pikepdf.open(input_pdf)
    try:
        new_pdf = pikepdf.Pdf.new()
        total_pages = len(pdf.pages)

        # Add only pages not in pages_to_remove
        for i, page in enumerate(pdf.pages):
            if i not in pages_to_remove:
                new_pdf.pages.append(page)

        new_pdf.save(output_pdf)
    finally:
        pdf.close()  # explicitly close the original PDF


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
    remove_pages_str=None,
    languages="eng",
    export_images_flag=False,
    image_dir="images",
    dpi=200,
):
    """Full PDF pipeline: remove pages, OCR if needed, export images."""
    tmp_pdf = output_pdf + ".tmp.pdf"

    # Step 1: Remove pages
    if remove_pages_str:
        pages_to_remove = parse_page_ranges(remove_pages_str)
        remove_pages(input_pdf, tmp_pdf, pages_to_remove)
    else:
        tmp_pdf = input_pdf

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
