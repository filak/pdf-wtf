import click
from .pipeline import process_pdf
from .utils import get_output_dir


@click.command()
@click.option(
    "--infile",
    "input_pdf",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Input PDF file",
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
    help="Input dir path prefix to create relative output subdirs",
)
@click.option(
    "--extract",
    "extract_pages_str",
    default=None,
    help="Pages to extract, e.g. '1-3,5,8-'",
)
@click.option(
    "--lang", "languages", default="eng", help="OCR language(s), e.g. 'eng+ces'"
)
@click.option(
    "--clean",
    "clean_scanned_flag",
    is_flag=True,
    help="Clean scanned PDF files - uses unpaper !",
)
@click.option(
    "--images",
    "export_images_flag",
    is_flag=True,
    help="Export pages as images - PNG files",
)
@click.option("--dpi", default=200, help="DPI for image export")
@click.option(
    "--texts", "export_texts_flag", is_flag=True, help="Export pages as text files"
)
@click.option(
    "--clear-temp", "clear_temp_flag", is_flag=True, help="Clear temporary files"
)
def main(
    input_pdf,
    output_dir,
    input_path_prefix,
    extract_pages_str,
    languages,
    clean_scanned_flag,
    clear_temp_flag,
    export_images_flag,
    dpi,
    export_texts_flag,
):
    output_dir = get_output_dir(output_dir=output_dir)

    """PDF processing pipeline with page removal, OCR, and image export."""
    click.echo(f"Input file :  {input_pdf}")
    click.echo(f"Output dir :  {output_dir}")

    process_pdf(
        input_pdf,
        output_dir,
        input_path_prefix=input_path_prefix,
        extract_pages_str=extract_pages_str,
        languages=languages,
        clean_scanned_flag=clean_scanned_flag,
        clear_temp_flag=clear_temp_flag,
        export_images_flag=export_images_flag,
        dpi=dpi,
        export_texts_flag=export_texts_flag,
    )

    click.echo("Done!")


if __name__ == "__main__":
    main(standalone_mode=False)
