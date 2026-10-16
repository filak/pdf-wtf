"""Tests for the optional reusable PDF-WTF-GUI blueprint."""

from io import BytesIO
from datetime import datetime, timedelta, UTC
import json
import mimetypes
from pathlib import Path
import re
from typing import Any

import pytest

pytest.importorskip("flask")

from pdfwtf import __version__  # noqa: E402
from pdfwtf.container_partition import partition_container  # noqa: E402
from pdfwtf.gui.adapter import DemoPartitionAdapter, JobStatus  # noqa: E402
from pdfwtf.gui.app import create_app  # noqa: E402


def _squash(data: bytes) -> bytes:
    """Collapse runs of whitespace so assertions ignore template layout."""
    return re.sub(rb"\s+", b" ", data)


class ImmediateAdapter:
    """Run partition immediately to keep route tests deterministic."""

    def __init__(self) -> None:
        self.jobs: dict[str, tuple[Path, dict[str, Any]]] = {}
        self.starts = 0

    def start(self, job_id: str, source: Path, document_type: str) -> None:
        self.starts += 1
        self.jobs[job_id] = (source, partition_container(source, document_type))

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
            "PDFWTF_GUI_PARTITION_ADAPTER": adapter,
        }
    )
    return app, adapter, configured_home


def test_app_config_includes_package_version(gui_app):
    app, _adapter, _configured_home = gui_app
    assert app.config["PDFWTF_GUI_VERSION"] == __version__


def test_index_uses_namespaced_local_assets_and_language(gui_app):
    app, _adapter, _configured_home = gui_app
    response = app.test_client().get("/?lang=cs")
    assert response.status_code == 200
    assert b"/assets/vendor/bootstrap/bootstrap.min.css" in response.data
    assert b"/assets/vendor/htmx/htmx.min.js" in response.data
    assert b'name="lang"' in response.data
    assert b'href="https://github.com/filak/pdf-wtf"' in response.data
    assert b'class="bi bi-github"' in response.data
    assert "Vybrat soubor".encode() in response.data
    assert "Není vybrán žádný soubor".encode() in response.data
    assert b"/assets/upload.js" in response.data
    assert b'id="document-drop-zone"' in response.data
    assert "nebo sem přetáhněte PDF".encode() in response.data
    assert b'id="delete-confirmation"' in response.data
    title_end = response.data.index(b"</title>")
    body_start = response.data.index(b"<body")
    assert b"<div" not in response.data[:title_end]
    assert response.data.index(b'id="delete-confirmation"') > body_start
    assert response.data.index(b"/assets/confirmation.js") > body_start
    assert (
        response.data.index(b"/assets/vendor/bootstrap/bootstrap.bundle.min.js")
        > body_start
    )
    assert b"/assets/vendor/bootstrap/bootstrap.bundle.min.js" in response.data
    assert b"/assets/confirmation.js" in response.data
    assert "Potvrdit smazání".encode() in response.data
    assert "Zrušit".encode() in response.data
    english = app.test_client().get("/?lang=en")
    assert b"Choose file" in english.data
    assert b"or drop a PDF here" in english.data
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
    assert f"/jobs/{job_id}/partition".encode() in listing.data
    response = client.post(f"/jobs/{job_id}/partition", data={"document_type": "unit"})
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
    assert b'id="export-options"' in review.data
    assert re.search(rb'<button id="export-units"[^>]*\sdisabled>', review.data)
    assert b"Show partition" in review.data
    assert b"Show plan" in review.data

    document = client.get(f"/jobs/{job_id}/document")
    assert document.status_code == 200
    assert document.mimetype == "application/pdf"
    assert document.headers["Cache-Control"] == "private, no-store"

    partition = adapter.result(job_id)
    partition_download = client.get(f"/jobs/{job_id}/partition.json")
    assert partition_download.status_code == 200
    assert partition_download.json == partition
    assert "source.partition.json" in partition_download.headers["Content-Disposition"]
    assert partition_download.headers["Cache-Control"] == "private, no-store"
    partition_view = client.get(f"/jobs/{job_id}/partition.json?inline=1")
    assert partition_view.status_code == 200
    assert partition_view.json == partition
    assert partition_view.headers["Content-Disposition"].startswith("inline;")
    assert partition_download.headers["Content-Disposition"].startswith("attachment;")
    plan = {
        "schema_version": partition["schema_version"],
        "kind": "plan",
        "source": partition["source"],
        "document_type": partition["document_type"],
        "pages": [{**page, "selected": True} for page in partition["pages"]],
        "units": [
            {
                "id": unit["id"],
                "title": unit["title"],
                "type": unit["type"],
                "selected": True,
                "boundary_status": "confirmed",
                "input_pages": unit["input_pages"],
            }
            for unit in partition["units"]
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
    assert f"/jobs/{job_id}/export".encode() in reopened.data
    assert not re.search(rb'<button id="export-units"[^>]*\sdisabled>', reopened.data)
    embedded_plan = re.search(
        r'<script id="plan-data" type="application/json">(.*?)</script>',
        reopened.get_data(as_text=True),
        re.DOTALL,
    )
    assert embedded_plan is not None
    persisted_plan = json.loads(embedded_plan.group(1))
    assert {
        key: value
        for key, value in persisted_plan.items()
        if key not in {"created", "updated", "pdf_type"}
    } == plan
    assert persisted_plan["created"] == persisted_plan["updated"]
    assert datetime.fromisoformat(persisted_plan["created"]).tzinfo == UTC
    assert adapter.result(job_id) == partition
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
    assert downloaded.headers["Content-Disposition"].startswith("attachment;")
    plan_view = client.get(f"/jobs/{job_id}/plan?inline=1")
    assert plan_view.status_code == 200
    assert plan_view.json["kind"] == "plan"
    assert plan_view.headers["Content-Disposition"].startswith("inline;")
    assert (
        configured_home / "instance/_data/out" / job_id / "approved.plan.json"
    ).is_file()

    document.close()
    downloaded.close()
    plan_view.close()
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
    assert adapter.result(job_id) == partition
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
    for suffix in ("status", "review", "document", "plan", "partition.json"):
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
            "PDFWTF_GUI_PARTITION_ADAPTER": ImmediateAdapter(),
        }
    )
    assert app.config["PDFWTF_GUI_INPUT_DIR"] == (configured_home / "instance/_data/in")


def test_standalone_host_enforces_csrf(configured_home: Path):
    app = create_app(
        {
            "TESTING": True,
            "PDFWTF_GUI_PARTITION_ADAPTER": ImmediateAdapter(),
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
        .post("/jobs/" + "a" * 32 + "/partition", data={"document_type": "unit"})
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
    app.config["PDFWTF_GUI_PARTITION_ADAPTER"] = ImmediateAdapter()
    assert b"&lt;example&gt;.pdf" in client.get("/").data
    app.config["PDFWTF_GUI_ACCESS_CHECK"] = (
        lambda action, identifier: identifier is None
    )
    assert b"&lt;example&gt;.pdf" not in client.get("/").data
    assert (
        client.post(
            f"/jobs/{job_id}/partition", data={"document_type": "unit"}
        ).status_code
        == 403
    )


def test_invalid_upload_and_partition_do_not_start_jobs(gui_app, make_pdf):
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
            f"/jobs/{job_id}/partition", data={"document_type": "invalid"}
        ).status_code
        == 400
    )
    assert client.post("/jobs/missing/partition").status_code == 404
    assert not adapter.jobs
    for _ in range(2):
        assert (
            client.post(
                f"/jobs/{job_id}/partition", data={"document_type": "unit"}
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
            f"/jobs/{job_id}/partition", data={"document_type": "unit"}
        ).status_code
        == 404
    )
    assert not adapter.jobs


