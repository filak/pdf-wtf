import re
import time
import base64
from pathlib import Path
import fitz  # PyMuPDF
from selenium.webdriver.support.ui import WebDriverWait

from pdfwtf.utils import (
    get_temp_dir,
)

from pdfwtf.scraper.utils import (
    get_user_agent,
    get_main_status_code,
    hash_url,
    filename_from_url,
)

from pdfwtf.scraper.service import create_custom_driver, close_driver


def wait_for_download(folder: Path, pdf_name: str, timeout: int = 30) -> Path:
    """Wait until Chrome finishes downloading the expected PDF file."""

    # Clean requested name (stem only, no extension)
    expected_stem = Path(pdf_name).stem
    end_time = time.time() + timeout

    while time.time() < end_time:
        # Skip while there are incomplete downloads
        if not list(folder.glob("*.crdownload")):
            for f in folder.glob("*.pdf"):
                # Normalize the stem: strip " (number)" or "(number)" at the end
                clean_stem = re.sub(r"\s*\(\d+\)$", "", f.stem)
                if clean_stem == expected_stem:
                    return f.resolve(strict=True)

        time.sleep(0.5)

    raise RuntimeError("Download timed out")


def screenshot_first_page(
    pdf_path: Path, output_image_path: Path, zoom: float = 2.0, debug=False
):
    """Take first page of PDF and save as PNG."""
    try:
        doc = fitz.open(str(pdf_path))
        page = doc.load_page(0)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        pix.save(str(output_image_path))
        doc.close()
        if debug:
            print(f"Screenshot saved to: {output_image_path}")
        return True
    except Exception as e:
        if debug:
            print(f"Could not create screenshot: {e}")


def _prepare_paths(url, temp_dir, pdf_dir, shot_dir, debug):
    output_dir = (
        Path(temp_dir).resolve(strict=True) if temp_dir else get_temp_dir(debug=debug)
    )
    fname = hash_url(url)

    download_dir = output_dir / "_downloads"
    download_dir.mkdir(parents=True, exist_ok=True)

    output_pdf = output_dir / pdf_dir / f"{fname}.pdf"
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    output_image = output_dir / shot_dir / f"{fname}.png"

    return output_dir, download_dir, output_pdf, output_image, fname


def _download_pdf(driver, url, download_dir, output_pdf, timeout, debug):
    main_status, mime_type = get_main_status_code(driver, url, mime=True)
    if debug:
        print(f"Detected Status: {main_status}")
        print(f"Detected Content-Type: {mime_type}")

    if main_status != 200 or not mime_type:
        return False

    if "application/pdf" in mime_type.lower() or url.lower().endswith(".pdf"):
        pdf_name = filename_from_url(url).removesuffix(".pdf").removesuffix(".PDF")
        if debug:
            print(f"PDF response detected - file: {pdf_name}")
        downloaded_pdf = wait_for_download(download_dir, pdf_name, timeout=timeout)
        if downloaded_pdf:
            if output_pdf.exists():
                output_pdf.unlink()
            downloaded_pdf.rename(output_pdf)
            if debug:
                print(f"PDF saved to {output_pdf}")
            return True
    else:
        if debug:
            print("HTML page detected - rendering to PDF")
        WebDriverWait(driver, 20).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        pdf_data = driver.execute_cdp_cmd(
            "Page.printToPDF",
            {"printBackground": True, "preferCSSPageSize": True, "scale": 1.0},
        )
        with open(output_pdf, "wb") as f:
            f.write(base64.b64decode(pdf_data["data"]))
        if debug:
            print(f"Page rendered to {output_pdf}")
        return True

    return False


def _take_screenshot(
    output_pdf, output_image, screenshot_path, force_download, zoom, debug
):
    if not output_pdf.exists():
        return None

    if screenshot_path:
        output_image = Path(screenshot_path).resolve(strict=True)
    else:
        output_image.parent.mkdir(parents=True, exist_ok=True)

    if output_image.exists() and not force_download:
        if debug:
            print(f"File already exists: {output_image}")
        return output_image

    screenshot_first_page(output_pdf, output_image, zoom=zoom, debug=debug)
    return output_image


def save_page_as_pdf(
    url: str,
    temp_dir: str = None,
    screenshot_path: str = None,
    timeout: int = 30,
    screenshot_zoom: float = 2.0,
    ua: str = None,
    viewport: str = "1920x1080",
    force_download: bool = False,
    make_shot: bool = False,
    pdf_dir: str = "docs",
    shot_dir: str = "shots",
    debug: bool = False,
):
    output_dir, download_dir, output_pdf, output_image, fname = _prepare_paths(
        url, temp_dir, pdf_dir, shot_dir, debug
    )

    ua = ua or get_user_agent()
    if debug:
        print("USER_AGENT: ", ua)

    # Skip download if file exists
    if not (output_pdf.exists() and not force_download):
        driver = create_custom_driver(
            download_dir=download_dir,
            force_download_pdf=True,
            ua=ua,
            viewport=viewport,
        )
        driver.get(url)
        time.sleep(1)
        _download_pdf(driver, url, download_dir, output_pdf, timeout, debug)
        close_driver()
    elif debug:
        print(f"File already exists: {output_pdf}")

    # Screenshot if requested
    shot_file = None
    if make_shot:
        shot_file = _take_screenshot(
            output_pdf,
            output_image,
            screenshot_path,
            force_download,
            screenshot_zoom,
            debug,
        )

    return (fname, output_pdf if output_pdf.exists() else None, shot_file)
