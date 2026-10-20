"""Synchronous enhancement, partitioning, and export operations."""

from dataclasses import dataclass
from datetime import datetime, UTC
import inspect
import logging
from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable

import ocrmypdf
from PIL import Image
import pymupdf as fitz

from .configuration import (
    COMMAND_ANALYZE,
    COMMAND_ENHANCE,
    COMMAND_EXPORT,
    COMMAND_PARTITION,
    load_config,
    AppConfig,
)
from .container_partition import (
    SCHEMA_VERSION,
    UNIT_TYPES,
    partition_container,
    export_document_html,
    export_units,
    load_plan,
    publish_unit_directory,
    source_identity,
    validate_plan,
    write_json_document,
)
from .diagnostics import analyze_document
from .llm_tasks import LLMReport, run_after_unit_html, validate_task_selection
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
    replace_file,
)

log = logging.getLogger(__name__)

# Enhancement writes the prepared PDF as "<input stem>.prepared.pdf".
PREPARED_SUFFIX = ".prepared.pdf"


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
        replace_file(staged, destination)
    finally:
        staged.unlink(missing_ok=True)


@dataclass(frozen=True)
class EnhanceResult:
    """Files written by one successful enhancement."""

    pdf: Path
    report: Path
    llm_report: LLMReport | None = None


@dataclass(frozen=True)
class _PageSettings:
    pdf_type: str
    ocrlib: str | None
    languages: str
    dpi: int
    optimize: int
    pre_rotate: int | None
    layout: str | None
    remove_background_flag: bool
    scan_dir: str
    debug_flag: bool


def _resolve_input(input_pdf: str | Path) -> Path:
    if not input_pdf:
        raise ValueError("An input PDF is required.")
    path = Path(input_pdf).resolve(strict=True)
    if not path.is_file():
        raise ValueError("The input PDF must be a file.")
    return path


def _check_dpi(dpi: int) -> None:
    if not 72 <= dpi <= 1200:
        raise ValueError("DPI must be between 72 and 1200.")


def _check_dir_names(*names: str) -> None:
    for name in names:
        if not name or Path(name).name != name or name in (".", ".."):
            raise ValueError("Export directory names must be single path components.")


def _final_output_dir(
    output_dir: str | Path | None,
    input_pdf: Path,
    input_path_prefix: str | Path | None,
    config: AppConfig,
) -> Path:
    return get_output_dir_final(
        get_output_dir(output_dir, config=config), input_pdf, input_path_prefix
    )


def _input_page_count(input_pdf: Path) -> int:
    total_pages = count_pdf_pages(input_pdf)
    if total_pages == 0:
        raise ProcessingError("The input PDF contains no pages.")
    return total_pages


