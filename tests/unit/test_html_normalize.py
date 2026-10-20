from pdfwtf.html_normalize import normalize_extracted_html


def test_normalize_extracted_html_merges_short_prose_paragraphs():
    result = normalize_extracted_html("<p>First sentence.</p><p>Second sentence.</p>")

    assert result.html == "<p>First sentence. Second sentence.</p>"
    assert "Merged short prose paragraphs for web readability" in result.changes
