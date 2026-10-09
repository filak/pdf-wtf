from PIL import Image
import pymupdf as fitz
import pytest
import pytesseract

from pdfwtf.utils.common import (
    _extract_doi_candidates,
    correct_images_orientation,
    export_thumbnails,
    extract_pages,
    get_doi,
    get_output_dir_final,
    images_to_pdf,
    parse_page_ranges,
)


@pytest.mark.parametrize(
    "raw, count, expected",
    [
        ("5", 10, [5]),
        ("2-4", 10, [2, 3, 4]),
        ("3-", 6, [3, 4, 5, 6]),
        ("1-2,5,7-", 8, [1, 2, 5, 7, 8]),
        (" 1 - 2 , 2 ", 3, [1, 2]),
    ],
)
def test_page_ranges(raw, count, expected):
    assert parse_page_ranges(raw, count) == expected


@pytest.mark.parametrize(
    "raw", ["0", "11", "9-12", "0-2", "5-2", "11-", "-1", "", "1,,2", "x", "1-2-3"]
)
def test_invalid_page_ranges(raw):
    with pytest.raises(ValueError):
        parse_page_ranges(raw, 10)


@pytest.mark.parametrize("count", [None, 0, -1])
def test_invalid_total_page_count(count):
    with pytest.raises(ValueError):
        parse_page_ranges("1", count)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("https://doi.org/10.1234/example", ["10.1234/example"]),
        ("http://dx.doi.org/10.1234/example", ["10.1234/example"]),
        ("doi.org/10.1234/example", ["10.1234/example"]),
        ("doi:10.1234/example", ["10.1234/example"]),
        ("DOI: 10.1234/example", ["10.1234/example"]),
        ("The identifier is 10.1234/example", ["10.1234/example"]),
    ],
)
def test_doi_representations(text, expected):
    assert _extract_doi_candidates(text) == expected


def test_doi_case_deduplication_preserves_first_occurrence():
    text = "10.1234/First DOI: 10.1234/first doi.org/10.1234/FIRST"
    assert _extract_doi_candidates(text) == ["10.1234/First"]


def test_doi_prefixes_are_distinct_identifiers():
    text = "10.1234/abc 10.1234/abc123"
    assert _extract_doi_candidates(text) == ["10.1234/abc", "10.1234/abc123"]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("10.1234/example(test)", ["10.1234/example(test)"]),
        ("(10.1234/example)", ["10.1234/example"]),
        ("(10.1234/example(test))", ["10.1234/example(test)"]),
        ('"10.1234/example"', ["10.1234/example"]),
        ("[DOI: 10.1234/example]", ["10.1234/example"]),
        ("<https://doi.org/10.1234/example>", ["10.1234/example"]),
    ],
)
def test_doi_parentheses_and_wrappers(text, expected):
    assert _extract_doi_candidates(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("DOI: 10.1234/example,", ["10.1234/example,"]),
        ("(10.1234/example).", ["10.1234/example."]),
        ("10.1234/example-", ["10.1234/example-"]),
        ("10.1234/a<b>;c&d", ["10.1234/a<b>;c&d"]),
    ],
)
def test_doi_trailing_punctuation_and_complex_suffixes(text, expected):
    assert _extract_doi_candidates(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("https://doi.org/10.1234/a%2Fb", ["10.1234/a/b"]),
        ("https://doi.org/10.1234/a%252Fb", ["10.1234/a%2Fb"]),
        ("https://doi.org/10.1234/a+b", ["10.1234/a+b"]),
        ("DOI: 10.1234/a%2Fb", ["10.1234/a%2Fb"]),
    ],
)
def test_doi_url_decoding(text, expected):
    assert _extract_doi_candidates(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("10.1234/exam\u00adple", ["10.1234/example"]),
        ("10.1234/exam\u200bple", ["10.1234/example"]),
        ("10.1234/example-\npart", ["10.1234/example-part"]),
        ("10.1234/\nexample", ["10.1234/example"]),
        ("10.1234/exam\nple", ["10.1234/example"]),
        ("10.1234/example-\r\npart", ["10.1234/example-part"]),
    ],
)
def test_doi_ocr_artifacts_and_line_continuations(text, expected):
    assert _extract_doi_candidates(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "DOI: 10.1234/\nThis line is ordinary prose.",
        "DOI: 10.1234/example-\nThis line is ordinary prose.",
    ],
)
def test_doi_rejects_ambiguous_broken_lines(text):
    assert _extract_doi_candidates(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "https://notdoi.org/10.1234/example",
        "https://doi.org.evil/10.1234/example",
        "notdoi.org/10.1234/example",
        "11.1234/example",
        "10.123/example",
        "DOI: 10.1234/",
    ],
)
def test_doi_invalid_candidates(text):
    assert _extract_doi_candidates(text) == []