def _prepare_pages(
    source: Path, directory: Path, split: str | None, settings: _PageSettings
) -> tuple[Path, bool]:
    """Run scan preparation and OCR when needed. Return the result and scan state."""
    directory.mkdir()
    working_pdf = directory / "working.pdf"
    result = directory / "result.pdf"
    shutil.copy2(source, working_pdf)
    scanned = settings.pdf_type != "born-digital" and (
        settings.pdf_type == "scanned" or is_scanned_or_hybrid(working_pdf)
    )
    if not scanned:
        # Born-digital pages are copied. They are not rasterized.
        shutil.copy2(working_pdf, result)
        return result, False
    images, rotated = _process_scanned(
        working_pdf,
        directory,
        settings.dpi,
        settings.pre_rotate,
        settings.layout,
        split,
        settings.remove_background_flag,
        settings.scan_dir,
    )
    expected_pages = count_pdf_pages(working_pdf)
    run_ocr(
        working_pdf,
        result,
        images,
        ocrlib=settings.ocrlib,
        lang=settings.languages,
        rotated=rotated,
        unpaper_ok=False,
        debug_flag=settings.debug_flag,
        dpi=settings.dpi,
        optimize=settings.optimize,
    )
    if count_pdf_pages(result) != expected_pages:
        raise ProcessingError("OCR did not preserve the prepared page count.")
    return result, True


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def enhance_pdf(
    input_pdf: str | Path,
    output_dir: str | Path | None,
    *,
    config: AppConfig | None = None,
    input_path_prefix: str | Path | None = None,
    extract_pages_str: str | None = None,
    skip_pages_str: str | None = None,
    pdf_type: str = "auto",
    ocrlib: str | None = None,
    languages: str = "eng",
    dpi: int = 300,
    optimize: int = 0,
    layout: str | None = None,
    output_pages: str | None = None,
    pre_rotate: int | None = None,
    remove_background_flag: bool = False,
    llm_tasks: tuple[str, ...] = (),
    debug_flag: bool = False,
    scan_dir: str = "_scans",
) -> EnhanceResult:
    """Write a prepared PDF and an enhancement report. Never modify the input."""
    if pdf_type not in {"auto", "born-digital", "scanned"}:
        raise ValueError("PDF type must be auto, born-digital, or scanned.")
    if ocrlib not in (None, "ocrmypdf", "pymupdf"):
        raise ValueError("Unsupported OCR backend.")
    if not isinstance(optimize, int) or not 0 <= optimize <= 3:
        raise ValueError("Optimization must be an integer between 0 and 3.")
    _check_dpi(dpi)
    if pre_rotate not in (None, 0, 90, 180, 270):
        raise ValueError("Unsupported pre-rotation angle.")
    if layout not in (None, "none", "single", "double"):
        raise ValueError("Unsupported scan layout.")
    if output_pages not in (None, "1", "2"):
        raise ValueError("Unsupported output page count.")
    if pdf_type == "born-digital" and (
        remove_background_flag
        or layout not in (None, "none")
        or output_pages is not None
        or pre_rotate is not None
    ):
        raise ValueError(
            "--pdf_type born-digital cannot be combined with --remove-bg, --layout "
            "(single or double), --output-pages, or --pre-rotate."
        )
    _check_dir_names(scan_dir)
    config = config or load_config()
    # No stage supports enhancement yet, so any selected task fails here.
    validate_task_selection(config, llm_tasks, COMMAND_ENHANCE, get_html=False)
    input_pdf = _resolve_input(input_pdf)
    output_dir = _final_output_dir(output_dir, input_pdf, input_path_prefix, config)
    output_pdf = output_dir / f"{input_pdf.stem}{PREPARED_SUFFIX}"
    report_path = output_dir / f"{input_pdf.stem}.enhance.json"
    if output_pdf.resolve() == input_pdf or (
        output_pdf.exists() and output_pdf.samefile(input_pdf)
    ):
        raise ValueError("The output PDF must differ from the input PDF.")
    settings = _PageSettings(
        pdf_type=pdf_type,
        ocrlib=ocrlib,
        languages=languages,
        dpi=dpi,
        optimize=optimize,
        pre_rotate=pre_rotate,
        layout=layout,
        remove_background_flag=remove_background_flag,
        scan_dir=scan_dir,
        debug_flag=debug_flag,
    )

    # OCR tools or a scanner can hold a work file briefly on Windows.
    # A leftover work file must not fail a published result.
    with tempfile.TemporaryDirectory(
        prefix="pdfwtf-", dir=get_temp_dir(config=config), ignore_cleanup_errors=True
    ) as temp:
        workspace = Path(temp)
        job_log = logging.LoggerAdapter(log, {"correlation_id": workspace.name})
        total_pages = _input_page_count(input_pdf)
        job_log.debug("Enhancement started. Input pages=%d.", total_pages)
        selection_requested = extract_pages_str is not None
        pages_to_keep = (
            parse_page_ranges(extract_pages_str, total_pages)
            if selection_requested
            else []
        )
        pages_to_skip = (
            parse_page_ranges(skip_pages_str, total_pages)
            if skip_pages_str is not None
            else []
        )
        selected = (
            pages_to_keep if selection_requested else list(range(1, total_pages + 1))
        )
        if not set(selected) - set(pages_to_skip):
            raise ValueError("Page removal must leave at least one page.")
        selected_pdf = workspace / "selected.pdf"
        if selection_requested:
            extract_pages(input_pdf, selected_pdf, pages_to_keep=selected)
        else:
            shutil.copy2(input_pdf, selected_pdf)
        pdf_result, scanned = _prepare_pages(
            selected_pdf, workspace / "pdf", output_pages, settings
        )
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
        report = {
            "schema_version": SCHEMA_VERSION,
            "kind": "enhancement",
            "created": _timestamp(),
            "input": {"filename": input_pdf.name, **source_identity(input_pdf)},
            "output": {"filename": output_pdf.name, **source_identity(pdf_result)},
            "settings": {
                "pdf_type": pdf_type,
                "ocrlib": ocrlib,
                "languages": languages,
                "dpi": dpi,
                "optimize": optimize,
                "layout": layout,
                "output_pages": output_pages,
                "pre_rotate": pre_rotate,
                "remove_background": remove_background_flag,
                "extract_pages": extract_pages_str,
                "remove_pages": skip_pages_str,
            },
            "scan_processing": scanned,
            "pages": {
                "selected": selected,
                "removed": sorted(set(pages_to_skip) & set(selected)),
                "pages_per_input_page": factor,
            },
        }
        _publish_pdf(pdf_result, output_pdf)
        staged_report = report_path.with_name(f".pdfwtf-{report_path.name}")
        try:
            write_json_document(report, staged_report)
            replace_file(staged_report, report_path)
        finally:
            staged_report.unlink(missing_ok=True)
        job_log.info("Enhancement completed.")
    return EnhanceResult(output_pdf, report_path)


