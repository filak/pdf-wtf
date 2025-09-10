import pytest
from pdfwtf.utils import parse_page_ranges

def test_single_page():
    assert parse_page_ranges("5", 10) == [5]

def test_range():
    assert parse_page_ranges("2-4", 10) == [2, 3, 4]

def test_open_range():
    assert parse_page_ranges("3-", 6) == [3, 4, 5, 6]

def test_multiple_ranges():
    assert parse_page_ranges("1-2,5,7-", 8) == [1, 2, 5, 7, 8]

def test_invalid_page():
    with pytest.raises(ValueError):
        parse_page_ranges("11", 10)

