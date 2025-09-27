import json
import os
import re
import shutil
from pathlib import Path
import fitz  # PyMuPDF
import img2pdf
import pikepdf
import cv2
from PIL import Image, ImageOps
from typing import Union, List, Dict, Any
import pytesseract

PAT_DOI = re.compile(r"(?:https?://)?doi\.org/(10\.\d{4,9}/[^\s]+)", re.IGNORECASE)

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


def get_temp_dir(clean: bool = False, debug=False) -> Path:

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
            except Exception as e:
                if debug:
                    print(f"Cannot clean the temp dir: {e}")

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
        f.write(img2pdf.convert([str(p) for p in image_files]))


def extract_pages(
    input_pdf: Path,
    output_pdf: Path,
    pages_to_keep: List[int] = None,
    pages_to_skip: List[int] = None,
    zero_based: bool = False,
):
    """
    Create a new PDF with specified pages.
    """
    if not pages_to_keep and not pages_to_skip:
        return

    # Convert to 0-based if needed
    if not zero_based:
        if pages_to_keep:
            pages_to_keep = [p - 1 for p in pages_to_keep]
        if pages_to_skip:
            pages_to_skip = [p - 1 for p in pages_to_skip]

    try:
        new_pdf = pikepdf.Pdf.new()

        with pikepdf.open(input_pdf) as pdf:
            for i, page in enumerate(pdf.pages):
                if pages_to_keep:
                    if i in pages_to_keep:
                        new_pdf.pages.append(page)
                elif pages_to_skip:
                    if i not in pages_to_skip:
                        new_pdf.pages.append(page)

        new_pdf.save(output_pdf)
        new_pdf.close()
    except Exception as e:
        raise RuntimeError(f"Failed to extract pages: {e}")


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
            osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
            rotate_angle = osd.get("rotate", 0)

            if rotate_angle != 0:
                img = img.rotate(-rotate_angle, expand=True)
                img.save(path)  # Overwrite original
                rotated_count += 1

    return rotated_count


def crop_dark_background(image_paths: List[Path], tool="opencv") -> int:
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
        img = cv2.imread(str(path))
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # Use adaptive threshold to handle uneven backgrounds
        thresh = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, -10
        )

        contours, _ = cv2.findContours(
            thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        if contours:
            c = max(contours, key=cv2.contourArea)
            x, y, w, h = cv2.boundingRect(c)
            if w < img.shape[1] or h < img.shape[0]:
                cropped = img[y : y + h, x : x + w]  # noqa: E203
                cv2.imwrite(str(path), cropped)
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

    # Normalize dashes → hyphen
    content = content.replace("\u2013", "-").replace("\u2014", "-")

    # Fix hyphenation at line breaks
    content = re.sub(r"-\s*\n\s*", "-", content)

    # Replace remaining newlines with space
    content = content.replace("\n", " ")

    matches = PAT_DOI.findall(content)

    # strip trailing punctuation & lowercase
    matches = [m.rstrip(".,;:)\"'").lower() for m in matches]

    seen = set()
    deduped = []
    for m in matches:
        if m not in seen:
            seen.add(m)
            deduped.append(m)

    final = []
    for m in deduped:
        if any(other != m and other.startswith(m) for other in deduped):
            continue
        final.append(m)

    return final


def write_json(data: Dict[str, Any], filepath: Path) -> None:
    with filepath.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
