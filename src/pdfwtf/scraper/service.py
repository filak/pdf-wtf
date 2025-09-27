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


def _configure_options(ua=None, locale=None, headless=True) -> Options:
    options = Options()
    options.page_load_strategy = "normal"

    if ua:
        options.add_argument(f"--user-agent={ua}")

    if locale:
        options.add_argument(f"--lang={locale}")

    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")

    # Common flags
    options.add_argument("--allow-insecure-localhost")
    options.add_argument("--log-level=3")
    options.add_argument("--mute-audio")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-extensions")

    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    options.add_experimental_option("excludeSwitches", ["enable-logging"])
    return options


def _configure_user_data_dir(options: Options, user_data_dir):
    if user_data_dir:
        user_data_dir.mkdir(parents=True, exist_ok=True)
        options.add_argument(f"--user-data-dir={str(user_data_dir)}")


def _configure_prefs(options: Options, download_dir=None, force_download_pdf=False):
    prefs = {}
    if force_download_pdf:
        prefs["plugins.always_open_pdf_externally"] = True
    if download_dir:
        prefs["download.default_directory"] = str(download_dir)
    if prefs:
        options.add_experimental_option("prefs", prefs)


def _set_viewport(driver, viewport: str):
    if not viewport:
        return
    try:
        width, height = map(int, viewport.split("x"))
        driver.set_window_size(width, height)
    except ValueError:
        print(f"Invalid viewport format: {viewport}")


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
        options = _configure_options(ua=ua, locale=locale, headless=headless)
        _configure_user_data_dir(options, user_data_dir)
        _configure_prefs(
            options, download_dir=download_dir, force_download_pdf=force_download_pdf
        )

        service = get_service()
        _driver = webdriver.Chrome(service=service, options=options)
        _set_viewport(_driver, viewport)

    return _driver


def close_driver():
    global _driver
    if _driver:
        _driver.quit()
        _driver = None
