"""Shared fixtures for tests without external services."""

import os
from pathlib import Path

from PIL import Image
import pymupdf as fitz
import pytest

LLM_TASKS_INI = """[pdf-wtf]
LLM_TASKS = {
    "clean_html": {
        "description": {"en": "Clean HTML output", "cs": "Vyčistit HTML výstup"},
        "commands": ["export"],
        "stage": "after_unit_html",
        "template": "wp_html",
        "output": {"suffix": ".clean.html"}
    }
    }
"""


@pytest.fixture(autouse=True)
def configured_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in list(os.environ):
        if key.startswith("PDFWTF_") or key in {"GEMINI_API_KEY", "GOOGLE_API_KEY"}:
            monkeypatch.delenv(key)
    home = tmp_path / "app"
    config = home / "instance/conf/pdf-wtf.ini"
    config.parent.mkdir(parents=True)
    config.write_text("[pdf-wtf]\n", encoding="utf-8")
    monkeypatch.setenv("PDFWTF_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def no_real_gemini_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that would reach the real Gemini API."""

    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Tests must mock Gemini calls.")

    monkeypatch.setattr("pdfwtf.llm_tasks._call_gemini", blocked)


@pytest.fixture
def llm_home(configured_home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Configure the clean_html task and a dummy key in the process environment."""
    config = configured_home / "instance/conf/pdf-wtf.ini"
    config.write_text(LLM_TASKS_INI, encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "test-dummy-key")
    return configured_home


@pytest.fixture
def fake_gemini(monkeypatch: pytest.MonkeyPatch):
    """Replace the Gemini call with a recorder that returns a JSON envelope."""
    calls: list[tuple[str, Path]] = []
    behaviour: dict[str, object] = {"fail_units": set(), "response": None}

    def call(task, html_path: Path):
        calls.append((task.name, html_path))
        unit_id = html_path.name.removesuffix(".html")
        if unit_id in behaviour["fail_units"]:
            raise RuntimeError("Gemini API Error: simulated")
        if behaviour["response"] is not None:
            return behaviour["response"]
        return {
            "response": f"<h1>Clean {unit_id}</h1>",
            "usage": {"prompt_token_count": 10, "total_token_count": 15},
        }

    monkeypatch.setattr("pdfwtf.llm_tasks._call_gemini", call)
    return calls, behaviour


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
