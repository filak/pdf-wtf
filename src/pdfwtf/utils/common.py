import json
import logging
import re
import shutil
from pathlib import Path
from urllib.parse import unquote, urlsplit

import img2pdf
import pikepdf
import cv3
from PIL import Image, ImageOps
from typing import Union, List, Dict, Any

from pdfwtf.configuration import AppConfig, load_config
import pytesseract

PAT_DOI = re.compile(
    r"""
    (?P<url>(?<![A-Za-z0-9])https?://[^\s]+)
    |(?P<resolver>(?<![A-Za-z0-9.-])(?:dx\.)?doi\.org/10\.\d{4,9}/[^\s]+)
    |(?P<label>(?<![A-Za-z0-9_])doi\s*:\s*[([{<\"']?\s*10\.\d{4,9}/[^\s]+)
    |(?P<bare>(?<![A-Za-z0-9_./])10\.\d{4,9}/[^\s]+)
    """,
    re.IGNORECASE | re.VERBOSE,
)

_DOI_PREFIX_AT_LINE_END = re.compile(
    r"""
    (?:
        https?://[^\s]*/
        |(?<![A-Za-z0-9.-])(?:dx\.)?doi\.org/
        |(?<![A-Za-z0-9_])doi\s*:\s*[([{<\"']?\s*
        |(?<![A-Za-z0-9_./])
    )
    10\.\d{4,9}/[^\s]*$
    """,
    re.IGNORECASE | re.VERBOSE,
)
_WRAPPERS = {"(": ")", "[": "]", "{": "}", "<": ">", '"': '"', "'": "'"}
_TRAILING_PUNCTUATION = frozenset(".,;:!?")
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")

log = logging.getLogger(__name__)


def find_project_root(marker: str = "instance") -> Path:
    """Return the configured root instead of discovering a source checkout."""
    return load_config().home


def get_temp_dir(
    clean: bool = False, debug: bool = False, *, config: AppConfig | None = None
) -> Path:
    temp_dir = (config or load_config()).temp_dir
    temp_dir.mkdir(parents=True, exist_ok=True)
    if clean:
        for item in temp_dir.iterdir():
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink()
    return temp_dir


def get_output_dir(
    output_dir: str | Path | None = None, *, config: AppConfig | None = None
) -> Path:
    config = config or load_config()
    if output_dir is None:
        path = config.output_dir
    else:
        path = Path(output_dir)
        if not path.is_absolute():
            path = config.home / path
        path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_output_dir_final(
    output_dir: Path, input_pdf: Path, input_path_prefix: str | Path | None = None
) -> Path:
    output_dir = Path(output_dir)
    if input_path_prefix is not None:
        try:
            relative = (
                Path(input_pdf).resolve().relative_to(Path(input_path_prefix).resolve())
            )
        except ValueError:
            raise ValueError(
                "The input PDF must be inside the relative path prefix."
            ) from None
        output_dir = output_dir / relative.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def parse_page_ranges(pages_str: str, total_pages: int | None = None) -> list[int]:
    """Parse validated 1-based page ranges, including open-ended ranges."""
    if not isinstance(total_pages, int) or total_pages < 1:
        raise ValueError("total_pages must be a positive integer.")
    if not isinstance(pages_str, str) or not pages_str.strip():
        raise ValueError("Specify at least one page.")
    pages = set()
    for part in pages_str.split(","):
        match = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d*)\s*)?", part)
        if not match:
            raise ValueError("Invalid page range.")
        start = int(match[1])
        end = start if match[2] is None else int(match[2]) if match[2] else total_pages
        if not 1 <= start <= end <= total_pages:
            raise ValueError(
                f"Page ranges must be ascending and within 1-{total_pages}."
            )
        pages.update(range(start, end + 1))
    return sorted(pages)


def page_sort_key(path: Path) -> tuple[int, ...]:
    """Order generated page and split-page filenames by page numbers."""
    return tuple(int(number) for number in re.findall(r"\d+", path.stem))


