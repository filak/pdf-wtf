"""Command-line entry point."""

from pathlib import Path

import click
from pydantic import BaseModel

from pdfwtf.configuration import ConfigurationError, load_config
from pdfwtf.logging_utils import configure_logging
from pdfwtf.pipeline import ProcessingError, process_pdf


class PdfCommand(click.Command):
    """Show configured directories when help is requested."""

    def format_epilog(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        super().format_epilog(ctx, formatter)
        with formatter.section("Current directories"):
            try:
                config = load_config()
            except ConfigurationError as error:
                formatter.write_text(f"Unavailable. {error}")
                return
            formatter.write_text(f"\b\nInput: {config.home / 'instance/_data/in'}")
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
    born_digital_flag: bool = False
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
            path = load_config().home / "instance/_data/in" / path
        except ConfigurationError as error:
            raise click.ClickException(str(error)) from error
    return click.Path(exists=True, dir_okay=False, resolve_path=True).convert(
        str(path), param, ctx
    )


@click.command(cls=PdfCommand)
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
    "--born-digital",
    "born_digital_flag",
    is_flag=True,
    help="Skip scan preparation and OCR. Image exports still render pages.",
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
    "--get-json",
    "export_json_flag",
    is_flag=True,
    help="Write JSON metadata.",
)
@click.option(
    "--get-doi",
    "get_doi_flag",
    is_flag=True,
    help="Find DOI links on the first output page and write JSON metadata.",
)
@click.option(
    "--get-img",
    "export_images_flag",
    is_flag=True,
    help="Export each output page as an image.",
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
@click.option(
    "--get-format",
    "export_format",
    default="png",
    type=click.Choice(["png"]),
    show_default=True,
    help="Select the page image format.",
)
@click.option("--debug", "debug_flag", is_flag=True, help="Enable debug logging.")
def main(input_pdf: str, output_dir: str | None, **kwargs: object) -> None:
    """Process one PDF. Scan layout, splitting, and pre-rotation require unpaper."""
    options = CliOptions(**kwargs)
    configure_logging(options.debug_flag)
    show_info(input_pdf, output_dir, options.debug_flag)
    try:
        process_pdf(input_pdf, output_dir, **options.model_dump())
    except (ConfigurationError, ProcessingError) as error:
        raise click.ClickException(str(error)) from error
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    except Exception as error:
        # Tool exceptions can contain document data. Do not print their payloads.
        raise click.ClickException(
            "PDF processing failed. Check the input and required tools."
        ) from error
    click.echo("Done!")


if __name__ == "__main__":
    main()
