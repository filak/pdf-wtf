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
    _unit_type,
    _valid_bbox,
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


def test_plan_reports_fingerprint_and_within_page_errors(tmp_path):
    source = make_structured_pdf(tmp_path / "source.pdf", 2)
    plan = reviewed_plan(source, [unit("one", 1, 2), unit("two", 2, 2)])
    plan["source"]["sha256"] = "0" * 64
    plan["units"][1]["shared_page"] = True
    with pytest.raises(PlanValidationError) as error:
        validate_plan(plan, source)
    message = str(error.value)
    assert "source.sha256 does not match" in message
    assert "overlap on input page" not in message
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
        ["analyse", str(source), "--outdir", str(analysis_output), "--doctype", "unit"],
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
            "enhance",
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
            "export",
            str(source),
            "--outdir",
            str(metadata_output),
            "--plan",
            str(plan_path),
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
            "export",
            str(source),
            "--outdir",
            str(result_output),
            "--plan",
            str(plan_path),
            "--get-html",
        ],
    )
    assert result.exit_code == 0, result.output
    unit_dir = result_output / "_units_input"
    assert (unit_dir / "manifest.json").is_file()
    assert (unit_dir / "unit-001.html").is_file()
    assert (unit_dir / "unit-001.metadata.json").is_file()


def test_plan_rejects_case_colliding_unit_ids(tmp_path):
    source = make_structured_pdf(tmp_path / "source.pdf", 2)
    plan = reviewed_plan(source, [unit("Article", 1, 1), unit("article", 2, 2)])
    with pytest.raises(PlanValidationError, match="unique, ignoring case"):
        validate_plan(plan, source)


def test_reading_order_preserves_overlapping_and_outside_items():
    from pdfwtf.container_analysis import _ordered_items

    items = [
        {"id": "wide", "bbox": [0, 100, 100, 200]},
        {"id": "overlap", "bbox": [5, 140, 30, 160]},
        {"id": "above", "bbox": [5, -20, 30, -10]},
        {"id": "below", "bbox": [5, 310, 30, 330]},
        {"id": "second-wide", "bbox": [0, 150, 100, 250]},
    ]
    ordered = _ordered_items(items, {"width": 100, "height": 300})
    assert len(ordered) == len(items)
    assert {item["id"] for item in ordered} == {item["id"] for item in items}


def test_captionless_reviewed_figure_exports_html(tmp_path):
    source = make_structured_pdf(tmp_path / "source.pdf", 1)
    plan = reviewed_plan(source, [unit("article", 1, 1)], "unit")
    plan["pages"][0]["figures"] = [
        {"id": "figure-1", "bbox": [50, 100, 250, 250], "xref": 0}
    ]
    destination = tmp_path / "units"
    export_units(
        source, plan, validate_plan(plan, source), destination, include_html=True
    )
    html = (destination / "article.html").read_text(encoding="utf-8")
    assert "Figure figure-1" in html
    assert "<figcaption>" not in html


def test_overlapping_figure_preserves_text_and_warns(tmp_path):
    source = make_structured_pdf(tmp_path / "source.pdf", 1)
    plan = reviewed_plan(source, [unit("article", 1, 1)], "unit")
    plan["pages"][0]["figures"] = [
        {"id": "figure-1", "bbox": [0, 30, 400, 45], "xref": 0}
    ]
    destination = tmp_path / "units"
    export_units(
        source, plan, validate_plan(plan, source), destination, include_html=True
    )
    html = (destination / "article.html").read_text(encoding="utf-8")
    metadata = json.loads(
        (destination / "article.metadata.json").read_text(encoding="utf-8")
    )
    assert "Unit 1 title" in html
    assert "Figure figure-1" in html
    assert any("overlapping blocks" in warning for warning in metadata["warnings"])


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Abstract", "abstract"),
        ("Abstracts", "abstract"),
        ("Poster", "poster"),
        ("Posters", "poster"),
        ("Poster abstract", "poster"),
    ],
)
def test_separate_abstract_and_poster_types(title, expected):
    assert _unit_type("proceedings", {"title": title}, {}) == expected


