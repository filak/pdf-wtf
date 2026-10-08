import pymupdf as fitz
import pytest

from pdfwtf.utils.analyze import is_scanned_or_hybrid, page_has_large_image


@pytest.mark.parametrize(
    "pages, expected",
    [
        (["digital"], False),
        (["scan"], True),
        (["digital", "scan"], True),
        (["scan", "digital"], True),
        (["digital", "blank"], False),
        (["blank"], False),
    ],
)
def test_document_classification(make_pdf, pages, expected):
    assert is_scanned_or_hybrid(make_pdf(pages)) is expected


def test_image_lookup_accepts_xref(make_pdf):
    with fitz.open(make_pdf(["scan"])) as doc:
        assert page_has_large_image(doc[0])


def test_image_with_meaningful_text(make_pdf):
    path = make_pdf(["scan"])
    with fitz.open(path) as doc:
        doc[0].insert_text(
            (20, 30), "This page has enough meaningful text words to classify."
        )
        data = doc.tobytes()
    path.write_bytes(data)
    assert is_scanned_or_hybrid(path)
