import click
from .pipeline import process_pdf


@click.command()
@click.option(
    "--infile",
    "input_pdf",
    required=True,
    type=click.Path(exists=True),
    help="Input PDF file",
)
@click.option(
    "--outfile", "output_pdf", required=True, type=click.Path(), help="Output PDF file"
)
@click.option(
    "--remove", "remove_pages_str", default=None, help="Pages to remove, e.g. '1-3,5'"
)
@click.option(
    "--lang", "languages", default="eng", help="OCR language(s), e.g. 'eng+ces'"
)
@click.option(
    "--images", "export_images_flag", is_flag=True, help="Export pages as images"
)
@click.option(
    "--imgdir",
    "image_dir",
    default="images",
    type=click.Path(),
    help="Directory for image export",
)
@click.option("--dpi", default=200, help="DPI for image export")
def main(
    input_pdf,
    output_pdf,
    remove_pages_str,
    languages,
    export_images_flag,
    image_dir,
    dpi,
):
    """PDF processing pipeline with page removal, OCR, and image export."""
    click.echo(f"📂 Input: {input_pdf}")
    click.echo(f"📄 Output: {output_pdf}")

    process_pdf(
        input_pdf,
        output_pdf,
        remove_pages_str=remove_pages_str,
        languages=languages,
        export_images_flag=export_images_flag,
        image_dir=image_dir,
        dpi=dpi,
    )

    click.echo("🎉 Done!")
