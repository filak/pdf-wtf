import json

import pymupdf as fitz
import pytest

from pdfwtf.page_metadata import build_page_metadata
from pdfwtf.pipeline import process_pdf


def make_numbered_pdf(path, labels):
    with fitz.open() as doc:
        for label in labels:
            page = doc.new_page(width=300, height=400)
            page.insert_text((30, 170), "Body number 999 and section 4")
            if label is not None:
                page.insert_text((130, 380), label)
        doc.save(path)
    return path


def test_numbers_and_selection_flags(tmp_path):
    source = make_numbered_pdf(tmp_path / "input.pdf", ["105", "106", None, "108"])
    pages = build_page_metadata(source, [4], [2])
    assert pages == {
        "page_001": {"index": 1, "pgn": "105"},
        "page_002": {"index": 2, "pgn": "106", "skip": True},
        "page_003": {"index": 3, "pgn": None},
        "page_004": {"index": 4, "pgn": "108", "keep": True},
    }


@pytest.mark.parametrize(
    "label, expected",
    [
        ("Page 105 of 200", "105"),
        ("- 105 -", "105"),
        ("iv", "iv"),
        ("Chapter 105", None),
        ("2026-10-09", None),
        ("IIII", None),
    ],
)
def test_number_patterns(tmp_path, label, expected):
    source = make_numbered_pdf(tmp_path / "input.pdf", [label])
    assert build_page_metadata(source, [], [])["page_001"]["pgn"] == expected


def test_conflicting_candidates_require_neighbor_support(tmp_path):
    source = make_numbered_pdf(tmp_path / "input.pdf", ["105", "106"])
    with fitz.open(source) as doc:
        doc[0].insert_text((130, 20), "2026")
        doc.saveIncr()
    assert build_page_metadata(source, [], [])["page_001"]["pgn"] == "105"
    single = make_numbered_pdf(tmp_path / "single.pdf", ["105"])
    with fitz.open(single) as doc:
        doc[0].insert_text((130, 20), "2026")
        doc.saveIncr()
    assert build_page_metadata(single, [], [])["page_001"]["pgn"] is None


@pytest.mark.parametrize("no_pdf", [False, True])
def test_derivatives_keep_all_input_pages_and_mark_selection(tmp_path, no_pdf):
    source = make_numbered_pdf(tmp_path / "input.pdf", ["105", "106", None, "108"])
    original = source.read_bytes()
    output = tmp_path / "out"
    process_pdf(
        source,
        output,
        born_digital_flag=True,
        no_pdf_flag=no_pdf,
        export_json_flag=True,
        export_texts_flag=True,
        export_images_flag=True,
        export_thumbs_flag=True,
        extract_pages_str="2,4",
        skip_pages_str="2",
        dpi=72,
    )
    metadata = json.loads((output / "input.meta.json").read_text())
    assert metadata == {
        "input": str(source.resolve()),
        "output": None if no_pdf else str((output / source.name).resolve()),
        "doi": [],
        "pages": {
            "page_001": {"index": 1, "pgn": "105"},
            "page_002": {"index": 2, "pgn": "106", "skip": True, "keep": True},
            "page_003": {"index": 3, "pgn": None},
            "page_004": {"index": 4, "pgn": "108", "keep": True},
        },
    }
    for folder, ext in (
        ("_texts_input", "txt"),
        ("_images_input", "png"),
        ("_thumbs_input", "jpg"),
    ):
        assert {p.stem for p in (output / folder).glob(f"*.{ext}")} == {
            "page_001",
            "page_002",
            "page_003",
            "page_004",
        }
    if no_pdf:
        assert not (output / source.name).exists()
    else:
        with fitz.open(output / source.name) as doc:
            assert len(doc) == 1
            assert "108" in doc[0].get_text()
    assert source.read_bytes() == original


def test_metadata_can_mark_all_pages_skipped_without_pdf(tmp_path):
    source = make_numbered_pdf(tmp_path / "input.pdf", [None])
    output = tmp_path / "out"
    process_pdf(
        source,
        output,
        born_digital_flag=True,
        no_pdf_flag=True,
        export_json_flag=True,
        skip_pages_str="1",
    )
    assert json.loads((output / "input.meta.json").read_text())["pages"] == {
        "page_001": {"index": 1, "pgn": None, "skip": True}
    }
