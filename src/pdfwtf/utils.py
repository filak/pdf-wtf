import os
import shutil
from pathlib import Path
import fitz  # PyMuPDF
import img2pdf
import pikepdf
from PIL import Image, ImageFilter
from typing import Union, List
from pdfminer.high_level import extract_text


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

    env_temp_dir = os.environ.get("PDFWTF_TEMP_DIR")
    if env_temp_dir:
        temp_dir = Path(env_temp_dir).resolve()
    else:
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
            output_dir = Path(env_outdir).resolve()
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

    output_dir = output_dir / relative_subpath

    output_dir.mkdir(parents=True, exist_ok=True)

    return output_dir


def has_no_text(filepath):
    """Check if a PDF has been likely scanned (no embedded text)."""
    with fitz.open(filepath) as doc:
        for page in doc:
            if page.get_text().strip():
                return False
    return True


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


def images_to_pdf(images_dir: Path, output_pdf: Path, dpi=300, fext="png"):
    # collect all images in natural sort order
    image_files = sorted(images_dir.glob(f"*.{fext}"))
    if not image_files:
        raise ValueError(f"No PNG images found in {images_dir}")

    with output_pdf.open("wb") as f:
        f.write(img2pdf.convert(
            [str(p) for p in image_files]
        ))


def export_thumbnails(
    images_dir: "Path",
    thumbs_dir: "Path",
    thumb_size=(400, 400),
    fext="jpg",
    quality=75,
):
    """
    Create thumbnails from existing images.

    :param images_dir: Path object for source images
    :param thumbs_dir: Path object for output thumbnails
    :param thumb_size: max (width, height) for thumbnails
    :param fext: output image format
    :param quality: JPEG quality
    """

    if thumbs_dir.is_dir():
        clear_dir(thumbs_dir)

    thumbs_dir.mkdir(parents=True, exist_ok=True)

    if not images_dir.is_dir():
        return

    for img_path in sorted(images_dir.iterdir()):
        if img_path.is_file() and img_path.suffix.lower() in [".png", ".jpg"]:
            with Image.open(img_path) as img:
                img.thumbnail(thumb_size, Image.LANCZOS)

                # Optional: slight sharpening for crisper results
                # img = img.filter(ImageFilter.SHARPEN)

                out_path = thumbs_dir / f"{img_path.stem}.{fext}"

                save_kwargs = (
                    {"quality": quality, "optimize": True}
                    if fext.lower() == "jpg"
                    else {}
                )
                img.save(out_path, **save_kwargs)


def get_unpaper_args(layout=None, output_pages=None, pre_rotate=None, as_string=False, full=False):
    unpaper_args_list = []
    if full:
        default_args = [
            '--mask-scan-size',
            '100',  # don't blank out narrow columns
            '--no-border-align',  # don't align visible content to borders
            '--no-mask-center',  # don't center visible content within page
            '--no-grayfilter',  # don't remove light gray areas
            '--no-blackfilter',  # don't remove solid black areas
        ]
        unpaper_args_list.extend(default_args)

    if layout is not None:
        unpaper_args_list.append("--layout")
        unpaper_args_list.append(layout)

    if pre_rotate is not None:
        unpaper_args_list.append("--pre-rotate")
        unpaper_args_list.append(pre_rotate)

    if output_pages in ["1", "2"]:
        unpaper_args_list.append("--output-pages")
        unpaper_args_list.append(output_pages)

    if as_string:
        return " ".join(unpaper_args_list)

    return unpaper_args_list


def find_files(
    p: Path,
    extensions: Union[str, List[str]],
    as_string: bool = False
) -> List[Union[Path, str]]:
    if not p.exists() or not p.is_dir():
        return []

    if isinstance(extensions, str):
        extensions = [extensions]

    files: List[Path] = []
    for ext in extensions:
        files.extend(p.glob(f"*{ext}"))

    if as_string:
        return [str(f) for f in files]
    return files


def clear_dir(p: Path):
    if not p.exists():
        return False
    if not p.is_dir():
        return False
    for item in p.iterdir():
        if item.is_file():
            item.unlink()

    return True


def count_pdf_pages(pdf_path: Path) -> int:
    if not pdf_path.is_file():
        return 0
    with pikepdf.open(pdf_path) as pdf:
        return len(pdf.pages)