@pytest.mark.parametrize(
    "bbox",
    [
        [4, 0, 1, 10],
        [0, 4, 10, 1],
        [0, 0, float("inf"), 10],
        [float("nan"), 0, 10, 10],
        [False, 0, 10, 10],
        [0, 0, 10],
    ],
)
def test_invalid_rectangles_remain_rejected(bbox):
    assert not _valid_bbox(bbox)


def test_plan_accepts_figure_extending_beyond_page(tmp_path):
    source = tmp_path / "bleed.pdf"
    image = io.BytesIO()
    Image.new("RGB", (80, 80), "blue").save(image, format="PNG")
    with fitz.open() as document:
        page = document.new_page(width=400, height=500)
        page.insert_text((30, 300), "Figure bleed test", fontsize=18)
        page.insert_image(fitz.Rect(-10, -10, 200, 200), stream=image.getvalue())
        document.save(source)
    plan = reviewed_plan(source, [unit("one", 1, 1)], document_type="unit")
    bbox = plan["pages"][0]["figures"][0]["bbox"]
    assert bbox[0] < 0 and bbox[1] < 0
    assert validate_plan(plan, source) == plan["units"]
    assert plan["pages"][0]["figures"][0]["bbox"] == bbox


@pytest.mark.parametrize(
    "ranges", [[(1, 2), (2, 3)], [(1, 3), (2, 2)], [(1, 3), (1, 3)]]
)
def test_overlapping_units_export_complete_shared_pages(tmp_path, ranges):
    source = make_structured_pdf(tmp_path / "source.pdf", 3)
    units = [unit("one", *ranges[0]), unit("two", *ranges[1])]
    plan = reviewed_plan(source, units)
    selected = validate_plan(plan, source)
    assert selected == units
    shared = set(range(ranges[0][0], ranges[0][1] + 1)).intersection(
        range(ranges[1][0], ranges[1][1] + 1)
    )
    for page in shared:
        assert plan["pages"][page - 1]["unit_ids"] == ["one", "two"]
    destination = tmp_path / "units"
    export_units(source, plan, selected, destination, include_html=True)
    for item in units:
        metadata = json.loads(
            (destination / f"{item['id']}.metadata.json").read_text("utf-8")
        )
        assert metadata["unit"]["selected_input_pages"] == list(
            range(item["input_pages"]["start"], item["input_pages"]["end"] + 1)
        )
        html = (destination / f"{item['id']}.html").read_text("utf-8")
        for page in shared:
            assert (
                f"Body text for absolute input page {page} with enough words." in html
            )


def test_overlapping_plan_requires_all_page_unit_links(tmp_path):
    source = make_structured_pdf(tmp_path / "source.pdf", 3)
    plan = reviewed_plan(source, [unit("one", 1, 2), unit("two", 2, 3)])
    plan["pages"][1]["unit_ids"] = ["one"]
    with pytest.raises(PlanValidationError, match="must match the unit ranges"):
        validate_plan(plan, source)


@pytest.mark.parametrize(
    "unit_type", ["cover", "abstract", "poster", "full-page-advertisement"]
)
def test_reviewed_unit_types_are_valid(tmp_path, unit_type):
    source = make_structured_pdf(tmp_path / "source.pdf", 1)
    plan = reviewed_plan(source, [unit("one", 1, 1)])
    plan["units"][0]["type"] = unit_type
    assert validate_plan(plan, source) == plan["units"]


@pytest.mark.parametrize(
    "footer",
    [
        "Journal of Footer Studies. 2024;12(3):101-110",
        "Journal of Footer Studies, 3/2024, vol. 12",
    ],
)
def test_source_metadata_prefers_repeated_late_page_footers(tmp_path, footer):
    source = tmp_path / "footers.pdf"
    with fitz.open() as document:
        for number in range(8):
            page = document.new_page(width=600, height=800)
            page.insert_text((30, 40), "Article title", fontsize=18)
            page.insert_text(
                (30, 150), "References: Other Journal. 1999;7(1):1-5", fontsize=10
            )
            if number >= 5:
                page.insert_text((30, 750), footer, fontsize=10)
                page.insert_text((30, 775), "ISSN 1234-567X", fontsize=10)
        document.save(source)
    bibliographic = analyze_container(source, "journal-issue")["source"][
        "bibliographic"
    ]
    assert bibliographic == {
        "kind": "journal",
        "journal_title": "Journal of Footer Studies",
        "year": "2024",
        "volume": "12",
        "issue": "3",
        "issn": "1234-567X",
    }


