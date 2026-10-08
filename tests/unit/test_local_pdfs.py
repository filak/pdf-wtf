"""Optional regression checks for local PDFs excluded from version control."""

from hashlib import sha256
from pathlib import Path

from PIL import Image
import pymupdf as fitz
import pytest

from pdfwtf.pipeline import process_pdf
from pdfwtf.utils.analyze import is_scanned_or_hybrid

INPUT_DIR = Path(__file__).resolve().parents[2] / "instance/_data/in"


@pytest.fixture
def sample_pdf(request):
    path = INPUT_DIR / request.param
    if not path.is_file():
        pytest.skip("The optional local PDF is not installed.")
    return path


def text_hash(text: str) -> str:
    # Compare content without exposing document text in assertion reports.
    return sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    "sample_pdf, expected_scan",
    [("born_digital.pdf", False), ("scan_with_text.pdf", True)],
    indirect=["sample_pdf"],
)
def test_local_sample_classification(sample_pdf, expected_scan):
    assert is_scanned_or_hybrid(sample_pdf) is expected_scan


@pytest.mark.parametrize(
    "sample_pdf, born_digital",
    [
        ("born_digital.pdf", False),
        ("born_digital.pdf", True),
        ("scan_with_text.pdf", True),
    ],
    indirect=["sample_pdf"],
)
def test_local_pdf_content_and_text_exports(
    sample_pdf, born_digital, tmp_path, configured_home, monkeypatch
):
    def unexpected(*args, **kwargs):
        pytest.fail("This sample test must not invoke scan preparation or OCR.")

    monkeypatch.setattr("pdfwtf.pipeline._process_scanned", unexpected)
    monkeypatch.setattr("pdfwtf.pipeline.run_ocr", unexpected)
    source_hash = sha256(sample_pdf.read_bytes()).hexdigest()
    output = tmp_path / "out"
    process_pdf(
        sample_pdf,
        output,
        born_digital_flag=born_digital,
        ocrlib="ocrmypdf",
        export_texts_flag=True,
    )
    assert sha256((output / sample_pdf.name).read_bytes()).hexdigest() == source_hash
    assert sha256(sample_pdf.read_bytes()).hexdigest() == source_hash
    with fitz.open(sample_pdf) as document:
        for number, page in enumerate(document, start=1):
            path = output / f"_texts_{sample_pdf.stem}" / f"page_{number:03d}.txt"
            assert text_hash(path.read_text(encoding="utf-8")) == text_hash(
                page.get_text("text")
            )
    assert not list((configured_home / "instance/temp").iterdir())


@pytest.mark.parametrize(
    "sample_pdf", ["born_digital.pdf", "scan_with_text.pdf"], indirect=True
)
def test_local_pdf_page_selection_and_exports(sample_pdf, tmp_path):
    source_hash = sha256(sample_pdf.read_bytes()).hexdigest()
    with fitz.open(sample_pdf) as document:
        expected_pages = len(document)
        expected_text = text_hash(document[1].get_text("text"))
        expected_images = len(document[1].get_images(full=True))
        expected_size = document[1].rect
    output = tmp_path / "out"
    process_pdf(
        sample_pdf,
        output,
        born_digital_flag=True,
        extract_pages_str="1-2",
        skip_pages_str="1",
        export_images_flag=True,
        export_thumbs_flag=True,
        dpi=72,
    )
    with fitz.open(output / sample_pdf.name) as document:
        assert len(document) == 1
        assert text_hash(document[0].get_text("text")) == expected_text
        assert len(document[0].get_images(full=True)) == expected_images
        assert document[0].rect == expected_size
    images = list((output / f"_images_{sample_pdf.stem}").glob("*.png"))
    thumbs = list((output / f"_thumbs_{sample_pdf.stem}").glob("*.jpg"))
    assert len(images) == len(thumbs) == expected_pages
    with Image.open(thumbs[0]) as thumbnail:
        assert thumbnail.width <= 400
        assert thumbnail.height <= 400
    assert sha256(sample_pdf.read_bytes()).hexdigest() == source_hash
