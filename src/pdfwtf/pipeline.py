"""Synchronous PDF processing and export coordination."""

import logging
from pathlib import Path
import shutil
import tempfile
from typing import Any

import ocrmypdf
from PIL import Image
import pymupdf as fitz

from .configuration import load_config
from .container_analysis import (
    UNIT_TYPES,
    analyze_container,
    direct_unit_plan,
    export_units,
    load_plan,
    publish_unit_directory,
    validate_plan,
    write_json_document,
)
from .page_metadata import build_page_metadata
from .unpaper_run import get_unpaper_args, get_unpaper_version, run_unpaper_simple
from .utils.analyze import is_scanned_or_hybrid
from .utils.common import (
    clear_dir,
    count_pdf_pages,
    extract_pages,
    get_output_dir,
    get_output_dir_final,
    get_temp_dir,
    correct_images_orientation,
    crop_dark_background,
    images_to_pdf,
    parse_page_ranges,
    export_thumbnails,
    get_doi,
    write_json,
    page_sort_key,
)

log = logging.getLogger(__name__)


class ProcessingError(RuntimeError):
    """Document processing could not complete safely."""


def run_ocr(
    input_pdf: Path,
    output_pdf: Path,
    img_dir: Path,
    ocrlib: str | None = None,
    lang: str = "eng",
    layout: str | None = None,
    output_pages: str | None = None,
    rotated: bool = False,
    unpaper_ok: bool = False,
    debug_flag: bool = False,
    dpi: int = 300,
    optimize: int = 0,
) -> None:
    if ocrlib == "pymupdf":
        run_pdfocr(img_dir, output_pdf, language=lang, dpi=dpi, debug_flag=debug_flag)
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
            optimize=optimize,
        )
    elif ocrlib is None:
        shutil.copy2(input_pdf, output_pdf)
    else:
        raise ValueError("Unsupported OCR backend.")


def run_pdfocr(
    img_dir: Path,
    output_pdf: Path,
    language: str = "eng",
    dpi: int = 300,
    debug_flag: bool = False,
) -> None:
    """Run Tesseract through PyMuPDF on the prepared images."""
    images = sorted(Path(img_dir).glob("*.png"), key=page_sort_key)
    if not images:
        raise ProcessingError("No prepared pages are available for OCR.")
    with fitz.open() as final_doc:
        for image in images:
            pix = fitz.Pixmap(str(image))
            pix.set_dpi(dpi, dpi)
            data = pix.pdfocr_tobytes(language=language)
            with fitz.open(stream=data, filetype="pdf") as page_doc:
                final_doc.insert_pdf(page_doc)
        final_doc.save(output_pdf, clean=True, deflate=True, use_objstms=True)


def run_ocrmypdf(
    input_pdf: Path,
    output_pdf: Path,
    lang: str = "eng",
    layout: str | None = None,
    output_pages: str | None = None,
    rotated: bool = False,
    clean_flag: bool = True,
    unpaper_ok: bool = False,
    debug_flag: bool = False,
    optimize: int = 0,
) -> None:
    """Run Tesseract through OCRmyPDF."""
    unpaper_args = get_unpaper_args(
        layout=None if output_pages or layout == "none" else layout,
        as_string=True,
        unpaper_ok=unpaper_ok,
    )
    ocrmypdf.ocr(
        input_pdf,
        output_pdf,
        language=lang,
        force_ocr=True,
        unpaper_args=unpaper_args,
        rotate_pages=not rotated,
        optimize=optimize,
        progress_bar=False,
        deskew=True,
        fast_web_view=0.75,
        clean=clean_flag and unpaper_ok,
        clean_final=clean_flag and unpaper_ok,
        continue_on_soft_render_error=False,
        output_type="pdf",
        keep_temporary_files=False,
    )


def export_images(
    pdf_path: Path, out_dir: Path, dpi: int = 300, fext: str = "png"
) -> None:
    if not pdf_path.is_file():
        raise FileNotFoundError("The PDF to export does not exist.")
    out_dir.mkdir(parents=True, exist_ok=True)
    clear_dir(out_dir)
    with fitz.open(pdf_path) as doc:
        for number, page in enumerate(doc, start=1):
            pix = page.get_pixmap(dpi=dpi)
            pix.save(str(out_dir / f"page_{number:03d}.{fext}"))


