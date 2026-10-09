import io
import json

from click.testing import CliRunner
from PIL import Image
import pymupdf as fitz
import pytest

from pdfwtf import cli
from pdfwtf.container_analysis import (
    PlanValidationError,
    SCHEMA_VERSION,
    _normalize_text,
    analyze_container,
    export_units,
    source_identity,
    validate_plan,
)


def make_structured_pdf(path, page_count=3):
    with fitz.open() as document:
        for number in range(1, page_count + 1):
            page = document.new_page(width=400, height=500)
            page.insert_text((30, 40), f"Unit {number} title", fontsize=18)
            page.insert_text(
                (30, 80),
                f"Body text for absolute input page {number} with enough words.",
                fontsize=10,
            )
            page.insert_text((190, 480), str(100 + number), fontsize=9)
        document.save(path)
    return path


def reviewed_plan(source, units, document_type="book"):
    analysis = analyze_container(source, document_type)
    pages = []
    for page in analysis["pages"]:
        input_page = page["input_page"]
        pages.append(
            {
                **page,
                "selected": True,
                "unit_ids": [
                    item["id"]
                    for item in units
                    if item["input_pages"]["start"]
                    <= input_page
                    <= item["input_pages"]["end"]
                ],
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "plan",
        "source": analysis["source"],
        "document_type": document_type,
        "pages": pages,
        "units": units,
    }


def unit(unit_id, start, end, selected=True):
    return {
        "id": unit_id,
        "title": f"Title {unit_id}",
        "type": "chapter",
        "selected": selected,
        "boundary_status": "confirmed",
        "input_pages": {"start": start, "end": end},
    }


def test_unit_analysis_has_contract_and_structure(make_pdf):
    source = make_pdf(["digital", "digital"])
    analysis = analyze_container(source, "unit")
    assert analysis["schema_version"] == "1.2"
    assert analysis["source"]["sha256"] == source_identity(source)["sha256"]
    assert analysis["source"]["page_count"] == 2
    assert analysis["source"]["bibliographic"]["kind"] == "journal"
    assert analysis["document_type"] == "unit"
    assert analysis["units"][0]["input_pages"] == {"start": 1, "end": 2}
    assert analysis["coordinate_system"]["origin"] == "top-left"
    assert "structure" not in analysis
    assert [page["input_page"] for page in analysis["pages"]] == [1, 2]
    assert analysis["pages"][0]["main_title"]["text"]
    assert analysis["pages"][0]["unit_ids"] == ["unit-001"]
    assert analysis["units"][0]["type"] in {
        "article",
        "chapter",
        "unknown",
    }


def test_extracted_text_normalization():
    assert (
        _normalize_text("  Ofﬁce\u00a0A\u200bB\u00adC&#x02014;D\ufffd ")
        == "Office ABC—D"
    )


def test_analysis_extracts_compact_journal_source(tmp_path):
    source = tmp_path / "journal.pdf"
    with fitz.open() as document:
        page = document.new_page(width=400, height=500)
        page.insert_text((30, 40), "Journal of Testing", fontsize=18)
        page.insert_text((30, 80), "ISSN 1234-567X", fontsize=10)
        page.insert_text((30, 110), "3-4/2022, vol. 12", fontsize=10)
        document.save(source)
    analysis = analyze_container(source, "journal-issue")
    assert analysis["source"]["bibliographic"] == {
        "kind": "journal",
        "journal_title": "Journal of Testing",
        "issn": "1234-567X",
        "year": "2022",
        "volume": "12",
        "issue": "3-4",
    }


def test_analysis_extracts_compact_book_source(tmp_path):
    source = tmp_path / "book.pdf"
    with fitz.open() as document:
        document.set_metadata({"title": "Collected Essays"})
        page = document.new_page(width=400, height=500)
        page.insert_text((30, 40), "Collected Essays", fontsize=18)
        page.insert_text((30, 80), "ISBN 978-1-4028-9462-6", fontsize=10)
        page.insert_text((30, 110), "Volume 2", fontsize=10)
        document.save(source)
    analysis = analyze_container(source, "book")
    assert analysis["source"]["bibliographic"] == {
        "kind": "book",
        "book_title": "Collected Essays",
        "isbn": "9781402894626",
        "volume": "2",
    }


def test_analysis_classifies_page_unit_and_auto_document_type(tmp_path):
    source = tmp_path / "editorial.pdf"
    with fitz.open() as document:
        page = document.new_page(width=400, height=500)
        page.insert_text((30, 40), "Editorial", fontsize=18)
        page.insert_text((30, 75), "Alice Smith", fontsize=10)
        page.insert_text((30, 105), "Abstract", fontsize=10)
        page.insert_text(
            (30, 135),
            "This editorial contains enough text to describe the page structure.",
            fontsize=10,
        )
        document.save(source)
    analysis = analyze_container(source, "auto")
    assert analysis["document_type"] == "unit"
    assert analysis["units"][0]["type"] == "editorial"
    assert analysis["pages"][0]["page_type"] == "normal"


def test_analysis_extracts_doi_candidates_for_each_unit(tmp_path):
    source = tmp_path / "articles.pdf"
    with fitz.open() as document:
        for number, doi in enumerate(("10.1234/first", "10.5678/second"), start=1):
            page = document.new_page(width=400, height=500)
            page.insert_text((30, 40), f"Article {number} title", fontsize=18)
            page.insert_text((30, 75), f"Author {number} Name", fontsize=10)
            page.insert_text((30, 105), "Abstract", fontsize=10)
            page.insert_text((30, 135), f"https://doi.org/{doi}", fontsize=10)
        document.save(source)

    analysis = analyze_container(source, "journal-issue")

    assert [item["input_pages"] for item in analysis["units"]] == [
        {"start": 1, "end": 1},
        {"start": 2, "end": 2},
    ]
    assert [item["doi"] for item in analysis["units"]] == [
        ["10.1234/first"],
        ["10.5678/second"],
    ]


def test_analysis_detects_full_page_advertisement(tmp_path):
    source = tmp_path / "advertisement.pdf"
    image = Image.new("RGB", (400, 500), "navy")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    with fitz.open() as document:
        page = document.new_page(width=400, height=500)
        page.insert_image(page.rect, stream=stream.getvalue())
        page.insert_text((30, 40), "Advertisement", fontsize=18, color=(1, 1, 1))
        document.save(source)
    analysis = analyze_container(source, "unit")
    assert analysis["pages"][0]["page_type"] == "full-page-advertisement"
    assert analysis["pages"][0]["figures"][0]["bbox"] == [0.0, 0.0, 400.0, 500.0]


def test_plan_allows_gaps_and_exports_only_selected_ranges(tmp_path):
    source = make_structured_pdf(tmp_path / "source.pdf", 4)
    plan = reviewed_plan(
        source,
        [
            unit("first", 1, 1),
            unit("third", 3, 3),
            unit("not-selected", 4, 4, selected=False),
        ],
    )
    selected = validate_plan(plan, source)
    destination = tmp_path / "units"
    export_units(source, plan, selected, destination, include_html=True)
    manifest = json.loads((destination / "manifest.json").read_text("utf-8"))
    assert [entry["id"] for entry in manifest["units"]] == ["first", "third"]
    first = json.loads((destination / "first.metadata.json").read_text("utf-8"))
    third = json.loads((destination / "third.metadata.json").read_text("utf-8"))
    assert {block["input_page"] for block in first["semantic_blocks"]} == {1}
    assert {block["input_page"] for block in third["semantic_blocks"]} == {3}
    assert not (destination / "second.metadata.json").exists()
    assert not (destination / "not-selected.metadata.json").exists()


def test_two_column_reading_order_with_full_width_heading(tmp_path):
    source = tmp_path / "columns.pdf"
    with fitz.open() as document:
        page = document.new_page(width=400, height=500)
        page.insert_textbox(
            fitz.Rect(20, 20, 380, 75),
            "A full width heading across the complete page",
            fontsize=18,
        )
        page.insert_textbox(
            fitz.Rect(20, 80, 185, 300),
            "Left column first paragraph with several words.",
            fontsize=10,
        )
        page.insert_textbox(
            fitz.Rect(215, 80, 380, 300),
            "Right column second paragraph with several words.",
            fontsize=10,
        )
        document.save(source)
    plan = reviewed_plan(source, [unit("article", 1, 1)], "unit")
    destination = tmp_path / "units"
    export_units(
        source,
        plan,
        validate_plan(plan, source),
        destination,
        include_html=True,
    )
    html = (destination / "article.html").read_text("utf-8")
    assert "<h1>" in html
    assert html.index("full width heading") < html.index("Left column")
    assert html.index("Left column") < html.index("Right column")


def test_raster_figure_has_stable_placeholder_and_caption(tmp_path):
    source = tmp_path / "figure.pdf"
    image = Image.new("RGB", (80, 60), "navy")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    with fitz.open() as document:
        page = document.new_page(width=400, height=500)
        page.insert_text((30, 40), "Figure example", fontsize=18)
        page.insert_image(fitz.Rect(50, 100, 250, 250), stream=stream.getvalue())
        page.insert_text((50, 275), "Figure 1. A navy rectangle.", fontsize=10)
        document.save(source)
    plan = reviewed_plan(source, [unit("article", 1, 1)], "unit")
    plan["pages"][0]["main_title"]["text"] = "Reviewed figure title"
    plan["pages"][0]["printed_page_number"] = "iv"
    plan["pages"][0]["figures"][0]["caption"] = "Figure 1. Reviewed caption."
    destination = tmp_path / "units"
    export_units(
        source,
        plan,
        validate_plan(plan, source),
        destination,
        include_html=True,
    )
    html = (destination / "article.html").read_text("utf-8")
    metadata = json.loads((destination / "article.metadata.json").read_text("utf-8"))
    assert "Figure figure-001-001" in html
    assert "<figcaption>Figure 1. Reviewed caption.</figcaption>" in html
    assert "A navy rectangle" not in html
    assert "Printed page iv" in html
    assert "Reviewed figure title" in html
    assert html.index("Reviewed figure title") < html.index("Figure figure-001-001")
    assert metadata["figures"][0]["input_page"] == 1
    assert metadata["figures"][0]["caption_bbox"] is not None


def test_plan_reports_fingerprint_overlap_and_within_page_errors(tmp_path):
    source = make_structured_pdf(tmp_path / "source.pdf", 2)
    plan = reviewed_plan(source, [unit("one", 1, 2), unit("two", 2, 2)])
    plan["source"]["sha256"] = "0" * 64
    plan["units"][1]["shared_page"] = True
    with pytest.raises(PlanValidationError) as error:
        validate_plan(plan, source)
    message = str(error.value)
    assert "source.sha256 does not match" in message
    assert "overlap on input page 2" in message
    assert "unsupported within-page boundary fields" in message


def test_plan_document_type_is_authoritative(tmp_path):
    source = make_structured_pdf(tmp_path / "source.pdf", 1)
    plan = reviewed_plan(source, [unit("one", 1, 1)], document_type="unit")
    with pytest.raises(PlanValidationError, match="conflicts with plan document_type"):
        validate_plan(plan, source, cli_document_type="book")


def test_plan_requires_complete_consistent_page_review(tmp_path):
    source = make_structured_pdf(tmp_path / "source.pdf", 2)
    plan = reviewed_plan(source, [unit("one", 1, 2)], document_type="unit")
    plan["pages"][0]["unit_ids"] = []
    plan["pages"][1]["selected"] = False
    with pytest.raises(PlanValidationError, match="must match the unit ranges"):
        validate_plan(plan, source)


def test_analysis_flags_possible_start_within_page(tmp_path):
    source = tmp_path / "shared.pdf"
    with fitz.open() as document:
        first = document.new_page(width=400, height=500)
        first.insert_text((30, 40), "First article title", fontsize=18)
        first.insert_text((30, 70), "Alice Smith", fontsize=10)
        first.insert_text((30, 100), "Abstract", fontsize=10)
        second = document.new_page(width=400, height=500)
        second.insert_text((30, 240), "Second article title", fontsize=18)
        second.insert_text((30, 270), "Bob Jones", fontsize=10)
        second.insert_text((30, 300), "Abstract", fontsize=10)
        document.save(source)
    analysis = analyze_container(source, "journal-issue")
    assert [
        item["input_page"] for item in analysis["suspected_shared_page_boundaries"]
    ] == [2]
    assert len(analysis["units"]) == 1
    assert analysis["units"][0]["input_pages"] == {"start": 1, "end": 2}


def test_cli_end_to_end_analysis_plan_and_html(make_pdf, tmp_path):
    source = make_pdf(["digital", "digital"])
    analysis_output = tmp_path / "analysis-out"
    result = CliRunner().invoke(
        cli.main,
        [
            str(source),
            "--outdir",
            str(analysis_output),
            "--born-digital",
            "--analysis",
            "--doctype",
            "unit",
        ],
    )
    assert result.exit_code == 0, result.output
    analysis = json.loads((analysis_output / "input.analysis.json").read_text("utf-8"))
    plan = {
        "schema_version": analysis["schema_version"],
        "kind": "plan",
        "source": analysis["source"],
        "document_type": analysis["document_type"],
        "pages": [{**page, "selected": True} for page in analysis["pages"]],
        "units": [
            {
                **analysis["units"][0],
                "selected": True,
                "boundary_status": "confirmed",
            }
        ],
    }
    plan["pages"][1]["selected"] = False
    plan_path = tmp_path / "input.plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    pdf_output = tmp_path / "pdf-out"
    result = CliRunner().invoke(
        cli.main,
        [
            str(source),
            "--outdir",
            str(pdf_output),
            "--born-digital",
            "--plan",
            str(plan_path),
        ],
    )
    assert result.exit_code == 0, result.output
    with fitz.open(pdf_output / "input.pdf") as output_document:
        assert len(output_document) == 1
    metadata_output = tmp_path / "metadata-out"
    result = CliRunner().invoke(
        cli.main,
        [
            str(source),
            "--outdir",
            str(metadata_output),
            "--born-digital",
            "--plan",
            str(plan_path),
            "--no-pdf-out",
        ],
    )
    assert result.exit_code == 0, result.output
    metadata_units = metadata_output / "_units_input"
    assert (metadata_units / "unit-001.metadata.json").is_file()
    assert not (metadata_units / "unit-001.html").exists()
    result_output = tmp_path / "result-out"
    result = CliRunner().invoke(
        cli.main,
        [
            str(source),
            "--outdir",
            str(result_output),
            "--born-digital",
            "--plan",
            str(plan_path),
            "--get-html",
            "--no-pdf-out",
        ],
    )
    assert result.exit_code == 0, result.output
    unit_dir = result_output / "_units_input"
    assert (unit_dir / "manifest.json").is_file()
    assert (unit_dir / "unit-001.html").is_file()
    assert (unit_dir / "unit-001.metadata.json").is_file()
