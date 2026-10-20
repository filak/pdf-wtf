import functools
import json
from pathlib import Path
import shutil

from PIL import Image
import pymupdf as fitz
import pytest

from pdfwtf import container_partition, pipeline
from pdfwtf.configuration import load_config
from pdfwtf.container_partition import PlanValidationError, write_json_document
from pdfwtf.llm_tasks import LLMTaskError
from test_container_partition import make_structured_pdf, reviewed_plan, unit


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


@pytest.fixture
def no_enhancement(monkeypatch):
    """Fail if any cleanup, scan detection, or OCR code runs."""

    def unexpected(*args, **kwargs):
        pytest.fail("Partitioning and export must not run cleanup or OCR.")

    for name in (
        "run_ocr",
        "run_ocrmypdf",
        "run_pdfocr",
        "_process_scanned",
        "is_scanned_or_hybrid",
        "run_unpaper_simple",
        "crop_dark_background",
        "correct_images_orientation",
    ):
        monkeypatch.setattr(pipeline, name, unexpected)


def page_count(path):
    with fitz.open(path) as doc:
        return len(doc)


# Enhancement


def test_enhance_writes_prepared_pdf_and_report_only(make_pdf, tmp_path):
    source = make_pdf(["digital", "digital"])
    original = source.read_bytes()
    output = tmp_path / "out"
    result = pipeline.enhance_pdf(source, output, extract_pages_str="1-2")
    assert result.pdf == output / "input.prepared.pdf"
    assert result.report == output / "input.enhance.json"
    assert {path.name for path in output.iterdir()} == {
        "input.prepared.pdf",
        "input.enhance.json",
    }
    report = json.loads(result.report.read_text(encoding="utf-8"))
    assert report["kind"] == "enhancement"
    assert report["input"]["filename"] == "input.pdf"
    assert report["input"]["page_count"] == 2
    assert report["output"]["filename"] == "input.prepared.pdf"
    assert report["scan_processing"] is False
    assert report["settings"]["extract_pages"] == "1-2"
    assert source.read_bytes() == original


def test_enhance_preserves_source_in_same_directory(make_pdf):
    source = make_pdf(["digital"])
    original = source.read_bytes()
    pipeline.enhance_pdf(source, source.parent)
    assert source.read_bytes() == original
    assert page_count(source.parent / "input.prepared.pdf") == 1


def test_rejects_hardlink_to_source(make_pdf, tmp_path):
    source = make_pdf(["digital"])
    out = tmp_path / "out"
    out.mkdir()
    target = out / "input.prepared.pdf"
    try:
        target.hardlink_to(source)
    except OSError:
        pytest.skip("Hard links are unavailable.")
    with pytest.raises(ValueError, match="differ"):
        pipeline.enhance_pdf(source, out)


def test_detection_uses_selected_pages(
    make_pdf, tmp_path, scans_without_external_tools, monkeypatch
):
    source = make_pdf(["digital", "scan"])
    observed = []

    def fake_ocr(input_pdf, output_pdf, images, **kwargs):
        observed.append(page_count(input_pdf))
        shutil.copy2(input_pdf, output_pdf)

    monkeypatch.setattr(pipeline, "run_ocr", fake_ocr)
    pipeline.enhance_pdf(source, tmp_path / "out", extract_pages_str="2", dpi=72)
    assert observed == [1]
    assert page_count(tmp_path / "out/input.prepared.pdf") == 1


def test_selected_digital_pages_do_not_use_ocr(make_pdf, tmp_path, monkeypatch):
    source = make_pdf(["digital", "scan"])

    def unexpected(*args, **kwargs):
        pytest.fail("OCR must not run for the selected digital page.")

    monkeypatch.setattr(pipeline, "run_ocr", unexpected)
    pipeline.enhance_pdf(source, tmp_path / "out", extract_pages_str="1")


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
    result = pipeline.enhance_pdf(source, out, remove_background_flag=True, dpi=72)
    assert received["rotated"] is True
    assert received["unpaper_ok"] is False
    report = json.loads(result.report.read_text(encoding="utf-8"))
    assert report["scan_processing"] is True
    assert report["settings"]["remove_background"] is True


