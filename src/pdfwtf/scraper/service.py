import os

from selenium import webdriver

from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

_service = None
_driver = None


def get_service():
    global _service
    if _service is None:
        _service = Service(
            executable_path=ChromeDriverManager().install(), log_path=os.devnull
        )
    return _service


def create_custom_driver(
    locale=None,
    viewport="1440x900",
    ua=None,
    headless=True,
    user_data_dir=None,
    download_dir=None,
    force_download_pdf=False,
):
    global _driver
    if _driver is None:
        options = Options()

        # Default: normal
        options.page_load_strategy = "normal"

        # Set user-agent
        if ua:
            options.add_argument(f"--user-agent={ua}")

        # Set locale/language
        if locale:
            options.add_argument(f"--lang={locale}")

        # Optional headless mode
        if headless:
            options.add_argument("--headless=new")
            options.add_argument("--disable-gpu")

        options.add_argument("--allow-insecure-localhost")
        options.add_argument("--log-level=3")
        options.add_argument("--mute-audio")

        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--disable-extensions")

        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
        options.add_experimental_option("excludeSwitches", ["enable-logging"])

        # options.add_argument("--ignore-certificate-errors")

        if user_data_dir:
            if not user_data_dir.exists():
                user_data_dir.mkdir(parents=True, exist_ok=True)
            options.add_argument(f"--user-data-dir={str(user_data_dir)}")

        # Configure PDF download preferences
        prefs = {}
        if force_download_pdf:
            prefs["plugins.always_open_pdf_externally"] = True

        if download_dir:
            prefs["download.default_directory"] = str(download_dir)

        if prefs:
            options.add_experimental_option("prefs", prefs)

        service = get_service()

        # Launch browser
        _driver = webdriver.Chrome(service=service, options=options)

        # Set viewport via window size
        if viewport:
            try:
                width, height = map(int, viewport.split("x"))
                _driver.set_window_size(width, height)
            except ValueError:
                print(f"Invalid viewport format: {viewport}")
                return

    return _driver


def close_driver():
    global _driver
    if _driver:
        _driver.quit()
        _driver = None