@pytest.mark.parametrize("state", ["queued", "processing"])
def test_delete_upload_blocks_active_partition(gui_app, make_pdf, monkeypatch, state):
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
    assert b"Wait for partition" in response.data
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


@pytest.mark.parametrize("pdf_type", ["born-digital", "scanned"])
def test_standalone_partition_persists_before_completion(
    configured_home, make_pdf, pdf_type
):
    output_dir = configured_home / "custom-output"
    app = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "PDFWTF_GUI_OUTPUT_DIR": output_dir,
        }
    )
    adapter = app.config["PDFWTF_GUI_PARTITION_ADAPTER"]
    source = make_pdf(["digital"])
    client = app.test_client()
    client.post(
        "/uploads",
        data={
            "document": (BytesIO(source.read_bytes()), "file.pdf"),
            "pdf_type": pdf_type,
        },
    )
    job_id = next((configured_home / "instance/_data/in").iterdir()).name
    client.post(f"/jobs/{job_id}/partition", data={"document_type": "unit"})
    adapter._executor.shutdown(wait=True)
    assert adapter.status(job_id).state == "complete"
    destination = output_dir / job_id / "source.partition.json"
    assert json.loads(destination.read_text(encoding="utf-8")) == adapter.result(job_id)
    persisted = json.loads(destination.read_text(encoding="utf-8"))
    assert persisted["pdf_type"] == pdf_type
    keys = list(persisted)
    assert keys[keys.index("document_type") + 1] == "pdf_type"
    plan = {
        **persisted,
        "kind": "plan",
        "pdf_type": "forged",
        "pages": [{**page, "selected": True} for page in persisted["pages"]],
        "units": [
            {**unit, "selected": True, "boundary_status": "confirmed"}
            for unit in persisted["units"]
        ],
    }
    assert client.post(f"/jobs/{job_id}/plan", json=plan).status_code == 200
    saved = json.loads(
        (output_dir / job_id / "approved.plan.json").read_text(encoding="utf-8")
    )
    assert saved["pdf_type"] == pdf_type
    keys = list(saved)
    assert keys[keys.index("document_type") + 1] == "pdf_type"
    assert not destination.with_suffix(".json.tmp").exists()
    assert b"Download partition" in client.get(f"/jobs/{job_id}/status").data
    assert client.get(f"/jobs/{job_id}/partition.json").json == adapter.result(job_id)
    assert client.delete(f"/uploads/{job_id}").status_code == 200
    assert not destination.exists()


def test_partition_write_failure_does_not_report_complete(
    tmp_path, make_pdf, monkeypatch, caplog
):
    adapter = DemoPartitionAdapter(output_dir=tmp_path / "output")
    source = make_pdf(["digital"])
    job_id = "partition-test-" + "a" * 24

    def fail_write(_document, path):
        path.write_text("partial", encoding="utf-8")
        raise OSError("private document payload")

    monkeypatch.setattr("pdfwtf.gui.adapter.write_json_document", fail_write)
    adapter.start(job_id, source, "unit")
    adapter._executor.shutdown(wait=True)
    assert adapter.status(job_id).state == "failed"
    with pytest.raises(LookupError):
        adapter.result(job_id)
    assert not list((tmp_path / "output" / job_id).iterdir())
    records = [
        record for record in caplog.records if record.name == "pdfwtf.gui.adapter"
    ]
    assert len(records) == 1
    assert records[0].levelname == "ERROR"
    assert records[0].correlation_id == job_id
    assert re.fullmatch(
        r"GUI partitioning failed \(OSError, at=test_gui\.py:\d+\)\.",
        records[0].getMessage(),
    )
    assert records[0].exc_info is None
    assert "private document payload" not in caplog.text


def test_partition_download_checks_access_and_readiness(gui_app, make_pdf, monkeypatch):
    app, adapter, home = gui_app
    client = app.test_client()
    source = make_pdf(["digital"])
    client.post(
        "/uploads", data={"document": (BytesIO(source.read_bytes()), "file.pdf")}
    )
    job_id = next((home / "instance/_data/in").iterdir()).name
    assert client.get(f"/jobs/{job_id}/partition.json").status_code == 404
    client.post(f"/jobs/{job_id}/partition", data={"document_type": "unit"})
    app.config["PDFWTF_GUI_ACCESS_CHECK"] = (
        lambda action, _job_id: action != "download_partition"
    )
    assert client.get(f"/jobs/{job_id}/partition.json").status_code == 403
    app.config["PDFWTF_GUI_ACCESS_CHECK"] = lambda _action, _job_id: True

    def not_ready(_job_id):
        raise LookupError("Not ready.")

    monkeypatch.setattr(adapter, "result", not_ready)
    assert client.get(f"/jobs/{job_id}/partition.json").status_code == 409


def test_listing_saved_partition_actions_survive_restart(gui_app, make_pdf):
    app, adapter, home = gui_app
    client = app.test_client()
    source = make_pdf(["digital"])
    client.post(
        "/uploads", data={"document": (BytesIO(source.read_bytes()), "file.pdf")}
    )
    job_id = next((home / "instance/_data/in").iterdir()).name
    assert f"/jobs/{job_id}/review".encode() not in client.get("/").data
    client.post(f"/jobs/{job_id}/partition", data={"document_type": "unit"})
    partition = adapter.result(job_id)
    destination = home / "instance/_data/out" / job_id / "source.partition.json"
    destination.parent.mkdir(parents=True)
    destination.write_text(json.dumps(partition), encoding="utf-8")
    app.config["PDFWTF_GUI_PARTITION_ADAPTER"] = ImmediateAdapter()
    listing = client.get("/").data
    assert f"/jobs/{job_id}/review".encode() in listing
    assert f"/jobs/{job_id}/partition.json".encode() not in listing
    assert b"Partition is complete." not in listing
    assert client.get(f"/jobs/{job_id}/review").status_code == 200
    response = client.get(f"/jobs/{job_id}/document")
    assert response.status_code == 200
    response.close()
    assert client.get(f"/jobs/{job_id}/partition.json").json == partition
    client.post(f"/jobs/{job_id}/partition", data={"document_type": "book"})
    assert (
        app.config["PDFWTF_GUI_PARTITION_ADAPTER"].result(job_id)["document_type"]
        == "book"
    )
    complete = client.get(f"/jobs/{job_id}/status", headers={"HX-Request": "true"})
    assert b"Partition is complete." in complete.data
    assert b'hx-swap-oob="outerHTML"' in complete.data
    assert f'id="partition-actions-{job_id}"'.encode() in complete.data


def test_demo_adapter_reruns_finished_job(tmp_path, make_pdf):
    adapter = DemoPartitionAdapter(max_workers=1, output_dir=tmp_path / "output")
    source = make_pdf(["digital"])
    job_id = "rerun-test-" + "a" * 24
    try:
        for document_type in ("unit", "book"):
            adapter.start(job_id, source, document_type)
            adapter._executor.submit(lambda: None).result(timeout=10)
            assert adapter.status(job_id).state == "complete"
            assert adapter.result(job_id)["document_type"] == document_type
            destination = tmp_path / "output" / job_id / "source.partition.json"
            assert (
                json.loads(destination.read_text(encoding="utf-8"))["document_type"]
                == document_type
            )
    finally:
        adapter.close()


@pytest.mark.parametrize("state", ["queued", "processing"])
def test_partition_does_not_restart_active_job(gui_app, make_pdf, monkeypatch, state):
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
            f"/jobs/{job_id}/partition", data={"document_type": "unit"}
        ).status_code
        == 200
    )
    assert adapter.starts == 0


