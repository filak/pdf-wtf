import hashlib
import os
import shutil
from pathlib import Path
import fitz  # PyMuPDF
import pikepdf
import ocrmypdf
from typing import List
from .utils import find_project_root, get_temp_dir, compute_relative_output, parse_page_ranges


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
    output_dir=None,
    relative_marker=None,
    extract_pages_str=None,
    languages="eng",
    export_images_flag=False,
    dpi=200,
    rotate_images_flag=False,
    clear_temp_flag=False,
    images_dir="_images",
):
    """Full PDF pipeline: remove pages, OCR if needed, export images."""
    input_pdf = Path(input_pdf).resolve()

    # Determine output directory: argument > env var > default
    if output_dir is not None:
        output_dir = Path(output_dir).expanduser().resolve()
    else:
        base_dir = find_project_root()
        output_dir = base_dir / "instance" / "_data" / "out-pdf"

    if relative_marker:
        marker_path = Path(relative_marker).resolve()
        final_output_dir = compute_relative_output(input_pdf, marker_path, output_dir)
    else:
        final_output_dir = output_dir

    final_output_dir.mkdir(parents=True, exist_ok=True)
    output_pdf = final_output_dir / input_pdf.name

    output_dir.mkdir(parents=True, exist_ok=True)

    # Final output filename = same as input, inside final_output_dir
    output_pdf = final_output_dir / input_pdf.name

    temp_dir = get_temp_dir(clean=clear_temp_flag)

    base_hash = hashlib.md5(str(input_pdf).encode("utf-8")).hexdigest()[:8]
    tmp_pdf = temp_dir / f"{base_hash}_{input_pdf.stem}.tmp.pdf"

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
            shutil.copy2(input_pdf, tmp_pdf)

        input_pikepdf.close()

        # Step 2: OCR if scanned
        if is_scanned_pdf(tmp_pdf):
            run_ocr(tmp_pdf, output_pdf, lang=languages)
        else:
            if tmp_pdf.resolve() != output_pdf:
                shutil.move(tmp_pdf, output_pdf)

        # Step 3: Export images if requested
        if export_images_flag:
            images_dir = final_output_dir / f"{input_pdf.stem}_{images_dir}"
            images_dir.mkdir(parents=True, exist_ok=True)
            export_images(output_pdf, images_dir, dpi=dpi)

    finally:
        if tmp_pdf.exists():
            try:
                tmp_pdf.unlink()
            except Exception:
                pass