def export_text(pdf_path: Path, out_dir: Path, level: str = "text") -> dict[int, str]:
    if not pdf_path.is_file():
        raise FileNotFoundError("The PDF to export does not exist.")
    out_dir.mkdir(parents=True, exist_ok=True)
    clear_dir(out_dir)
    texts = {}
    with fitz.open(pdf_path) as doc:
        for number, page in enumerate(doc, start=1):
            text = page.get_text(level)
            texts[number] = text
            (out_dir / f"page_{number:03d}.txt").write_text(text, encoding="utf-8")
    return texts


def _process_scanned(
    working_pdf: Path,
    workspace: Path,
    dpi: int,
    pre_rotate: int | None,
    layout: str | None,
    output_pages: str | None,
    remove_background_flag: bool,
    scan_dir_name: str,
) -> tuple[Path, bool]:
    scans_dir = workspace / scan_dir_name
    export_images(working_pdf, scans_dir, dpi=dpi)
    files = sorted(scans_dir.glob("*.png"), key=page_sort_key)
    if not files:
        raise ProcessingError("The selected PDF contains no pages.")
    rotated = False
    if pre_rotate is None:
        rotated = correct_images_orientation(files)
    if remove_background_flag:
        crop_dark_background(files, tool="pillow")

    available, _ = get_unpaper_version()
    requested = (
        layout not in (None, "none")
        or output_pages is not None
        or pre_rotate is not None
    )
    if requested and not available:
        raise ProcessingError("The requested scan options require unpaper.")
    images_dir = scans_dir
    if available:
        args = get_unpaper_args(
            layout=layout,
            output_pages=output_pages,
            pre_rotate=pre_rotate,
            get_default=True,
            unpaper_ok=True,
        )
        pnm_dir = workspace / "_pnm"
        pnm_dir.mkdir()
        images_dir = workspace / "_prepared"
        images_dir.mkdir()
        expected = int(output_pages or "1")
        for infile in files:
            outfile = pnm_dir / (
                f"{infile.stem}_%03d.pnm" if output_pages else f"{infile.stem}.pnm"
            )
            run_unpaper_simple(infile, outfile, workspace, dpi=dpi, mode_args=args)
            generated = sorted(
                (
                    pnm_dir.glob(f"{infile.stem}_*.pnm")
                    if output_pages
                    else pnm_dir.glob(f"{infile.stem}.pnm")
                ),
                key=page_sort_key,
            )
            if len(generated) != expected:
                raise ProcessingError("unpaper did not produce all expected pages.")
            for image_path in generated:
                with Image.open(image_path) as image:
                    image.save(images_dir / f"{image_path.stem}.png", dpi=(dpi, dpi))
        rotated = rotated or pre_rotate is not None
    else:
        log.warning("unpaper is unavailable. Optional scan cleaning is disabled.")
    # Both backends receive exactly the same prepared pages.
    images_to_pdf(images_dir, working_pdf, dpi=dpi)
    return images_dir, rotated


def _publish_pdf(source: Path, destination: Path) -> None:
    # Stage on the destination filesystem before replacing existing output.
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, prefix=".pdfwtf-", suffix=".pdf", delete=False
    ) as stream:
        staged = Path(stream.name)
    try:
        shutil.copy2(source, staged)
        staged.replace(destination)
    finally:
        staged.unlink(missing_ok=True)