def test_invalid_saved_partition_does_not_show_actions(gui_app, make_pdf):
    app, _adapter, home = gui_app
    client = app.test_client()
    source = make_pdf(["digital"])
    client.post(
        "/uploads", data={"document": (BytesIO(source.read_bytes()), "file.pdf")}
    )
    job_id = next((home / "instance/_data/in").iterdir()).name
    destination = home / "instance/_data/out" / job_id / "source.partition.json"
    destination.parent.mkdir(parents=True)
    destination.write_text('{"kind": "partition"}', encoding="utf-8")
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
    upload_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    created = upload_metadata.pop("created")
    assert created.endswith("Z")
    assert datetime.fromisoformat(created).tzinfo == UTC
    assert upload_metadata == {
        "filename": "original.pdf",
        "document_type": "auto",
        "pdf_type": "born-digital",
    }
    listing = client.get("/").data
    assert (
        f'href="/jobs/{job_id}/document?variant=source" target="_blank"'.encode()
        in _squash(listing)
    )
    document = client.get(f"/jobs/{job_id}/document")
    assert document.status_code == 200
    assert document.mimetype == "application/pdf"
    document.close()
    for document_type in ("book", "journal-issue"):
        response = client.post(
            f"/jobs/{job_id}/partition", data={"document_type": document_type}
        )
        assert response.status_code == 200
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        assert metadata["filename"] == "original.pdf"
        assert metadata["pdf_type"] == "born-digital"
        assert metadata["document_type"] == document_type
        assert metadata["created"] == created
        assert adapter.result(job_id)["document_type"] == document_type
        assert f'<option value="{document_type}" selected>'.encode() in _squash(
            client.get("/").data
        )
    app.config["PDFWTF_GUI_PARTITION_ADAPTER"] = ImmediateAdapter()
    assert b'<option value="journal-issue" selected>' in _squash(client.get("/").data)
    client.post(f"/jobs/{job_id}/partition", data={"document_type": "invalid"})
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
def test_detected_type_updates_only_successful_auto_partition(
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

    def partition(_source, _document_type):
        if failed:
            raise RuntimeError("Partition failed.")
        return {"document_type": detected}

    monkeypatch.setattr("pdfwtf.gui.adapter.partition_container", partition)
    adapter = DemoPartitionAdapter(output_dir=tmp_path / "output")
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
    partition = partition_container(source, "book")
    monkeypatch.setattr(
        "pdfwtf.gui.adapter.partition_container", lambda *_args: partition
    )
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False})
    client = app.test_client()
    adapter = app.config["PDFWTF_GUI_PARTITION_ADAPTER"]
    client.post(
        "/uploads", data={"document": (BytesIO(source.read_bytes()), "file.pdf")}
    )
    job_id = next((configured_home / "instance/_data/in").iterdir()).name
    assert b'<option value="auto" selected>' in client.get("/").data
    client.post(f"/jobs/{job_id}/partition", data={"document_type": "auto"})
    adapter._executor.shutdown(wait=True)
    response = client.get(f"/jobs/{job_id}/status", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert b"Partition is complete." in response.data
    assert f'id="document-type-{job_id}"'.encode() in response.data
    assert b'name="document_type" required hx-swap-oob="outerHTML"' in _squash(
        response.data
    )
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
        {"TESTING": True, "PDFWTF_GUI_PARTITION_ADAPTER": ImmediateAdapter()}
    )
    assert app.test_client().delete(f"/jobs/{'a' * 20}/plan").status_code == 400


@pytest.mark.parametrize("pdf_type", [None, "born-digital", "scanned", "invalid"])
def test_upload_pdf_type(gui_app, make_pdf, pdf_type):
    app, adapter, home = gui_app
    source = make_pdf(["digital"])
    data = {"document": (BytesIO(source.read_bytes()), "source.pdf")}
    if pdf_type is not None:
        data["pdf_type"] = pdf_type
    client = app.test_client()
    form = client.get("/").get_data(as_text=True)
    assert 'name="pdf_type"' in form
    assert '<option value="born-digital" selected>' in form
    assert '<option value="scanned">' in form
    response = client.post("/uploads", data=data)
    directories = list((home / "instance/_data/in").iterdir())
    assert not adapter.jobs
    if pdf_type == "invalid":
        assert response.status_code == 400
        assert not directories
    else:
        assert response.status_code == 302
        metadata = json.loads(
            (directories[0] / "upload.json").read_text(encoding="utf-8")
        )
        assert metadata["pdf_type"] == (pdf_type or "born-digital")
        listing = client.get("/").get_data(as_text=True)
        expected_icon = (
            "bi-file-earmark-break-fill"
            if pdf_type == "scanned"
            else "bi-file-pdf-fill"
        )
        other_icon = (
            "bi-file-pdf-fill"
            if pdf_type == "scanned"
            else "bi-file-earmark-break-fill"
        )
        assert expected_icon in listing
        assert other_icon not in listing


