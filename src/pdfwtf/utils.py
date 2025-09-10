import os
import shutil
from pathlib import Path

RELATIVE_OUTPUT_DIR = "_data/out-pdf"


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


def get_output_dir(output_dir=None) -> Path:

    if output_dir:
        output_dir = Path(output_dir).resolve()
    else:
        env_outdir = os.environ.get("PDFWTF_OUTPUT_DIR")
        if env_outdir:
            output_dir = env_outdir
        else:
            base_dir = find_project_root()
            output_dir = base_dir / "instance" / RELATIVE_OUTPUT_DIR

    output_dir.mkdir(parents=True, exist_ok=True)

    return output_dir


def get_output_dir_final(
    output_dir: Path, input_pdf: Path, input_path_prefix: str = None
) -> Path:

    if not input_path_prefix:
        return output_dir

    input_str = str(input_pdf)
    prefix_str = str(Path(input_path_prefix))

    if prefix_str not in input_str:
        raise ValueError(
            f"Input path prefix '{prefix_str}' not found in input file path '{input_str}'"
        )

    # Everything after the first occurrence of marker
    relative_subpath = Path(
        input_str.split(prefix_str, 1)[1].strip("/").strip("\\")
    ).parent

    # Compute final output directory
    output_dir = output_dir / relative_subpath

    output_dir.mkdir(parents=True, exist_ok=True)

    return output_dir


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
