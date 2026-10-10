"""Tests for the optional reusable PDF-WTF-GUI blueprint."""

from io import BytesIO
import json
import mimetypes
from pathlib import Path
import re
from typing import Any

import pytest

pytest.importorskip("flask")

from pdfwtf.container_analysis import analyze_container  # noqa: E402
from pdfwtf.gui.adapter import DemoAnalysisAdapter, JobStatus  # noqa: E402
from pdfwtf.gui.app import create_app  # noqa: E402


class ImmediateAdapter:
    """Run analysis immediately to keep route tests deterministic."""

    def __init__(self) -> None:
        self.jobs: dict[str, tuple[Path, dict[str, Any]]] = {}
        self.starts = 0

    def start(self, job_id: str, source: Path, document_type: str) -> None:
        self.starts += 1
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
    assert "Vybrat soubor".encode() in response.data
    assert "Není vybrán žádný soubor".encode() in response.data
    assert b"/assets/upload.js" in response.data
    english = app.test_client().get("/?lang=en")
    assert b"Choose file" in english.data
    assert b"No file selected" in english.data


def test_upload_review_document_and_approved_plan(gui_app, make_pdf):
    app, adapter, configured_home = gui_app
    source = make_pdf(["digital", "digital"])
    client = app.test_client()
    response = client.post(
        "/uploads",
        data={
            "document_type": "unit",
            "document": (BytesIO(source.read_bytes()), "source.pdf"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    assert not adapter.jobs
    job_id = next((configured_home / "instance/_data/in").iterdir()).name
    listing = client.get("/")
    assert b"source.pdf" in listing.data
    assert f"/jobs/{job_id}/analyze".encode() in listing.data
    response = client.post(f"/jobs/{job_id}/analyze", data={"document_type": "unit"})
    assert response.status_code == 200
    assert adapter.document_path(job_id) == (
        configured_home / "instance/_data/in" / job_id / "source.pdf"
    )

    status = client.get(f"/jobs/{job_id}/status")
    assert b"Review" in status.data
    review = client.get(f"/jobs/{job_id}/review")
    assert review.status_code == 200
    assert b"pdf.min.mjs" not in review.data
    assert b"gui.js" in review.data
    assert b'id="review-more"' in review.data
    assert b'id="delete-plan"' in review.data
    assert b"Show analysis" in review.data

    document = client.get(f"/jobs/{job_id}/document")
    assert document.status_code == 200
    assert document.mimetype == "application/pdf"
    assert document.headers["Cache-Control"] == "private, no-store"

    analysis = adapter.result(job_id)
    analysis_download = client.get(f"/jobs/{job_id}/analysis")
    assert analysis_download.status_code == 200
    assert analysis_download.json == analysis
    assert "source.analysis.json" in analysis_download.headers["Content-Disposition"]
    assert analysis_download.headers["Cache-Control"] == "private, no-store"
    analysis_view = client.get(f"/jobs/{job_id}/analysis?inline=1")
    assert analysis_view.status_code == 200
    assert analysis_view.json == analysis
    assert analysis_view.headers["Content-Disposition"].startswith("inline;")
    assert analysis_download.headers["Content-Disposition"].startswith("attachment;")
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
    plan["units"][0]["title"] = "Reviewed unit title"
    plan["units"][0]["type"] = "full-page-advertisement"
    plan["units"][0]["selected"] = False
    plan["units"][0]["input_pages"] = {"start": 1, "end": 2}
    plan["units"].append(
        {
            **plan["units"][0],
            "id": "reviewed-second-unit",
            "title": "Added unit",
            "selected": True,
            "input_pages": {"start": 2, "end": 2},
        }
    )
    for page in plan["pages"]:
        page["unit_ids"] = [
            unit["id"]
            for unit in plan["units"]
            if unit["input_pages"]["start"]
            <= page["input_page"]
            <= unit["input_pages"]["end"]
        ]
    metadata_path = configured_home / "instance/_data/in" / job_id / "upload.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["document_type"] = "book"
    metadata["extra"] = "preserved"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    saved = client.post(f"/jobs/{job_id}/plan", json=plan)
    assert saved.status_code == 200
    reopened = client.get(f"/jobs/{job_id}/review")
    assert reopened.status_code == 200
    embedded_plan = re.search(
        r'<script id="plan-data" type="application/json">(.*?)</script>',
        reopened.get_data(as_text=True),
        re.DOTALL,
    )
    assert embedded_plan is not None
    assert json.loads(embedded_plan.group(1)) == plan
    assert adapter.result(job_id) == analysis
    updated_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert updated_metadata["document_type"] == "book"
    assert updated_metadata["filename"] == "source.pdf"
    assert updated_metadata["extra"] == "preserved"
    assert not metadata_path.with_suffix(".json.tmp").exists()
    invalid = client.post(
        f"/jobs/{job_id}/plan", json={**plan, "document_type": "invalid"}
    )
    assert invalid.status_code == 400
    assert json.loads(metadata_path.read_text(encoding="utf-8")) == updated_metadata
    downloaded = client.get(saved.json["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.json["kind"] == "plan"
    assert "approved.plan.json" in downloaded.headers["Content-Disposition"]
    assert (
        configured_home / "instance/_data/out" / job_id / "approved.plan.json"
    ).is_file()

    document.close()
    downloaded.close()
    app.config["PDFWTF_GUI_ACCESS_CHECK"] = (
        lambda action, _job_id: action != "delete_plan"
    )
    assert client.delete(f"/jobs/{job_id}/plan").status_code == 403
    app.config["PDFWTF_GUI_ACCESS_CHECK"] = None
    assert client.delete(f"/jobs/{job_id}/plan").status_code == 200
    assert client.get(f"/jobs/{job_id}/plan").status_code == 404
    assert not (
        configured_home / "instance/_data/out" / job_id / "approved.plan.json"
    ).exists()
    assert (configured_home / "instance/_data/in" / job_id / "source.pdf").is_file()
    assert adapter.result(job_id) == analysis
    restored = client.get(f"/jobs/{job_id}/review")
    assert (
        b'<script id="plan-data" type="application/json">null</script>' in restored.data
    )
    assert client.delete(f"/jobs/{job_id}/plan").status_code == 200
    assert client.post(f"/jobs/{job_id}/plan", json=plan).status_code == 200
    deleted = client.delete(f"/uploads/{job_id}")
    assert deleted.status_code == 200
    assert not (configured_home / "instance/_data/in" / job_id).exists()
    assert not (configured_home / "instance/_data/out" / job_id).exists()
    for suffix in ("status", "review", "document", "plan", "analysis"):
        assert client.get(f"/jobs/{job_id}/{suffix}").status_code == 404
    assert client.post(f"/jobs/{job_id}/plan", json=plan).status_code == 404


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
        "/uploads",
        data={"document_type": "unit"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert (
        app.test_client()
        .post("/jobs/" + "a" * 32 + "/analyze", data={"document_type": "unit"})
        .status_code
        == 400
    )


def test_upload_listing_persists_and_filters_access(gui_app, make_pdf):
    app, adapter, home = gui_app
    client = app.test_client()
    source = make_pdf(["digital"])
    response = client.post(
        "/uploads",
        data={"document": (BytesIO(source.read_bytes()), "<example>.pdf")},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert b"&lt;example&gt;.pdf" in response.data
    assert not adapter.jobs
    job_id = next((home / "instance/_data/in").iterdir()).name
    app.config["PDFWTF_GUI_ANALYSIS_ADAPTER"] = ImmediateAdapter()
    assert b"&lt;example&gt;.pdf" in client.get("/").data
    app.config["PDFWTF_GUI_ACCESS_CHECK"] = (
        lambda action, identifier: identifier is None
    )
    assert b"&lt;example&gt;.pdf" not in client.get("/").data
    assert (
        client.post(
            f"/jobs/{job_id}/analyze", data={"document_type": "unit"}
        ).status_code
        == 403
    )


def test_invalid_upload_and_analysis_do_not_start_jobs(gui_app, make_pdf):
    app, adapter, home = gui_app
    client = app.test_client()
    assert (
        client.post(
            "/uploads", data={"document": (BytesIO(b"not PDF"), "bad.pdf")}
        ).status_code
        == 400
    )
    assert not list((home / "instance/_data/in").iterdir())
    source = make_pdf(["digital"])
    client.post(
        "/uploads", data={"document": (BytesIO(source.read_bytes()), "valid.pdf")}
    )
    job_id = next((home / "instance/_data/in").iterdir()).name
    assert (
        client.post(
            f"/jobs/{job_id}/analyze", data={"document_type": "invalid"}
        ).status_code
        == 400
    )
    assert client.post("/jobs/missing/analyze").status_code == 404
    assert not adapter.jobs
    for _ in range(2):
        assert (
            client.post(
                f"/jobs/{job_id}/analyze", data={"document_type": "unit"}
            ).status_code
            == 200
        )
    assert len(adapter.jobs) == 1
    assert adapter.starts == 2


def test_delete_upload_removes_only_selected_file(gui_app, make_pdf):
    app, adapter, home = gui_app
    client = app.test_client()
    source = make_pdf(["digital"])
    for name in ("first.pdf", "second.pdf"):
        client.post("/uploads", data={"document": (BytesIO(source.read_bytes()), name)})
    directories = sorted((home / "instance/_data/in").iterdir())
    job_id = directories[0].name
    listing = client.get("/").data
    delete_button = f'hx-delete="/uploads/{job_id}"'.encode()
    assert delete_button in listing
    assert listing.index(delete_button) < listing.index(
        f'id="document-type-{job_id}"'.encode()
    )
    assert client.delete(f"/uploads/{job_id}").status_code == 200
    assert not directories[0].exists()
    assert (directories[1] / "source.pdf").is_file()
    assert f"/uploads/{job_id}".encode() not in client.get("/").data
    assert client.delete(f"/uploads/{job_id}").status_code == 404
    assert client.delete("/uploads/invalid").status_code == 404
    assert (
        client.post(
            f"/jobs/{job_id}/analyze", data={"document_type": "unit"}
        ).status_code
        == 404
    )
    assert not adapter.jobs


@pytest.mark.parametrize("state", ["queued", "processing"])
def test_delete_upload_blocks_active_analysis(gui_app, make_pdf, monkeypatch, state):
    app, adapter, home = gui_app
    client = app.test_client()
    source = make_pdf(["digital"])
    client.post(
        "/uploads", data={"document": (BytesIO(source.read_bytes()), "file.pdf")}
    )
    directory = next((home / "instance/_data/in").iterdir())
    monkeypatch.setattr(adapter, "status", lambda _job_id: JobStatus(state))
    response = client.delete(f"/uploads/{directory.name}")
    assert response.status_code == 409
    assert response.headers["HX-Retarget"] == f"#job-state-{directory.name}"
    assert b"Wait for analysis" in response.data
    assert (directory / "source.pdf").is_file()
    assert (directory / "upload.json").is_file()


def test_delete_upload_checks_access_and_csrf(gui_app, make_pdf):
    app, _adapter, home = gui_app
    client = app.test_client()
    source = make_pdf(["digital"])
    client.post(
        "/uploads", data={"document": (BytesIO(source.read_bytes()), "file.pdf")}
    )
    directory = next((home / "instance/_data/in").iterdir())
    actions = []

    def access_check(action, job_id):
        actions.append((action, job_id))
        return action != "delete"

    app.config["PDFWTF_GUI_ACCESS_CHECK"] = access_check
    assert client.delete(f"/uploads/{directory.name}").status_code == 403
    assert ("delete", directory.name) in actions
    assert (directory / "source.pdf").is_file()
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.delete(f"/uploads/{directory.name}").status_code == 400
    assert (directory / "source.pdf").is_file()
    app.config["PDFWTF_GUI_ACCESS_CHECK"] = lambda _action, _job_id: True
    token = re.search(rb'name="csrf_token" value="([^"]+)"', client.get("/").data)[1]
    assert (
        client.delete(
            f"/uploads/{directory.name}", data={"csrf_token": token.decode()}
        ).status_code
        == 200
    )


def test_delete_upload_reports_filesystem_failure(gui_app, make_pdf, monkeypatch):
    app, _adapter, home = gui_app
    client = app.test_client()
    source = make_pdf(["digital"])
    client.post(
        "/uploads", data={"document": (BytesIO(source.read_bytes()), "file.pdf")}
    )
    directory = next((home / "instance/_data/in").iterdir())

    def fail_unlink(_path, **_kwargs):
        raise PermissionError("File is locked.")

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    response = client.delete(f"/uploads/{directory.name}")
    assert response.status_code == 500
    assert response.headers["HX-Retarget"] == f"#job-state-{directory.name}"
    assert b"The file could not be deleted" in response.data
    assert (directory / "source.pdf").is_file()


def test_delete_upload_rejects_plan_outside_output_directory(
    gui_app, make_pdf, monkeypatch, tmp_path
):
    app, _adapter, home = gui_app
    client = app.test_client()
    source = make_pdf(["digital"])
    client.post(
        "/uploads", data={"document": (BytesIO(source.read_bytes()), "file.pdf")}
    )
    directory = next((home / "instance/_data/in").iterdir())
    outside_plan = tmp_path / "unrelated-plan.json"
    outside_plan.write_text("private plan", encoding="utf-8")
    monkeypatch.setattr("pdfwtf.gui.blueprint._plan_path", lambda _job_id: outside_plan)
    assert client.delete(f"/uploads/{directory.name}").status_code == 404
    assert outside_plan.read_text(encoding="utf-8") == "private plan"
    assert (directory / "source.pdf").is_file()


@pytest.mark.parametrize(
    "filename",
    ["gui.js", "vendor/pdfjs/pdf.min.mjs", "vendor/pdfjs/pdf.worker.min.mjs"],
)
def test_gui_scripts_use_javascript_mime_despite_system_mapping(
    gui_app, monkeypatch, filename
):
    app, _adapter, _home = gui_app
    monkeypatch.setattr(
        mimetypes, "guess_type", lambda *_args, **_kwargs: ("text/plain", None)
    )
    response = app.test_client().get(f"/assets/{filename}")
    assert response.status_code == 200
    assert response.mimetype == "text/javascript"
    response.close()


def test_standalone_analysis_persists_before_completion(configured_home, make_pdf):
    output_dir = configured_home / "custom-output"
    app = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "PDFWTF_GUI_OUTPUT_DIR": output_dir,
        }
    )
    adapter = app.config["PDFWTF_GUI_ANALYSIS_ADAPTER"]
    source = make_pdf(["digital"])
    client = app.test_client()
    client.post(
        "/uploads", data={"document": (BytesIO(source.read_bytes()), "file.pdf")}
    )
    job_id = next((configured_home / "instance/_data/in").iterdir()).name
    client.post(f"/jobs/{job_id}/analyze", data={"document_type": "unit"})
    adapter._executor.shutdown(wait=True)
    assert adapter.status(job_id).state == "complete"
    destination = output_dir / job_id / "source.analysis.json"
    assert json.loads(destination.read_text(encoding="utf-8")) == adapter.result(job_id)
    assert not destination.with_suffix(".json.tmp").exists()
    assert b"Download analysis" in client.get(f"/jobs/{job_id}/status").data
    assert client.get(f"/jobs/{job_id}/analysis").json == adapter.result(job_id)
    assert client.delete(f"/uploads/{job_id}").status_code == 200
    assert not destination.exists()


def test_analysis_write_failure_does_not_report_complete(
    tmp_path, make_pdf, monkeypatch
):
    adapter = DemoAnalysisAdapter(output_dir=tmp_path / "output")
    source = make_pdf(["digital"])
    job_id = "analysis-test-" + "a" * 24

    def fail_write(_document, path):
        path.write_text("partial", encoding="utf-8")
        raise OSError("Cannot write analysis.")

    monkeypatch.setattr("pdfwtf.gui.adapter.write_json_document", fail_write)
    adapter.start(job_id, source, "unit")
    adapter._executor.shutdown(wait=True)
    assert adapter.status(job_id).state == "failed"
    with pytest.raises(LookupError):
        adapter.result(job_id)
    assert not list((tmp_path / "output" / job_id).iterdir())


def test_analysis_download_checks_access_and_readiness(gui_app, make_pdf, monkeypatch):
    app, adapter, home = gui_app
    client = app.test_client()
    source = make_pdf(["digital"])
    client.post(
        "/uploads", data={"document": (BytesIO(source.read_bytes()), "file.pdf")}
    )
    job_id = next((home / "instance/_data/in").iterdir()).name
    assert client.get(f"/jobs/{job_id}/analysis").status_code == 404
    client.post(f"/jobs/{job_id}/analyze", data={"document_type": "unit"})
    app.config["PDFWTF_GUI_ACCESS_CHECK"] = (
        lambda action, _job_id: action != "download_analysis"
    )
    assert client.get(f"/jobs/{job_id}/analysis").status_code == 403
    app.config["PDFWTF_GUI_ACCESS_CHECK"] = lambda _action, _job_id: True

    def not_ready(_job_id):
        raise LookupError("Not ready.")

    monkeypatch.setattr(adapter, "result", not_ready)
    assert client.get(f"/jobs/{job_id}/analysis").status_code == 409


def test_listing_saved_analysis_actions_survive_restart(gui_app, make_pdf):
    app, adapter, home = gui_app
    client = app.test_client()
    source = make_pdf(["digital"])
    client.post(
        "/uploads", data={"document": (BytesIO(source.read_bytes()), "file.pdf")}
    )
    job_id = next((home / "instance/_data/in").iterdir()).name
    assert f"/jobs/{job_id}/review".encode() not in client.get("/").data
    client.post(f"/jobs/{job_id}/analyze", data={"document_type": "unit"})
    analysis = adapter.result(job_id)
    destination = home / "instance/_data/out" / job_id / "source.analysis.json"
    destination.parent.mkdir(parents=True)
    destination.write_text(json.dumps(analysis), encoding="utf-8")
    app.config["PDFWTF_GUI_ANALYSIS_ADAPTER"] = ImmediateAdapter()
    listing = client.get("/").data
    assert f"/jobs/{job_id}/review".encode() in listing
    assert f"/jobs/{job_id}/analysis".encode() not in listing
    assert b"Analysis is complete." not in listing
    assert client.get(f"/jobs/{job_id}/review").status_code == 200
    response = client.get(f"/jobs/{job_id}/document")
    assert response.status_code == 200
    response.close()
    assert client.get(f"/jobs/{job_id}/analysis").json == analysis
    client.post(f"/jobs/{job_id}/analyze", data={"document_type": "book"})
    assert (
        app.config["PDFWTF_GUI_ANALYSIS_ADAPTER"].result(job_id)["document_type"]
        == "book"
    )
    complete = client.get(f"/jobs/{job_id}/status", headers={"HX-Request": "true"})
    assert b"Analysis is complete." in complete.data
    assert b'hx-swap-oob="outerHTML"' in complete.data
    assert f'id="analysis-actions-{job_id}"'.encode() in complete.data


def test_demo_adapter_reruns_finished_job(tmp_path, make_pdf):
    adapter = DemoAnalysisAdapter(max_workers=1, output_dir=tmp_path / "output")
    source = make_pdf(["digital"])
    job_id = "rerun-test-" + "a" * 24
    try:
        for document_type in ("unit", "book"):
            adapter.start(job_id, source, document_type)
            adapter._executor.submit(lambda: None).result(timeout=10)
            assert adapter.status(job_id).state == "complete"
            assert adapter.result(job_id)["document_type"] == document_type
            destination = tmp_path / "output" / job_id / "source.analysis.json"
            assert (
                json.loads(destination.read_text(encoding="utf-8"))["document_type"]
                == document_type
            )
    finally:
        adapter.close()


@pytest.mark.parametrize("state", ["queued", "processing"])
def test_analyze_does_not_restart_active_job(gui_app, make_pdf, monkeypatch, state):
    app, adapter, home = gui_app
    client = app.test_client()
    source = make_pdf(["digital"])
    client.post(
        "/uploads", data={"document": (BytesIO(source.read_bytes()), "file.pdf")}
    )
    job_id = next((home / "instance/_data/in").iterdir()).name
    monkeypatch.setattr(adapter, "status", lambda _job_id: JobStatus(state))
    assert (
        client.post(
            f"/jobs/{job_id}/analyze", data={"document_type": "unit"}
        ).status_code
        == 200
    )
    assert adapter.starts == 0


def test_invalid_saved_analysis_does_not_show_actions(gui_app, make_pdf):
    app, _adapter, home = gui_app
    client = app.test_client()
    source = make_pdf(["digital"])
    client.post(
        "/uploads", data={"document": (BytesIO(source.read_bytes()), "file.pdf")}
    )
    job_id = next((home / "instance/_data/in").iterdir()).name
    destination = home / "instance/_data/out" / job_id / "source.analysis.json"
    destination.parent.mkdir(parents=True)
    destination.write_text('{"kind": "analysis"}', encoding="utf-8")
    assert f"/jobs/{job_id}/review".encode() not in client.get("/").data
    assert client.get(f"/jobs/{job_id}/review").status_code == 404


def test_file_link_and_document_type_persistence(gui_app, make_pdf):
    app, adapter, home = gui_app
    client = app.test_client()
    source = make_pdf(["digital"])
    client.post(
        "/uploads", data={"document": (BytesIO(source.read_bytes()), "original.pdf")}
    )
    directory = next((home / "instance/_data/in").iterdir())
    job_id = directory.name
    metadata_path = directory / "upload.json"
    assert json.loads(metadata_path.read_text(encoding="utf-8")) == {
        "filename": "original.pdf",
        "document_type": "auto",
    }
    listing = client.get("/").data
    assert f'href="/jobs/{job_id}/document" target="_blank"'.encode() in listing
    document = client.get(f"/jobs/{job_id}/document")
    assert document.status_code == 200
    assert document.mimetype == "application/pdf"
    document.close()
    for document_type in ("book", "journal-issue"):
        response = client.post(
            f"/jobs/{job_id}/analyze", data={"document_type": document_type}
        )
        assert response.status_code == 200
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        assert metadata["filename"] == "original.pdf"
        assert metadata["document_type"] == document_type
        assert adapter.result(job_id)["document_type"] == document_type
        assert (
            f'<option value="{document_type}" selected>'.encode()
            in client.get("/").data
        )
    app.config["PDFWTF_GUI_ANALYSIS_ADAPTER"] = ImmediateAdapter()
    assert b'<option value="journal-issue" selected>' in client.get("/").data
    client.post(f"/jobs/{job_id}/analyze", data={"document_type": "invalid"})
    assert (
        json.loads(metadata_path.read_text(encoding="utf-8"))["document_type"]
        == "journal-issue"
    )


def test_uploaded_files_sorted_oldest_first(gui_app, make_pdf):
    import os

    app, _adapter, home = gui_app
    source = make_pdf(["digital"])
    input_dir = home / "instance/_data/in"
    for job_id, filename, uploaded_at in (
        ("a" * 32, "last.pdf", 300),
        ("z" * 32, "first.pdf", 100),
        ("m" * 32, "middle.pdf", 200),
    ):
        directory = input_dir / job_id
        directory.mkdir()
        pdf = directory / "source.pdf"
        pdf.write_bytes(source.read_bytes())
        os.utime(pdf, (uploaded_at, uploaded_at))
        (directory / "upload.json").write_text(
            json.dumps({"filename": filename}), encoding="utf-8"
        )
    listing = app.test_client().get("/").data
    assert (
        listing.index(b"first.pdf")
        < listing.index(b"middle.pdf")
        < listing.index(b"last.pdf")
    )


@pytest.mark.parametrize(
    "requested,detected,failed,expected",
    [
        ("auto", "book", False, "book"),
        ("auto", "unknown", False, "auto"),
        ("book", "book", False, "book"),
        ("auto", "book", True, "auto"),
    ],
)
def test_detected_type_updates_only_successful_auto_analysis(
    tmp_path, make_pdf, monkeypatch, requested, detected, failed, expected
):
    source = make_pdf(["digital"])
    metadata_path = source.parent / "upload.json"
    metadata_path.write_text(
        json.dumps(
            {"filename": "original.pdf", "document_type": requested, "extra": 42}
        ),
        encoding="utf-8",
    )

    def analyze(_source, _document_type):
        if failed:
            raise RuntimeError("Analysis failed.")
        return {"document_type": detected}

    monkeypatch.setattr("pdfwtf.gui.adapter.analyze_container", analyze)
    adapter = DemoAnalysisAdapter(output_dir=tmp_path / "output")
    adapter.start("auto-detection-" + "a" * 24, source, requested)
    adapter._executor.shutdown(wait=True)
    assert adapter.status("auto-detection-" + "a" * 24).state == (
        "failed" if failed else "complete"
    )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["document_type"] == expected
    assert metadata["filename"] == "original.pdf"
    assert metadata["extra"] == 42
    assert not metadata_path.with_suffix(".json.tmp").exists()


def test_completion_refreshes_document_type_selector(
    configured_home, make_pdf, monkeypatch
):
    source = make_pdf(["digital"])
    analysis = analyze_container(source, "book")
    monkeypatch.setattr("pdfwtf.gui.adapter.analyze_container", lambda *_args: analysis)
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False})
    client = app.test_client()
    adapter = app.config["PDFWTF_GUI_ANALYSIS_ADAPTER"]
    client.post(
        "/uploads", data={"document": (BytesIO(source.read_bytes()), "file.pdf")}
    )
    job_id = next((configured_home / "instance/_data/in").iterdir()).name
    assert b'<option value="auto" selected>' in client.get("/").data
    client.post(f"/jobs/{job_id}/analyze", data={"document_type": "auto"})
    adapter._executor.shutdown(wait=True)
    response = client.get(f"/jobs/{job_id}/status", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert b"Analysis is complete." in response.data
    assert f'id="document-type-{job_id}"'.encode() in response.data
    assert b'name="document_type" required hx-swap-oob="outerHTML"' in response.data
    assert b'<option value="book" selected>' in response.data
    assert b'<option value="auto" selected>' not in response.data


@pytest.mark.parametrize("filename", ["openjpeg.wasm", "jbig2.wasm", "qcms_bg.wasm"])
def test_image_decoder_assets_are_served_as_wasm(gui_app, monkeypatch, filename):
    app, _adapter, _home = gui_app
    monkeypatch.setattr(
        mimetypes, "guess_type", lambda *_args, **_kwargs: ("text/plain", None)
    )
    response = app.test_client().get(f"/assets/vendor/pdfjs/wasm/{filename}")
    assert response.status_code == 200
    assert response.mimetype == "application/wasm"
    assert response.data.startswith(b"\x00asm")
    response.close()


def test_plan_delete_enforces_csrf(configured_home):
    app = create_app(
        {"TESTING": True, "PDFWTF_GUI_ANALYSIS_ADAPTER": ImmediateAdapter()}
    )
    assert app.test_client().delete(f"/jobs/{'a' * 20}/plan").status_code == 400
