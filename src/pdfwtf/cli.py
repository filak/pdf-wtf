import click
import sys
from pydantic import BaseModel, HttpUrl, model_validator, ValidationError
from pdfwtf.pipeline import process_pdf
from pdfwtf.utils import get_output_dir


class InputModel(BaseModel):
    infile: str | None = None
    url: HttpUrl | None = None

    @model_validator(mode="before")
    def check_exclusivity(cls, values):
        infile, url = values.get("infile"), values.get("url")
        if bool(infile) == bool(url):
            raise ValueError("You must provide exactly one of --infile or --url")
        if url and not url.startswith(("http://", "https://")):
            raise ValueError("Only http(s) URLs are allowed")
        return values


class CliOptions(BaseModel):
    input_path_prefix: str | None = None
    extract_pages_str: str | None = None
    skip_pages_str: str | None = None
    ocrlib: str = "ocrmypdf"
    languages: str = "eng"
    remove_background_flag: bool = False
    dpi: int = 300
    layout: str | None = None
    output_pages: str | None = None
    pre_rotate: int | None = None
    get_doi_flag: bool = False
    export_images_flag: bool = False
    export_texts_flag: bool = False
    export_thumbs_flag: bool = False
    debug_flag: bool = False


def validate_input(input_pdf: str | None, url: str | None):
    try:
        return InputModel(infile=input_pdf, url=url)
    except ValidationError as e:
        raise click.UsageError(str(e))


def show_info(input_pdf: str | None, url: str | None, output_dir, debug_flag: bool):
    input_show = input_pdf or url
    click.echo(f"Input  :  {input_show}")
    click.echo(f"Output :  {output_dir}")
    if debug_flag:
        click.echo(f"[DEBUG] {' '.join(sys.argv)}")


@click.command()
@click.option("--infile", "input_pdf", type=click.Path(exists=True, dir_okay=False))
@click.option("--url", type=str)
@click.option(
    "--outdir", "output_dir", type=click.Path(file_okay=False, resolve_path=True)
)
@click.option("--relative", "input_path_prefix", type=click.Path(file_okay=False))
@click.option("--extract", "extract_pages_str", default=None)
@click.option("--skip-post", "skip_pages_str", default=None)
@click.option("--lang", "languages", default="eng")
@click.option("--dpi", default=300, type=click.IntRange(72, 1200))
@click.option(
    "--ocrlib", "ocrlib", default="ocrmypdf", type=click.Choice(["ocrmypdf", "pymupdf"])
)
@click.option(
    "--layout", "layout", default=None, type=click.Choice(["single", "double", "none"])
)
@click.option(
    "--output-pages", "output_pages", default=None, type=click.Choice(["1", "2"])
)
@click.option(
    "--pre-rotate", "pre_rotate", default=None, type=click.Choice([0, 90, 180, 270])
)
@click.option("--remove-bg", "remove_background_flag", is_flag=True)
@click.option("--get-doi", "get_doi_flag", is_flag=True)
@click.option("--get-png", "export_images_flag", is_flag=True)
@click.option("--get-text", "export_texts_flag", is_flag=True)
@click.option("--get-thumb", "export_thumbs_flag", is_flag=True)
@click.option("--debug", "debug_flag", is_flag=True)
def main(input_pdf, url, output_dir, **kwargs):
    """Main entrypoint for PDF WTF CLI"""

    # Validate input (infile xor url)
    validate_input(input_pdf, url)

    # Normalize output directory
    output_dir = get_output_dir(output_dir=output_dir)

    # Collect CLI options into Pydantic model
    options = CliOptions(**kwargs)

    # Show info
    show_info(input_pdf, url, output_dir, options.debug_flag)

    # Run processing
    try:
        process_pdf(
            input_pdf,
            url,
            output_dir,
            **options.model_dump(),
        )
    except Exception as e:
        click.echo(f"Error: {e}", err=True)

    click.echo("Done!")


if __name__ == "__main__":
    main()
