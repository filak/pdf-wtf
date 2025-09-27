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
    url_to_path,
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
                    return f.resolve()

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
    if temp_dir:
        output_dir = Path(temp_dir).resolve()
    else:
        output_dir = get_temp_dir()

    if not ua:
        ua = get_user_agent()

    if debug:
        print("USER_AGENT: ", ua)

    download_dir = output_dir / "_downloads"
    download_dir.mkdir(parents=True, exist_ok=True)

    # Generate filename from URL
    fname = url_to_path(url)
    output_pdf = output_dir / pdf_dir / f"{fname}.pdf"

    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    main_status = None

    # If file already exists and force_download=False, skip download
    if output_pdf.exists() and not force_download:
        if debug:
            print(f"File already exists: {output_pdf}")
    else:
        # --- Launch Chrome ---
        driver = create_custom_driver(
            download_dir=download_dir,
            force_download_pdf=True,
            ua=ua,
            viewport=viewport,
        )
        # driver.requests.clear()
        driver.get(url)
        time.sleep(1)

        main_status, mime_type = get_main_status_code(driver, url, mime=True)

        if debug:
            print(f"Detected Status: {main_status}")
            print(f"Detected Content-Type: {mime_type}")

        if main_status == 200 and mime_type:

            if "application/pdf" in mime_type.lower() or url.lower().endswith(".pdf"):
                # --- Handle direct PDF ---

                pdf_name = (
                    filename_from_url(url).removesuffix(".pdf").removesuffix(".PDF")
                )

                if debug:
                    print(f"PDF response detected - file:  {pdf_name}")

                downloaded_pdf = wait_for_download(
                    download_dir, pdf_name, timeout=timeout
                )
                if downloaded_pdf:
                    if output_pdf.exists():
                        output_pdf.unlink()

                    downloaded_pdf.rename(output_pdf)
                    if debug:
                        print(f"PDF saved to {output_pdf}")

            else:
                # --- Handle HTML page ---
                if debug:
                    print("HTML page detected - rendering to PDF")
                WebDriverWait(driver, 20).until(
                    lambda d: d.execute_script("return document.readyState")
                    == "complete"  # noqa: W503
                )  # noqa: W503
                pdf_data = driver.execute_cdp_cmd(
                    "Page.printToPDF",
                    {"printBackground": True, "preferCSSPageSize": True, "scale": 1.0},
                )
                with open(output_pdf, "wb") as f:
                    f.write(base64.b64decode(pdf_data["data"]))
                if debug:
                    print(f"Page rendered to {output_pdf}")

        close_driver()

    output_image = None
    shot_created = False

    if make_shot and output_pdf.exists():
        # --- Take first-page screenshot ---
        if screenshot_path:
            output_image = Path(screenshot_path).resolve()
        else:
            output_image = output_dir / shot_dir / f"{fname}.png"
            output_image.parent.mkdir(parents=True, exist_ok=True)

        if output_image.exists() and not force_download:
            if debug:
                print(f"File already exists: {output_image}")
        else:
            shot_created = screenshot_first_page(
                output_pdf, output_image, zoom=screenshot_zoom, debug=debug
            )

    if output_pdf.exists():
        return fname, output_pdf, output_image

    return (None, None, None)


# ------------------------
# Example usage
# ------------------------
if __name__ == "__main__":
    resp = save_page_as_pdf(
        # "https://www.apsscr.cz/files/files/%C5%BDivot%20s%20demenc%C3%AD_Tipy%20pro%20rodinn%C3%A9%20p%C5%99%C3%ADslu%C5%A1n%C3%ADky.pdf",
        # "https://www.medvik.cz/medlike/",
        # "https://doi.org/10.1002/pbc.29779", # "https://onlinelibrary.wiley.com/doi/10.1002/pbc.29779",
        "https://nlk.cz/sluzby/vzdaleny-pristup/",
        # force_download=True,
        debug=True,
    )
    print(resp)
