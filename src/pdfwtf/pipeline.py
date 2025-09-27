import hashlib
import logging  # noqa: F401
import os
import shutil
import tempfile
from pathlib import Path
import fitz  # PyMuPDF
import pikepdf
from PIL import Image
from pdfwtf.unpaper_run import get_unpaper_args, run_unpaper_simple, run_unpaper_version
from pdfwtf.scraper.tools import save_page_as_pdf

from typing import List
from .utils import (
    clear_dir,
    count_pdf_pages,
    get_output_dir_final,
    get_temp_dir,
    correct_images_orientation,
    crop_dark_background,
    images_to_pdf,
    has_no_text,
    parse_page_ranges,
    export_thumbnails,
    get_doi,
    write_json,
)

if os.environ.get("PDFWTF_TEMP_DIR"):
    os.environ["TMPDIR"] = os.environ.get("PDFWTF_TEMP_DIR")
    os.environ["TEMP"] = os.environ.get("PDFWTF_TEMP_DIR")

import ocrmypdf
from ocrmypdf.api import configure_logging, Verbosity

configure_logging(verbosity=Verbosity.quiet, progress_bar_friendly=False)


def extract_pages(
    input_pdf: Path,
    output_pdf: Path,
    pages_to_keep: List[int] = None,
    pages_to_skip: List[int] = None,
    zero_based: bool = False,
):
    """
    Create a new PDF with specified pages.
    """
    if not pages_to_keep and not pages_to_skip:
        return

    # Convert to 0-based if needed
    if not zero_based:
        if pages_to_keep:
            pages_to_keep = [p - 1 for p in pages_to_keep]
        if pages_to_skip:
            pages_to_skip = [p - 1 for p in pages_to_skip]

    try:
        new_pdf = pikepdf.Pdf.new()

        with pikepdf.open(input_pdf) as pdf:
            for i, page in enumerate(pdf.pages):
                if pages_to_keep:
                    if i in pages_to_keep:
                        new_pdf.pages.append(page)
                elif pages_to_skip:
                    if i not in pages_to_skip:
                        new_pdf.pages.append(page)

        new_pdf.save(output_pdf)
        new_pdf.close()
    except Exception as e:
        raise RuntimeError(f"Failed to extract pages: {e}")


def run_ocr(
    input_pdf,
    output_pdf,
    img_dir,
    ocrlib=None,
    lang="eng",
    layout=None,
    output_pages=None,
    rotated=False,
    debug_flag=False,
):
    if ocrlib == "pymupdf":
        run_pdfocr(img_dir, output_pdf, language=lang, debug_flag=debug_flag)

    elif ocrlib == "ocrmypdf":
        run_ocrmypdf(
            input_pdf,
            output_pdf,
            lang=lang,
            layout=layout,
            output_pages=output_pages,
            rotated=rotated,
            debug_flag=debug_flag,
        )
    else:
        shutil.copy2(input_pdf, output_pdf)


def run_pdfocr(img_dir, output_pdf, language="eng", dpi=300, debug_flag=False):
    """Run OCR with Tesseract via PyMuPDF."""

    img_dir = Path(img_dir)
    final_doc = fitz.open()

    for img_file in sorted(img_dir.glob("*.png")):
        pix = fitz.Pixmap(str(img_file))
        ocr_bytes = pix.pdfocr_tobytes(language=language)
        tmp_doc = fitz.open(stream=ocr_bytes, filetype="pdf")
        final_doc.insert_pdf(tmp_doc)
        tmp_doc.close()
        pix = None

    final_doc.save(output_pdf, clean=True, deflate=True, use_objstms=True)
    final_doc.close()


def run_ocrmypdf(
    input_pdf,
    output_pdf,
    lang="eng",
    layout=None,
    output_pages=None,
    rotated=False,
    clean_flag=True,
    debug_flag=False,
):
    """Run OCR with Tesseract via OCRmyPDF."""

    keep_temporary_files = False
    if debug_flag:
        keep_temporary_files = True

    if layout == "none":
        layout = None

    if output_pages:
        layout = None

    # Skip --output-pages and --pre-rotate with ocrmypdf
    unpaper_args = get_unpaper_args(layout=layout, as_string=True, full=False)

    rotate_pages = True
    if rotated:
        rotate_pages = False

    ocrmypdf.ocr(
        input_pdf,
        output_pdf,
        language=lang,
        force_ocr=True,
        unpaper_args=unpaper_args,
        rotate_pages=rotate_pages,
        optimize=3,
        progress_bar=False,
        deskew=True,
        fast_web_view=0.75,
        clean=clean_flag,
        clean_final=clean_flag,
        continue_on_soft_render_error=True,
        output_type="pdf",
        keep_temporary_files=keep_temporary_files,
    )


