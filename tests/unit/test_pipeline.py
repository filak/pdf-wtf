import json
from pathlib import Path
import shutil

from PIL import Image
import pymupdf as fitz
import pytest

from pdfwtf import pipeline


@pytest.fixture
def scans_without_external_tools(monkeypatch):
    monkeypatch.setattr(pipeline, "get_unpaper_version", lambda: (False, "unavailable"))
    monkeypatch.setattr(pipeline, "correct_images_orientation", lambda files: False)
    monkeypatch.setattr(
        pipeline,
        "run_ocr",
        lambda input_pdf, output_pdf, images, **kwargs: shutil.copy2(
            input_pdf, output_pdf
        ),
    )


def page_count(path):
    with fitz.open(path) as doc:
        return len(doc)


def test_digital_exports(make_pdf, configured_home):
    source = make_pdf(["digital", "digital"])
    original = source.read_bytes()
    pipeline.process_pdf(
        source,
        None,
        export_texts_flag=True,
        export_images_flag=True,
        export_thumbs_flag=True,
        dpi=72,
    )
    out = configured_home / "instance/_data/out"
    assert page_count(out / source.name) == 2
    assert len(list((out / "_images_input").glob("*.png"))) == 2
    assert len(list((out / "_thumbs_input").glob("*.jpg"))) == 2
    assert len(list((out / "_texts_input").glob("*.txt"))) == 2
    assert "Page 2 of 2" in (out / "input.txt").read_text()
    assert not (out / "input.meta.json").exists()
    assert source.read_bytes() == original
    assert not list((configured_home / "instance/temp").iterdir())


def test_rejects_source_output(make_pdf):
    source = make_pdf(["digital"])
    original = source.read_bytes()
    with pytest.raises(ValueError, match="differ"):
        pipeline.process_pdf(source, source.parent)
    assert source.read_bytes() == original


def test_rejects_hardlink_to_source(make_pdf, tmp_path):
    source = make_pdf(["digital"])
    out = tmp_path / "out"
    out.mkdir()
    target = out / source.name
    try:
        target.hardlink_to(source)
    except OSError:
        pytest.skip("Hard links are unavailable.")
    with pytest.raises(ValueError, match="differ"):
        pipeline.process_pdf(source, out)


def test_detection_uses_selected_pages(
    make_pdf, tmp_path, scans_without_external_tools, monkeypatch
):
    source = make_pdf(["digital", "scan"])
    observed = []

    def fake_ocr(input_pdf, output_pdf, images, **kwargs):
        observed.append(page_count(input_pdf))
        shutil.copy2(input_pdf, output_pdf)

    monkeypatch.setattr(pipeline, "run_ocr", fake_ocr)
    pipeline.process_pdf(source, tmp_path / "out", extract_pages_str="2", dpi=72)
    assert observed == [1]
    assert page_count(tmp_path / "out/input.pdf") == 1


def test_selected_digital_pages_do_not_use_ocr(make_pdf, tmp_path, monkeypatch):
    source = make_pdf(["digital", "scan"])

    def unexpected(*args, **kwargs):
        pytest.fail("OCR must not run for the selected digital page.")

    monkeypatch.setattr(pipeline, "run_ocr", unexpected)
    pipeline.process_pdf(source, tmp_path / "out", extract_pages_str="1")