@pytest.mark.parametrize(
    "options", [{"layout": "double"}, {"output_pages": "2"}, {"pre_rotate": 90}]
)
def test_requested_unpaper_options_require_tool(
    make_pdf, tmp_path, scans_without_external_tools, options
):
    source = make_pdf(["scan"])
    with pytest.raises(pipeline.ProcessingError, match="require unpaper"):
        pipeline.enhance_pdf(source, tmp_path / "out", dpi=72, **options)
    assert not (tmp_path / "out/input.prepared.pdf").exists()


def test_partial_unpaper_failure_preserves_output_and_cleans_workspace(
    make_pdf, tmp_path, configured_home, scans_without_external_tools, monkeypatch
):
    source = make_pdf(["scan", "scan"])
    out = tmp_path / "out"
    out.mkdir()
    existing = out / "input.prepared.pdf"
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
        pipeline.enhance_pdf(source, out, layout="single", dpi=72)
    assert existing.read_bytes() == b"previous output"
    assert not (out / "input.enhance.json").exists()
    assert not list((configured_home / "instance/temp").iterdir())


def test_missing_unpaper_page_is_fatal(
    make_pdf, tmp_path, scans_without_external_tools, monkeypatch
):
    monkeypatch.setattr(pipeline, "get_unpaper_version", lambda: (True, "version"))
    monkeypatch.setattr(pipeline, "run_unpaper_simple", lambda *a, **kw: None)
    with pytest.raises(pipeline.ProcessingError, match="expected pages"):
        pipeline.enhance_pdf(make_pdf(["scan"]), tmp_path / "out", dpi=72)


def test_split_pages_preserve_input_selection_mapping(
    make_pdf, tmp_path, scans_without_external_tools, monkeypatch
):
    monkeypatch.setattr(pipeline, "get_unpaper_version", lambda: (True, "version"))

    def split(input_file, output_file, *args, **kwargs):
        for number in (1, 2):
            target = Path(str(output_file).replace("%03d", f"{number:03d}"))
            Image.new("RGB", (20, 30), "white").save(target, format="PPM")

    monkeypatch.setattr(pipeline, "run_unpaper_simple", split)
    result = pipeline.enhance_pdf(
        make_pdf(["scan", "scan"]),
        tmp_path / "out",
        output_pages="2",
        skip_pages_str="2",
        dpi=72,
    )
    assert page_count(result.pdf) == 2
    report = json.loads(result.report.read_text(encoding="utf-8"))
    assert report["pages"]["pages_per_input_page"] == 2
    assert report["pages"]["removed"] == [2]


def test_remove_uses_original_input_indices_after_extraction(make_pdf, tmp_path):
    source = make_pdf(["digital", "digital", "digital"])
    pipeline.enhance_pdf(
        source, tmp_path / "out", extract_pages_str="1-2", skip_pages_str="3"
    )
    assert page_count(tmp_path / "out/input.prepared.pdf") == 2


def test_all_pages_cannot_be_removed(make_pdf, tmp_path):
    with pytest.raises(ValueError, match="at least one"):
        pipeline.enhance_pdf(
            make_pdf(["digital"]), tmp_path / "out", skip_pages_str="1"
        )


