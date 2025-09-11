import hashlib
import shutil
from pathlib import Path
import fitz  # PyMuPDF
import pikepdf
import ocrmypdf
from ocrmypdf.api import configure_logging, Verbosity
from typing import List
from .utils import get_output_dir_final, get_temp_dir, is_scanned_pdf, parse_page_ranges

configure_logging(verbosity=Verbosity.quiet, progress_bar_friendly=False)


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


def run_ocr(
    input_pdf, output_pdf, img_dir, lang="eng", clean_scanned_flag=False, backend=None
):
    if backend == "ocrmypdf":
        run_ocrmypdf(
            input_pdf, output_pdf, lang=lang, clean_scanned_flag=clean_scanned_flag
        )
    else:
        run_pdfocr(img_dir, output_pdf, language=lang)


def run_pdfocr(img_dir, output_pdf, language="eng", dpi=300):
    img_dir = Path(img_dir)
    final_doc = fitz.open()

    for img_file in sorted(img_dir.glob("*.png")):
        pix = fitz.Pixmap(str(img_file))
        ocr_bytes = pix.pdfocr_tobytes(language=language)
        tmp_doc = fitz.open(stream=ocr_bytes, filetype="pdf")
        final_doc.insert_pdf(tmp_doc)
        tmp_doc.close()
        pix = None

    final_doc.save(output_pdf)
    final_doc.close()


def run_ocrmypdf(input_pdf, output_pdf, lang="eng", clean_scanned_flag=False):
    """Run OCR with Tesseract via OCRmyPDF."""
    if clean_scanned_flag:
        ocrmypdf.ocr(
            input_pdf,
            output_pdf,
            language=lang,
            force_ocr=True,
            rotate_pages=True,
            optimize=0,
            progress_bar=False,
            deskew=True,
            fast_web_view=False,
            clean=True,
            clean_final=True,
            continue_on_soft_render_error=True,
            output_type="pdf",
        )
    else:
        ocrmypdf.ocr(
            input_pdf,
            output_pdf,
            language=lang,
            force_ocr=True,
            deskew=True,
            fast_web_view=False,
            rotate_pages=True,
            optimize=0,
            progress_bar=False,
            continue_on_soft_render_error=True,
            output_type="pdf",
        )


def export_images(pdf_path, out_dir, dpi=300):

    out_dir = Path(out_dir)

    if out_dir.exists():
        shutil.rmtree(out_dir)

    out_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    try:
        for i, page in enumerate(doc, start=1):
            pix = page.get_pixmap(dpi=dpi)
            out_path = out_dir / f"page_{str(i).zfill(3)}.png"
            pix.save(str(out_path))  # PyMuPDF expects a str path
    finally:
        doc.close()


def export_text(pdf_path, out_dir, level="text") -> dict:

    out_dir = Path(out_dir)

    if out_dir.exists():
        shutil.rmtree(out_dir)

    out_dir.mkdir(parents=True, exist_ok=True)  # ensure output dir exists

    doc = fitz.open(pdf_path)
    text_pages = {}

    try:
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text(level)
            text_pages[page_num + 1] = text

            cnt = page_num + 1

            out_path = out_dir / f"page_{str(cnt).zfill(3)}.txt"
            out_path.write_text(text, encoding="utf-8")

    finally:
        doc.close()

    return text_pages


def process_pdf(
    input_pdf,
    output_dir,
    input_path_prefix=None,
    extract_pages_str=None,
    languages="eng",
    clean_scanned_flag=False,
    clear_temp_flag=False,
    export_texts_flag=False,
    txt_dir="_txt",
    dpi=300,
    img_dir="_img",
):
    """Full PDF pipeline: remove pages, OCR if needed, export images."""
    input_pdf = Path(input_pdf).resolve()

    output_dir = get_output_dir_final(output_dir, input_pdf, input_path_prefix)
    output_pdf = output_dir / input_pdf.name

    temp_dir = get_temp_dir(clean=clear_temp_flag)

    base_hash = hashlib.md5(str(input_pdf).encode("utf-8")).hexdigest()[:8]
    tmp_pdf = temp_dir / f"{base_hash}_{input_pdf.stem}.tmp.pdf"

    # try:
    input_pikepdf = pikepdf.open(input_pdf)
    total_pages_in = len(input_pikepdf.pages)

    # Step 1: Extract pages
    if extract_pages_str:
        pages_to_keep = parse_page_ranges(extract_pages_str, total_pages=total_pages_in)
        extract_pages(input_pikepdf, str(tmp_pdf), pages_to_keep)
    else:
        shutil.copy2(input_pdf, tmp_pdf)

    input_pikepdf.close()

    is_scan = is_scanned_pdf(tmp_pdf)

    # Step 2: Extract images
    images_dir = None
    if tmp_pdf.exists():
        images_dir = output_dir / f"{img_dir}_{input_pdf.stem}"
        images_dir.mkdir(parents=True, exist_ok=True)
        export_images(tmp_pdf, images_dir, dpi=dpi)

    # Step 3: OCR if scanned
    if is_scan:

        # Use unpaper via Docker to enhance the images before OCR
        # Try to use https://pymupdf.readthedocs.io/en/latest/recipes-ocr.html
        # instead of ocrmypdf for better performance

        if images_dir:
            # TBD: unpaper call
            pass

        run_ocr(
            tmp_pdf,
            output_pdf,
            images_dir,
            lang=languages,
            clean_scanned_flag=clean_scanned_flag,
            # backend="ocrmypdf",
        )
    else:
        if tmp_pdf.resolve() != output_pdf:
            shutil.copy2(tmp_pdf, output_pdf)

    total_pages_out = 0

    if output_pdf.exists():
        print(f"Output PDF :  {str(output_pdf)}")
        output_pikepdf = pikepdf.open(output_pdf)
        total_pages_out = len(output_pikepdf.pages)
        output_pikepdf.close()

    # Step 4: Extract texts
    if export_texts_flag and total_pages_out > 0:
        texts_dir = output_dir / f"{txt_dir}_{input_pdf.stem}"
        texts_dir.mkdir(parents=True, exist_ok=True)
        text_pages = export_text(output_pdf, texts_dir)

        if text_pages:
            summary_txt = output_dir / f"{input_pdf.stem}.txt"
            with summary_txt.open("w", encoding="utf-8") as f:
                for page_num, text in text_pages.items():
                    f.write(f"--- Page {page_num} of {total_pages_out} ---\n")
                    f.write(text)
                    f.write("\n\n")