def test_get_doi_missing_directory_and_empty_file(tmp_path):
    assert get_doi(tmp_path / "missing") == []
    texts = tmp_path / "texts"
    texts.mkdir()
    (texts / "page_001.txt").write_text("", encoding="utf-8")
    assert get_doi(texts) == []


def test_get_doi_reads_only_first_exported_page(tmp_path):
    texts = tmp_path / "texts"
    texts.mkdir()
    (texts / "page_001.txt").write_text("No identifier", encoding="utf-8")
    (texts / "page_002.txt").write_text("10.1234/second", encoding="utf-8")
    assert get_doi(texts) == []


def test_extract_pages_in_place(make_pdf):
    path = make_pdf(["digital", "blank", "digital"])
    extract_pages(path, path, pages_to_skip=[2])
    with fitz.open(path) as doc:
        assert len(doc) == 2


def test_empty_selection_does_not_replace_input(make_pdf):
    path = make_pdf(["digital"])
    original = path.read_bytes()
    with pytest.raises(ValueError):
        extract_pages(path, path, pages_to_keep=[])
    assert path.read_bytes() == original


def test_relative_prefix_must_be_parent(tmp_path):
    input_pdf = tmp_path / "documents-old/input.pdf"
    with pytest.raises(ValueError):
        get_output_dir_final(tmp_path / "out", input_pdf, tmp_path / "documents")


def test_relative_output_preserves_subdirectories(tmp_path):
    output = get_output_dir_final(
        tmp_path / "out", tmp_path / "in/sub/input.pdf", tmp_path / "in"
    )
    assert output == tmp_path / "out/sub"
    assert output.is_dir()


def test_orientation_failure_is_nonfatal_and_does_not_log_content(
    tmp_path, monkeypatch, caplog
):
    path = tmp_path / "page.png"
    Image.new("RGB", (10, 20)).save(path)

    def fail(*args, **kwargs):
        raise pytesseract.TesseractError(1, "private document text")

    monkeypatch.setattr(pytesseract, "image_to_osd", fail)
    assert correct_images_orientation([path]) is False
    assert "private document text" not in caplog.text


def test_orientation_returns_boolean_and_rotates(tmp_path, monkeypatch):
    path = tmp_path / "page.png"
    Image.new("RGB", (10, 20)).save(path)
    monkeypatch.setattr(pytesseract, "image_to_osd", lambda *a, **kw: {"rotate": 90})
    assert correct_images_orientation([path]) is True
    with Image.open(path) as image:
        assert image.size == (20, 10)


def test_images_to_pdf_uses_requested_dpi(tmp_path):
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    Image.new("RGB", (300, 600)).save(image_dir / "page_001.png")
    result = tmp_path / "result.pdf"
    images_to_pdf(image_dir, result, dpi=300)
    with fitz.open(result) as doc:
        assert doc[0].rect.width == pytest.approx(72)
        assert doc[0].rect.height == pytest.approx(144)


def test_rgba_thumbnail_export(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    Image.new("RGBA", (100, 200)).save(images / "page.png")
    output = tmp_path / "thumbs"
    export_thumbnails(images, output)
    with Image.open(output / "page.jpg") as image:
        assert image.mode == "RGB"