@pytest.mark.parametrize(
    "contact", ["prof. MUDr. Alex Smith, DrSc.", "MUDr. Alex Smith, PhD."]
)
def test_references_and_contacts_do_not_create_unit_starts(tmp_path, contact):
    source = tmp_path / "contacts.pdf"
    with fitz.open() as document:
        first = document.new_page(width=400, height=500)
        first.insert_text((30, 40), "First article title", fontsize=18)
        first.insert_text((30, 70), "Alice Smith", fontsize=10)
        first.insert_text((30, 100), "Abstract", fontsize=10)
        for number in range(2):
            page = document.new_page(width=400, height=500)
            page.insert_text((30, 40), "Literatúra", fontsize=9)
            for line in range(8):
                page.insert_text(
                    (30, 65 + line * 10),
                    "Smith AB, Jones CD. Journal reference.",
                    fontsize=7,
                )
            page.insert_text((30, 155), "doi:10.1234/reference", fontsize=7)
            page.insert_text((30, 220), contact, fontsize=9)
            page.insert_text((30, 235), "alex@example.org", fontsize=9)
            if number:
                page.insert_text((30, 460), "www.example.org", fontsize=11)
        document.save(source)
    analysis = analyze_container(source, "journal-issue")
    assert analysis["suspected_shared_page_boundaries"] == []
    assert len(analysis["units"]) == 1
    assert analysis["units"][0]["input_pages"] == {"start": 1, "end": 3}


@pytest.mark.parametrize("changing_footer", [False, True])
def test_repeated_citation_footer_changes_propose_unit_boundaries(
    tmp_path, changing_footer
):
    source = tmp_path / "footer-units.pdf"
    with fitz.open() as document:
        for number in range(4):
            page = document.new_page(width=600, height=800)
            page.insert_text((30, 100), "Unmarked article heading", fontsize=10)
            page.insert_text(
                (30, 130), "Body content without author or DOI signals.", fontsize=10
            )
            page_range = "12-13" if changing_footer and number >= 2 else "10-11"
            page.insert_text(
                (30, 760), f"Test Journal 2024;12(3): {page_range}", fontsize=8
            )
            page.insert_text((550, 760), str(10 + number), fontsize=8)
        document.save(source)
    analysis = analyze_container(source, "journal-issue")
    assert [unit["input_pages"]["start"] for unit in analysis["units"]] == (
        [1, 3] if changing_footer else [1]
    )
    assert analysis["suspected_shared_page_boundaries"] == []


def test_magazine_issue_analysis_and_plan(tmp_path):
    source = make_structured_pdf(tmp_path / "source.pdf", 2)
    analysis = analyze_container(source, "magazine-issue")
    assert analysis["document_type"] == "magazine-issue"
    assert analysis["source"]["bibliographic"]["kind"] == "journal"
    plan = reviewed_plan(source, [unit("one", 1, 2)], "magazine-issue")
    assert validate_plan(plan, source) == plan["units"]


def test_magazine_uses_large_multiline_titles_without_academic_signals(tmp_path):
    source = tmp_path / "magazine.pdf"
    with fitz.open() as document:
        for number in range(4):
            page = document.new_page(width=600, height=800)
            for line in range(15):
                page.insert_text(
                    (30, 100 + line * 15),
                    "Magazine body text and travel descriptions.",
                    fontsize=10,
                )
            page.insert_text((30, 30), "TRAVEL SECTION", fontsize=14)
            if number in (0, 2):
                page.insert_text((30, 430), f"Journey {number + 1}", fontsize=44)
                page.insert_text((30, 480), "Across the mountains", fontsize=32)
            else:
                page.insert_text((30, 430), "A LARGE PULL QUOTE", fontsize=16)
            page.insert_text((500, 760), str(number + 1), fontsize=50)
        document.save(source)
    analysis = analyze_container(source, "magazine-issue")
    assert [unit["input_pages"]["start"] for unit in analysis["units"]] == [1, 3]
    assert (
        analysis["pages"][2]["main_title"]["text"] == "Journey 3 Across the mountains"
    )
    assert analysis["units"][1]["title"] == analysis["pages"][2]["main_title"]["text"]
    assert analysis["suspected_shared_page_boundaries"] == []


