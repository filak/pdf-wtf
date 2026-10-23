from pdfwtf.html_normalize import normalize_extracted_html


def test_normalize_extracted_html_merges_short_prose_paragraphs():
    result = normalize_extracted_html("<p>First sentence.</p><p>Second sentence.</p>")

    assert result.html == "<p>First sentence. Second sentence.</p>"
    assert "Merged short prose paragraphs for web readability" in result.changes


def test_normalize_extracted_html_preserves_all_heading_levels():
    html = (
        "<h2>Section before title</h2>"
        "<h1>First title</h1>"
        "<h3>Subsection</h3>"
        "<h4>Fourth level</h4>"
        "<h5>Fifth level</h5>"
        "<h6>Sixth level</h6>"
        "<h1>Later title</h1>"
    )

    result = normalize_extracted_html(html)

    assert result.html == html
    assert result.changes == []


def test_normalize_extracted_html_keeps_generated_figure_attribute_order():
    html = (
        '<div class="figure-placeholder" id="figure-001-001">'
        "Figure figure-001-001</div>"
    )

    result = normalize_extracted_html(html)

    assert result.html == html