def test_prepared_images_reach_ocr_and_stale_exports_are_not_inputs(
    make_pdf, tmp_path, scans_without_external_tools, monkeypatch
):
    source = make_pdf(["scan"])
    out = tmp_path / "out"
    stale = out / "_images_input"
    stale.mkdir(parents=True)
    Image.new("RGB", (50, 50)).save(stale / "page_999.png")
    received = {}

    def rotate(files):
        with Image.open(files[0]) as image:
            image.rotate(90, expand=True).save(files[0])
        return True

    def crop(files, **kwargs):
        with Image.open(files[0]) as image:
            image.crop((0, 0, image.width // 2, image.height)).save(files[0])
        return 1

    def ocr(input_pdf, output_pdf, images, **kwargs):
        received.update(kwargs)
        assert images != stale
        assert len(list(images.glob("*.png"))) == 1
        with fitz.open(input_pdf) as doc:
            assert doc[0].rect.width == pytest.approx(150)
            assert doc[0].rect.height == pytest.approx(200)
        shutil.copy2(input_pdf, output_pdf)

    monkeypatch.setattr(pipeline, "correct_images_orientation", rotate)
    monkeypatch.setattr(pipeline, "crop_dark_background", crop)
    monkeypatch.setattr(pipeline, "run_ocr", ocr)
    pipeline.process_pdf(source, out, remove_background_flag=True, dpi=72)
    assert received["rotated"] is True
    assert received["unpaper_ok"] is False


@pytest.mark.parametrize(
    "options", [{"layout": "double"}, {"output_pages": "2"}, {"pre_rotate": 90}]
)
def test_requested_unpaper_options_require_tool(
    make_pdf, tmp_path, scans_without_external_tools, options
):
    source = make_pdf(["scan"])
    with pytest.raises(pipeline.ProcessingError, match="require unpaper"):
        pipeline.process_pdf(source, tmp_path / "out", dpi=72, **options)
    assert not (tmp_path / "out/input.pdf").exists()


def test_partial_unpaper_failure_preserves_output_and_cleans_workspace(
    make_pdf, tmp_path, configured_home, scans_without_external_tools, monkeypatch
):
    source = make_pdf(["scan", "scan"])
    out = tmp_path / "out"
    out.mkdir()
    existing = out / source.name
    existing.write_bytes(b"previous output")
    monkeypatch.setattr(pipeline, "get_unpaper_version", lambda: (True, "version"))
    calls = []

    def unpaper(input_file, output_file, *args, **kwargs):
        calls.append(input_file)
        if len(calls) == 2:
            raise RuntimeError("page failed")
        Image.new("RGB", (20, 30), "white").save(output_file, format="PPM")

    monkeypatch.setattr(pipeline, "run_unpaper_simple", unpaper)
    with pytest.raises(RuntimeError):
        pipeline.process_pdf(source, out, layout="single", dpi=72)
    assert existing.read_bytes() == b"previous output"
    assert not list((configured_home / "instance/temp").iterdir())


def test_missing_unpaper_page_is_fatal(
    make_pdf, tmp_path, scans_without_external_tools, monkeypatch
):
    monkeypatch.setattr(pipeline, "get_unpaper_version", lambda: (True, "version"))
    monkeypatch.setattr(pipeline, "run_unpaper_simple", lambda *a, **kw: None)
    with pytest.raises(pipeline.ProcessingError, match="expected pages"):
        pipeline.process_pdf(make_pdf(["scan"]), tmp_path / "out", dpi=72)


def test_split_pages_preserve_input_selection_mapping(
    make_pdf, tmp_path, scans_without_external_tools, monkeypatch
):
    monkeypatch.setattr(pipeline, "get_unpaper_version", lambda: (True, "version"))

    def split(input_file, output_file, *args, **kwargs):
        for number in (1, 2):
            target = Path(str(output_file).replace("%03d", f"{number:03d}"))
            Image.new("RGB", (20, 30), "white").save(target, format="PPM")

    monkeypatch.setattr(pipeline, "run_unpaper_simple", split)
    pipeline.process_pdf(
        make_pdf(["scan", "scan"]),
        tmp_path / "out",
        output_pages="2",
        skip_pages_str="2",
        dpi=72,
    )
    assert page_count(tmp_path / "out/input.pdf") == 2


def test_remove_uses_original_input_indices_after_extraction(make_pdf, tmp_path):
    source = make_pdf(["digital", "digital", "digital"])
    pipeline.process_pdf(
        source, tmp_path / "out", extract_pages_str="1-2", skip_pages_str="3"
    )
    assert page_count(tmp_path / "out/input.pdf") == 2


def test_all_pages_cannot_be_removed(make_pdf, tmp_path):
    with pytest.raises(ValueError, match="at least one"):
        pipeline.process_pdf(
            make_pdf(["digital"]), tmp_path / "out", skip_pages_str="1"
        )


def test_ocr_failure_cleans_workspace(
    make_pdf, tmp_path, configured_home, scans_without_external_tools, monkeypatch
):
    def fail(*args, **kwargs):
        raise RuntimeError("OCR failed")

    monkeypatch.setattr(pipeline, "run_ocr", fail)
    with pytest.raises(RuntimeError):
        pipeline.process_pdf(make_pdf(["scan"]), tmp_path / "out", dpi=72)
    assert not list((configured_home / "instance/temp").iterdir())
    assert not (tmp_path / "out/input.pdf").exists()


def test_ocr_page_loss_is_fatal(
    make_pdf, tmp_path, scans_without_external_tools, monkeypatch
):
    replacement = make_pdf(["digital"], "replacement.pdf")
    monkeypatch.setattr(
        pipeline,
        "run_ocr",
        lambda input_pdf, output_pdf, images, **kw: shutil.copy2(
            replacement, output_pdf
        ),
    )
    with pytest.raises(pipeline.ProcessingError, match="page count"):
        pipeline.process_pdf(make_pdf(["scan", "scan"]), tmp_path / "out", dpi=72)
    assert not (tmp_path / "out/input.pdf").exists()


def test_invocations_have_separate_workspaces(
    make_pdf, tmp_path, scans_without_external_tools, monkeypatch
):
    workspaces = []

    def ocr(input_pdf, output_pdf, images, **kw):
        workspaces.append(input_pdf.parent)
        shutil.copy2(input_pdf, output_pdf)

    monkeypatch.setattr(pipeline, "run_ocr", ocr)
    source = make_pdf(["scan"])
    for _ in range(2):
        pipeline.process_pdf(source, tmp_path / "out", dpi=72)
    assert workspaces[0] != workspaces[1]
    assert all(not path.exists() for path in workspaces)


def test_ocrmypdf_options_do_not_repeat_preparation(monkeypatch, tmp_path):
    options = {}
    monkeypatch.setattr(pipeline.ocrmypdf, "ocr", lambda *a, **kw: options.update(kw))
    pipeline.run_ocrmypdf(tmp_path / "in.pdf", tmp_path / "out.pdf", rotated=True)
    assert options["optimize"] == 0
    assert options["rotate_pages"] is False
    assert options["clean"] is False
    assert options["continue_on_soft_render_error"] is False
    assert options["keep_temporary_files"] is False


@pytest.mark.parametrize("backend", ["ocrmypdf", "pymupdf"])
def test_backend_dispatch(monkeypatch, tmp_path, backend):
    calls = []
    monkeypatch.setattr(
        pipeline, "run_pdfocr", lambda *a, **kw: calls.append(("pymupdf", kw))
    )
    monkeypatch.setattr(
        pipeline, "run_ocrmypdf", lambda *a, **kw: calls.append(("ocrmypdf", kw))
    )
    pipeline.run_ocr(
        tmp_path / "in.pdf", tmp_path / "out.pdf", tmp_path, ocrlib=backend, dpi=150
    )
    assert calls[0][0] == backend
    if backend == "pymupdf":
        assert calls[0][1]["dpi"] == 150


@pytest.mark.parametrize(
    "directory, options",
    [
        ("_images_input", {"export_images_flag": True}),
        ("_thumbs_input", {"export_thumbs_flag": True}),
        ("_texts_input", {"export_texts_flag": True}),
    ],
)
def test_export_cleanup_cannot_delete_source(make_pdf, tmp_path, directory, options):
    source = make_pdf(["digital"], f"out/{directory}/input.pdf")
    original = source.read_bytes()
    with pytest.raises(ValueError, match="must not contain"):
        pipeline.process_pdf(source, tmp_path / "out", **options)
    assert source.read_bytes() == original


def test_pymupdf_ocr_merges_in_page_order_and_passes_dpi(tmp_path, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    for name in ("page_999.png", "page_1000.png"):
        Image.new("RGB", (10, 20)).save(images / name)
    seen = []

    class FakePixmap:
        def __init__(self, name):
            self.name = Path(name).stem

        def set_dpi(self, x, y):
            seen.append((self.name, x, y))

        def pdfocr_tobytes(self, language):
            assert language == "eng"
            with fitz.open() as doc:
                page = doc.new_page()
                page.insert_text((20, 30), self.name)
                return doc.tobytes()

    monkeypatch.setattr(pipeline.fitz, "Pixmap", FakePixmap)
    result = tmp_path / "result.pdf"
    pipeline.run_pdfocr(images, result, dpi=150)
    with fitz.open(result) as doc:
        assert len(doc) == 2
        assert doc[0].get_text().strip() == "page_999"
        assert doc[1].get_text().strip() == "page_1000"
    assert seen == [("page_999", 150, 150), ("page_1000", 150, 150)]


@pytest.mark.parametrize("level", [-1, 4, 1.5, "1"])
def test_pipeline_rejects_invalid_optimization(make_pdf, tmp_path, level):
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="Optimization"):
        pipeline.process_pdf(make_pdf(["digital"]), output, optimize=level)
    assert not output.exists()


def test_pdf_only_is_the_default(make_pdf, tmp_path):
    output = tmp_path / "out"
    pipeline.process_pdf(make_pdf(["digital"]), output)
    assert {p.name for p in output.iterdir()} == {"input.pdf"}


def test_no_output_is_rejected_before_directory_creation(make_pdf, tmp_path):
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="No output selected"):
        pipeline.process_pdf(make_pdf(["digital"]), output, no_pdf_flag=True)
    assert not output.exists()


def test_derivatives_can_share_source_directory(make_pdf):
    source = make_pdf(["digital"])
    original = source.read_bytes()
    pipeline.process_pdf(source, source.parent, no_pdf_flag=True, export_json_flag=True)
    assert source.read_bytes() == original
    assert json.loads((source.parent / "input.meta.json").read_text()) == {
        "input": str(source.resolve()),
        "output": None,
        "doi": [],
        "pages": {"page_001": {"index": 1, "pgn": None}},
    }


def test_derivative_flow_preserves_existing_output_pdf(make_pdf, tmp_path):
    source = make_pdf(["digital"])
    output = tmp_path / "out"
    output.mkdir()
    pdf = output / source.name
    pdf.write_bytes(b"existing output")
    pipeline.process_pdf(source, output, no_pdf_flag=True, export_json_flag=True)
    assert pdf.read_bytes() == b"existing output"


def test_derivative_exports_still_protect_source(make_pdf):
    source = make_pdf(["digital"], "_texts_input/input.pdf")
    with pytest.raises(ValueError, match="must not contain"):
        pipeline.process_pdf(
            source,
            source.parent.parent,
            no_pdf_flag=True,
            export_texts_flag=True,
        )


def test_scanned_derivative_flow_cleans_temporary_pdf(
    make_pdf, tmp_path, configured_home, scans_without_external_tools
):
    output = tmp_path / "out"
    pipeline.process_pdf(
        make_pdf(["scan"]), output, no_pdf_flag=True, export_json_flag=True, dpi=72
    )
    assert {p.name for p in output.iterdir()} == {"input.meta.json"}
    assert not list((configured_home / "instance/temp").iterdir())


def test_json_pdf_paths_include_preserved_subdirectories(
    make_pdf, tmp_path, monkeypatch, configured_home
):
    source = make_pdf(["digital"], "in/reports/input.pdf")
    monkeypatch.chdir(tmp_path)
    pipeline.process_pdf(
        source, "out", input_path_prefix=tmp_path / "in", export_json_flag=True
    )
    output_pdf = configured_home / "out/reports/input.pdf"
    metadata = json.loads((output_pdf.parent / "input.meta.json").read_text())
    assert metadata["input"] == str(source.resolve())
    assert metadata["output"] == str(output_pdf.resolve())
    assert Path(metadata["input"]).is_absolute()
    assert Path(metadata["output"]).is_absolute()
    assert output_pdf.is_file()


@pytest.mark.parametrize("no_pdf", [False, True])
def test_unit_exports_protect_source(make_pdf, tmp_path, no_pdf):
    source = make_pdf(["digital"], "out/_units_input/input.pdf")
    original = source.read_bytes()
    with pytest.raises(ValueError, match="must not contain"):
        pipeline.process_pdf(
            source,
            tmp_path / "out",
            born_digital_flag=True,
            export_html_flag=True,
            no_pdf_flag=no_pdf,
        )
    assert source.read_bytes() == original


@pytest.mark.parametrize("existing_output", [False, True])
def test_unit_publication_failure_preserves_pdf(
    make_pdf, tmp_path, configured_home, monkeypatch, existing_output
):
    source = make_pdf(["digital"])
    output = tmp_path / "out"
    output.mkdir()
    pdf = output / source.name
    if existing_output:
        pdf.write_bytes(b"previous PDF")

    def fail(*args):
        raise OSError("Unit publication failed")

    monkeypatch.setattr(pipeline, "publish_unit_directory", fail)
    with pytest.raises(OSError, match="Unit publication failed"):
        pipeline.process_pdf(
            source, output, born_digital_flag=True, export_html_flag=True
        )
    if existing_output:
        assert pdf.read_bytes() == b"previous PDF"
    else:
        assert not pdf.exists()
    assert not list((configured_home / "instance/temp").iterdir())


def test_scanned_pdf_type_forces_scan_processing(make_pdf, tmp_path, monkeypatch):
    source = make_pdf(["digital"])
    observed = []

    def unexpected(*args, **kwargs):
        pytest.fail("Explicit scanned type must bypass automatic detection.")

    def prepare(source, *args):
        observed.append("prepare")
        return [], False

    def ocr(source, destination, *args, **kwargs):
        observed.append("ocr")
        shutil.copy2(source, destination)

    monkeypatch.setattr(pipeline, "is_scanned_or_hybrid", unexpected)
    monkeypatch.setattr(pipeline, "_process_scanned", prepare)
    monkeypatch.setattr(pipeline, "run_ocr", ocr)
    pipeline.process_pdf(source, tmp_path / "out", pdf_type="scanned", dpi=72)
    assert observed == ["prepare", "ocr"]
    assert page_count(tmp_path / "out/input.pdf") == 1


def test_born_digital_pdf_type_bypasses_detection_and_ocr(
    make_pdf, tmp_path, monkeypatch
):
    def unexpected(*args, **kwargs):
        pytest.fail("Born-digital type must bypass detection and scan processing.")

    source = make_pdf(["scan"])
    monkeypatch.setattr(pipeline, "is_scanned_or_hybrid", unexpected)
    monkeypatch.setattr(pipeline, "_process_scanned", unexpected)
    monkeypatch.setattr(pipeline, "run_ocr", unexpected)
    pipeline.process_pdf(source, tmp_path / "out", pdf_type="born-digital")
    assert page_count(tmp_path / "out/input.pdf") == 1
