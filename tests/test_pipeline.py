import pytest
from pdfwtf.pipeline import parse_page_ranges

def test_page_range_parser():
    assert parse_page_ranges("1-3,5", 10) == [1, 2, 3, 5]