def test_ocr_failure_cleans_workspace(
    make_pdf, tmp_path, configured_home, scans_without_external_tools, monkeypatch
):
    def fail(*args, **kwargs):
        raise RuntimeError("OCR failed")

    monkeypatch.setattr(pipeline, "run_ocr", fail)
    with pytest.raises(RuntimeError):
        pipeline.enhance_pdf(make_pdf(["scan"]), tmp_path / "out", dpi=72)
    assert not list((configured_home / "instance/temp").iterdir())
    assert not (tmp_path / "out/input.prepared.pdf").exists()


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
        pipeline.enhance_pdf(make_pdf(["scan", "scan"]), tmp_path / "out", dpi=72)
    assert not (tmp_path / "out/input.prepared.pdf").exists()


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
        pipeline.enhance_pdf(source, tmp_path / "out", dpi=72)
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
def test_enhance_rejects_invalid_optimization(make_pdf, tmp_path, level):
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="Optimization"):
        pipeline.enhance_pdf(make_pdf(["digital"]), output, optimize=level)
    assert not output.exists()


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
    pipeline.enhance_pdf(source, tmp_path / "out", pdf_type="scanned", dpi=72)
    assert observed == ["prepare", "ocr"]
    assert page_count(tmp_path / "out/input.prepared.pdf") == 1


def test_born_digital_pdf_type_bypasses_detection_and_ocr(
    make_pdf, tmp_path, monkeypatch
):
    def unexpected(*args, **kwargs):
        pytest.fail("Born-digital type must bypass detection and scan processing.")

    source = make_pdf(["scan"])
    monkeypatch.setattr(pipeline, "is_scanned_or_hybrid", unexpected)
    monkeypatch.setattr(pipeline, "_process_scanned", unexpected)
    monkeypatch.setattr(pipeline, "run_ocr", unexpected)
    pipeline.enhance_pdf(source, tmp_path / "out", pdf_type="born-digital")
    assert page_count(tmp_path / "out/input.prepared.pdf") == 1


# Export


def test_export_alone_writes_derivatives_without_a_pdf(
    make_pdf, configured_home, no_enhancement
):
    source = make_pdf(["digital", "digital"])
    original = source.read_bytes()
    pipeline.export_pdf(
        source,
        None,
        export_texts_flag=True,
        export_images_flag=True,
        export_thumbs_flag=True,
        dpi=72,
    )
    out = configured_home / "instance/_data/out"
    assert not list(out.glob("*.pdf"))
    assert len(list((out / "_images_input").glob("*.png"))) == 2
    assert len(list((out / "_thumbs_input").glob("*.jpg"))) == 2
    assert len(list((out / "_texts_input").glob("*.txt"))) == 2
    assert "Page 2 of 2" in (out / "input.txt").read_text()
    assert not (out / "input.meta.json").exists()
    assert source.read_bytes() == original
    assert not list((configured_home / "instance/temp").iterdir())


def test_export_scanned_pdf_never_runs_ocr(
    make_pdf, tmp_path, configured_home, no_enhancement
):
    output = tmp_path / "out"
    pipeline.export_pdf(
        make_pdf(["scan"]),
        output,
        export_json_flag=True,
        export_texts_flag=True,
        export_images_flag=True,
        dpi=72,
    )
    # Export renders pages but does not create text from images.
    assert len(list((output / "_images_input").glob("*.png"))) == 1
    assert (output / "_texts_input/page_001.txt").read_text() == ""
    assert not list((configured_home / "instance/temp").iterdir())


def test_export_alone_writes_direct_unit_html(make_pdf, tmp_path, no_enhancement):
    source = make_pdf(["digital"])
    output = tmp_path / "out"
    pipeline.export_pdf(source, output, export_html_flag=True, document_type="unit")
    manifest = json.loads((output / "_units_input/manifest.json").read_text())
    assert [item["html"] for item in manifest["units"]] == ["unit-001.html"]


def test_export_alone_writes_normalized_direct_unit_html(
    make_pdf, tmp_path, no_enhancement, monkeypatch
):
    from pdfwtf.html_normalize import NormalizationResult

    monkeypatch.setattr(
        container_partition,
        "normalize_extracted_html",
        lambda _html: NormalizationResult("<p>Normalized HTML.</p>"),
    )
    source = make_pdf(["digital"])
    output = tmp_path / "out"

    pipeline.export_pdf(
        source,
        output,
        export_html_normalized_flag=True,
        document_type="unit",
    )

    assert (output / "_units_input/unit-001.html").read_text("utf-8") == (
        "<p>Normalized HTML.</p>"
    )


