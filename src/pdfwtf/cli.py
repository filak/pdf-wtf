import click
import sys
from pydantic import BaseModel, HttpUrl, model_validator, ValidationError
from .pipeline import process_pdf
from .utils import get_output_dir


class InputModel(BaseModel):
    infile: str | None = None
    url: HttpUrl | None = None

    @model_validator(mode="before")  # validate before parsing fields
    def check_exclusivity(cls, values):
        infile, url = values.get("infile"), values.get("url")
        # Ensure exactly one of infile or url is provided
        if bool(infile) == bool(url):
            raise ValueError("You must provide exactly one of --infile or --url")
        return values


@click.command()
@click.option(
    "--infile",
    "input_pdf",
    type=click.Path(exists=True, dir_okay=False),
    help="Input PDF file",
)
@click.option(
    "--url",
    type=str,
    help="Input URL to fetch PDF from",
)
@click.option(
    "--outdir",
    "output_dir",
    required=False,
    type=click.Path(exists=False, file_okay=False),
    help="Output directory",
)
@click.option(
    "--relative",
    "input_path_prefix",
    type=click.Path(file_okay=False),
    help="Part of the input path separating the output subdirs path (e.g. '_data/in')",
)
@click.option(
    "--extract",
    "extract_pages_str",
    default=None,
    help="[Pre-process with pikepdf]: Pages to extract before processing, e.g. '1-3,5,8-'",
)
@click.option(
    "--skip-post",
    "skip_pages_str",
    default=None,
    help="[Post-process with pikepdf]: Pages to skip after processing, e.g. '1,8-'",
)
@click.option(
    "--lang", "languages", default="eng", help="OCR language(s), e.g. 'eng+ces'"
)
@click.option("--dpi", default=300, help="DPI for image export")
@click.option(
    "--ocrlib",
    "ocrlib",
    default="ocrmypdf",
    help="Python library to do OCR",
    type=click.Choice(["ocrmypdf", "pymupdf"]),
)
@click.option(
    "--layout",
    "layout",
    default=None,
    help="[unpaper]: single or double - ignored when --output-pages is used",
    type=click.Choice(["single", "double", "none"]),
)
@click.option(
    "--output-pages",
    "output_pages",
    default=None,
    help="[Pre-process with unpaper]: 1 or 2",
    type=click.Choice(["1", "2"]),
)
@click.option(
    "--pre-rotate",
    "pre_rotate",
    default=None,
    help="[Pre-process with unpaper]: 0, 90, 180, 270",
    type=int,
)
@click.option(
    "--remove-bg",
    "remove_background_flag",
    is_flag=True,
    help="Pre-process: Remove background",
)
@click.option(
    "--get-doi", "get_doi_flag", is_flag=True, help="Extract DOI from the first page"
)
@click.option(
    "--get-png", "export_images_flag", is_flag=True, help="Export pages as PNG files"
)
@click.option(
    "--get-text", "export_texts_flag", is_flag=True, help="Export pages as text files"
)
@click.option(
    "--get-thumb",
    "export_thumbs_flag",
    is_flag=True,
    help="Export pages as thumbnail PNG files",
)
@click.option(
    "--clear-temp", "clear_temp_flag", is_flag=True, help="Clear temporary files"
)
@click.option("--debug", "debug_flag", is_flag=True, help="Debugging mode")
def main(
    input_pdf,
    url,
    output_dir,
    input_path_prefix,
    extract_pages_str,
    skip_pages_str,
    ocrlib,
    languages,
    remove_background_flag,
    clear_temp_flag,
    dpi,
    layout,
    output_pages,
    pre_rotate,
    get_doi_flag,
    export_images_flag,
    export_texts_flag,
    export_thumbs_flag,
    debug_flag,
):
    # Validate input using Pydantic
    try:
        data = InputModel(infile=input_pdf, url=url)  # noqa: F841
    except ValidationError as e:
        raise click.UsageError(str(e))

    output_dir = get_output_dir(output_dir=output_dir)

    click.echo(f"Input file :  {input_pdf}")
    click.echo(f"Output dir :  {output_dir}")

    if debug_flag:
        click.echo(f"[DEBUG] {' '.join(sys.argv)}")

    process_pdf(
        input_pdf,
        url,
        output_dir,
        input_path_prefix=input_path_prefix,
        extract_pages_str=extract_pages_str,
        skip_pages_str=skip_pages_str,
        ocrlib=ocrlib,
        languages=languages,
        remove_background_flag=remove_background_flag,
        dpi=dpi,
        layout=layout,
        output_pages=output_pages,
        pre_rotate=pre_rotate,
        clear_temp_flag=clear_temp_flag,
        get_doi_flag=get_doi_flag,
        export_images_flag=export_images_flag,
        export_thumbs_flag=export_thumbs_flag,
        export_texts_flag=export_texts_flag,
        debug_flag=debug_flag,
    )

    click.echo("Done!")


if __name__ == "__main__":
    main(standalone_mode=False)
