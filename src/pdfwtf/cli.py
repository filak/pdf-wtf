import os
import click
from .pipeline import find_project_root, process_pdf


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
    type=click.Path(file_okay=False),
    help="Output directory",
)
@click.option(
    "--relative",
    "relative_marker",
    type=click.Path(file_okay=False),
    help="Part of input file path to compute relative output subfolders",
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
    "--images", "export_images_flag", is_flag=True, help="Export pages as images"
)
@click.option(
    "--rotate",
    "rotate_images_flag",
    is_flag=True,
    help="TBD: Autorotate images in scanned PDFs",
)
@click.option("--clear", "clear_temp_flag", is_flag=True, help="Clear temporary files")
@click.option("--dpi", default=200, help="DPI for image export")
def main(
    input_pdf,
    output_dir,
    relative_marker,
    extract_pages_str,
    languages,
    export_images_flag,
    dpi,
    rotate_images_flag,
    clear_temp_flag,
):
    if not output_dir:
        env_outdir = os.environ.get("PDFWTF_OUTPUT_DIR")
        if env_outdir:
            output_dir = env_outdir

    """PDF processing pipeline with page removal, OCR, and image export."""
    click.echo(f"Input: {input_pdf}")
    click.echo(f"Output: {output_dir}")

    process_pdf(
        input_pdf,
        output_dir=output_dir,
        relative_marker=relative_marker,
        extract_pages_str=extract_pages_str,
        languages=languages,
        export_images_flag=export_images_flag,
        dpi=dpi,
        rotate_images_flag=rotate_images_flag,
        clear_temp_flag=clear_temp_flag,
    )

    click.echo("Done!")


if __name__ == "__main__":
    main(standalone_mode=False)