def export_images(pdf_path: "Path", out_dir: "Path", dpi=300, fext="png"):

    if out_dir.is_dir():
        clear_dir(out_dir)

    out_dir.mkdir(parents=True, exist_ok=True)

    if not pdf_path.exists():
        return

    doc = fitz.open(pdf_path)
    try:
        for i, page in enumerate(doc, start=1):
            pix = page.get_pixmap(dpi=dpi)
            out_path = out_dir / f"page_{str(i).zfill(3)}.{fext}"
            pix.save(str(out_path))  # PyMuPDF expects a str path
    finally:
        doc.close()


def export_text(pdf_path: "Path", out_dir: "Path", level="text") -> dict:

    if out_dir.is_dir():
        clear_dir(out_dir)

    out_dir.mkdir(parents=True, exist_ok=True)

    if not pdf_path.exists():
        return {}

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
    url,
    output_dir,
    input_path_prefix=None,
    extract_pages_str=None,
    skip_pages_str=None,
    ocrlib=None,
    remove_background_flag=False,
    languages="eng",
    clear_temp_flag=False,
    dpi=300,
    layout=None,
    output_pages=None,
    pre_rotate=None,
    get_doi_flag=False,
    export_images_flag=False,
    export_texts_flag=False,
    export_thumbs_flag=False,
    scan_dir="_scans",
    txt_dir="_texts",
    img_dir="_images",
    thumb_dir="_thumbs",
    debug_flag=False,
):
    metadata = {}

    temp_dir = get_temp_dir(clean=clear_temp_flag)

    if not input_pdf and url:
        handle, input_pdf, img_shot = save_page_as_pdf(url, debug=debug_flag)
    else:
        input_pdf = Path(input_pdf).resolve(strict=True)

    if not input_pdf:
        print("ERROR: No input !")
        return

    output_dir = get_output_dir_final(output_dir, input_pdf, input_path_prefix)
    output_pdf = output_dir / input_pdf.name

    base_hash = hashlib.md5(str(input_pdf).encode("utf-8")).hexdigest()[:8]

    tmp_pdf = temp_dir / f"{base_hash}_{input_pdf.stem}.tmp.pdf"
    scan_pdf = temp_dir / f"{base_hash}_{input_pdf.stem}.scan.pdf"

    images_dir = output_dir / f"{img_dir}_{input_pdf.stem}"
    thumbs_dir = output_dir / f"{thumb_dir}_{input_pdf.stem}"

    # is_scan = was_scanned_pdf(input_pdf)
    is_scan = has_no_text(input_pdf)
    rotated = False

    if debug_flag:
        print(f"[DEBUG] Using temporary dir:  {temp_dir}")
        print(f"[DEBUG] PDF was scanned:  {is_scan}")

    total_pages_in = count_pdf_pages(input_pdf)

    # Step 1: Extract pages
    if extract_pages_str:
        output_orig = output_dir / f"{input_pdf.stem}.orig.pdf"
        shutil.copy2(input_pdf, output_orig)

        pages_to_keep = parse_page_ranges(extract_pages_str, total_pages=total_pages_in)
        extract_pages(input_pdf, tmp_pdf, pages_to_keep=pages_to_keep)
    else:
        shutil.copy2(input_pdf, tmp_pdf)

    # Step 2: Process scanned documents
    if is_scan:
        # Copy the PDF to a working location
        shutil.copy2(tmp_pdf, scan_pdf)

        # Safe temp folder for exported PNGs and Unpaper outputs
        temp_subdir = Path(tempfile.mkdtemp())
        temp_subdir.mkdir(parents=True, exist_ok=True)

        # Directory where PNGs will be exported
        scans_dir = temp_subdir / scan_dir
        export_images(tmp_pdf, scans_dir, dpi=dpi, fext="png")

        pnm_subdir = temp_subdir / "_pnm"
        pnm_subdir.mkdir(parents=True, exist_ok=True)

        # Collect PNG files to process
        files_to_process = sorted(scans_dir.glob("*.png"))

        # TBD - check page orientation !
        # for files, orientation in files_to_process...
        if pre_rotate:
            rotated = True
        else:
            rotated = correct_images_orientation(files_to_process)

        background_removed = False
        if remove_background_flag:
            # background_removed = crop_dark_background(files_to_process, tool="opencv")
            background_removed = crop_dark_background(files_to_process, tool="pillow")

        if debug_flag:
            run_unpaper_version()
            print(f"[DEBUG] Rotated pages:  {rotated}")
            print(f"[DEBUG] Background removed from:  {background_removed}")

        # Get Unpaper arguments
        unpaper_args = get_unpaper_args(
            layout=layout, output_pages=output_pages, pre_rotate=pre_rotate, full=True
        )

        for infile in files_to_process:
            try:
                if output_pages:
                    temp_outfile = pnm_subdir / f"{infile.stem}_%03d.pnm"
                else:
                    temp_outfile = pnm_subdir / f"{infile.stem}.pnm"

                # Run Unpaper
                run_unpaper_simple(
                    input_file=infile,
                    output_file=temp_outfile,
                    dpi=dpi,
                    mode_args=unpaper_args,
                    tmpdir=temp_subdir,
                )

            except Exception as e:
                print(f"Unpaper failed for {infile}: {e}")
                # Optional: print the command for debugging
                if debug_flag:
                    cmd_debug = [
                        "unpaper",
                        "-v",
                        "--dpi",
                        str(round(dpi, 6)),
                    ] + unpaper_args
                    cmd_debug.extend(
                        [
                            str(infile.resolve(strict=True)),
                            str(temp_outfile.resolve(strict=True)),
                        ]
                    )
                    print(" ".join(cmd_debug))

        has_images = False
        if pnm_subdir.exists() and pnm_subdir.is_dir():
            if any(pnm_subdir.iterdir()):

                if images_dir.is_dir():
                    clear_dir(images_dir)

                images_dir.mkdir(parents=True, exist_ok=True)

                for pnm_file in pnm_subdir.glob("*.pnm"):
                    temp_outfile = pnm_subdir / f"{pnm_file.stem}.pnm"
                    final_path = images_dir / f"{pnm_file.stem}.png"
                    with Image.open(temp_outfile) as im:
                        im.save(final_path, dpi=(dpi, dpi))

                if any(images_dir.iterdir()):
                    has_images = True

        if has_images:
            images_to_pdf(images_dir, tmp_pdf, dpi=dpi, fext="png")
        else:
            shutil.copytree(scans_dir, images_dir, dirs_exist_ok=True)

    # Step 3: Perform OCR if scanned document
    if is_scan:
        run_ocr(
            tmp_pdf,
            output_pdf,
            images_dir,
            lang=languages,
            ocrlib=ocrlib,
            layout=layout,
            output_pages=output_pages,
            rotated=rotated,
            debug_flag=debug_flag,
        )
    else:
        if tmp_pdf != output_pdf:
            shutil.copy2(tmp_pdf, output_pdf)

    if tmp_pdf.exists():
        tmp_pdf.unlink()

    # Step 4: Remove pages to skip
    if skip_pages_str:
        pages_to_skip = parse_page_ranges(skip_pages_str, total_pages=total_pages_in)
        extract_pages(output_pdf, output_pdf, pages_to_skip=pages_to_skip)

    # Step 5: Extract images and thumbnails
    if output_pdf.exists():
        if export_images_flag or export_thumbs_flag:
            export_images(output_pdf, images_dir, dpi=dpi, fext="png")

        if export_thumbs_flag:
            # export_images(output_pdf, thumbs_dir, dpi=48, fext="jpg")
            export_thumbnails(images_dir, thumbs_dir)

    total_pages_out = count_pdf_pages(output_pdf)

    # Step 6: Export texts
    if (export_texts_flag or get_doi_flag) and total_pages_out > 0:
        texts_dir = output_dir / f"{txt_dir}_{input_pdf.stem}"
        text_pages = export_text(output_pdf, texts_dir)

        if text_pages:
            summary_txt = output_dir / f"{input_pdf.stem}.txt"
            with summary_txt.open("w", encoding="utf-8") as f:
                for page_num, text in text_pages.items():
                    f.write(f"--- Page {page_num} of {total_pages_out} ---\n")
                    f.write(text)
                    f.write("\n\n")

            # Step 7: Detect DOI on first page
            if get_doi_flag:
                doi_list = get_doi(texts_dir)
                metadata["doi"] = doi_list
                if doi_list:
                    print("DOI: ", doi_list)

    output_json = output_dir / f"{input_pdf.stem}.meta.json"
    write_json(metadata, output_json)