def images_to_pdf(
    images_dir: Path, output_pdf: Path, dpi: int = 300, fext: str = "png"
) -> None:
    # collect all images in natural sort order
    image_files = sorted(images_dir.glob(f"*.{fext}"), key=page_sort_key)
    if not image_files:
        raise ValueError(f"No PNG images found in {images_dir}")

    with output_pdf.open("wb") as f:
        f.write(
            img2pdf.convert(
                [str(p) for p in image_files],
                layout_fun=img2pdf.get_fixed_dpi_layout_fun((dpi, dpi)),
            )
        )


def extract_pages(
    input_pdf: Path,
    output_pdf: Path,
    pages_to_keep: list[int] | None = None,
    pages_to_skip: list[int] | None = None,
    zero_based: bool = False,
) -> None:
    """
    Create a new PDF with specified pages.
    """
    if pages_to_keep is None and pages_to_skip is None:
        return

    # Convert to 0-based if needed
    if not zero_based:
        if pages_to_keep:
            pages_to_keep = [p - 1 for p in pages_to_keep]
        if pages_to_skip:
            pages_to_skip = [p - 1 for p in pages_to_skip]

    with pikepdf.Pdf.new() as new_pdf:
        with pikepdf.open(input_pdf) as pdf:
            for i, page in enumerate(pdf.pages):
                if pages_to_keep is not None:
                    keep = i in pages_to_keep
                else:
                    keep = i not in (pages_to_skip or [])
                if keep:
                    new_pdf.pages.append(page)
        if not len(new_pdf.pages):
            raise ValueError("Page selection must leave at least one page.")
        new_pdf.save(output_pdf)


def export_thumbnails(
    images_dir: "Path",
    thumbs_dir: "Path",
    thumb_size: tuple[int, int] = (400, 400),
    fext: str = "jpg",
    quality: int = 75,
) -> None:
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
                img.thumbnail(thumb_size, Image.Resampling.LANCZOS)
                if fext.lower() in ("jpg", "jpeg") and img.mode != "RGB":
                    img = img.convert("RGB")

                # Optional: slight sharpening for crisper results
                # img = img.filter(ImageFilter.SHARPEN)

                out_path = thumbs_dir / f"{img_path.stem}.{fext}"

                save_kwargs = (
                    {"quality": quality, "optimize": True}
                    if fext.lower() == "jpg"
                    else {}
                )
                img.save(out_path, **save_kwargs)


