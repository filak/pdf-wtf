"""Tests for the optional reusable PDF-WTF-GUI blueprint."""

from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("flask")

from pdfwtf.container_analysis import analyze_container  # noqa: E402
from pdfwtf.gui.adapter import JobStatus  # noqa: E402
from pdfwtf.gui.app import create_app  # noqa: E402


class ImmediateAdapter:
    """Run analysis immediately to keep route tests deterministic."""

    def __init__(self) -> None:
        self.jobs: dict[str, tuple[Path, dict[str, Any]]] = {}

    def start(self, job_id: str, source: Path, document_type: str) -> None:
        self.jobs[job_id] = (source, analyze_container(source, document_type))

    def status(self, job_id: str) -> JobStatus:
        self._get(job_id)
        return JobStatus("complete", demo=True)

    def result(self, job_id: str) -> dict[str, Any]:
        return self._get(job_id)[1]

    def document_path(self, job_id: str) -> Path:
        return self._get(job_id)[0]

    def _get(self, job_id: str) -> tuple[Path, dict[str, Any]]:
        try:
            return self.jobs[job_id]
        except KeyError:
            raise KeyError("Unknown job.") from None


@pytest.fixture
def gui_app(configured_home: Path):
    adapter = ImmediateAdapter()
    app = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "PDFWTF_GUI_ANALYSIS_ADAPTER": adapter,
        }
    )
    return app, adapter, configured_home


def test_index_uses_namespaced_local_assets_and_language(gui_app):
    app, _adapter, _configured_home = gui_app
    response = app.test_client().get("/?lang=cs")
    assert response.status_code == 200
    assert b"/assets/vendor/bootstrap/bootstrap.min.css" in response.data
    assert b"/assets/vendor/htmx/htmx.min.js" in response.data
    assert b'name="lang"' in response.data


def test_upload_review_document_and_approved_plan(gui_app, make_pdf):
    app, adapter, configured_home = gui_app
    source = make_pdf(["digital"])
    client = app.test_client()
    response = client.post(
        "/jobs",
        data={
            "document_type": "unit",
            "document": (BytesIO(source.read_bytes()), "source.pdf"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    job_id = next(iter(adapter.jobs))
    assert adapter.document_path(job_id) == (
        configured_home / "instance/_data/in" / job_id / "source.pdf"
    )

    status = client.get(f"/jobs/{job_id}/status")
    assert b"Review" in status.data
    review = client.get(f"/jobs/{job_id}/review")
    assert review.status_code == 200
    assert b"pdf.min.mjs" not in review.data
    assert b"gui.js" in review.data

    document = client.get(f"/jobs/{job_id}/document")
    assert document.status_code == 200
    assert document.mimetype == "application/pdf"
    assert document.headers["Cache-Control"] == "private, no-store"

    analysis = adapter.result(job_id)
    plan = {
        "schema_version": analysis["schema_version"],
        "kind": "plan",
        "source": analysis["source"],
        "document_type": analysis["document_type"],
        "pages": [{**page, "selected": True} for page in analysis["pages"]],
        "units": [
            {
                "id": unit["id"],
                "title": unit["title"],
                "type": unit["type"],
                "selected": True,
                "boundary_status": "confirmed",
                "input_pages": unit["input_pages"],
            }
            for unit in analysis["units"]
        ],
    }
    saved = client.post(f"/jobs/{job_id}/plan", json=plan)
    assert saved.status_code == 200
    downloaded = client.get(saved.json["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.json["kind"] == "plan"
    assert (
        configured_home / "instance/_data/out" / job_id / "approved-plan.json"
    ).is_file()


def test_access_callback_protects_blueprint(gui_app):
    app, _adapter, _configured_home = gui_app
    app.config["PDFWTF_GUI_ACCESS_CHECK"] = lambda _action, _job_id: False
    assert app.test_client().get("/").status_code == 403


def test_standalone_host_keeps_default_input_directory(
    configured_home: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PDFWTF_INPUT_DIR", "cli-imports")
    app = create_app(
        {
            "TESTING": True,
            "PDFWTF_GUI_ANALYSIS_ADAPTER": ImmediateAdapter(),
        }
    )
    assert app.config["PDFWTF_GUI_INPUT_DIR"] == (configured_home / "instance/_data/in")


def test_standalone_host_enforces_csrf(configured_home: Path):
    app = create_app(
        {
            "TESTING": True,
            "PDFWTF_GUI_ANALYSIS_ADAPTER": ImmediateAdapter(),
        }
    )
    response = app.test_client().post(
        "/jobs",
        data={"document_type": "unit"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
