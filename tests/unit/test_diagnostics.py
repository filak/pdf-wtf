"""Tests for read-only PDF diagnostics (``pdfwtf analyze``).

The fixtures use observable PDF properties. The tests do not assume that the
heuristics can establish OCR accuracy.
"""

from hashlib import sha256
import io
import json

from click.testing import CliRunner
from PIL import Image
import pymupdf as fitz
import pytest

from pdfwtf import cli, diagnostics, pipeline
from pdfwtf.diagnostics import (
    ACTIONS,
    ANALYSIS_KIND,
    ANALYSIS_SCHEMA_VERSION,
    COMPOSITIONS,
    DOCUMENT_COMPOSITIONS,
    OCR_NEEDS,
    TEXT_LAYERS,
    TEXT_QUALITIES,
    PdfInspectionError,
    analyze_document,
    text_quality,
    union_area,
)

TEXT = (
    "This document has enough meaningful words\nto identify the page as digital text."
)


def _png(width=200, height=300):
    stream = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(stream, format="PNG")
    return stream.getvalue()


def _digital(page):
    page.insert_text((20, 30), TEXT)


def _scan(page):
    page.insert_image(page.rect, stream=_png())


def _scan_with_ocr(page):
    _scan(page)
    page.insert_text((20, 30), TEXT, render_mode=3)


def _scan_with_short_text(page):
    _scan(page)
    page.insert_text((20, 30), "x y", render_mode=3)


def _photo(page):
    _digital(page)
    # 100 x 150 of a 200 x 300 page: 25 % coverage.
    page.insert_image(fitz.Rect(50, 100, 150, 250), stream=_png(100, 150))


def _vector(page):
    page.draw_rect(fitz.Rect(10, 10, 60, 60))


def _blank(page):
    pass


def _partial_image(page):
    page.insert_image(fitz.Rect(0, 0, 100, 150), stream=_png(100, 150))


@pytest.fixture
def make_doc(tmp_path):
    def create(builders, name="input.pdf", size=(200, 300)):
        path = tmp_path / name
        with fitz.open() as document:
            for build in builders:
                build(document.new_page(width=size[0], height=size[1]))
            document.save(path)
        return path

    return create


def _page(report, number):
    page = report["pages"][number - 1]
    assert page["number"] == number
    return page


# Page classification


def test_born_digital_text_page(make_doc):
    page = _page(analyze_document(make_doc([_digital])), 1)
    assert page["composition"] == "born_digital"
    assert page["text"]["characters"] > 30
    assert page["text"]["words"] > 5
    assert page["images"] == {"count": 0, "coverage": 0.0}
    assert page["ocr"] == {
        "need": "unlikely",
        "text_layer": "present",
        "quality": "apparently_usable",
        "reasons": ["image_coverage_0.00", "meaningful_text"],
    }
    assert page["recommendations"] == []


def test_image_only_scan_needs_ocr(make_doc):
    page = _page(analyze_document(make_doc([_scan])), 1)
    assert page["composition"] == "scanned"
    assert page["images"] == {"count": 1, "coverage": 1.0}
    assert page["ocr"]["need"] == "likely"
    assert page["ocr"]["text_layer"] == "absent"
    assert page["ocr"]["quality"] == "not_applicable"
    assert "no_extractable_text" in page["ocr"]["reasons"]
    assert [item["action"] for item in page["recommendations"]] == ["run_ocr"]


def test_scan_with_existing_text_layer_is_not_sent_to_ocr(make_doc):
    page = _page(analyze_document(make_doc([_scan_with_ocr])), 1)
    # Composition and OCR status are separate: a scan can have a text layer.
    assert page["composition"] == "scanned"
    assert page["ocr"]["text_layer"] == "suspected_ocr"
    assert page["ocr"]["quality"] == "apparently_usable"
    assert page["ocr"]["need"] == "unlikely"
    assert page["text"]["invisible_characters"] > 0
    assert page["text"]["visible_characters"] == 0
    assert "invisible_text_layer" in page["ocr"]["reasons"]