@pytest.mark.parametrize(
    "document_type, bold, expected",
    [
        ("magazine-issue", True, "Mountain lake at sunrise"),
        ("magazine-issue", False, None),
        ("journal-issue", True, None),
    ],
)
def test_magazine_photo_caption_without_prefix(tmp_path, document_type, bold, expected):
    source = tmp_path / "caption.pdf"
    image = io.BytesIO()
    Image.new("RGB", (100, 80), "navy").save(image, format="PNG")
    with fitz.open() as document:
        page = document.new_page(width=400, height=500)
        page.insert_text((30, 40), "Article opening", fontsize=36)
        page.insert_image(fitz.Rect(50, 100, 250, 260), stream=image.getvalue())
        page.insert_text(
            (50, 273),
            "Mountain lake at sunrise",
            fontsize=9,
            fontname="hebo" if bold else "helv",
        )
        page.insert_text(
            (50, 310), "Unrelated bold heading", fontsize=12, fontname="hebo"
        )
        document.save(source)
    figure = analyze_container(source, document_type)["pages"][0]["figures"][0]
    assert figure["caption"] == expected
    assert (figure["caption_bbox"] is not None) == (expected is not None)


def test_cli_analysis_rejects_unit_type_filter():
    result = CliRunner().invoke(cli.main, ["analyse", "--unit_type", "article"])
    assert result.exit_code == 2
    assert "No such option" in result.output
    assert "--unit_type" in result.output
    help_result = CliRunner().invoke(cli.main, ["analyse", "--help"])
    assert "--unit_type" not in help_result.output


@pytest.mark.parametrize(
    "types, expected",
    [
        (("chapter",), ["first"]),
        (("chapter", "editorial"), ["first", "second"]),
        (("poster",), []),
    ],
)
def test_cli_export_unit_type_selection(tmp_path, types, expected):
    source = make_structured_pdf(tmp_path / "input.pdf")
    units = [
        unit("first", 1, 1),
        unit("second", 2, 2),
        unit("excluded", 3, 3, selected=False),
    ]
    units[1]["type"] = "editorial"
    plan = reviewed_plan(source, units)
    plan_path = tmp_path / "approved.plan.json"
    original = json.dumps(plan)
    plan_path.write_text(original, encoding="utf-8")
    output = tmp_path / "export"
    args = [
        "export",
        str(source),
        "--outdir",
        str(output),
        "--plan",
        str(plan_path),
        "--get-html",
    ]
    for value in types:
        args.extend(["--unit_type", value])
    result = CliRunner().invoke(cli.main, args)
    assert result.exit_code == 0, result.output
    destination = output / "_units_input"
    manifest = json.loads((destination / "manifest.json").read_text("utf-8"))
    assert [item["id"] for item in manifest["units"]] == expected
    assert sorted(path.stem for path in destination.glob("*.html")) == sorted(expected)
    assert plan_path.read_text("utf-8") == original


def test_cli_unit_type_requires_unit_export(make_pdf):
    source = make_pdf(["digital"])
    result = CliRunner().invoke(
        cli.main, ["export", str(source), "--get-text", "--unit_type", "article"]
    )
    assert result.exit_code != 0
    assert "requires --plan or --get-html" in result.output


def test_cli_unit_type_rejects_unknown_type():
    result = CliRunner().invoke(cli.main, ["export", "--unit_type", "invalid"])
    assert result.exit_code == 2
    assert "Invalid value for '--unit_type'" in result.output