def test_direct_html_does_not_use_partition_heuristics(
    make_pdf, tmp_path, no_enhancement, monkeypatch
):
    def fail(*args, **kwargs):
        raise AssertionError("Direct HTML must not partition the PDF.")

    monkeypatch.setattr(pipeline, "partition_container", fail)
    monkeypatch.setattr(container_partition, "partition_container", fail)
    source = make_pdf(["digital", "digital"])
    output = tmp_path / "out"
    pipeline.export_pdf(source, output, export_html_flag=True)
    units = output / "_units_input"
    manifest = json.loads((units / "manifest.json").read_text())
    assert manifest["document_type"] == "unit"
    [entry] = manifest["units"]
    assert entry["type"] == "unknown"
    assert entry["input_pages"] == {"start": 1, "end": 2}
    assert entry["selected_input_pages"] == [1, 2]
    assert (units / "unit-001.html").read_text("utf-8")


def test_export_writes_unit_pdfs_from_plan(tmp_path, no_enhancement):
    source = make_structured_pdf(tmp_path / "issue.pdf", 3)
    plan_path = tmp_path / "plan.json"
    write_json_document(
        reviewed_plan(source, [unit("chapter-a", 1, 2), unit("chapter-b", 3, 3)]),
        plan_path,
    )
    output = tmp_path / "out"
    pipeline.export_pdf(source, output, plan_path=plan_path, export_unit_pdfs_flag=True)
    units = output / "_units_issue"
    assert page_count(units / "chapter-a.pdf") == 2
    assert page_count(units / "chapter-b.pdf") == 1


def test_unit_pdfs_require_plan(make_pdf, tmp_path):
    with pytest.raises(pipeline.ProcessingError, match="reviewed plan"):
        pipeline.export_pdf(
            make_pdf(["digital"]),
            tmp_path / "out",
            export_html_flag=True,
            export_unit_pdfs_flag=True,
        )


def test_prepared_pdf_is_partitioned_and_exported_without_reenhancement(
    tmp_path, monkeypatch
):
    source = make_structured_pdf(tmp_path / "issue.pdf", 3)
    output = tmp_path / "out"
    prepared = pipeline.enhance_pdf(source, output, extract_pages_str="1-2").pdf

    def unexpected(*args, **kwargs):
        pytest.fail("Partitioning and export must not repeat enhancement.")

    for name in ("enhance_pdf", "_prepare_pages", "run_ocr", "is_scanned_or_hybrid"):
        monkeypatch.setattr(pipeline, name, unexpected)
    partition_path = pipeline.partition_pdf(prepared, output, document_type="book")
    assert partition_path == output / "issue.prepared.partition.json"
    plan_path = tmp_path / "plan.json"
    write_json_document(reviewed_plan(prepared, [unit("chapter-a", 1, 2)]), plan_path)
    for _ in range(2):
        pipeline.export_pdf(
            prepared,
            output,
            plan_path=plan_path,
            export_html_flag=True,
            export_unit_pdfs_flag=True,
        )
    assert page_count(output / "_units_issue.prepared/chapter-a.pdf") == 2


def test_plan_for_another_pdf_fails_clearly(tmp_path):
    source = make_structured_pdf(tmp_path / "issue.pdf", 3)
    prepared = pipeline.enhance_pdf(source, tmp_path / "out", skip_pages_str="3").pdf
    plan_path = tmp_path / "plan.json"
    # The plan describes the original PDF, but the prepared PDF is exported.
    write_json_document(reviewed_plan(source, [unit("chapter-a", 1, 3)]), plan_path)
    with pytest.raises(PlanValidationError, match="created for a different PDF"):
        pipeline.export_pdf(
            prepared, tmp_path / "export", plan_path=plan_path, export_html_flag=True
        )