def test_insufficient_text_on_scan_is_suspect(make_doc):
    page = _page(analyze_document(make_doc([_scan_with_short_text])), 1)
    assert page["composition"] == "scanned"
    assert page["ocr"]["quality"] == "suspect"
    assert page["ocr"]["need"] == "likely"
    assert "insufficient_text" in page["ocr"]["reasons"]


def test_digital_text_with_photograph_is_mixed_and_uncertain(make_doc):
    page = _page(analyze_document(make_doc([_photo])), 1)
    assert page["composition"] == "mixed"
    assert page["images"] == {"count": 1, "coverage": 0.25}
    assert page["ocr"]["text_layer"] == "present"
    assert page["ocr"]["need"] == "uncertain"
    assert "images_may_contain_text" in page["ocr"]["reasons"]
    # A photograph is not a reason to recommend OCR.
    assert page["recommendations"] == []


def test_blank_page(make_doc):
    page = _page(analyze_document(make_doc([_blank])), 1)
    assert page["composition"] == "blank"
    assert page["ocr"]["need"] == "unlikely"
    assert [item["action"] for item in page["recommendations"]] == ["review_blank_page"]


def test_vector_only_page_is_not_forced_into_an_ocr_conclusion(make_doc):
    page = _page(analyze_document(make_doc([_vector])), 1)
    assert page["composition"] == "born_digital"
    assert page["drawings"] == 1
    assert page["ocr"]["need"] == "uncertain"
    assert page["ocr"]["text_layer"] == "absent"
    assert "vector_only" in {item["code"] for item in page["findings"]}


def test_partial_image_without_text_is_uncertain(make_doc):
    page = _page(analyze_document(make_doc([_partial_image])), 1)
    assert page["composition"] == "uncertain"
    assert page["images"]["coverage"] == 0.25
    assert page["ocr"]["need"] == "uncertain"
    assert [item["action"] for item in page["recommendations"]] == [
        "check_page_content"
    ]


# Text quality and image coverage


def test_text_quality_counts_suspect_characters():
    quality = text_quality("Good text�\x07 more\ttext\n")
    assert quality["replacement_characters"] == 1
    assert quality["control_characters"] == 1
    assert quality["private_use_characters"] == 1
    # "Good", "text", "more", "text", and the three suspect characters.
    assert quality["characters"] == 19
    assert quality["suspect_character_ratio"] == round(3 / 19, 4)
    assert text_quality("")["suspect_character_ratio"] == 0.0


def test_suspect_characters_mark_page_text_suspect(make_doc, monkeypatch):
    original = fitz.Page.get_text

    def damaged(page, option="text", *args, **kwargs):
        value = original(page, option, *args, **kwargs)
        return value + "�" * 10 if option == "text" else value

    monkeypatch.setattr(fitz.Page, "get_text", damaged)
    page = _page(analyze_document(make_doc([_digital])), 1)
    assert page["ocr"]["quality"] == "suspect"
    assert page["text"]["replacement_characters"] == 10
    assert [item["action"] for item in page["recommendations"]] == [
        "review_ocr_quality"
    ]


def test_union_area_does_not_double_count_overlaps():
    rects = [fitz.Rect(0, 0, 10, 10), fitz.Rect(5, 5, 15, 15), fitz.Rect(0, 0, 10, 10)]
    assert union_area(rects) == pytest.approx(175)
    assert union_area([]) == 0.0


def test_overlapping_and_off_page_images_are_counted_once(make_doc):
    def overlapping(page):
        page.insert_image(fitz.Rect(0, 0, 100, 150), stream=_png(100, 150))
        page.insert_image(fitz.Rect(50, 0, 150, 150), stream=_png(100, 150))

    def off_page(page):
        # Half of the image is outside the 200 pt wide page.
        page.insert_image(fitz.Rect(100, 0, 300, 300), stream=_png())

    report = analyze_document(make_doc([overlapping, off_page]))
    assert _page(report, 1)["images"] == {"count": 2, "coverage": 0.375}
    assert _page(report, 2)["images"]["coverage"] == 0.5


