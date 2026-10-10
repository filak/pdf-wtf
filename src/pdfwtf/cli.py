"""Command-line entry point."""

import logging
from pathlib import Path
from typing import Literal

import click
from pydantic import BaseModel

from pdfwtf.configuration import ConfigurationError, load_config
from pdfwtf.container_analysis import UNIT_TYPES
from pdfwtf.logging_utils import configure_logging
from pdfwtf.pipeline import ProcessingError, process_pdf


class PdfCommand(click.Command):
    """Show configured directories when help is requested."""

    def format_options(
        self, ctx: click.Context, formatter: click.HelpFormatter
    ) -> None:
        if self.name != "enhance":
            return super().format_options(ctx, formatter)
        groups = {
            "Common options": {"output_dir", "input_path_prefix", "debug_flag", "help"},
            "Page selection": {"extract_pages_str", "skip_pages_str"},
            "Enhancement options": {
                "pdf_type",
                "languages",
                "dpi",
                "ocrlib",
                "optimize",
                "layout",
                "output_pages",
                "pre_rotate",
                "remove_background_flag",
            },
            "Analyze options": {"document_type"},
            "Export options": {
                "plan_path",
                "no_pdf_flag",
                "get_doi_flag",
                "export_format",
                "export_html_flag",
                "export_images_flag",
                "export_json_flag",
                "export_texts_flag",
                "export_thumbs_flag",
            },
        }
        for title, names in groups.items():
            rows = [
                record
                for param in self.get_params(ctx)
                if param.name in names
                and (record := param.get_help_record(ctx)) is not None
            ]
            if rows:
                with formatter.section(title):
                    formatter.write_dl(rows)

    def format_epilog(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        super().format_epilog(ctx, formatter)
        with formatter.section("Current directories"):
            try:
                config = load_config()
            except ConfigurationError as error:
                formatter.write_text(f"Unavailable. {error}")
                return
            try:
                input_dir = str(config.input_dir)
            except ConfigurationError as error:
                input_dir = f"Unavailable. {error}"
            formatter.write_text(f"\b\nInput: {input_dir}")
            try:
                output = str(config.output_dir)
            except ConfigurationError as error:
                output = f"Unavailable. {error}"
            formatter.write_text(f"\b\nOutput (default): {output}")
            formatter.write_text(
                "Pass a filename or relative path below Input, or an absolute path. "
                "Use --outdir to change the output."
            )


class CliOptions(BaseModel):
    input_path_prefix: str | None = None
    extract_pages_str: str | None = None
    skip_pages_str: str | None = None
    ocrlib: str = "ocrmypdf"
    optimize: int = 0
    pdf_type: Literal["auto", "born-digital", "scanned"] = "auto"
    languages: str = "eng"
    remove_background_flag: bool = False
    dpi: int = 300
    layout: str | None = None
    output_pages: str | None = None
    pre_rotate: int | None = None
    get_doi_flag: bool = False
    no_pdf_flag: bool = False
    export_json_flag: bool = False
    export_images_flag: bool = False
    export_format: str = "png"
    export_texts_flag: bool = False
    export_thumbs_flag: bool = False
    analysis_flag: bool = False
    unit_types: tuple[str, ...] = ()
    document_type: str | None = None
    plan_path: str | None = None
    export_html_flag: bool = False
    debug_flag: bool = False


def show_info(input_pdf: str, output_dir: str | Path | None, debug_flag: bool) -> None:
    click.echo(f"Input  :  {input_pdf}")
    if output_dir is not None:
        click.echo(f"Output :  {output_dir}")


def _resolve_input_pdf(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> str | None:
    """Resolve relative CLI inputs from the default input directory."""
    if value is None:
        return None
    path = Path(value)
    if not path.is_absolute():
        try:
            path = load_config().input_dir / path
        except ConfigurationError as error:
            raise click.ClickException(str(error)) from error
    return click.Path(exists=True, dir_okay=False, resolve_path=True).convert(
        str(path), param, ctx
    )


@click.command("enhance", cls=PdfCommand)
@click.argument(
    "input_pdf",
    required=True,
    type=click.Path(),
    callback=_resolve_input_pdf,
)
@click.option(
    "--outdir",
    "output_dir",
    type=click.Path(file_okay=False, resolve_path=True),
    help="Write output files to this directory.",
)
@click.option(
    "--prefix",
    "input_path_prefix",
    type=click.Path(file_okay=False),
    help="Preserve input subdirectories below this path.",
)
@click.option(
    "--pdf_type",
    type=click.Choice(["auto", "born-digital", "scanned"]),
    default="auto",
    show_default=True,
    help="Set the PDF type: auto detects scans, born-digital skips scan preparation "
    "and OCR, scanned forces scan processing. Image exports still render pages.",
)
@click.option(
    "--doc_type",
    "document_type",
    type=click.Choice(
        ["unit", "journal-issue", "magazine-issue", "book", "proceedings", "auto"]
    ),
    help="Describe the document structure. The reviewed plan is authoritative.",
)
@click.option(
    "--plan",
    "plan_path",
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
    help="Process units from a reviewed JSON plan.",
)
@click.option(
    "--extract",
    "extract_pages_str",
    default=None,
    help="Keep these input pages in the output PDF, for example 1-3,5.",
)
@click.option(
    "--remove",
    "skip_pages_str",
    default=None,
    help="Remove these input pages from the output PDF, " "for example 2,4-6.",
)
@click.option(
    "--lang",
    "languages",
    default="eng",
    show_default=True,
    help="Select OCR languages, for example eng+ces.",
)
@click.option(
    "--dpi",
    default=300,
    type=click.IntRange(72, 1200),
    show_default=True,
    help="Set the page rendering resolution in DPI.",
)
@click.option(
    "--ocrlib",
    default="ocrmypdf",
    type=click.Choice(["ocrmypdf", "pymupdf"]),
    show_default=True,
    help="Select the OCR backend.",
)
@click.option(
    "--optimize",
    default=0,
    type=click.IntRange(0, 3),
    show_default=True,
    help="Set OCRmyPDF optimization: 0=off, 1=lossless, 2=lossy, 3=aggressive.",
)
@click.option(
    "--layout",
    default=None,
    type=click.Choice(["single", "double", "none"]),
    help="Select the unpaper page layout.",
)
@click.option(
    "--output-pages",
    default=None,
    type=click.Choice(["1", "2"]),
    help="Set unpaper output pages per input page.",
)
@click.option(
    "--pre-rotate",
    default=None,
    type=click.Choice([0, 90, 180, 270]),
    help="Rotate scanned pages before OCR, in degrees.",
)
@click.option(
    "--remove-bg",
    "remove_background_flag",
    is_flag=True,
    help="Crop dark page backgrounds.",
)
@click.option(
    "--no-pdf-out",
    "no_pdf_flag",
    is_flag=True,
    help="Do not write the output PDF.",
)
@click.option(
    "--get-doi",
    "get_doi_flag",
    is_flag=True,
    help="Find DOI candidates on the first derivative page and write JSON metadata.",
)
@click.option(
    "--get-format",
    "export_format",
    default="png",
    type=click.Choice(["png"]),
    show_default=True,
    help="Select the page image format.",
)
@click.option(
    "--get-html",
    "export_html_flag",
    is_flag=True,
    help="Write a UTF-8 HTML fragment for each selected unit.",
)
@click.option(
    "--get-img",
    "export_images_flag",
    is_flag=True,
    help="Export every input page as an image. Page selection does not filter images.",
)
@click.option(
    "--get-meta",
    "export_json_flag",
    is_flag=True,
    help="Write JSON metadata.",
)
@click.option(
    "--get-text",
    "export_texts_flag",
    is_flag=True,
    help="Export page text and combined text.",
)
@click.option(
    "--get-thumb",
    "export_thumbs_flag",
    is_flag=True,
    help="Export page images and JPEG thumbnails.",
)
@click.option("--debug", "debug_flag", is_flag=True, help="Enable debug logging.")
def enhance(input_pdf: str, output_dir: str | None, **kwargs: object) -> None:
    """Enhance a PDF with scan cleanup, OCR, and optional exports."""
    _execute(input_pdf, output_dir, **kwargs)


def _execute(input_pdf: str, output_dir: str | None, **kwargs: object) -> None:
    """Invoke the shared processor with action-specific options."""
    options = CliOptions(**kwargs)
    show_info(input_pdf, output_dir, options.debug_flag)
    try:
        configure_logging(
            options.debug_flag, load_config().logs_dir / "pdf-wtf-log.txt"
        )
        logging.getLogger(__name__).info("PDF processing started.")
        process_pdf(input_pdf, output_dir, **options.model_dump())
    except (ConfigurationError, ProcessingError) as error:
        logging.getLogger(__name__).error("PDF processing failed.")
        raise click.ClickException(str(error)) from error
    except ValueError as error:
        logging.getLogger(__name__).error("PDF processing failed.")
        raise click.ClickException(str(error)) from error
    except Exception as error:
        logging.getLogger(__name__).error("PDF processing failed.")
        # Tool exceptions can contain document data. Do not print their payloads.
        raise click.ClickException(
            "PDF processing failed. Check the input and required tools."
        ) from error
    logging.getLogger(__name__).info("PDF processing completed.")
    click.echo("Done!")


class PdfGroup(click.Group, PdfCommand):
    """Show configured directories for the action selector."""


@click.group(cls=PdfGroup)
def main() -> None:
    """Analyze, enhance, or export one PDF. Select an action first."""


def analyze(input_pdf: str, output_dir: str | None, **kwargs: object) -> None:
    """Write analysis JSON from existing PDF text without OCR or unit exports."""
    _execute(
        input_pdf, output_dir, analysis_flag=True, pdf_type="born-digital", **kwargs
    )


def export(input_pdf: str, output_dir: str | None, **kwargs: object) -> None:
    """Export selected derivatives from the input PDF without OCR or a PDF output."""
    _execute(input_pdf, output_dir, pdf_type="born-digital", no_pdf_flag=True, **kwargs)


_COMMON_PARAMS = {"input_pdf", "output_dir", "input_path_prefix", "debug_flag"}
_ANALYZE_PARAMS = _COMMON_PARAMS | {"document_type"}
_EXPORT_PARAMS = _COMMON_PARAMS | {
    "dpi",
    "get_doi_flag",
    "export_json_flag",
    "export_images_flag",
    "export_format",
    "export_texts_flag",
    "export_thumbs_flag",
    "document_type",
    "plan_path",
    "export_html_flag",
}


def _unit_type_option() -> click.Option:
    return click.Option(
        ["--unit_type", "unit_types"],
        type=click.Choice(sorted(UNIT_TYPES)),
        multiple=True,
        help="Select units of this type. Repeat to select several types.",
    )


main.add_command(enhance)
main.add_command(
    PdfCommand(
        "analyze",
        params=[param for param in enhance.params if param.name in _ANALYZE_PARAMS],
        callback=analyze,
        help=analyze.__doc__,
    )
)
main.add_command(
    PdfCommand(
        "export",
        params=[param for param in enhance.params if param.name in _EXPORT_PARAMS]
        + [_unit_type_option()],
        callback=export,
        help=export.__doc__,
    )
)


if __name__ == "__main__":
    main()
