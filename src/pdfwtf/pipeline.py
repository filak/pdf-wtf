import hashlib
import os
import shutil
import tempfile
from pathlib import Path
import fitz  # PyMuPDF
from PIL import Image
from pdfwtf.unpaper_run import get_unpaper_args, get_unpaper_version, run_unpaper_simple

from .utils import (
    clear_dir,
    count_pdf_pages,
    extract_pages,
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


def run_ocr(
    input_pdf,
    output_pdf,
    img_dir,
    ocrlib=None,
    lang="eng",
    layout=None,
    output_pages=None,
    rotated=False,
    unpaper_ok=False,
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
            unpaper_ok=unpaper_ok,
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
    unpaper_ok=False,
    debug_flag=False,
):
    """Run OCR with Tesseract via OCRmyPDF."""

    keep_temporary_files = bool(debug_flag)

    if layout == "none":
        layout = None

    if output_pages:
        layout = None

    if unpaper_ok is False:
        clean_flag = False

    else:
        # Skipping --output-pages and --pre-rotate with ocrmypdf
        unpaper_args = get_unpaper_args(
            layout=layout, as_string=True, get_default=False, unpaper_ok=unpaper_ok
        )

    rotate_pages = not rotated

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


def export_images(pdf_path: Path, out_dir: Path, dpi=300, fext="png"):

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


def export_text(pdf_path: Path, out_dir: Path, level="text") -> dict:

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


def _prepare_temp_and_paths(input_pdf, debug_flag):
    temp_dir = get_temp_dir(clean=False, debug=debug_flag)
    input_pdf = Path(input_pdf).resolve(strict=True)

    return temp_dir, input_pdf


def _build_output_paths(
    input_pdf: Path, output_dir, input_path_prefix, img_dir, thumb_dir
):
    output_dir = get_output_dir_final(output_dir, input_pdf, input_path_prefix)
    output_pdf = output_dir / input_pdf.name

    base_hash = hashlib.md5(str(input_pdf).encode("utf-8")).hexdigest()[:8]
    tmp_pdf = Path(tempfile.gettempdir()) / f"{base_hash}_{input_pdf.stem}.tmp.pdf"
    scan_pdf = Path(tempfile.gettempdir()) / f"{base_hash}_{input_pdf.stem}.scan.pdf"

    images_dir = output_dir / f"{img_dir}_{input_pdf.stem}"
    thumbs_dir = output_dir / f"{thumb_dir}_{input_pdf.stem}"

    return output_dir, output_pdf, tmp_pdf, scan_pdf, images_dir, thumbs_dir


def _extract_or_copy_pages(input_pdf, tmp_pdf, extract_pages_str, total_pages_in):
    if extract_pages_str:
        output_orig = tmp_pdf.parent / f"{input_pdf.stem}.orig.pdf"
        shutil.copy2(input_pdf, output_orig)
        pages_to_keep = parse_page_ranges(extract_pages_str, total_pages=total_pages_in)
        extract_pages(input_pdf, tmp_pdf, pages_to_keep=pages_to_keep)
    else:
        shutil.copy2(input_pdf, tmp_pdf)


def _process_scanned(
    tmp_pdf,
    scan_pdf,
    dpi,
    pre_rotate,
    layout,
    output_pages,
    remove_background_flag,
    debug_flag,
    scan_dir_name,
    img_dir,
):
    # Copy working PDF
    shutil.copy2(tmp_pdf, scan_pdf)

    temp_subdir = Path(tempfile.mkdtemp())
    scans_dir = temp_subdir / scan_dir_name
    export_images(tmp_pdf, scans_dir, dpi=dpi, fext="png")

    pnm_subdir = temp_subdir / "_pnm"
    pnm_subdir.mkdir(parents=True, exist_ok=True)

    files_to_process = sorted(scans_dir.glob("*.png"))

    rotated = bool(pre_rotate) or correct_images_orientation(files_to_process)

    background_removed = False
    if remove_background_flag:
        background_removed = crop_dark_background(files_to_process, tool="pillow")

    unpaper_ok, unpaper_msg = get_unpaper_version()
    if not unpaper_ok:
        print("[WARNING] unpaper not running")

    if debug_flag:
        print(f"[DEBUG] unpaper version: {unpaper_msg}")
        print(f"[DEBUG] Rotated pages: {rotated}")
        print(f"[DEBUG] Background removed from: {background_removed}")

    unpaper_args = get_unpaper_args(
        layout=layout,
        output_pages=output_pages,
        pre_rotate=pre_rotate,
        get_default=True,
        unpaper_ok=unpaper_ok,
    )

    # Run unpaper over each image
    if unpaper_ok and unpaper_args:
        for infile in files_to_process:
            try:
                if output_pages:
                    temp_outfile = pnm_subdir / f"{infile.stem}_%03d.pnm"
                else:
                    temp_outfile = pnm_subdir / f"{infile.stem}.pnm"

                run_unpaper_simple(
                    input_file=infile,
                    output_file=temp_outfile,
                    dpi=dpi,
                    mode_args=unpaper_args,
                    tmpdir=temp_subdir,
                )

            except Exception as e:
                print(f"Unpaper failed for {infile}: {e}")
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

    # Convert PNM -> PNG and collect
    has_images = False

    if unpaper_ok:
        if pnm_subdir.exists() and any(pnm_subdir.iterdir()):
            if Path(images_dir := Path(img_dir)).is_dir():
                clear_dir(images_dir)
            Path(images_dir).mkdir(parents=True, exist_ok=True)

            for pnm_file in pnm_subdir.glob("*.pnm"):
                final_path = Path(images_dir) / f"{pnm_file.stem}.png"
                with Image.open(pnm_file) as im:
                    im.save(final_path, dpi=(dpi, dpi))

            if any(Path(images_dir).iterdir()):
                has_images = True

    if has_images:
        images_to_pdf(images_dir, tmp_pdf, dpi=dpi, fext="png")
    else:
        images_dir = img_dir
        shutil.copytree(scans_dir, images_dir, dirs_exist_ok=True)

    return unpaper_ok, tmp_pdf, images_dir


def process_pdf(
    input_pdf,
    output_dir,
    input_path_prefix=None,
    extract_pages_str=None,
    skip_pages_str=None,
    ocrlib=None,
    remove_background_flag=False,
    languages="eng",
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

    # Prepare temp dir and input PDF
    temp_dir, input_pdf = _prepare_temp_and_paths(input_pdf, debug_flag)

    if not input_pdf:
        print("ERROR: No input !")
        return

    # Build output and working paths
    output_dir, output_pdf, tmp_pdf, scan_pdf, images_dir, thumbs_dir = (
        _build_output_paths(
            input_pdf, output_dir, input_path_prefix, img_dir, thumb_dir
        )
    )

    if debug_flag:
        print(f"[DEBUG] Using temporary dir:  {temp_dir}")

    total_pages_in = count_pdf_pages(input_pdf)

    # Extract or copy pages -> tmp_pdf
    _extract_or_copy_pages(input_pdf, tmp_pdf, extract_pages_str, total_pages_in)

    # Detect if scanned
    is_scan = has_no_text(input_pdf)
    rotated = False

    if debug_flag:
        print(f"[DEBUG] PDF was scanned:  {is_scan}")

    # If scanned -> process scanned pipeline
    unpaper_ok = False
    if is_scan:
        unpaper_ok, tmp_pdf, images_dir = _process_scanned(
            tmp_pdf,
            scan_pdf,
            dpi,
            pre_rotate,
            layout,
            output_pages,
            remove_background_flag,
            debug_flag,
            scan_dir,
            images_dir,
        )

    # OCR or copy final
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
            unpaper_ok=unpaper_ok,
            debug_flag=debug_flag,
        )
    else:
        if tmp_pdf != output_pdf:
            shutil.copy2(tmp_pdf, output_pdf)

    if tmp_pdf.exists():
        tmp_pdf.unlink()

    # Remove pages to skip
    if skip_pages_str:
        pages_to_skip = parse_page_ranges(skip_pages_str, total_pages=total_pages_in)
        extract_pages(output_pdf, output_pdf, pages_to_skip=pages_to_skip)

    # Extract images and thumbnails
    if output_pdf.exists():
        if export_images_flag or export_thumbs_flag:
            export_images(output_pdf, images_dir, dpi=dpi, fext="png")

        if export_thumbs_flag:
            export_thumbnails(images_dir, thumbs_dir)

    total_pages_out = count_pdf_pages(output_pdf)

    # Export texts and detect DOI
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

            if get_doi_flag:
                doi_list = get_doi(texts_dir)
                metadata["doi"] = doi_list
                if doi_list:
                    print("DOI: ", doi_list)

    output_json = output_dir / f"{input_pdf.stem}.meta.json"
    write_json(metadata, output_json)