def process_pdf(
    input_pdf: str | Path,
    output_dir: str | Path | None,
    input_path_prefix: str | Path | None = None,
    extract_pages_str: str | None = None,
    skip_pages_str: str | None = None,
    ocrlib: str | None = None,
    remove_background_flag: bool = False,
    languages: str = "eng",
    dpi: int = 300,
    layout: str | None = None,
    output_pages: str | None = None,
    pre_rotate: int | None = None,
    get_doi_flag: bool = False,
    export_format: str = "png",
    export_images_flag: bool = False,
    export_texts_flag: bool = False,
    export_thumbs_flag: bool = False,
    scan_dir: str = "_scans",
    txt_dir: str = "_texts",
    img_dir: str = "_images",
    thumb_dir: str = "_thumbs",
    debug_flag: bool = False,
    born_digital_flag: bool = False,
    optimize: int = 0,
    no_pdf_flag: bool = False,
    export_json_flag: bool = False,
    analysis_flag: bool = False,
    unit_types: tuple[str, ...] = (),
    document_type: str | None = None,
    plan_path: str | Path | None = None,
    export_html_flag: bool = False,
    pdf_type: str = "auto",
    export_unit_pdfs_flag: bool = False,
) -> None:
    """Process one PDF without replacing its source or publishing partial OCR."""
    if pdf_type not in {"auto", "born-digital", "scanned"}:
        raise ValueError("PDF type must be auto, born-digital, or scanned.")
    if born_digital_flag and pdf_type == "scanned":
        raise ValueError("born_digital_flag cannot be combined with pdf_type scanned.")
    born_digital_flag = born_digital_flag or pdf_type == "born-digital"
    if analysis_flag and plan_path is not None:
        raise ValueError("--analysis and --plan are mutually exclusive.")
    if export_unit_pdfs_flag and plan_path is None:
        raise ProcessingError("Unit PDF export requires a reviewed plan.")
    unit_workflow = analysis_flag or plan_path is not None or export_html_flag
    if set(unit_types) - UNIT_TYPES:
        raise ProcessingError("Unknown unit type in --unit_type.")
    if unit_types and analysis_flag:
        raise ProcessingError("--unit_type is only supported for unit export.")
    if unit_types and not unit_workflow:
        raise ProcessingError("--unit_type requires --plan or --get-html for export.")
    if unit_workflow and not born_digital_flag:
        raise ValueError(
            "Container analysis and unit export currently require --pdf_type born-digital."
        )
    if analysis_flag and any(
        (
            extract_pages_str,
            skip_pages_str,
            no_pdf_flag,
            export_json_flag,
            get_doi_flag,
            export_images_flag,
            export_texts_flag,
            export_thumbs_flag,
            export_html_flag,
        )
    ):
        raise ValueError(
            "--analysis cannot be combined with page selection or export options."
        )
    if plan_path is not None and (extract_pages_str or skip_pages_str):
        raise ValueError("--plan cannot be combined with --extract or --remove.")
    if export_html_flag and plan_path is None and document_type not in (None, "unit"):
        raise ValueError("Direct --get-html processing requires --doc_type unit.")
    if no_pdf_flag and not any(
        (
            export_json_flag,
            get_doi_flag,
            export_images_flag,
            export_texts_flag,
            export_thumbs_flag,
            plan_path is not None,
            export_html_flag,
        )
    ):
        raise ValueError(
            "No output selected. Select --get-meta, --get-doi, --get-img, "
            "--get-text, --get-thumb, --plan, or --get-html."
        )
    config = load_config()
    if not input_pdf:
        raise ValueError("An input PDF is required.")
    input_pdf = Path(input_pdf).resolve(strict=True)
    if not input_pdf.is_file():
        raise ValueError("The input PDF must be a file.")
    if ocrlib not in (None, "ocrmypdf", "pymupdf"):
        raise ValueError("Unsupported OCR backend.")
    if not isinstance(optimize, int) or not 0 <= optimize <= 3:
        raise ValueError("Optimization must be an integer between 0 and 3.")
    if export_format != "png":
        raise ValueError("Unsupported page image format.")
    if not 72 <= dpi <= 1200:
        raise ValueError("DPI must be between 72 and 1200.")
    if pre_rotate not in (None, 0, 90, 180, 270):
        raise ValueError("Unsupported pre-rotation angle.")
    if layout not in (None, "none", "single", "double"):
        raise ValueError("Unsupported scan layout.")
    if output_pages not in (None, "1", "2"):
        raise ValueError("Unsupported output page count.")
    if born_digital_flag and (
        remove_background_flag
        or layout not in (None, "none")
        or output_pages is not None
        or pre_rotate is not None
    ):
        raise ValueError(
            "--pdf_type born-digital cannot be combined with --remove-bg, --layout "
            "(single or double), --output-pages, or --pre-rotate."
        )
    for name in (scan_dir, txt_dir, img_dir, thumb_dir):
        if not name or Path(name).name != name or name in (".", ".."):
            raise ValueError("Export directory names must be single path components.")
    output_dir = get_output_dir_final(
        get_output_dir(output_dir, config=config), input_pdf, input_path_prefix
    )
    output_pdf = output_dir / input_pdf.name
    if (
        not no_pdf_flag
        and not analysis_flag
        and (
            output_pdf.resolve() == input_pdf
            or (output_pdf.exists() and output_pdf.samefile(input_pdf))
        )
    ):
        raise ValueError("The output PDF must differ from the input PDF.")

    export_dirs = []
    if plan_path is not None or export_html_flag:
        export_dirs.append(output_dir / f"_units_{input_pdf.stem}")
    if export_images_flag or export_thumbs_flag:
        export_dirs.append(output_dir / f"{img_dir}_{input_pdf.stem}")
    if export_thumbs_flag:
        export_dirs.append(output_dir / f"{thumb_dir}_{input_pdf.stem}")
    if export_texts_flag:
        export_dirs.append(output_dir / f"{txt_dir}_{input_pdf.stem}")
    if any(input_pdf.is_relative_to(path.resolve()) for path in export_dirs):
        raise ValueError("Export directories must not contain the input PDF.")

    with tempfile.TemporaryDirectory(
        prefix="pdfwtf-", dir=get_temp_dir(config=config)
    ) as temp:
        workspace = Path(temp)
        job_log = logging.LoggerAdapter(log, {"correlation_id": workspace.name})
        total_pages = count_pdf_pages(input_pdf)
        job_log.debug("Processing started. Input pages=%d.", total_pages)
        if total_pages == 0:
            raise ProcessingError("The input PDF contains no pages.")
        if analysis_flag:
            analysis = analyze_container(input_pdf, document_type or "auto")
            write_json_document(
                analysis, output_dir / f"{input_pdf.stem}.analysis.json"
            )
            job_log.info("Container analysis completed.")
            return

        unit_plan = None
        selected_units = None
        if plan_path is not None:
            unit_plan = load_plan(Path(plan_path))
            selected_units = validate_plan(unit_plan, input_pdf, document_type)
        elif export_html_flag:
            unit_plan = direct_unit_plan(input_pdf)
            selected_units = validate_plan(unit_plan, input_pdf, "unit")
        staged_units = None
        if unit_plan is not None and selected_units is not None:
            if unit_types:
                selected_units = [
                    unit for unit in selected_units if unit["type"] in unit_types
                ]
            staged_units = workspace / "unit_exports"
            export_units(
                input_pdf,
                unit_plan,
                selected_units,
                staged_units,
                include_html=export_html_flag,
                include_pdf=export_unit_pdfs_flag,
            )
        pages_to_keep = (
            [page["input_page"] for page in unit_plan["pages"] if page["selected"]]
            if unit_plan is not None
            else (
                parse_page_ranges(extract_pages_str, total_pages)
                if extract_pages_str is not None
                else []
            )
        )
        pages_to_skip = (
            parse_page_ranges(skip_pages_str, total_pages)
            if skip_pages_str is not None
            else []
        )
        selection_requested = unit_plan is not None or extract_pages_str is not None
        selected = (
            pages_to_keep if selection_requested else list(range(1, total_pages + 1))
        )
        if not no_pdf_flag and not (set(selected) - set(pages_to_skip)):
            raise ValueError("Page removal must leave at least one page.")

        def prepare(source: Path, directory: Path, split: str | None) -> Path:
            directory.mkdir()
            working_pdf = directory / "working.pdf"
            result = directory / "result.pdf"
            shutil.copy2(source, working_pdf)
            if not born_digital_flag and (
                pdf_type == "scanned" or is_scanned_or_hybrid(working_pdf)
            ):
                images, rotated = _process_scanned(
                    working_pdf,
                    directory,
                    dpi,
                    pre_rotate,
                    layout,
                    split,
                    remove_background_flag,
                    scan_dir,
                )
                expected_pages = count_pdf_pages(working_pdf)
                run_ocr(
                    working_pdf,
                    result,
                    images,
                    ocrlib=ocrlib,
                    lang=languages,
                    rotated=rotated,
                    unpaper_ok=False,
                    debug_flag=debug_flag,
                    dpi=dpi,
                    optimize=optimize,
                )
                if count_pdf_pages(result) != expected_pages:
                    raise ProcessingError(
                        "OCR did not preserve the prepared page count."
                    )
            else:
                shutil.copy2(working_pdf, result)
            return result

        derivatives = any(
            (
                export_json_flag,
                get_doi_flag,
                export_images_flag,
                export_texts_flag,
                export_thumbs_flag,
            )
        )
        result_pdf = (
            prepare(input_pdf, workspace / "derivatives", None) if derivatives else None
        )
        if result_pdf is not None and count_pdf_pages(result_pdf) != total_pages:
            raise ProcessingError("Derivative processing must preserve input indices.")
        pdf_result = None
        if not no_pdf_flag:
            if result_pdf is not None and output_pages in (None, "1"):
                pdf_result = workspace / "pdf_result.pdf"
                if selection_requested:
                    extract_pages(result_pdf, pdf_result, pages_to_keep=selected)
                else:
                    shutil.copy2(result_pdf, pdf_result)
            else:
                selected_pdf = workspace / "selected.pdf"
                if selection_requested:
                    extract_pages(input_pdf, selected_pdf, pages_to_keep=selected)
                else:
                    shutil.copy2(input_pdf, selected_pdf)
                pdf_result = prepare(selected_pdf, workspace / "pdf", output_pages)
            # A split input page maps to all of its generated PDF pages.
            count = count_pdf_pages(pdf_result)
            factor, remainder = divmod(count, len(selected))
            if remainder or factor < 1:
                raise ProcessingError("PDF processing did not preserve page mapping.")
            remove = [
                position * factor + part + 1
                for position, original in enumerate(selected)
                if original in pages_to_skip
                for part in range(factor)
            ]
            if remove:
                extract_pages(pdf_result, pdf_result, pages_to_skip=remove)

        images_dir = output_dir / f"{img_dir}_{input_pdf.stem}"
        thumbs_dir = output_dir / f"{thumb_dir}_{input_pdf.stem}"
        if export_images_flag or export_thumbs_flag:
            export_images(result_pdf, images_dir, dpi=dpi)
        if export_thumbs_flag:
            export_thumbnails(images_dir, thumbs_dir)
        metadata: dict[str, Any] = {
            "input": str(input_pdf),
            "output": str(output_pdf.resolve()) if not no_pdf_flag else None,
            "doi": [],
        }
        if export_json_flag or get_doi_flag:
            metadata["pages"] = build_page_metadata(
                result_pdf, pages_to_keep, pages_to_skip
            )
        if export_texts_flag or get_doi_flag:
            texts_dir = (
                output_dir / f"{txt_dir}_{input_pdf.stem}"
                if export_texts_flag
                else workspace / "doi_texts"
            )
            texts = export_text(result_pdf, texts_dir)
            if export_texts_flag:
                with (output_dir / f"{input_pdf.stem}.txt").open(
                    "w", encoding="utf-8"
                ) as stream:
                    for number, text in texts.items():
                        stream.write(
                            f"--- Page {number} of {len(texts)} ---\n{text}\n\n"
                        )
            if get_doi_flag:
                metadata["doi"] = get_doi(texts_dir)
        if export_json_flag or get_doi_flag:
            write_json(metadata, output_dir / f"{input_pdf.stem}.meta.json")
        if staged_units is not None:
            publish_unit_directory(
                staged_units, output_dir / f"_units_{input_pdf.stem}"
            )
        if not no_pdf_flag:
            _publish_pdf(pdf_result, output_pdf)
        job_log.info("PDF processing completed.")