def _saved_export_job(gui_app, make_pdf):
    app, adapter, home = gui_app
    client = app.test_client()
    source = make_pdf(["digital", "digital"])
    client.post(
        "/uploads",
        data={"document": (BytesIO(source.read_bytes()), "issue.pdf")},
        content_type="multipart/form-data",
    )
    job_id = next((home / "instance/_data/in").iterdir()).name
    client.post(f"/jobs/{job_id}/partition", data={"document_type": "unit"})
    partition = adapter.result(job_id)
    plan = {
        "schema_version": partition["schema_version"],
        "kind": "plan",
        "source": partition["source"],
        "document_type": partition["document_type"],
        "pages": [
            {**page, "selected": page["input_page"] == 1, "unit_ids": ["reviewed"]}
            for page in partition["pages"]
        ],
        "units": [
            {
                "id": "reviewed",
                "title": "Reviewed title",
                "type": "article",
                "selected": True,
                "boundary_status": "confirmed",
                "input_pages": {"start": 1, "end": 2},
            }
        ],
    }
    assert client.post(f"/jobs/{job_id}/plan", json=plan).status_code == 200
    output = home / "instance/_data/out" / job_id
    (output / "source.partition.json").write_text(
        json.dumps(partition, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    persisted_plan = json.loads(
        (output / "approved.plan.json").read_text(encoding="utf-8")
    )
    return client, job_id, persisted_plan


def test_export_button_works_without_plan_and_modal_defaults_off(gui_app, make_pdf):
    client, job_id, _plan = _saved_export_job(gui_app, make_pdf)
    listing = client.get("/").get_data(as_text=True)
    assert f'data-export-url="/jobs/{job_id}/export"' in listing
    assert 'id="export-no-pdf-out"' in listing
    assert 'id="export-get-html"' in listing
    for control in (
        "export-no-pdf-out",
        "export-get-html",
        "export-get-meta",
        "export-include-source",
        "export-debug",
    ):
        tag = re.search(rf'<input[^>]*id="{control}"[^>]*>', listing).group()
        assert "checked" not in tag
        assert 'role="switch"' in tag
    assert "Export the units of the saved plan." in listing
    assert client.delete(f"/jobs/{job_id}/plan").status_code == 200
    # Partitioning is optional. Without a plan, export uses the whole PDF.
    listing = client.get("/").get_data(as_text=True)
    assert f'data-export-url="/jobs/{job_id}/export"' in listing
    assert "Export the whole PDF as one unit." in listing


@pytest.mark.parametrize("no_pdf_out", [False, True])
@pytest.mark.parametrize("get_html", [False, True])
@pytest.mark.parametrize("include_source", [False, True])
@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("get_meta", [False, True])
def test_saved_plan_export_downloads_selected_results(
    gui_app, make_pdf, no_pdf_out, get_html, include_source, debug, get_meta
):
    from zipfile import ZipFile
    import pymupdf

    app, _partition_adapter, home = gui_app
    client, job_id, saved_plan = _saved_export_job(gui_app, make_pdf)
    upload_path = home / "instance/_data/in" / job_id / "upload.json"
    upload = json.loads(upload_path.read_text(encoding="utf-8"))
    upload["extra"] = {"unicode": "Žluťoučký", "values": [1, False, None]}
    upload_path.write_text(json.dumps(upload, ensure_ascii=False), encoding="utf-8")
    exporter = DemoPartitionAdapter(output_dir=home / "instance/_data/out")
    app.config["PDFWTF_GUI_EXPORT_ADAPTER"] = exporter
    try:
        response = client.post(
            f"/jobs/{job_id}/export",
            json={
                "no_pdf_out": no_pdf_out,
                "get_html": get_html,
                "get_meta": get_meta,
                "include_source": include_source,
                "debug": debug,
            },
        )
        assert response.status_code == 202
        exporter._executor.shutdown(wait=True)
        status = client.get(response.json["status_url"])
        assert status.json["state"] == "complete"
        archive = client.get(status.json["download_url"])
        assert archive.status_code == 200
        assert archive.mimetype == "application/zip"
        assert archive.headers["Cache-Control"] == "private, no-store"
        with ZipFile(BytesIO(archive.data)) as bundle:
            names = bundle.namelist()
            assert ("source.pdf" in names) is include_source
            for filename in ("approved.plan.json", "source.partition.json"):
                assert (filename in names) is debug
                if debug:
                    saved_file = home / "instance/_data/out" / job_id / filename
                    assert bundle.read(filename) == saved_file.read_bytes()
            if include_source:
                original_path = home / "instance/_data/in" / job_id / "source.pdf"
                assert bundle.read("source.pdf") == original_path.read_bytes()
            assert all(not name.startswith("_units_source/") for name in names)
            assert "reviewed.pdf" not in names
            assert ("_units/reviewed.pdf" in names) is not no_pdf_out
            manifest = json.loads(bundle.read("_units/manifest.json"))
            upload_path = home / "instance/_data/in" / job_id / "upload.json"
            assert manifest["upload"] == json.loads(
                upload_path.read_text(encoding="utf-8")
            )
            keys = list(manifest)
            assert keys[keys.index("kind") + 1] == "upload"
            assert manifest["created"].endswith("Z")
            assert datetime.fromisoformat(manifest["created"]).tzinfo == UTC
            if no_pdf_out:
                assert "pdf" not in manifest["units"][0]
            else:
                assert manifest["units"][0]["pdf"] == "reviewed.pdf"
            if not no_pdf_out:
                with pymupdf.open(
                    stream=bundle.read("_units/reviewed.pdf"), filetype="pdf"
                ) as pdf:
                    assert pdf.page_count == 1
                    source = home / "instance/_data/in" / job_id / "source.pdf"
                    with pymupdf.open(source) as original:
                        assert original.page_count == 2
                        assert pdf[0].get_text() == original[0].get_text()
            assert ("_units/reviewed.html" in names) is get_html
            metadata_files = [name for name in names if name.endswith(".metadata.json")]
            assert bool(metadata_files) is get_meta
            if get_meta:
                assert manifest["units"][0]["metadata"] == "reviewed.metadata.json"
                metadata = json.loads(bundle.read("_units/reviewed.metadata.json"))
                assert metadata["unit"]["title"] == "Reviewed title"
            else:
                assert manifest["units"][0]["metadata"] is None
        plan_path = home / "instance/_data/out" / job_id / "approved.plan.json"
        assert json.loads(plan_path.read_text(encoding="utf-8")) == saved_plan
    finally:
        exporter.close()


@pytest.mark.parametrize(
    "options", [None, [], {"get_html": "true"}, {"include_source": 1}, {"other": True}]
)
def test_export_rejects_invalid_options(gui_app, make_pdf, options):
    client, job_id, _plan = _saved_export_job(gui_app, make_pdf)
    assert client.post(f"/jobs/{job_id}/export", json=options).status_code == 400


def test_export_requires_host_adapter(gui_app, make_pdf):
    client, job_id, _plan = _saved_export_job(gui_app, make_pdf)
    assert client.post(f"/jobs/{job_id}/export", json={}).status_code == 503


def test_export_access_checks_and_csrf(gui_app, make_pdf):
    app, _adapter, _home = gui_app
    client, job_id, _plan = _saved_export_job(gui_app, make_pdf)
    observed = []

    def deny(action, identifier):
        observed.append((action, identifier))
        return False

    app.config["PDFWTF_GUI_ACCESS_CHECK"] = deny
    assert client.post(f"/jobs/{job_id}/export", json={}).status_code == 403
    assert client.get(f"/jobs/{job_id}/export/status").status_code == 403
    assert client.get(f"/jobs/{job_id}/export/download").status_code == 403
    assert observed == [
        ("export", job_id),
        ("export_status", job_id),
        ("download_export", job_id),
    ]
    app.config["PDFWTF_GUI_ACCESS_CHECK"] = lambda _action, _job_id: True
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post(f"/jobs/{job_id}/export", json={}).status_code == 400


def test_active_export_blocks_duplicate_partition_and_delete(
    gui_app, make_pdf, monkeypatch
):
    from threading import Event

    app, _adapter, home = gui_app
    client, job_id, _plan = _saved_export_job(gui_app, make_pdf)
    exporter = DemoPartitionAdapter(output_dir=home / "instance/_data/out")
    app.config["PDFWTF_GUI_EXPORT_ADAPTER"] = exporter
    started = Event()
    release = Event()
    from pdfwtf.gui import adapter as adapter_module

    original = adapter_module.export_pdf

    def blocked(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(adapter_module, "export_pdf", blocked)
    try:
        assert client.post(f"/jobs/{job_id}/export", json={}).status_code == 202
        assert started.wait(5)
        assert client.post(f"/jobs/{job_id}/export", json={}).status_code == 409
        assert (
            client.post(
                f"/jobs/{job_id}/partition", data={"document_type": "unit"}
            ).status_code
            == 409
        )
        assert client.delete(f"/uploads/{job_id}").status_code == 409
        assert client.get(f"/jobs/{job_id}/export/download").status_code == 409
    finally:
        release.set()
        exporter._executor.shutdown(wait=True)
        exporter.close()
    assert client.delete(f"/uploads/{job_id}").status_code == 200
    assert not (home / "instance/_data/out" / job_id / "export.zip").exists()


def test_gui_export_writes_each_included_unit_pdf_from_source(gui_app, make_pdf):
    from zipfile import ZipFile
    import pymupdf

    app, _adapter, home = gui_app
    client, job_id, plan = _saved_export_job(gui_app, make_pdf)
    plan["pages"][1]["selected"] = True
    plan["units"].extend(
        [
            {
                **plan["units"][0],
                "id": "second",
                "input_pages": {"start": 2, "end": 2},
            },
            {
                **plan["units"][0],
                "id": "removed",
                "selected": False,
                "input_pages": {"start": 1, "end": 1},
            },
        ]
    )
    for page in plan["pages"]:
        page["unit_ids"] = [
            unit["id"]
            for unit in plan["units"]
            if unit["input_pages"]["start"]
            <= page["input_page"]
            <= unit["input_pages"]["end"]
        ]
    assert client.post(f"/jobs/{job_id}/plan", json=plan).status_code == 200
    exporter = DemoPartitionAdapter(output_dir=home / "instance/_data/out")
    app.config["PDFWTF_GUI_EXPORT_ADAPTER"] = exporter
    try:
        response = client.post(f"/jobs/{job_id}/export", json={})
        assert response.status_code == 202
        exporter._executor.shutdown(wait=True)
        status = client.get(response.json["status_url"])
        assert status.json["state"] == "complete"
        download = client.get(status.json["download_url"])
        source = home / "instance/_data/in" / job_id / "source.pdf"
        with (
            ZipFile(BytesIO(download.data)) as bundle,
            pymupdf.open(source) as original,
        ):
            assert {name for name in bundle.namelist() if name.endswith(".pdf")} == {
                "_units/reviewed.pdf",
                "_units/second.pdf",
            }
            for identifier, indices in (("reviewed", [0, 1]), ("second", [1])):
                with pymupdf.open(
                    stream=bundle.read(f"_units/{identifier}.pdf"),
                    filetype="pdf",
                ) as unit_pdf:
                    assert unit_pdf.page_count == len(indices)
                    assert [page.get_text() for page in unit_pdf] == [
                        original[index].get_text() for index in indices
                    ]
            manifest = json.loads(bundle.read("_units/manifest.json"))
            assert [unit["pdf"] for unit in manifest["units"]] == [
                "reviewed.pdf",
                "second.pdf",
            ]
    finally:
        exporter.close()


def test_plan_timestamps_are_server_owned_and_creation_is_preserved(
    gui_app, make_pdf, monkeypatch
):
    client, job_id, plan = _saved_export_job(gui_app, make_pdf)
    first_created = plan["created"]
    first_updated = plan["updated"]
    assert first_created == first_updated
    assert first_created.endswith("Z")
    assert datetime.fromisoformat(first_created).tzinfo == UTC
    next_time = datetime.fromisoformat(first_updated) + timedelta(seconds=1)

    class SaveClock:
        @staticmethod
        def now(timezone):
            assert timezone == UTC
            return next_time

    monkeypatch.setattr("pdfwtf.gui.blueprint.datetime", SaveClock)
    plan["created"] = "forged"
    plan["updated"] = "forged"
    plan["units"][0]["title"] = "Edited title"
    assert client.post(f"/jobs/{job_id}/plan", json=plan).status_code == 200
    saved = client.get(f"/jobs/{job_id}/plan").json
    assert saved["created"] == first_created
    assert saved["updated"] == next_time.isoformat().replace("+00:00", "Z")
    assert datetime.fromisoformat(saved["updated"]) >= datetime.fromisoformat(
        first_updated
    )
    assert saved["units"][0]["title"] == "Edited title"


def test_debug_export_fails_if_saved_partition_is_missing(gui_app, make_pdf):
    app, _adapter, home = gui_app
    client, job_id, _plan = _saved_export_job(gui_app, make_pdf)
    (home / "instance/_data/out" / job_id / "source.partition.json").unlink()
    exporter = DemoPartitionAdapter(output_dir=home / "instance/_data/out")
    app.config["PDFWTF_GUI_EXPORT_ADAPTER"] = exporter
    try:
        response = client.post(f"/jobs/{job_id}/export", json={"debug": True})
        assert response.status_code == 202
        exporter._executor.shutdown(wait=True)
        assert client.get(response.json["status_url"]).json["state"] == "failed"
        assert client.get(f"/jobs/{job_id}/export/download").status_code == 409
    finally:
        exporter.close()


@pytest.mark.parametrize(
    "method,endpoint",
    [("GET", "review"), ("GET", "plan"), ("DELETE", "plan"), ("POST", "plan")],
)
def test_plan_routes_reject_paths_outside_output(
    gui_app, make_pdf, tmp_path, monkeypatch, method, endpoint
):
    client, job_id, plan = _saved_export_job(gui_app, make_pdf)
    outside = tmp_path / "outside.plan.json"
    original = json.dumps(plan).encode("utf-8")
    outside.write_bytes(original)
    monkeypatch.setattr("pdfwtf.gui.blueprint._plan_path", lambda _job_id: outside)
    response = client.open(f"/jobs/{job_id}/{endpoint}", method=method, json=plan)
    assert response.status_code == 404
    assert outside.read_bytes() == original
    response.close()


@pytest.mark.parametrize("state", ["queued", "processing", "complete", "failed"])
def test_plan_mutation_is_blocked_only_while_export_is_active(gui_app, make_pdf, state):
    app, _adapter, home = gui_app
    client, job_id, plan = _saved_export_job(gui_app, make_pdf)

    class ExportStatusAdapter:
        def export_status(self, identifier):
            assert identifier == job_id
            return JobStatus(state)

    app.config["PDFWTF_GUI_EXPORT_ADAPTER"] = ExportStatusAdapter()
    destination = home / "instance/_data/out" / job_id / "approved.plan.json"
    original = destination.read_bytes()
    plan["units"][0]["title"] = "Changed during export"
    save = client.post(f"/jobs/{job_id}/plan", json=plan)
    delete = client.delete(f"/jobs/{job_id}/plan")
    if state in {"queued", "processing"}:
        assert save.status_code == delete.status_code == 409
        assert save.json["error"] == delete.json["error"]
        assert destination.read_bytes() == original
    else:
        assert save.status_code == delete.status_code == 200
        assert not destination.exists()


# LLM tasks in GUI export. Gemini calls are always mocked.


def _llm_export(gui_app, make_pdf, options, exporter=None):
    from pdfwtf.configuration import load_config

    app, _partition_adapter, home = gui_app
    client, job_id, _plan = _saved_export_job(gui_app, make_pdf)
    exporter = exporter or DemoPartitionAdapter(
        output_dir=home / "instance/_data/out", config=load_config()
    )
    app.config["PDFWTF_GUI_EXPORT_ADAPTER"] = exporter
    response = client.post(f"/jobs/{job_id}/export", json=options)
    exporter._executor.shutdown(wait=True)
    return client, job_id, exporter, response


def test_export_modal_lists_localized_llm_tasks_unselected(llm_home, gui_app):
    from pdfwtf.configuration import load_config

    app, _adapter, home = gui_app
    app.config["PDFWTF_GUI_EXPORT_ADAPTER"] = DemoPartitionAdapter(
        output_dir=home / "instance/_data/out", config=load_config()
    )
    client = app.test_client()
    listing = client.get("/").get_data(as_text=True)
    tag = re.search(r'<input[^>]*id="export-llm-clean_html"[^>]*>', listing).group()
    assert 'value="clean_html"' in tag
    assert "checked" not in tag
    assert "Clean HTML output" in listing
    assert "Vyčistit HTML výstup" in client.get("/?lang=cs").get_data(as_text=True)


def test_export_modal_hides_llm_tasks_without_key(llm_home, gui_app, monkeypatch):
    from pdfwtf.configuration import load_config

    monkeypatch.delenv("GEMINI_API_KEY")
    app, _adapter, home = gui_app
    app.config["PDFWTF_GUI_EXPORT_ADAPTER"] = DemoPartitionAdapter(
        output_dir=home / "instance/_data/out", config=load_config()
    )
    listing = app.test_client().get("/").get_data(as_text=True)
    assert "export-llm-tasks" not in listing


def test_standalone_host_does_not_copy_gemini_key(llm_home, gui_app):
    app, _adapter, _home = gui_app
    assert "GEMINI_API_KEY" not in app.config
    assert "test-dummy-key" not in repr(dict(app.config))


def test_gui_export_runs_selected_llm_task(llm_home, gui_app, make_pdf, fake_gemini):
    from zipfile import ZipFile

    client, _job_id, exporter, response = _llm_export(
        gui_app, make_pdf, {"get_html": True, "llm_tasks": ["clean_html"]}
    )
    try:
        assert response.status_code == 202
        status = client.get(response.json["status_url"]).json
        assert status["state"] == "complete"
        assert "warning" not in status
        archive = client.get(status["download_url"])
        with ZipFile(BytesIO(archive.data)) as bundle:
            names = bundle.namelist()
            assert "_units/reviewed.html" in names
            assert "_units/reviewed.clean.html" in names
            manifest = json.loads(bundle.read("_units/manifest.json"))
        assert manifest["llm"]["status"] == "complete"
        entry = manifest["units"][0]["llm_tasks"]["clean_html"]
        assert entry["output"] == "reviewed.clean.html"
        assert entry["usage"]["total_token_count"] == 15
        assert len(fake_gemini[0]) == 1
    finally:
        exporter.close()


def test_gui_export_reports_incomplete_llm_processing(
    llm_home, gui_app, make_pdf, fake_gemini
):
    from zipfile import ZipFile

    fake_gemini[1]["fail_units"] = {"reviewed"}
    client, _job_id, exporter, response = _llm_export(
        gui_app, make_pdf, {"get_html": True, "llm_tasks": ["clean_html"]}
    )
    try:
        status = client.get(response.json["status_url"]).json
        assert status["state"] == "complete"
        assert "LLM processing is incomplete" in status["warning"]
        assert status["warning_details"] == [
            "clean_html / reviewed: RuntimeError: Gemini API Error: simulated"
        ]
        archive = client.get(status["download_url"])
        with ZipFile(BytesIO(archive.data)) as bundle:
            names = bundle.namelist()
            manifest = json.loads(bundle.read("_units/manifest.json"))
        assert "_units/reviewed.html" in names
        assert "_units/reviewed.clean.html" not in names
        assert manifest["llm"]["failed_units"] == {"clean_html": ["reviewed"]}
    finally:
        exporter.close()


@pytest.mark.parametrize(
    "options",
    [
        {"get_html": False, "llm_tasks": ["clean_html"]},
        {"get_html": True, "llm_tasks": ["unknown"]},
        {"get_html": True, "llm_tasks": "clean_html"},
        {"get_html": True, "llm_tasks": [1]},
    ],
)
def test_gui_export_rejects_invalid_llm_tasks(
    llm_home, gui_app, make_pdf, fake_gemini, options
):
    client, job_id, exporter, response = _llm_export(gui_app, make_pdf, options)
    try:
        assert response.status_code == 400
        assert response.json["error"] == "Select valid LLM tasks."
        assert fake_gemini[0] == []
        status = client.get(f"/jobs/{job_id}/export/status")
        assert status.status_code == 404
    finally:
        exporter.close()


def test_gui_export_rejects_llm_tasks_without_key(
    llm_home, gui_app, make_pdf, fake_gemini, monkeypatch
):
    monkeypatch.delenv("GEMINI_API_KEY")
    _client, _job_id, exporter, response = _llm_export(
        gui_app, make_pdf, {"get_html": True, "llm_tasks": ["clean_html"]}
    )
    try:
        assert response.status_code == 400
        assert fake_gemini[0] == []
    finally:
        exporter.close()


def test_cli_and_gui_llm_results_match(llm_home, gui_app, make_pdf, fake_gemini):
    from zipfile import ZipFile

    from click.testing import CliRunner

    from pdfwtf import cli

    _app, _adapter, home = gui_app
    client, job_id, exporter, response = _llm_export(
        gui_app, make_pdf, {"get_html": True, "llm_tasks": ["clean_html"]}
    )
    try:
        status = client.get(response.json["status_url"]).json
        archive = client.get(status["download_url"])
        with ZipFile(BytesIO(archive.data)) as bundle:
            gui_manifest = json.loads(bundle.read("_units/manifest.json"))
            gui_html = bundle.read("_units/reviewed.clean.html")
    finally:
        exporter.close()
    source = home / "instance/_data/in" / job_id / "source.pdf"
    plan = home / "instance/_data/out" / job_id / "approved.plan.json"
    output = home / "cli-out"
    result = CliRunner().invoke(
        cli.main,
        [
            "export",
            str(source),
            "--outdir",
            str(output),
            "--plan",
            str(plan),
            "--get-html",
            "--llm-task",
            "clean_html",
        ],
    )
    assert result.exit_code == 0, result.output
    units = output / "_units_source"
    cli_manifest = json.loads((units / "manifest.json").read_text(encoding="utf-8"))
    assert cli_manifest["llm"] == gui_manifest["llm"]
    assert (
        cli_manifest["units"][0]["llm_tasks"] == gui_manifest["units"][0]["llm_tasks"]
    )
    assert (units / "reviewed.clean.html").read_bytes() == gui_html


def _upload(client, home, source, name="issue.pdf", pdf_type="born-digital"):
    uploads = home / "instance/_data/in"
    before = set(uploads.iterdir()) if uploads.is_dir() else set()
    client.post(
        "/uploads",
        data={"document": (BytesIO(source.read_bytes()), name), "pdf_type": pdf_type},
        content_type="multipart/form-data",
    )
    return (set(uploads.iterdir()) - before).pop().name


def test_export_without_partition_exports_whole_pdf(gui_app, make_pdf):
    from zipfile import ZipFile

    app, adapter, home = gui_app
    client = app.test_client()
    job_id = _upload(client, home, make_pdf(["digital", "digital"]))
    exporter = DemoPartitionAdapter(output_dir=home / "instance/_data/out")
    app.config["PDFWTF_GUI_EXPORT_ADAPTER"] = exporter
    try:
        response = client.post(
            f"/jobs/{job_id}/export", json={"get_html": True, "debug": True}
        )
        assert response.status_code == 202
        exporter._executor.shutdown(wait=True)
        status = client.get(response.json["status_url"])
        assert status.json["state"] == "complete"
        archive = client.get(status.json["download_url"])
        with ZipFile(BytesIO(archive.data)) as bundle:
            names = bundle.namelist()
            manifest = json.loads(bundle.read("_units/manifest.json"))
            plan = json.loads(bundle.read("approved.plan.json"))
        assert [unit["id"] for unit in manifest["units"]] == ["unit-001"]
        assert "_units/unit-001.html" in names
        assert "_units/unit-001.pdf" in names
        assert "source.partition.json" not in names
        assert plan["document_type"] == "unit"
        # A direct export does not save a plan or start partitioning.
        assert not (
            home / "instance/_data/out" / job_id / "approved.plan.json"
        ).exists()
        assert adapter.starts == 0
    finally:
        exporter.close()


@pytest.fixture
def scan_tools_mocked(monkeypatch):
    import shutil

    from pdfwtf import pipeline

    monkeypatch.setattr(pipeline, "get_unpaper_version", lambda: (False, "none"))
    monkeypatch.setattr(pipeline, "correct_images_orientation", lambda files: False)
    calls = []

    def ocr(input_pdf, output_pdf, images, **kwargs):
        calls.append(kwargs)
        shutil.copy2(input_pdf, output_pdf)

    monkeypatch.setattr(pipeline, "run_ocr", ocr)
    return calls


def test_preparation_failure_logs_locked_file_name(
    tmp_path, make_pdf, monkeypatch, caplog
):
    adapter = DemoPartitionAdapter(output_dir=tmp_path / "output")
    source = make_pdf(["digital"])
    job_id = "prepare-test-" + "a" * 24

    def locked(*_args, **_kwargs):
        raise PermissionError(13, "private document payload", "prepared.pdf")

    monkeypatch.setattr("pdfwtf.gui.adapter.enhance_pdf", locked)
    adapter.start_prepare(
        job_id, source, source.parent / "prepared.pdf", options={"pdf_type": "auto"}
    )
    adapter._executor.shutdown(wait=True)
    assert adapter.prepare_status(job_id).state == "failed"
    records = [
        record for record in caplog.records if record.name == "pdfwtf.gui.adapter"
    ]
    assert len(records) == 1
    message = records[0].getMessage()
    assert message.startswith(
        "GUI preparation failed (PermissionError, file=prepared.pdf, at=test_gui.py:"
    )
    assert records[0].exc_info is None
    assert "private document payload" not in caplog.text


def _prepare(app, client, home, job_id, **form):
    preparer = DemoPartitionAdapter(output_dir=home / "instance/_data/out")
    app.config["PDFWTF_GUI_PREPARE_ADAPTER"] = preparer
    response = client.post(f"/jobs/{job_id}/prepare", data=form)
    preparer._executor.shutdown(wait=True)
    return preparer, response


def test_preparation_changes_active_pdf_and_invalidates_plan(
    gui_app, make_pdf, scan_tools_mocked
):
    app, _adapter, home = gui_app
    client, job_id, _plan = _saved_export_job(gui_app, make_pdf)
    source = home / "instance/_data/in" / job_id / "source.pdf"
    original = source.read_bytes()
    output = home / "instance/_data/out" / job_id
    assert (output / "approved.plan.json").is_file()
    preparer, response = _prepare(
        app, client, home, job_id, pdf_type="scanned", languages="eng+ces"
    )
    try:
        assert response.status_code == 200
        assert f'id="prepare-state-{job_id}"' in response.get_data(as_text=True)
        status = client.get(f"/jobs/{job_id}/prepare/status")
        assert status.status_code == 200
        assert status.headers["HX-Refresh"] == "true"
        assert scan_tools_mocked[0]["lang"] == "eng+ces"
        prepared = source.parent / "prepared.pdf"
        assert prepared.is_file()
        assert prepared.read_bytes() != original
        assert source.read_bytes() == original
        assert (output / "enhance.json").is_file()
        # The plan and partition described the original PDF.
        assert not (output / "approved.plan.json").exists()
        assert not (output / "source.partition.json").exists()
        listing = client.get("/").get_data(as_text=True)
        assert "Active: prepared PDF" in listing
        assert f"/jobs/{job_id}/review" not in listing
        assert "Export the whole PDF as one unit." in listing
        # The file name opens the original. Delete follows it.
        assert f"/jobs/{job_id}/document?variant=source" in listing
        assert f"/jobs/{job_id}/document?variant=prepared" in listing
        assert ">Prepared PDF<" in listing
        assert ">Original<" not in listing
        assert "Download prepared PDF" not in listing
        assert listing.index(f"/uploads/{job_id}") < listing.index(
            f'id="active-pdf-{job_id}"'
        )
        # Close file responses so Windows can delete the files later.
        with client.get(f"/jobs/{job_id}/document") as active:
            assert active.data == prepared.read_bytes()
        with client.get(f"/jobs/{job_id}/document?variant=source") as first:
            assert first.data == original
        with client.get(
            f"/jobs/{job_id}/document?variant=prepared&download=1"
        ) as download:
            assert download.headers["Content-Disposition"].startswith("attachment")
        # Partitioning now uses the prepared PDF.
        client.post(f"/jobs/{job_id}/partition", data={"document_type": "unit"})
        assert _adapter.document_path(job_id) == prepared
        assert client.get(f"/jobs/{job_id}/review").status_code == 200
    finally:
        preparer.close()
    assert client.delete(f"/uploads/{job_id}").status_code == 200
    assert not prepared.exists()
    assert not (output / "enhance.json").exists()


def test_stale_plan_is_not_reused_after_active_pdf_changes(gui_app, make_pdf, tmp_path):
    app, _adapter, home = gui_app
    client, job_id, _plan = _saved_export_job(gui_app, make_pdf)
    # A host adapter replaced the active PDF but left the old plan.
    other = make_pdf(["digital"], "other.pdf")
    (home / "instance/_data/in" / job_id / "prepared.pdf").write_bytes(
        other.read_bytes()
    )
    exporter = DemoPartitionAdapter(output_dir=home / "instance/_data/out")
    app.config["PDFWTF_GUI_EXPORT_ADAPTER"] = exporter
    try:
        listing = client.get("/").get_data(as_text=True)
        assert "Export the units of the saved plan." not in listing
        assert f"/jobs/{job_id}/review" not in listing
        response = client.post(f"/jobs/{job_id}/export", json={})
        assert response.status_code == 409
        assert "does not match the active PDF" in response.json["error"]
        assert client.get(f"/jobs/{job_id}/partition.json").status_code == 404
    finally:
        exporter.close()


def test_preparation_controls_require_prepare_adapter(gui_app, make_pdf):
    app, _adapter, home = gui_app
    client = app.test_client()
    job_id = _upload(client, home, make_pdf(["scan"]), pdf_type="scanned")
    digital_id = _upload(client, home, make_pdf(["digital"], "digital.pdf"))
    listing = client.get("/").get_data(as_text=True)
    assert "prepare-form-" not in listing
    # Only a prepared PDF is marked. Unprepared rows use the original PDF.
    assert "Active:" not in listing
    assert client.post(f"/jobs/{job_id}/prepare").status_code == 503
    app.config["PDFWTF_GUI_PREPARE_ADAPTER"] = DemoPartitionAdapter(
        output_dir=home / "instance/_data/out"
    )
    try:
        listing = client.get("/").get_data(as_text=True)
        # Preparation is offered only for scanned uploads.
        assert f'id="prepare-form-{job_id}"' in listing
        assert f'id="prepare-form-{digital_id}"' not in listing
    finally:
        app.config["PDFWTF_GUI_PREPARE_ADAPTER"].close()


@pytest.mark.parametrize(
    "form", [{"pdf_type": "other"}, {"languages": "eng;rm"}, {"languages": "e"}]
)
def test_invalid_preparation_options_are_rejected(gui_app, make_pdf, form):
    app, _adapter, home = gui_app
    client = app.test_client()
    job_id = _upload(client, home, make_pdf(["digital"]))
    preparer, response = _prepare(app, client, home, job_id, **form)
    preparer.close()
    assert response.status_code == 400
    assert not (home / "instance/_data/in" / job_id / "prepared.pdf").exists()


def test_preparation_blocks_while_export_is_active(gui_app, make_pdf, monkeypatch):
    from threading import Event

    from pdfwtf.gui import adapter as adapter_module

    app, _adapter, home = gui_app
    client, job_id, _plan = _saved_export_job(gui_app, make_pdf)
    shared = DemoPartitionAdapter(output_dir=home / "instance/_data/out")
    app.config["PDFWTF_GUI_EXPORT_ADAPTER"] = shared
    app.config["PDFWTF_GUI_PREPARE_ADAPTER"] = shared
    started = Event()
    release = Event()
    original = adapter_module.export_pdf

    def blocked(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(adapter_module, "export_pdf", blocked)
    try:
        assert client.post(f"/jobs/{job_id}/export", json={}).status_code == 202
        assert started.wait(5)
        response = client.post(f"/jobs/{job_id}/prepare", data={"pdf_type": "auto"})
        assert response.status_code == 409
    finally:
        release.set()
        shared._executor.shutdown(wait=True)
        shared.close()
    assert not (home / "instance/_data/in" / job_id / "prepared.pdf").exists()


def test_adapter_rejects_incompatible_tasks_before_scheduling(llm_home, tmp_path):
    from pdfwtf.configuration import load_config

    adapter = DemoPartitionAdapter(output_dir=tmp_path / "out", config=load_config())
    source = tmp_path / "source.pdf"
    try:
        with pytest.raises(ValueError, match="does not support the 'enhance'"):
            adapter.start_prepare(
                "job",
                source,
                tmp_path / "prepared.pdf",
                options={},
                llm_tasks=("clean_html",),
            )
        with pytest.raises(ValueError, match="does not support the 'partition'"):
            adapter.start("job", source, "unit", llm_tasks=("clean_html",))
        with pytest.raises(ValueError, match="requires --get-html"):
            adapter.start_export("job", source, None, llm_tasks=("clean_html",))
        with pytest.raises(KeyError):
            adapter.prepare_status("job")
        assert [task.name for task in adapter.llm_tasks()] == ["clean_html"]
        assert adapter.llm_tasks("enhance") == []
        assert adapter.llm_tasks("partition") == []
    finally:
        adapter.close()


def _analyze(app, client, home, job_id):
    analyzer = DemoPartitionAdapter(output_dir=home / "instance/_data/out")
    app.config["PDFWTF_GUI_ANALYZE_ADAPTER"] = analyzer
    response = client.post(f"/jobs/{job_id}/analyze")
    analyzer._executor.shutdown(wait=True)
    return analyzer, response


def _analyze_button(listing, job_id):
    match = re.search(rf'<button id="analyze-button-{job_id}"[^>]*>', listing)
    assert match is not None
    return match.group()


def test_analyze_button_precedes_document_type_and_requires_adapter(gui_app, make_pdf):
    app, _adapter, home = gui_app
    client = app.test_client()
    job_id = _upload(client, home, make_pdf(["digital"]))
    listing = client.get("/").get_data(as_text=True)
    assert f'id="analyze-button-{job_id}"' not in listing
    assert 'id="analysis-modal"' not in listing
    assert client.post(f"/jobs/{job_id}/analyze").status_code == 503
    analyzer = DemoPartitionAdapter(output_dir=home / "instance/_data/out")
    app.config["PDFWTF_GUI_ANALYZE_ADAPTER"] = analyzer
    try:
        listing = client.get("/").get_data(as_text=True)
        button = _analyze_button(listing, job_id)
        assert "btn-info" in button
        assert f'hx-post="/jobs/{job_id}/analyze"' in button
        assert listing.index(f'id="analyze-button-{job_id}"') < listing.index(
            f'id="document-type-{job_id}"'
        )
        assert 'id="analysis-modal"' in listing
    finally:
        analyzer.close()


def test_analysis_runs_and_switches_button_to_report_modal(gui_app, make_pdf):
    app, _adapter, home = gui_app
    client = app.test_client()
    job_id = _upload(client, home, make_pdf(["digital", "scan"]), "Žluťoučký.pdf")
    source = home / "instance/_data/in" / job_id / "source.pdf"
    original = source.read_bytes()
    report_path = home / "instance/_data/out" / job_id / "analysis.json"
    analyzer, response = _analyze(app, client, home, job_id)
    try:
        assert response.status_code == 200
        assert f'id="analysis-state-{job_id}"' in response.get_data(as_text=True)
        status = client.get(f"/jobs/{job_id}/analyze/status").get_data(as_text=True)
        assert "Analysis is complete." in status
        oob = _analyze_button(status, job_id)
        assert "btn-outline-info" in oob
        assert 'hx-swap-oob="outerHTML"' in oob
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["kind"] == "pdf-analysis"
        assert report["source"]["filename"] == "source.pdf"
        assert source.read_bytes() == original

        listing = client.get("/").get_data(as_text=True)
        button = _analyze_button(listing, job_id)
        assert "btn-outline-info" in button
        assert 'data-bs-target="#analysis-modal"' in button
        assert f'hx-get="/jobs/{job_id}/analysis/summary"' in button

        summary = client.get(f"/jobs/{job_id}/analysis/summary")
        assert summary.status_code == 200
        text = summary.get_data(as_text=True)
        assert "Analyzed PDF: original PDF" in text
        assert "Different page types" in text
        assert "Run OCR" in text
        assert f'href="/jobs/{job_id}/analysis.json"' in text
        assert f'hx-post="/jobs/{job_id}/analyze"' in text
        assert 'name="csrf_token"' in text

        download = client.get(f"/jobs/{job_id}/analysis.json")
        assert download.status_code == 200
        assert download.json == report
        assert download.headers["Cache-Control"] == "private, no-store"
        disposition = download.headers["Content-Disposition"]
        assert disposition.startswith("attachment")
        assert ".analysis.json" in disposition

        # Run again replaces the report.
        report_path.write_text(
            json.dumps({**report, "created_at": "old"}), encoding="utf-8"
        )
        analyzer.close()
        analyzer, _rerun = _analyze(app, client, home, job_id)
        rerun = json.loads(report_path.read_text(encoding="utf-8"))
        assert rerun["created_at"] != "old"
    finally:
        analyzer.close()
    assert client.delete(f"/uploads/{job_id}").status_code == 200
    assert not report_path.exists()


def test_analysis_of_other_pdf_is_stale(gui_app, make_pdf):
    app, _adapter, home = gui_app
    client = app.test_client()
    job_id = _upload(client, home, make_pdf(["digital"]))
    analyzer, _response = _analyze(app, client, home, job_id)
    try:
        assert client.get(f"/jobs/{job_id}/analysis/summary").status_code == 200
        # The active PDF changes, for example after preparation by a host.
        (home / "instance/_data/in" / job_id / "prepared.pdf").write_bytes(
            make_pdf(["scan"], "other.pdf").read_bytes()
        )
        listing = client.get("/").get_data(as_text=True)
        assert "btn-info" in _analyze_button(listing, job_id)
        assert client.get(f"/jobs/{job_id}/analysis/summary").status_code == 404
        assert client.get(f"/jobs/{job_id}/analysis.json").status_code == 404
        analyzer.close()
        analyzer, _response = _analyze(app, client, home, job_id)
        summary = client.get(f"/jobs/{job_id}/analysis/summary")
        assert "Analyzed PDF: prepared PDF" in summary.get_data(as_text=True)
    finally:
        analyzer.close()


def test_legacy_analysis_file_is_not_a_report(gui_app, make_pdf):
    app, _adapter, home = gui_app
    client = app.test_client()
    job_id = _upload(client, home, make_pdf(["digital"]))
    output = home / "instance/_data/out" / job_id
    output.mkdir(parents=True)
    (output / "analysis.json").write_text('{"kind": "analysis"}', encoding="utf-8")
    analyzer = DemoPartitionAdapter(output_dir=home / "instance/_data/out")
    app.config["PDFWTF_GUI_ANALYZE_ADAPTER"] = analyzer
    try:
        listing = client.get("/").get_data(as_text=True)
        assert "btn-info" in _analyze_button(listing, job_id)
        assert client.get(f"/jobs/{job_id}/analysis/summary").status_code == 404
    finally:
        analyzer.close()


def test_failed_analysis_shows_error(gui_app):
    app, _adapter, home = gui_app
    client = app.test_client()
    client.post(
        "/uploads",
        data={"document": (BytesIO(b"%PDF-1.7 broken"), "broken.pdf")},
        content_type="multipart/form-data",
    )
    job_id = next((home / "instance/_data/in").iterdir()).name
    analyzer, _response = _analyze(app, client, home, job_id)
    try:
        status = client.get(f"/jobs/{job_id}/analyze/status").get_data(as_text=True)
        assert "Analysis failed. Check the host logs." in status
        assert not (home / "instance/_data/out" / job_id / "analysis.json").exists()
    finally:
        analyzer.close()


class _BusyPreparation:
    def prepare_status(self, job_id):
        return JobStatus("processing", demo=True)


def test_analysis_waits_for_preparation(gui_app, make_pdf):
    app, _adapter, home = gui_app
    client = app.test_client()
    job_id = _upload(client, home, make_pdf(["digital"]))
    app.config["PDFWTF_GUI_PREPARE_ADAPTER"] = _BusyPreparation()
    analyzer = DemoPartitionAdapter(output_dir=home / "instance/_data/out")
    app.config["PDFWTF_GUI_ANALYZE_ADAPTER"] = analyzer
    try:
        assert client.post(f"/jobs/{job_id}/analyze").status_code == 409
    finally:
        analyzer.close()


def test_adapter_rejects_llm_tasks_for_analysis(llm_home, tmp_path):
    from pdfwtf.configuration import load_config

    adapter = DemoPartitionAdapter(output_dir=tmp_path / "out", config=load_config())
    try:
        with pytest.raises(ValueError, match="does not support the 'analyze'"):
            adapter.start_analysis(
                "job", tmp_path / "source.pdf", llm_tasks=("clean_html",)
            )
        with pytest.raises(KeyError):
            adapter.analysis_status("job")
        assert adapter.llm_tasks("analyze") == []
    finally:
        adapter.close()