# Sizes, rotation, and document summary


def test_page_sizes_and_declared_rotation(make_doc, tmp_path):
    path = tmp_path / "sizes.pdf"
    with fitz.open() as document:
        _digital(document.new_page(width=612, height=792))
        rotated = document.new_page(width=612, height=792)
        _digital(rotated)
        rotated.set_rotation(90)
        _digital(document.new_page(width=595.28, height=841.89))
        document.save(path)
    report = analyze_document(path)
    rotated_page = _page(report, 2)
    assert rotated_page["size"] == {"width": 612.0, "height": 792.0, "label": "Letter"}
    assert rotated_page["rotation"] == 90
    assert "declared_rotation" in {item["code"] for item in rotated_page["findings"]}
    # Rotation alone never leads to a recommendation.
    assert rotated_page["recommendations"] == []
    summary = report["summary"]
    assert summary["rotations"] == {"0": [1, 3], "90": [2]}
    assert [(item["label"], item["pages"]) for item in summary["page_sizes"]] == [
        ("Letter", [1, 2]),
        ("A4", [3]),
    ]


def test_document_with_different_page_types_is_heterogeneous(make_doc):
    report = analyze_document(
        make_doc([_digital, _scan, _scan_with_ocr, _photo, _blank, _vector])
    )
    summary = report["summary"]
    assert summary["document_composition"] == "heterogeneous"
    assert summary["pages_by_composition"] == {
        "born_digital": 2,
        "scanned": 2,
        "mixed": 1,
        "blank": 1,
        "uncertain": 0,
    }
    assert summary["pages_by_ocr_need"] == {
        "likely": 1,
        "unlikely": 3,
        "uncertain": 2,
    }
    assert summary["pages_without_text"] == [2, 5, 6]
    assert summary["blank_pages"] == [5]
    recommendations = {item["action"]: item for item in summary["recommendations"]}
    # OCR is advised only for the page that needs it, not for every scan.
    assert recommendations["run_ocr"]["pages"] == [2]
    assert "--extract" in recommendations["run_ocr"]["reason"]
    assert summary["recommendations_are_advisory"] is True


def test_document_of_mixed_pages_is_mixed(make_doc):
    summary = analyze_document(make_doc([_photo, _photo, _blank]))["summary"]
    assert summary["document_composition"] == "mixed"


def test_all_scanned_document_needs_no_extract_note(make_doc):
    summary = analyze_document(make_doc([_scan, _scan]))["summary"]
    assert summary["document_composition"] == "scanned"
    (item,) = summary["recommendations"]
    assert item["pages"] == [1, 2]
    assert "--extract" not in item["reason"]


def test_report_schema_numbering_and_summary_consistency(make_doc):
    source = make_doc([_digital, _scan, _photo, _blank])
    report = analyze_document(source)
    assert list(report) == [
        "schema_version",
        "kind",
        "created_at",
        "source",
        "document",
        "summary",
        "pages",
        "warnings",
    ]
    assert report["schema_version"] == ANALYSIS_SCHEMA_VERSION
    assert report["kind"] == ANALYSIS_KIND
    assert report["created_at"].endswith("Z")
    assert report["source"] == {
        "filename": "input.pdf",
        "size_bytes": source.stat().st_size,
        "sha256": sha256(source.read_bytes()).hexdigest(),
    }
    assert report["document"]["page_count"] == 4
    assert report["document"]["metadata"]["title"] is None
    assert report["document"]["encryption"] == {
        "encrypted": False,
        "method": None,
        "permissions": None,
    }
    assert [page["number"] for page in report["pages"]] == [1, 2, 3, 4]
    summary = report["summary"]
    assert summary["inspection"] == "complete"
    assert summary["document_composition"] in DOCUMENT_COMPOSITIONS
    assert sum(summary["pages_by_composition"].values()) == 4
    assert sum(summary["pages_by_ocr_need"].values()) == 4
    for page in report["pages"]:
        assert page["status"] == "ok"
        assert page["composition"] in COMPOSITIONS
        assert page["ocr"]["need"] in OCR_NEEDS
        assert page["ocr"]["text_layer"] in TEXT_LAYERS
        assert page["ocr"]["quality"] in TEXT_QUALITIES
        assert all(item["action"] in ACTIONS for item in page["recommendations"])
    listed = {number for item in summary["recommendations"] for number in item["pages"]}
    assert listed == {
        page["number"] for page in report["pages"] if page["recommendations"]
    }
    assert report["warnings"]
    json.dumps(report)