def find_files(
    p: Path, extensions: Union[str, List[str]], as_string: bool = False
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


def clear_dir(p: Path) -> bool:
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


def detect_orientation(image_path: Path) -> dict:
    """Detect page orientation using Tesseract OSD."""
    with Image.open(image_path) as img:
        osd_dict = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
    return osd_dict


def correct_images_orientation(image_paths: list[Path]) -> bool:
    """
    Detects the orientation of multiple images, rotates them in-place if needed,
    and returns True if any image was rotated.

    :param image_paths: List of Path objects pointing to image files
    :return: True if at least one image was rotated, False otherwise
    """
    rotated_count = 0

    for path in image_paths:
        with Image.open(path) as img:
            try:
                osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
            except pytesseract.TesseractError:
                log.warning("Page orientation could not be determined.")
                continue
            rotate_angle = osd.get("rotate", 0)

            if rotate_angle != 0:
                img = img.rotate(-rotate_angle, expand=True)
                img.save(path)  # Overwrite original
                rotated_count += 1

    return bool(rotated_count)


def crop_dark_background(image_paths: List[Path], tool: str = "pillow") -> int:
    """
    Crop the main content of multiple images with dark backgrounds.

    :param image_paths: List of Path objects pointing to image files
    :param tool: "opencv" or "pillow" to choose the cropping method
    :return: Number of images that were actually cropped
    """
    if tool == "opencv":
        return crop_dark_background_opencv(image_paths)
    elif tool == "pillow":
        return crop_dark_background_pillow(image_paths)
    else:
        raise ValueError("Invalid tool specified. Use 'opencv' or 'pillow'.")


def crop_dark_background_opencv(image_paths: list[Path]) -> int:
    cropped_count = 0

    for path in image_paths:
        img = cv3.imread(str(path))
        gray = cv3.cvtColor(img, cv3.COLOR_BGR2GRAY)

        # Use adaptive threshold to handle uneven backgrounds
        thresh = cv3.adaptiveThreshold(
            gray, 255, cv3.ADAPTIVE_THRESH_MEAN_C, cv3.THRESH_BINARY, 15, -10
        )

        contours, _ = cv3.findContours(
            thresh, cv3.RETR_EXTERNAL, cv3.CHAIN_APPROX_SIMPLE
        )
        if contours:
            c = max(contours, key=cv3.contourArea)
            x, y, w, h = cv3.boundingRect(c)
            if w < img.shape[1] or h < img.shape[0]:
                cropped = img[y : y + h, x : x + w]  # noqa: E203
                cv3.imwrite(str(path), cropped)
                cropped_count += 1

    return cropped_count


def crop_dark_background_pillow(image_paths: list[Path]) -> int:
    cropped_count = 0

    for path in image_paths:
        with Image.open(path) as img:
            # Convert to grayscale
            gray = img.convert("L")
            # Invert so that content is dark, background is white
            inverted = ImageOps.invert(gray)
            # Optional: enhance contrast
            bw = inverted.point(lambda x: 0 if x < 30 else 255, mode="1")
            bbox = bw.getbbox()
            if bbox and (bbox[2] < img.width or bbox[3] < img.height):
                cropped = img.crop(bbox)
                cropped.save(path)
                cropped_count += 1

    return cropped_count


def _join_doi_continuations(text: str) -> str:
    """Join a DOI line break only when the next line is one isolated token."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    result = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.rstrip()
        if index + 1 < len(lines) and _DOI_PREFIX_AT_LINE_END.search(stripped):
            continuation = lines[index + 1].strip()
            if (
                continuation
                and re.fullmatch(r"[^\s]+", continuation)
                and stripped[-1] not in ")]}>\"'.,;:!?"
            ):
                line = stripped + continuation
                index += 1
        result.append(line)
        index += 1
    return "\n".join(result)


def _opening_wrapper(text: str, match: re.Match, doi_start: int) -> str | None:
    prefix = text[match.start() : doi_start].rstrip()
    if prefix and prefix[-1] in _WRAPPERS:
        return prefix[-1]
    if match.start() and text[match.start() - 1] in _WRAPPERS:
        return text[match.start() - 1]
    return None


def _remove_clear_wrapper(value: str, opening: str | None) -> str:
    if opening is None:
        return value
    closing = _WRAPPERS[opening]
    position = value.rfind(closing)
    if position < 0:
        return value
    trailing = value[position + 1 :]
    if trailing and any(char not in _TRAILING_PUNCTUATION for char in trailing):
        return value
    return value[:position] + trailing


def _extract_doi_candidates(text: str) -> List[str]:
    """Extract unverified DOI candidates in order of first occurrence."""
    text = text.replace("\u00ad", "").replace("\u200b", "")
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = _join_doi_continuations(text)

    candidates = []
    seen = set()
    for match in PAT_DOI.finditer(text):
        representation = match.group()
        relative_doi = re.search(r"10\.", representation, re.IGNORECASE)
        if relative_doi is None:
            continue
        doi_start = match.start() + relative_doi.start()
        opening = _opening_wrapper(text, match, doi_start)
        representation = _remove_clear_wrapper(representation, opening)

        if match.lastgroup == "url":
            parsed = urlsplit(representation)
            if (parsed.hostname or "").lower() not in {"doi.org", "dx.doi.org"}:
                continue
            candidate = unquote(parsed.path.lstrip("/"))
        elif match.lastgroup == "resolver":
            parsed = urlsplit(f"//{representation}")
            candidate = unquote(parsed.path.lstrip("/"))
        else:
            candidate = representation[relative_doi.start() :]

        if not re.fullmatch(r"10\.\d{4,9}/[^\s]+", candidate, re.IGNORECASE):
            continue
        at_line_break = match.end() < len(text) and text[match.end()] == "\n"
        if at_line_break and candidate.endswith("-"):
            continue

        key = candidate.translate(_ASCII_LOWER)
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)
    return candidates


def get_doi(texts_dir: Path) -> List[str]:
    if not texts_dir or not texts_dir.exists() or not texts_dir.is_dir():
        return []

    txt_files = sorted(texts_dir.glob("*.txt"))
    if not txt_files:
        return []

    try:
        content = txt_files[0].read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    return _extract_doi_candidates(content)


def write_json(data: Dict[str, Any], filepath: Path) -> None:
    with filepath.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
