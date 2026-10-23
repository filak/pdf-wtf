from pdfwtf.html_format import indent_html_fragment


def test_indents_nested_block_elements_and_keeps_inline_content_together():
    html = (
        '<section class="figures"><h2>Figures</h2><ul>'
        '<li><a href="#one">One</a> -- Caption</li></ul></section>'
    )

    assert indent_html_fragment(html) == (
        '<section class="figures">\n'
        "  <h2>Figures</h2>\n"
        "  <ul>\n"
        '    <li><a href="#one">One</a> -- Caption</li>\n'
        "  </ul>\n"
        "</section>\n"
    )


def test_does_not_repair_missing_or_mismatched_tags():
    html = "<section><p>Unclosed</section></unexpected>"

    formatted = indent_html_fragment(html)

    assert formatted.replace("\n", "").replace("  ", "") == html
    assert "</p>" not in formatted


def test_preserves_inline_spacing_entities_comments_and_source_tags():
    html = "<p><SPAN data-x='1'>A</SPAN> &nbsp; &#169 <em>B</em><!-- keep --></p>"

    formatted = indent_html_fragment(html)

    assert formatted == html + "\n"


def test_preserves_whitespace_sensitive_content():
    html = (
        "<pre> first\n  <b>second</b>\n</pre><script>if (a < b) {\n  x();\n}</script>"
    )

    formatted = indent_html_fragment(html)

    assert "<pre> first\n  <b>second</b>\n</pre>" in formatted
    assert "<script>if (a < b) {\n  x();\n}</script>" in formatted


def test_formatting_is_idempotent():
    html = "<section><h2>Title</h2><p>Text</p></section>"
    once = indent_html_fragment(html)

    assert indent_html_fragment(once) == once
