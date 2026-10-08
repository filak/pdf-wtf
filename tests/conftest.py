"""Shared fixtures for tests without external services."""

import os
from pathlib import Path

from PIL import Image
import pymupdf as fitz
import pytest


@pytest.fixture(autouse=True)
def configured_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in list(os.environ):
        if key.startswith("PDFWTF_"):
            monkeypatch.delenv(key)
    home = tmp_path / "app"
    config = home / "instance/conf/pdf-wtf.ini"
    config.parent.mkdir(parents=True)
    config.write_text("[pdf-wtf]\n", encoding="utf-8")
    monkeypatch.setenv("PDFWTF_HOME", str(home))
    return home


@pytest.fixture
def make_pdf(tmp_path: Path):
    def create(pages: list[str], name: str = "input.pdf") -> Path:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with fitz.open() as doc:
            for kind in pages:
                page = doc.new_page(width=200, height=300)
                if kind == "digital":
                    page.insert_text(
                        (20, 30),
                        "This document has enough meaningful words\n"
                        "to identify the page as digital text.",
                    )
                elif kind == "scan":
                    image = Image.new("RGB", (100, 150), "white")
                    import io

                    stream = io.BytesIO()
                    image.save(stream, format="PNG")
                    page.insert_image(page.rect, stream=stream.getvalue())
            doc.save(path)
        return path

    return create
