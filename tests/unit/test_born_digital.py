import json

from click.testing import CliRunner
import pymupdf as fitz
import pytest

from pdfwtf import cli, pipeline


@pytest.fixture
def vector_pdf(tmp_path):
    path = tmp_path / "graphics.pdf"
    with fitz.open() as document:
        for size in (20, 40, 60):
            page = document.new_page(width=200, height=300)
            page.draw_rect(fitz.Rect(10, 10, 10 + size, 10 + size))
        document.save(path)
    return path


@pytest.fixture
def forbid_scan_processing(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Born-digital mode must not detect, rasterize, or OCR pages.")

    for name in ("is_scanned_or_hybrid", "_process_scanned", "run_ocr"):
        monkeypatch.setattr(pipeline, name, unexpected)
    monkeypatch.setattr(fitz.Page, "get_pixmap", unexpected)


@pytest.mark.parametrize("kind", ["digital", "scan", "blank"])
def test_override_skips_detection_and_processing(
    make_pdf, tmp_path, forbid_scan_processing, kind
):
    source = make_pdf([kind])
    original = source.read_bytes()
    output = tmp_path / "out"
    pipeline.process_pdf(source, output, born_digital_flag=True, ocrlib="ocrmypdf")
    assert (output / source.name).read_bytes() == original
    assert source.read_bytes() == original


def test_vector_only_pdf_has_empty_text_exports_without_ocr(
    vector_pdf, tmp_path, forbid_scan_processing
):
    output = tmp_path / "out"
    pipeline.process_pdf(
        vector_pdf,
        output,
        born_digital_flag=True,
        export_texts_flag=True,
        get_doi_flag=True,
    )
    assert (output / vector_pdf.name).read_bytes() == vector_pdf.read_bytes()
    texts = sorted((output / "_texts_graphics").glob("*.txt"))
    assert len(texts) == 3
    assert all(path.read_text(encoding="utf-8") == "" for path in texts)
    assert (output / "graphics.txt").read_text(encoding="utf-8") == (
        "--- Page 1 of 3 ---\n\n\n"
        "--- Page 2 of 3 ---\n\n\n"
        "--- Page 3 of 3 ---\n\n\n"
    )
    assert json.loads((output / "graphics.meta.json").read_text()) == {
        "input": str(vector_pdf.resolve()),
        "output": str((output / vector_pdf.name).resolve()),
        "doi": [],
        "pages": {
            f"page_{index:03d}": {"index": index, "pgn": None} for index in range(1, 4)
        },
    }


def test_page_selection_and_removal_preserve_vector_content(
    vector_pdf, tmp_path, forbid_scan_processing
):
    output = tmp_path / "out"
    pipeline.process_pdf(
        vector_pdf,
        output,
        born_digital_flag=True,
        extract_pages_str="2-3",
        skip_pages_str="2",
    )
    with fitz.open(output / vector_pdf.name) as document:
        assert len(document) == 1
        assert document[0].get_images() == []
        assert document[0].get_text() == ""
        assert document[0].get_drawings()[0]["rect"] == fitz.Rect(10, 10, 70, 70)


@pytest.mark.parametrize(
    "export_options", [{"export_images_flag": True}, {"export_thumbs_flag": True}]
)
def test_optional_exports_render_without_changing_pdf(
    vector_pdf, tmp_path, monkeypatch, export_options
):
    def unexpected(*args, **kwargs):
        pytest.fail("Image export must not invoke scan preparation or OCR.")

    monkeypatch.setattr(pipeline, "is_scanned_or_hybrid", unexpected)
    monkeypatch.setattr(pipeline, "_process_scanned", unexpected)
    monkeypatch.setattr(pipeline, "run_ocr", unexpected)
    output = tmp_path / "out"
    pipeline.process_pdf(
        vector_pdf, output, born_digital_flag=True, dpi=72, **export_options
    )
    assert (output / vector_pdf.name).read_bytes() == vector_pdf.read_bytes()
    assert len(list((output / "_images_graphics").glob("*.png"))) == 3
    if export_options.get("export_thumbs_flag"):
        assert len(list((output / "_thumbs_graphics").glob("*.jpg"))) == 3


@pytest.mark.parametrize(
    "options",
    [
        {"remove_background_flag": True},
        {"layout": "single"},
        {"layout": "double"},
        {"output_pages": "1"},
        {"output_pages": "2"},
        {"pre_rotate": 0},
        {"pre_rotate": 90},
    ],
)
def test_scan_options_are_rejected(
    vector_pdf, tmp_path, forbid_scan_processing, options
):
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="--pdf_type born-digital cannot be combined"):
        pipeline.process_pdf(vector_pdf, output, born_digital_flag=True, **options)
    assert not output.exists()


def test_layout_none_is_allowed(vector_pdf, tmp_path, forbid_scan_processing):
    pipeline.process_pdf(
        vector_pdf, tmp_path / "out", born_digital_flag=True, layout="none"
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_cli_passes_explicit_flag(make_pdf, monkeypatch, enabled):
    received = {}
    monkeypatch.setattr(
        cli, "process_pdf", lambda *args, **kwargs: received.update(kwargs)
    )
    args = [str(make_pdf(["digital"]))]
    if enabled:
        args.extend(["--pdf_type", "born-digital"])
    result = CliRunner().invoke(cli.main, ["enhance", *(args)])
    assert result.exit_code == 0, result.output
    assert received["pdf_type"] == ("born-digital" if enabled else "auto")
    assert "born_digital_flag" not in received


def test_cli_processes_vector_only_pdf(vector_pdf, tmp_path, forbid_scan_processing):
    output = tmp_path / "out"
    result = CliRunner().invoke(
        cli.main,
        [
            "enhance",
            str(vector_pdf),
            "--outdir",
            str(output),
            "--pdf_type",
            "born-digital",
            "--get-text",
            "--get-doi",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (output / vector_pdf.name).read_bytes() == vector_pdf.read_bytes()
    assert json.loads((output / "graphics.meta.json").read_text()) == {
        "input": str(vector_pdf.resolve()),
        "output": str((output / vector_pdf.name).resolve()),
        "doi": [],
        "pages": {
            f"page_{index:03d}": {"index": index, "pgn": None} for index in range(1, 4)
        },
    }


def test_cli_reports_conflicting_options(vector_pdf, tmp_path):
    result = CliRunner().invoke(
        cli.main,
        [
            "enhance",
            str(vector_pdf),
            "--outdir",
            str(tmp_path / "out"),
            "--pdf_type",
            "born-digital",
            "--remove-bg",
        ],
    )
    assert result.exit_code == 1
    assert "--pdf_type born-digital cannot be combined" in result.output
    assert "Done!" not in result.output


def test_help_explains_override():
    result = CliRunner().invoke(cli.main, ["enhance", "--help"])
    assert result.exit_code == 0
    assert "--pdf_type" in result.output
    assert "Image exports still" in " ".join(result.output.split())
