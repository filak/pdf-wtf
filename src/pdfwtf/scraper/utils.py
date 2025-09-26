import json
import hashlib
import base64
from pathlib import Path
from urllib.parse import urlparse, unquote


def get_main_status_code(driver, target_url, mime=False):
    logs = driver.get_log("performance")
    for entry in logs:
        try:
            log = json.loads(entry["message"])["message"]
            if log["method"] == "Network.responseReceived":
                response = log["params"]["response"]
                if response["url"].startswith(target_url):
                    if mime:
                        return (response["status"], response["mimeType"])
                    else:
                        return response["status"]
        except (KeyError, ValueError, TypeError):
            continue
    if mime:
        return (None, None)
    return None


def filename_from_url(url: str) -> str:
    path = urlparse(url).path  # get the path part of URL
    name = Path(path).name  # get the last segment
    return unquote(name)  # decode URL-encoded characters


def url_to_path(url: str, length: int = 32) -> str:
    """
    Converts a URL into a filesystem-safe folder name using SHA256 and Base64 encoding.
    length: number of characters in the folder name (default 32 for low collision probability).
    """
    # Hash the URL
    digest = hashlib.sha256(url.encode("utf-8")).digest()

    # URL-safe Base64 encode
    folder_name = base64.urlsafe_b64encode(digest).decode("utf-8")

    # Remove any trailing '=' padding
    folder_name = folder_name.rstrip("=")

    # Truncate to desired length
    return folder_name[:length]
