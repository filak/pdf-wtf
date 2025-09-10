import shutil
from pathlib import Path


def find_project_root(marker="instance") -> Path:
    """
    Walk up the directory tree until a folder containing the marker exists.
    Returns the parent directory containing the marker.

    Raises RuntimeError if no project root is found.
    """
    current = Path(__file__).resolve()
    for parent in [current.parent] + list(current.parents):
        if (parent / marker).exists():
            return parent
    raise RuntimeError(f"Project root with marker '{marker}' not found.")


def get_temp_dir(clean: bool = False) -> Path:
    """Get or create a temporary directory inside the project instance/temp."""
    base_dir = find_project_root()
    temp_dir = base_dir / "instance" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    if clean:
        for item in temp_dir.iterdir():
            try:
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            except Exception:
                pass

    return temp_dir


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


def compute_relative_output(input_pdf: Path, marker: Path, base_output_dir: Path) -> Path:
    """
    Compute the output directory based on the relative path from marker.
    """
    input_str = str(input_pdf)       # convert Path to string
    marker_str = str(marker)         # convert Path to string

    # Normalize separators (cross-platform)
    input_str_norm = input_str.replace("\\", "/")
    marker_norm = marker_str.replace("\\", "/")

    if marker_norm not in input_str_norm:
        raise ValueError(f"Marker '{marker}' not found in input file path '{input_str}'")

    # Everything after the first occurrence of marker
    relative_subpath = input_str_norm.split(marker_norm, 1)[1].lstrip("/")

    # Compute final output directory
    return base_output_dir / Path(relative_subpath).parent
