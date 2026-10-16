"""Regression cases for contextual unit HTML heading reconstruction."""

import pymupdf as fitz
import pytest

from pdfwtf.container_partition import (
    _export_heading_evidence,
    _resolve_unit_headings,
    _unit_body_size,
    direct_unit_plan,
    export_units,
)


def text_block(text, size=10, bold=False, top=100):
    return {
        "bbox": [30, top, 330, top + size],
        "lines": [
            {
                "text": text,
                "spans": [
                    {
                        "text": text,
                        "size": size,
                        "font": "Helvetica",
                        "flags": 16 if bold else 0,
                    }
                ],
            }
        ],
    }


def heading(text, size=14, top=100, page=1, reviewed=False, level=2):
    return {
        "kind": "heading",
        "text": text,
        "level": level,
        "input_page": page,
        "bbox": [30, top, 330, top + size],
        "_style": (size, False),
        "_reviewed": reviewed,
        "heading_evidence": ["larger than unit body text"],
    }


def test_inline_bold_does_not_promote_body_paragraph():
    block = text_block("Ordinary body text with one emphasized word")
    block["lines"][0]["spans"].append(
        {
            "text": "important",
            "size": 10,
            "font": "Bold",
            "flags": 16,
        }
    )
    assert not _export_heading_evidence(block, 10, 20, 5)


@pytest.mark.parametrize(
    "text",
    [
        "A complete bold sentence.",
        "Figure 2. Description",
        "author@example.org",
        "ISSN 1234-5678",
        "12",
    ],
)
def test_non_headings_are_not_promoted(text):
    assert not _export_heading_evidence(text_block(text, bold=True), 10, 20, 5)


def test_body_sized_numbered_bold_section_is_a_heading():
    evidence = _export_heading_evidence(text_block("2.1 Methods", bold=True), 10, 0, 0)
    assert "section numbering" in evidence


def test_unit_baseline_weights_characters_and_excludes_captions_and_headers():
    page = {
        "height": 500,
        "text_blocks": [
            text_block("Body prose " * 40),
            *[text_block("X", size=18, top=200) for _ in range(20)],
            text_block("Figure 1. " + "caption " * 100, size=8),
            text_block("Running title " * 100, size=16, top=10),
        ],
    }
    running = {"running title " * 99 + "running title"}
    assert _unit_body_size([page], running) == 10


def test_wrapped_heading_fragments_merge_and_keep_union_bbox():
    blocks, warnings = _resolve_unit_headings(
        [
            heading("A wrapped", size=18, top=100),
            heading("article title", size=18, top=120),
        ],
        "A wrapped article title",
        10,
    )
    assert len(blocks) == 1
    assert blocks[0]["text"] == "A wrapped article title"
    assert blocks[0]["bbox"] == [30, 100, 330, 138]
    assert blocks[0]["level"] == 1
    assert not warnings
    assert "_style" not in blocks[0]


def test_numbered_sibling_headings_remain_separate_and_warn():
    blocks, warnings = _resolve_unit_headings(
        [
            heading("1 Methods", top=100),
            heading("2 Results", top=116),
        ],
        "Missing title",
        10,
    )
    assert len(blocks) == 2
    assert len(warnings) == 1


def test_parent_followed_by_subsection_is_valid():
    blocks, warnings = _resolve_unit_headings(
        [
            heading("Methods", size=14),
            heading("Participants", size=12, top=140),
        ],
        "Missing title",
        12,
    )
    assert [b["level"] for b in blocks] == [2, 3]
    assert not warnings


def test_repeated_style_keeps_same_level_across_pages():
    blocks, _ = _resolve_unit_headings(
        [
            heading("Title", size=20),
            heading("Methods", size=14, top=150),
            {"kind": "page_marker", "input_page": 2},
            heading("Results", size=14, page=2),
        ],
        "Title",
        10,
    )
    assert [b["level"] for b in blocks if b["kind"] == "heading"] == [1, 2, 2]


@pytest.mark.parametrize("label", ["Abstract", "Introduction", "Úvod", "1 Methods"])
def test_no_h1_for_recognized_section_label_even_when_large(label):
    blocks, _ = _resolve_unit_headings([heading(label, size=18)], label, 10)
    assert blocks[0]["level"] == 2


def test_no_h1_when_first_heading_is_body_sized_abstract():
    blocks, _ = _resolve_unit_headings([heading("Abstract", size=10)], "", 10)
    assert blocks[0]["level"] == 2


def test_reviewed_headings_are_neither_merged_nor_releveled():
    blocks, _ = _resolve_unit_headings(
        [
            heading("Title", size=18, reviewed=True, level=1),
            heading("Subtitle", size=18, top=120, reviewed=True, level=3),
        ],
        "Title Subtitle",
        10,
    )
    assert [b["level"] for b in blocks] == [1, 3]


def test_direct_export_reconsiders_automatic_page_titles(tmp_path):
    source = tmp_path / "article.pdf"
    with fitz.open() as document:
        for label in ("Article title", "Methods"):
            page = document.new_page(width=400, height=500)
            page.insert_text((30, 90), label, fontsize=18)
            page.insert_textbox(
                fitz.Rect(30, 130, 370, 400),
                "Body prose for this article. " * 30,
                fontsize=10,
            )
        document.save(source)
    plan = direct_unit_plan(source)
    destination = tmp_path / "units"
    export_units(
        source,
        plan,
        plan["units"],
        destination,
        include_html=True,
        use_reviewed_headings=False,
    )
    html = next(destination.glob("*.html")).read_text("utf-8")
    assert html.count("<h1>") == 1
    assert "<h2>Methods</h2>" in html
