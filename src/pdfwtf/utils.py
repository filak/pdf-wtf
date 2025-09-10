def parse_page_ranges(pages_str, total_pages=None):
    """Parse page ranges like '1-3,5' into 1-based page indices."""
    if total_pages is None:
        raise ValueError("total_pages must be specified for open-ended ranges")

    pages = set()
    for part in pages_str.split(","):
        if "-" in part:
            start, end = part.split("-")
            start = int(start)
            if end == "":
                end = total_pages
            else:
                end = int(end)
            pages.update(range(start, end + 1))
        else:
            page = int(part)
            if total_pages is not None and page > total_pages:
                raise ValueError(f"Page {page} is out of range (1-{total_pages})")
            pages.add(page)
    return sorted(pages)