def test_plan_follows_content_when_pdf_is_renamed(tmp_path):
    source = make_structured_pdf(tmp_path / "issue.pdf", 2)
    plan_path = tmp_path / "plan.json"
    write_json_document(reviewed_plan(source, [unit("chapter-a", 1, 2)]), plan_path)
    moved = tmp_path / "moved" / "renamed.pdf"
    moved.parent.mkdir()
    shutil.copy2(source, moved)
    pipeline.export_pdf(
        moved, tmp_path / "out", plan_path=plan_path, export_html_flag=True
    )
    assert (tmp_path / "out/_units_renamed/chapter-a.html").is_file()


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
        pipeline.export_pdf(source, tmp_path / "out", **options)
    assert source.read_bytes() == original


def test_no_export_output_is_rejected_before_directory_creation(make_pdf, tmp_path):
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="No output selected"):
        pipeline.export_pdf(make_pdf(["digital"]), output)
    assert not output.exists()


def test_derivatives_can_share_source_directory(make_pdf):
    source = make_pdf(["digital"])
    original = source.read_bytes()
    pipeline.export_pdf(source, source.parent, export_json_flag=True)
    assert source.read_bytes() == original
    assert json.loads((source.parent / "input.meta.json").read_text()) == {
        "input": str(source.resolve()),
        "output": None,
        "doi": [],
        "pages": {"page_001": {"index": 1, "pgn": None}},
    }


def test_export_preserves_existing_pdfs(make_pdf, tmp_path):
    source = make_pdf(["digital"])
    output = tmp_path / "out"
    output.mkdir()
    pdf = output / "input.prepared.pdf"
    pdf.write_bytes(b"existing output")
    pipeline.export_pdf(source, output, export_json_flag=True)
    assert pdf.read_bytes() == b"existing output"


def test_derivative_exports_still_protect_source(make_pdf):
    source = make_pdf(["digital"], "_texts_input/input.pdf")
    with pytest.raises(ValueError, match="must not contain"):
        pipeline.export_pdf(source, source.parent.parent, export_texts_flag=True)


def test_json_paths_include_preserved_subdirectories(
    make_pdf, tmp_path, monkeypatch, configured_home
):
    source = make_pdf(["digital"], "in/reports/input.pdf")
    monkeypatch.chdir(tmp_path)
    pipeline.export_pdf(
        source, "out", input_path_prefix=tmp_path / "in", export_json_flag=True
    )
    metadata = json.loads((configured_home / "out/reports/input.meta.json").read_text())
    assert metadata["input"] == str(source.resolve())
    assert Path(metadata["input"]).is_absolute()
    assert metadata["output"] is None


def test_enhance_preserves_subdirectories(make_pdf, tmp_path, configured_home):
    source = make_pdf(["digital"], "in/reports/input.pdf")
    pipeline.enhance_pdf(source, "out", input_path_prefix=tmp_path / "in")
    assert (configured_home / "out/reports/input.prepared.pdf").is_file()


def test_unit_exports_protect_source(make_pdf, tmp_path):
    source = make_pdf(["digital"], "out/_units_input/input.pdf")
    original = source.read_bytes()
    with pytest.raises(ValueError, match="must not contain"):
        pipeline.export_pdf(source, tmp_path / "out", export_html_flag=True)
    assert source.read_bytes() == original


def test_unit_publication_failure_leaves_no_partial_units(
    make_pdf, tmp_path, configured_home, monkeypatch
):
    source = make_pdf(["digital"])
    output = tmp_path / "out"

    def fail(*args):
        raise OSError("Unit publication failed")

    monkeypatch.setattr(pipeline, "publish_unit_directory", fail)
    with pytest.raises(OSError, match="Unit publication failed"):
        pipeline.export_pdf(source, output, export_html_flag=True)
    assert not (output / "_units_input").exists()
    assert not list((configured_home / "instance/temp").iterdir())