# Errors


def test_invalid_pdf_is_rejected(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"This is not a PDF.")
    with pytest.raises(PdfInspectionError):
        analyze_document(path)


def test_password_protected_pdf_is_rejected(make_doc, tmp_path):
    source = make_doc([_digital])
    locked = tmp_path / "locked.pdf"
    with fitz.open(source) as document:
        document.save(
            locked,
            encryption=fitz.PDF_ENCRYPT_AES_256,
            user_pw="user",
            owner_pw="owner",
        )
    with pytest.raises(PdfInspectionError, match="requires a password"):
        analyze_document(locked)


def test_owner_password_pdf_reports_encryption_and_permissions(make_doc, tmp_path):
    source = make_doc([_digital])
    restricted = tmp_path / "restricted.pdf"
    with fitz.open(source) as document:
        document.save(
            restricted,
            encryption=fitz.PDF_ENCRYPT_AES_256,
            owner_pw="owner",
            permissions=fitz.PDF_PERM_PRINT,
        )
    report = analyze_document(restricted)
    encryption = report["document"]["encryption"]
    assert encryption["encrypted"] is True
    assert encryption["method"]
    assert encryption["permissions"]["print"] is True
    assert encryption["permissions"]["copy"] is False
    assert any("encrypted" in warning for warning in report["warnings"])


def test_page_error_keeps_entry_and_marks_report_incomplete(make_doc, monkeypatch):
    original = diagnostics._inspect_page

    def fail_second(page):
        if page.number == 1:
            raise RuntimeError("damaged content stream")
        return original(page)

    monkeypatch.setattr(diagnostics, "_inspect_page", fail_second)
    report = analyze_document(make_doc([_digital, _digital, _scan]))
    assert [page["number"] for page in report["pages"]] == [1, 2, 3]
    failed = _page(report, 2)
    assert failed["status"] == "error"
    assert failed["composition"] is None
    assert failed["findings"][0]["code"] == "inspection_error"
    summary = report["summary"]
    assert summary["inspection"] == "incomplete"
    assert summary["error_pages"] == [2]
    assert sum(summary["pages_by_composition"].values()) == 2
    assert "Page 2 could not be inspected." in report["warnings"]


# Read-only behavior and the pipeline entry point