@dataclass(frozen=True)
class AnalysisResult:
    """The diagnostic report of one analysis and where it was written."""

    report_path: Path
    report: dict[str, Any]


def analyze_pdf(
    input_pdf: str | Path,
    output_dir: str | Path | None,
    *,
    config: AppConfig | None = None,
    input_path_prefix: str | Path | None = None,
    llm_tasks: tuple[str, ...] = (),
    debug_flag: bool = False,
) -> AnalysisResult:
    """Write a read-only diagnostic report. Never modify the PDF or run OCR."""
    config = config or load_config()
    # No stage supports analysis yet, so any selected task fails here.
    validate_task_selection(config, llm_tasks, COMMAND_ANALYZE, get_html=False)
    input_pdf = _resolve_input(input_pdf)
    # Inspect before creating output, so an unreadable PDF leaves no files.
    report = analyze_document(input_pdf)
    output_dir = _final_output_dir(output_dir, input_pdf, input_path_prefix, config)
    destination = output_dir / f"{input_pdf.stem}.analysis.json"
    staged = destination.with_name(f".pdfwtf-{destination.name}")
    try:
        write_json_document(report, staged)
        staged.replace(destination)
    finally:
        staged.unlink(missing_ok=True)
    log.info("PDF analysis completed. Inspection=%s.", report["summary"]["inspection"])
    return AnalysisResult(destination, report)


def partition_pdf(
    input_pdf: str | Path,
    output_dir: str | Path | None,
    *,
    config: AppConfig | None = None,
    input_path_prefix: str | Path | None = None,
    document_type: str | None = None,
    llm_tasks: tuple[str, ...] = (),
    debug_flag: bool = False,
) -> Path:
    """Write a reviewable partition from existing PDF text. Never run OCR."""
    config = config or load_config()
    # No stage supports partitioning yet, so any selected task fails here.
    validate_task_selection(config, llm_tasks, COMMAND_PARTITION, get_html=False)
    input_pdf = _resolve_input(input_pdf)
    output_dir = _final_output_dir(output_dir, input_pdf, input_path_prefix, config)
    _input_page_count(input_pdf)
    destination = output_dir / f"{input_pdf.stem}.partition.json"
    partition = partition_container(input_pdf, document_type or "auto")
    write_json_document(partition, destination)
    log.info("Container partitioning completed.")
    return destination