# Partitioning


def test_partition_never_runs_cleanup_or_ocr(make_pdf, tmp_path, no_enhancement):
    destination = pipeline.partition_pdf(make_pdf(["scan"]), tmp_path / "out")
    assert json.loads(destination.read_text())["kind"] == "partition"


# Dispatcher, configuration, and command identity


@pytest.mark.parametrize(
    "command, options, expected",
    [
        ("analyze", {}, "analyze_pdf"),
        ("enhance", {}, "enhance_pdf"),
        ("partition", {}, "partition_pdf"),
        ("export", {"export_texts_flag": True}, "export_pdf"),
    ],
)
def test_process_pdf_dispatches_with_config(
    make_pdf, tmp_path, monkeypatch, command, options, expected
):
    calls = []
    config = load_config()

    # Keep the real signature so the dispatcher checks the real options.
    @functools.wraps(pipeline.OPERATIONS[command])
    def record(input_pdf, output_dir, **kwargs):
        calls.append((expected, kwargs))

    monkeypatch.setitem(pipeline.OPERATIONS, command, record)
    pipeline.process_pdf(
        make_pdf(["digital"]), tmp_path, command=command, config=config, **options
    )
    assert calls == [(expected, {"config": config, **options})]


def test_process_pdf_maps_partition_flag(make_pdf, tmp_path):
    result = pipeline.process_pdf(make_pdf(["digital"]), tmp_path, partition_flag=True)
    assert result == tmp_path / "input.partition.json"


def test_process_pdf_rejects_misplaced_options(make_pdf, tmp_path):
    with pytest.raises(ValueError, match="'export_texts_flag' is not supported"):
        pipeline.process_pdf(make_pdf(["digital"]), tmp_path, export_texts_flag=True)
    with pytest.raises(ValueError, match="'pdf_type' is not supported"):
        pipeline.process_pdf(
            make_pdf(["digital"]),
            tmp_path,
            command="export",
            pdf_type="born-digital",
            export_texts_flag=True,
        )
    with pytest.raises(ValueError, match="Unknown command"):
        pipeline.process_pdf(make_pdf(["digital"]), tmp_path, command="diagnose")


@pytest.mark.parametrize(
    "operation, command, options",
    [
        ("analyze_pdf", "analyze", {}),
        ("enhance_pdf", "enhance", {}),
        ("partition_pdf", "partition", {}),
        ("export_pdf", "export", {"export_texts_flag": True}),
    ],
)
def test_entry_points_validate_tasks_with_own_identity(
    make_pdf, tmp_path, monkeypatch, operation, command, options
):
    seen = []
    config = load_config()

    def spy(received_config, names, received_command, *, get_html):
        seen.append((received_config, tuple(names), received_command))
        return ()

    monkeypatch.setattr(pipeline, "validate_task_selection", spy)
    getattr(pipeline, operation)(
        make_pdf(["digital"]), tmp_path / "out", config=config, **options
    )
    assert seen == [(config, (), command)]


@pytest.mark.parametrize(
    "operation, options",
    [
        ("analyze_pdf", {}),
        ("enhance_pdf", {}),
        ("partition_pdf", {}),
        ("export_pdf", {"export_texts_flag": True}),
    ],
)
def test_incompatible_task_fails_before_processing(
    make_pdf, tmp_path, llm_home, operation, options
):
    output = tmp_path / "out"
    task = "clean_html"
    if operation == "export_pdf":
        # The export stage needs unit HTML, so plain derivatives are incompatible.
        match = "requires --get-html"
    else:
        match = "does not support"
    with pytest.raises(LLMTaskError, match=match):
        getattr(pipeline, operation)(
            make_pdf(["digital"]), output, llm_tasks=(task,), **options
        )
    assert not output.exists()
    assert not list((llm_home / "instance/temp").glob("*"))