@pytest.fixture
def no_processing(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Analysis must not render, clean, OCR, partition, or export.")

    for name in (
        "run_ocr",
        "_process_scanned",
        "_prepare_pages",
        "is_scanned_or_hybrid",
        "partition_container",
        "export_units",
        "export_images",
        "export_text",
        "export_thumbnails",
    ):
        monkeypatch.setattr(pipeline, name, unexpected)
    monkeypatch.setattr(fitz.Page, "get_pixmap", unexpected)


def test_analysis_does_not_modify_source_or_process(make_doc, tmp_path, no_processing):
    source = make_doc([_digital, _scan, _photo, _blank, _vector])
    original = source.read_bytes()
    result = pipeline.analyze_pdf(source, tmp_path / "out")
    assert source.read_bytes() == original
    assert result.report_path == tmp_path / "out/input.analysis.json"
    assert {path.name for path in (tmp_path / "out").iterdir()} == {
        "input.analysis.json"
    }
    saved = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert saved["pages"] == result.report["pages"]


def test_process_pdf_dispatches_analysis(make_doc, tmp_path):
    result = pipeline.process_pdf(make_doc([_digital]), tmp_path, command="analyze")
    assert result.report_path == tmp_path / "input.analysis.json"


def test_unreadable_pdf_writes_no_report(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.7 broken")
    output = tmp_path / "out"
    with pytest.raises(ValueError):
        pipeline.analyze_pdf(path, output)
    assert not output.exists()


def test_llm_tasks_are_rejected_before_analysis(make_doc, tmp_path, llm_home):
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="does not support the 'analyze' command"):
        pipeline.analyze_pdf(make_doc([_digital]), output, llm_tasks=("clean_html",))
    assert not output.exists()


# CLI


def test_cli_writes_report_and_prints_summary(make_doc, tmp_path):
    source = make_doc([_digital, _scan, _blank], name="issue.pdf")
    output = tmp_path / "out"
    result = CliRunner().invoke(
        cli.main, ["analyze", str(source), "--outdir", str(output)]
    )
    assert result.exit_code == 0, result.output
    report_path = output / "issue.analysis.json"
    assert json.loads(report_path.read_text(encoding="utf-8"))["kind"] == ANALYSIS_KIND
    assert "Composition  :  heterogeneous" in result.output
    assert "Advice       :  run_ocr for pages 2" in result.output
    assert "Advice       :  review_blank_page for pages 3" in result.output
    assert f"Report       :  {report_path}" in result.output


def test_cli_uses_configured_output_directory(make_doc, configured_home):
    source = make_doc([_digital])
    result = CliRunner().invoke(cli.main, ["analyze", str(source)])
    assert result.exit_code == 0, result.output
    assert (configured_home / "instance/_data/out/input.analysis.json").is_file()


def test_cli_incomplete_report_still_succeeds(make_doc, tmp_path, monkeypatch):
    monkeypatch.setattr(
        diagnostics,
        "_inspect_page",
        lambda page: (_ for _ in ()).throw(RuntimeError("bad page")),
    )
    result = CliRunner().invoke(
        cli.main,
        ["analyze", str(make_doc([_digital, _digital])), "--outdir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert "Inspection   :  incomplete. Pages not inspected: 1-2" in result.output


@pytest.mark.parametrize("kind", ["invalid", "locked"])
def test_cli_rejects_unreadable_pdfs(make_doc, tmp_path, kind):
    path = tmp_path / f"{kind}.pdf"
    if kind == "invalid":
        path.write_bytes(b"not a pdf")
    else:
        with fitz.open(make_doc([_digital])) as document:
            document.save(
                path, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o"
            )
    output = tmp_path / "out"
    result = CliRunner().invoke(
        cli.main, ["analyze", str(path), "--outdir", str(output)]
    )
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert not (output / f"{kind}.analysis.json").exists()


def test_cli_rejects_llm_task_and_lists_none(make_doc, tmp_path, llm_home):
    output = tmp_path / "out"
    result = CliRunner().invoke(
        cli.main,
        [
            "analyze",
            str(make_doc([_digital])),
            "--outdir",
            str(output),
            "--llm-task",
            "clean_html",
        ],
    )
    assert result.exit_code == 1
    assert "does not support the 'analyze' command" in result.output
    assert not output.exists()
    listing = CliRunner().invoke(cli.main, ["analyze", "--list-llm-tasks"])
    assert listing.exit_code == 0
    assert "No LLM tasks are configured for 'analyze'." in listing.output


def test_cli_rejects_processing_options_with_hint(make_doc):
    result = CliRunner().invoke(
        cli.main, ["analyze", str(make_doc([_digital])), "--get-text"]
    )
    assert result.exit_code == 2
    assert "Use 'pdfwtf export' for --get-text." in result.output