def export_pdf(
    input_pdf: str | Path,
    output_dir: str | Path | None,
    *,
    config: AppConfig | None = None,
    input_path_prefix: str | Path | None = None,
    dpi: int = 300,
    export_format: str = "png",
    get_doi_flag: bool = False,
    export_json_flag: bool = False,
    export_images_flag: bool = False,
    export_texts_flag: bool = False,
    export_thumbs_flag: bool = False,
    document_type: str | None = None,
    plan_path: str | Path | None = None,
    export_html_flag: bool = False,
    export_html_normalized_flag: bool = False,
    export_unit_pdfs_flag: bool = False,
    unit_doi_flag: bool = False,
    unit_types: tuple[str, ...] = (),
    llm_tasks: tuple[str, ...] = (),
    debug_flag: bool = False,
    txt_dir: str = "_texts",
    img_dir: str = "_images",
    thumb_dir: str = "_thumbs",
) -> LLMReport | None:
    """Export derivatives from the supplied PDF without cleanup or OCR.

    Return the LLM task report when tasks were selected, otherwise None.
    """
    if export_unit_pdfs_flag and plan_path is None:
        raise ProcessingError("Unit PDF export requires a reviewed plan.")
    include_html = export_html_flag or export_html_normalized_flag
    unit_workflow = plan_path is not None or include_html
    if set(unit_types) - UNIT_TYPES:
        raise ProcessingError("Unknown unit type in --unit_type.")
    if unit_types and not unit_workflow:
        raise ProcessingError("--unit_type requires --plan or --get-html for export.")
    if include_html and plan_path is None and document_type not in (None, "unit"):
        option = (
            "--get-html-normalized"
            if export_html_normalized_flag and not export_html_flag
            else "--get-html"
        )
        raise ValueError(f"Direct {option} processing requires --doc_type unit.")
    derivatives = any(
        (
            export_json_flag,
            get_doi_flag,
            export_images_flag,
            export_texts_flag,
            export_thumbs_flag,
        )
    )
    if not derivatives and not unit_workflow:
        raise ValueError(
            "No output selected. Select --get-meta, --get-doi, --get-img, "
            "--get-text, --get-thumb, --plan, --get-html, or --get-html-normalized."
        )
    if export_format != "png":
        raise ValueError("Unsupported page image format.")
    _check_dpi(dpi)
    _check_dir_names(txt_dir, img_dir, thumb_dir)
    config = config or load_config()
    selected_tasks = validate_task_selection(
        config, llm_tasks, COMMAND_EXPORT, get_html=include_html
    )
    input_pdf = _resolve_input(input_pdf)
    output_dir = _final_output_dir(output_dir, input_pdf, input_path_prefix, config)
    units_dir = output_dir / f"_units_{input_pdf.stem}"
    images_dir = output_dir / f"{img_dir}_{input_pdf.stem}"
    thumbs_dir = output_dir / f"{thumb_dir}_{input_pdf.stem}"
    texts_out = output_dir / f"{txt_dir}_{input_pdf.stem}"
    export_dirs = []
    if unit_workflow:
        export_dirs.append(units_dir)
    if export_images_flag or export_thumbs_flag:
        export_dirs.append(images_dir)
    if export_thumbs_flag:
        export_dirs.append(thumbs_dir)
    if export_texts_flag:
        export_dirs.append(texts_out)
    if any(input_pdf.is_relative_to(path.resolve()) for path in export_dirs):
        raise ValueError("Export directories must not contain the input PDF.")

    # OCR tools or a scanner can hold a work file briefly on Windows.
    # A leftover work file must not fail a published result.
    with tempfile.TemporaryDirectory(
        prefix="pdfwtf-", dir=get_temp_dir(config=config), ignore_cleanup_errors=True
    ) as temp:
        workspace = Path(temp)
        job_log = logging.LoggerAdapter(log, {"correlation_id": workspace.name})
        total_pages = _input_page_count(input_pdf)
        job_log.debug("Export started. Input pages=%d.", total_pages)
        unit_plan = None
        selected_units = None
        if plan_path is not None:
            unit_plan = load_plan(Path(plan_path))
            selected_units = validate_plan(unit_plan, input_pdf, document_type)
        staged_units = None
        direct_html = unit_plan is None and include_html
        if direct_html and (not unit_types or "unknown" in unit_types):
            # Any PDF can give HTML. All pages become one unit of type
            # "unknown" without partition heuristics.
            staged_units = workspace / "unit_exports"
            export_document_html(
                input_pdf,
                staged_units,
                normalize_html=export_html_normalized_flag,
            )
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
                include_html=include_html,
                normalize_html=export_html_normalized_flag,
                use_reviewed_headings=plan_path is not None,
                include_pdf=export_unit_pdfs_flag,
                extract_doi=unit_doi_flag or get_doi_flag,
            )
        llm_report = None
        if selected_tasks and staged_units is not None:
            llm_report = run_after_unit_html(selected_tasks, staged_units)
            if not llm_report.complete:
                job_log.warning("LLM processing incomplete.")
        if unit_plan is not None:
            pages_to_keep = [
                page["input_page"] for page in unit_plan["pages"] if page["selected"]
            ]
        else:
            pages_to_keep = list(range(1, total_pages + 1)) if direct_html else []
        if export_images_flag or export_thumbs_flag:
            export_images(input_pdf, images_dir, dpi=dpi)
        if export_thumbs_flag:
            export_thumbnails(images_dir, thumbs_dir)
        metadata: dict[str, Any] = {"input": str(input_pdf), "output": None, "doi": []}
        if export_json_flag or get_doi_flag:
            metadata["pages"] = build_page_metadata(input_pdf, pages_to_keep, [])
        if export_texts_flag or get_doi_flag:
            texts_dir = texts_out if export_texts_flag else workspace / "doi_texts"
            texts = export_text(input_pdf, texts_dir)
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
            publish_unit_directory(staged_units, units_dir)
        job_log.info("Export completed.")
        return llm_report


OPERATIONS: dict[str, Callable[..., Any]] = {
    COMMAND_ANALYZE: analyze_pdf,
    COMMAND_ENHANCE: enhance_pdf,
    COMMAND_PARTITION: partition_pdf,
    COMMAND_EXPORT: export_pdf,
}


def _inactive(value: object) -> bool:
    return value is None or value is False or value == () or value == ""


def process_pdf(
    input_pdf: str | Path,
    output_dir: str | Path | None,
    *,
    command: str = COMMAND_ENHANCE,
    config: AppConfig | None = None,
    partition_flag: bool = False,
    **options: Any,
) -> AnalysisResult | EnhanceResult | Path | LLMReport | None:
    """Dispatch to one operation. Kept for compatibility with older callers.

    ``partition_flag=True`` selects partitioning. An option that the selected
    operation does not accept is rejected unless it has an inactive value.
    """
    if partition_flag:
        if command not in (COMMAND_ENHANCE, COMMAND_PARTITION):
            raise ValueError("partition_flag cannot be combined with this command.")
        command = COMMAND_PARTITION
    if options.pop("born_digital_flag", False):
        # Older callers used born_digital_flag for pdf_type="born-digital".
        if options.get("pdf_type", "auto") == "scanned":
            raise ValueError(
                "born_digital_flag cannot be combined with pdf_type scanned."
            )
        options["pdf_type"] = "born-digital"
    operation = OPERATIONS.get(command)
    if operation is None:
        raise ValueError(f"Unknown command '{command}'.")
    accepted = inspect.signature(operation).parameters
    for name, value in list(options.items()):
        if name not in accepted:
            if not _inactive(value):
                raise ValueError(
                    f"Option '{name}' is not supported by the '{command}' command."
                )
            del options[name]
    return operation(input_pdf, output_dir, config=config, **options)
