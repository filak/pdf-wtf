"""Routes for the reusable PDF-WTF GUI blueprint."""

from __future__ import annotations

import json
from datetime import datetime, UTC
from pathlib import Path
import re
import secrets
from typing import Any, Callable

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_babel import get_locale, gettext as _
from werkzeug.datastructures import FileStorage
from werkzeug.exceptions import RequestEntityTooLarge

from pdfwtf.configuration import COMMAND_ENHANCE, COMMAND_EXPORT
from pdfwtf.container_partition import (
    PlanValidationError,
    source_identity,
    validate_plan,
)
from pdfwtf.diagnostics import ANALYSIS_KIND, format_page_ranges
from pdfwtf.gui.adapter import (
    LLM_INCOMPLETE,
    AnalyzeAdapter,
    PartitionAdapter,
    ExportAdapter,
    PrepareAdapter,
    document_with_pdf_type,
)

_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{20,128}$")
_DOCUMENT_TYPES = (
    "auto",
    "unit",
    "journal-issue",
    "magazine-issue",
    "book",
    "proceedings",
)
_MAX_WARNING_DETAILS = 10
_LANGUAGES = re.compile(r"^[a-z_]{3,16}(\+[a-z_]{3,16}){0,7}$")
AccessCheck = Callable[[str, str | None], bool]
# Cache of active PDF hashes, keyed by path, size, and modification time.
_IDENTITY_CACHE: dict[tuple[str, int, int], str] = {}


def create_gui_blueprint() -> Blueprint:
    """Create the GUI blueprint without initializing host extensions."""
    blueprint = Blueprint(
        "pdfwtf_gui",
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/assets",
    )

    @blueprint.after_request
    def javascript_content_type(response: Response) -> Response:
        """Serve GUI browser assets independently of system MIME mappings."""
        if request.endpoint == "pdfwtf_gui.static" and request.path.endswith(
            (".js", ".mjs")
        ):
            response.mimetype = "text/javascript"
        elif request.endpoint == "pdfwtf_gui.static" and request.path.endswith(".wasm"):
            response.mimetype = "application/wasm"
        return response

    @blueprint.url_defaults
    def preserve_language(endpoint: str, values: dict[str, Any]) -> None:
        """Keep an explicit language choice in blueprint-local links."""
        if endpoint.startswith("pdfwtf_gui.") and "lang" not in values:
            language = request.args.get("lang")
            if language in {"en", "cs"}:
                values["lang"] = language

    @blueprint.get("/")
    def index() -> str:
        _authorize("view", None)
        return render_template(
            "pdfwtf_gui/input.html",
            uploads=_uploaded_files(),
            llm_tasks=_llm_task_choices(),
            prepare_enabled=_prepare_adapter_or_none() is not None,
            analyze_enabled=_analyze_adapter_or_none() is not None,
        )

    @blueprint.post("/uploads")
    def upload_document() -> tuple[str, int] | str | Response:
        _authorize("upload", None)
        upload = request.files.get("document")
        error = _upload_error(upload)
        if error:
            return render_template("pdfwtf_gui/_job_error.html", message=error), 400

        pdf_type = request.form.get("pdf_type", "born-digital")
        if pdf_type not in {"born-digital", "scanned"}:
            return (
                render_template(
                    "pdfwtf_gui/_job_error.html",
                    message=_("Select a valid PDF type."),
                ),
                400,
            )

        job_id = secrets.token_urlsafe(24)
        job_dir = _input_dir() / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        source = job_dir / "source.pdf"
        assert upload is not None
        try:
            upload.save(source)
            with source.open("rb") as stream:
                valid_pdf = stream.read(5) == b"%PDF-"
            if not valid_pdf:
                source.unlink(missing_ok=True)
                job_dir.rmdir()
                return (
                    render_template(
                        "pdfwtf_gui/_job_error.html",
                        message=_("Select a valid PDF document."),
                    ),
                    400,
                )
            (job_dir / "upload.json").write_text(
                json.dumps(
                    {
                        "filename": upload.filename,
                        "created": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                        "document_type": "auto",
                        "pdf_type": pdf_type,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception:
            source.unlink(missing_ok=True)
            (job_dir / "upload.json").unlink(missing_ok=True)
            job_dir.rmdir()
            current_app.logger.exception("Cannot save a GUI upload")
            return (
                render_template(
                    "pdfwtf_gui/_job_error.html",
                    message=_("Upload could not be saved. Try again."),
                ),
                500,
            )
        if request.headers.get("HX-Request") != "true":
            return redirect(url_for("pdfwtf_gui.index"))
        return render_template(
            "pdfwtf_gui/_uploads.html",
            uploads=_uploaded_files(),
            prepare_enabled=_prepare_adapter_or_none() is not None,
            analyze_enabled=_analyze_adapter_or_none() is not None,
        )

    @blueprint.delete("/uploads/<job_id>")
    def delete_upload(job_id: str) -> Response:
        _valid_job_id(job_id)
        _authorize("delete", job_id)
        if (
            _export_active(job_id)
            or _prepare_active(job_id)
            or _analysis_active(job_id)
        ):
            return _delete_error(
                job_id, _("Wait for the active operation to finish."), 409
            )
        try:
            status = _adapter().status(job_id)
        except KeyError:
            status = None
        if status is not None and status.state in {"queued", "processing"}:
            return _delete_error(
                job_id,
                _("Wait for partitioning to finish before deleting the file."),
                409,
            )
        source = _input_dir() / job_id / "source.pdf"
        prepared = source.parent / "prepared.pdf"
        metadata = source.parent / "upload.json"
        plan = _plan_path(job_id)
        partition = plan.parent / "source.partition.json"
        archive = plan.parent / "export.zip"
        report = plan.parent / "enhance.json"
        analysis = plan.parent / "analysis.json"
        paths = (
            (source, _input_dir()),
            (prepared, _input_dir()),
            (metadata, _input_dir()),
            (plan, _output_dir()),
            (partition, _output_dir()),
            (archive, _output_dir()),
            (report, _output_dir()),
            (analysis, _output_dir()),
        )
        if any(not path.resolve().is_relative_to(root) for path, root in paths):
            abort(404)
        try:
            # Remove only the files owned by this upload, never an entire tree.
            archive.unlink(missing_ok=True)
            report.unlink(missing_ok=True)
            analysis.unlink(missing_ok=True)
            partition.unlink(missing_ok=True)
            prepared.unlink(missing_ok=True)
            plan.unlink(missing_ok=True)
            metadata.unlink(missing_ok=True)
            source.unlink()
            for directory in {source.parent, plan.parent}:
                if directory.is_dir() and not any(directory.iterdir()):
                    directory.rmdir()
        except OSError:
            current_app.logger.exception("Cannot delete a GUI upload")
            return _delete_error(
                job_id, _("The file could not be deleted. Try again."), 500
            )
        return Response("", status=200)

    @blueprint.post("/jobs/<job_id>/partition")
    def start_job(job_id: str) -> tuple[str, int] | str:
        _valid_job_id(job_id)
        _authorize("start", job_id)
        if _export_active(job_id) or _prepare_active(job_id):
            return (
                render_template(
                    "pdfwtf_gui/_job_error.html",
                    message=_("Wait for the active operation to finish."),
                ),
                409,
            )
        source = _input_dir() / job_id / "source.pdf"
        if not source.is_file() or not source.resolve().is_relative_to(_input_dir()):
            abort(404)
        document_type = request.form.get("document_type", "")
        if document_type not in _DOCUMENT_TYPES:
            return (
                render_template(
                    "pdfwtf_gui/_job_error.html",
                    message=_("Select a valid document type."),
                ),
                400,
            )
        try:
            status = _adapter().status(job_id)
        except KeyError:
            status = None
        if status is None or status.state not in {"queued", "processing"}:
            try:
                _save_document_type(source, document_type)
                # Partition the active PDF: the prepared PDF when it exists.
                _adapter().start(job_id, _document_path(job_id), document_type)
            except Exception:
                current_app.logger.exception("Cannot start a GUI partitioning job")
                return (
                    render_template(
                        "pdfwtf_gui/_job_error.html",
                        message=_("Partitioning could not start. Try again."),
                    ),
                    500,
                )
        return render_template("pdfwtf_gui/_job_status.html", job_id=job_id)

    @blueprint.get("/jobs/<job_id>/status")
    def job_status(job_id: str) -> str:
        _valid_job_id(job_id)
        _authorize("status", job_id)
        try:
            status = _adapter().status(job_id)
        except KeyError:
            abort(404)
        if status.state == "failed":
            return render_template(
                "pdfwtf_gui/_job_error.html",
                message=_(
                    "Partitioning failed. Upload the document again or contact support."
                ),
            )
        if status.state == "complete":
            return render_template(
                "pdfwtf_gui/_job_complete.html",
                job_id=job_id,
                demo=status.demo,
                update_actions=request.headers.get("HX-Request") == "true",
                has_plan=_has_plan(job_id),
                document_type=_upload_metadata(
                    _input_dir() / job_id / "source.pdf"
                ).get("document_type", "auto"),
            )
        return render_template(
            "pdfwtf_gui/_job_status.html", job_id=job_id, state=status.state
        )

    @blueprint.get("/jobs/<job_id>/partition.json")
    def download_partition(job_id: str) -> Response:
        _valid_job_id(job_id)
        _authorize("download_partition", job_id)
        try:
            partition = _partition_result(job_id)
        except KeyError:
            abort(404)
        except LookupError:
            abort(409)
        response = jsonify(partition)
        disposition = "inline" if request.args.get("inline") == "1" else "attachment"
        response.headers["Content-Disposition"] = (
            f'{disposition}; filename="source.partition.json"'
        )
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @blueprint.get("/jobs/<job_id>/review")
    def review(job_id: str) -> str:
        _valid_job_id(job_id)
        _authorize("review", job_id)
        try:
            partition = _partition_result(job_id)
        except KeyError:
            abort(404)
        except LookupError:
            return redirect(url_for("pdfwtf_gui.index"))
        plan = None
        destination = _plan_path(job_id)
        if not destination.resolve().is_relative_to(_output_dir()):
            abort(404)
        # A plan for another PDF is stale. Review then starts from the partition.
        if destination.is_file() and _has_plan(job_id):
            try:
                plan = json.loads(destination.read_text(encoding="utf-8"))
                validate_plan(plan, _document_path(job_id))
            except (OSError, ValueError, PlanValidationError):
                abort(400, description="The saved plan is not valid.")
        source_filename = _upload_metadata(_document_path(job_id)).get(
            "filename", "source.pdf"
        )
        if not isinstance(source_filename, str):
            source_filename = "source.pdf"
        return render_template(
            "pdfwtf_gui/review.html",
            job_id=job_id,
            partition=partition,
            plan=plan,
            partition_json=json.dumps(partition, ensure_ascii=False),
            unit_types=(
                "preface",
                "editorial",
                "table-of-contents",
                "programme",
                "article",
                "chapter",
                "abstract",
                "poster",
                "book-review",
                "full-page-advertisement",
                "cover",
                "unknown",
            ),
            unit_type_labels={
                "preface": _("Preface"),
                "editorial": _("Editorial"),
                "table-of-contents": _("Table of contents"),
                "programme": _("Programme"),
                "article": _("Article"),
                "chapter": _("Chapter"),
                "abstract": _("Abstract"),
                "poster": _("Poster"),
                "book-review": _("Book review"),
                "full-page-advertisement": _("Full-page advertisement"),
                "cover": _("Cover"),
                "unknown": _("Unknown"),
            },
            llm_tasks=_llm_task_choices(),
            source_filename=source_filename,
        )

    @blueprint.get("/jobs/<job_id>/document")
    def document(job_id: str) -> Response:
        _valid_job_id(job_id)
        _authorize("document", job_id)
        variant = request.args.get("variant", "active")
        if variant not in {"active", "source", "prepared"}:
            abort(404)
        source = (
            _document_path(job_id)
            if variant == "active"
            else _input_dir() / job_id / f"{variant}.pdf"
        )
        if not source.is_file() or not source.resolve().is_relative_to(_input_dir()):
            abort(404)
        response = send_file(
            source,
            mimetype="application/pdf",
            as_attachment=request.args.get("download") == "1",
            conditional=True,
            download_name=(
                "document.pdf" if variant == "active" else f"{source.stem}.pdf"
            ),
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @blueprint.post("/jobs/<job_id>/export")
    def start_export(job_id: str) -> tuple[Response, int]:
        _valid_job_id(job_id)
        _authorize("export", job_id)
        source = _document_path(job_id)
        plan_path = _plan_path(job_id)
        if (
            not source.is_file()
            or not source.resolve().is_relative_to(_input_dir())
            or not plan_path.resolve().is_relative_to(_output_dir())
        ):
            abort(404)
        if _prepare_active(job_id):
            return jsonify(error=_("Wait for the active operation to finish.")), 409
        options = request.get_json(silent=True)
        if not isinstance(options, dict):
            return jsonify(error=_("Select valid export options.")), 400
        llm_tasks = options.pop("llm_tasks", [])
        if set(options) - {
            "get_html",
            "get_meta",
            "get_img",
            "include_source",
            "debug",
        } or any(not isinstance(value, bool) for value in options.values()):
            return jsonify(error=_("Select valid export options.")), 400
        if not isinstance(llm_tasks, list) or any(
            not isinstance(name, str) for name in llm_tasks
        ):
            return jsonify(error=_("Select valid LLM tasks.")), 400
        if llm_tasks:
            options["llm_tasks"] = tuple(llm_tasks)
        # Without a saved plan, export the whole active PDF as one unit.
        plan = None
        if plan_path.is_file():
            try:
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                if not isinstance(plan, dict):
                    raise ValueError("Invalid plan.")
            except (OSError, ValueError):
                return jsonify(error=_("The saved plan could not be read.")), 400
            try:
                validate_plan(plan, source)
            except PlanValidationError:
                return (
                    jsonify(
                        error=_(
                            "The saved plan does not match the active PDF. "
                            "Partition the document again."
                        )
                    ),
                    409,
                )
        adapter = _export_adapter()
        try:
            partition_status = _adapter().status(job_id)
        except KeyError:
            partition_status = None
        if partition_status is not None and partition_status.state in {
            "queued",
            "processing",
        }:
            return jsonify(error=_("Wait for the active operation to finish.")), 409
        try:
            adapter.start_export(job_id, source, plan, **options)
        except LookupError:
            return jsonify(error=_("Wait for the active operation to finish.")), 409
        except ValueError as error:
            current_app.logger.warning("GUI export rejected: %s", error)
            return jsonify(error=_("Select valid LLM tasks.")), 400
        except Exception:
            current_app.logger.exception("Cannot start GUI export")
            return jsonify(error=_("Export could not start. Try again.")), 500
        return (
            jsonify(status_url=url_for("pdfwtf_gui.export_status", job_id=job_id)),
            202,
        )

    @blueprint.get("/jobs/<job_id>/export/status")
    def export_status(job_id: str) -> Response:
        _valid_job_id(job_id)
        _authorize("export_status", job_id)
        source = _input_dir() / job_id / "source.pdf"
        if not source.is_file() or not source.resolve().is_relative_to(_input_dir()):
            abort(404)
        try:
            status = _export_adapter().export_status(job_id)
        except KeyError:
            abort(404)
        payload = {"state": status.state}
        if status.state == "complete":
            payload["download_url"] = url_for(
                "pdfwtf_gui.download_export", job_id=job_id
            )
            if status.message == LLM_INCOMPLETE:
                payload["warning"] = _(
                    "Export finished, but LLM processing is incomplete. "
                    "See manifest.json."
                )
                details = list(status.details[:_MAX_WARNING_DETAILS])
                hidden = len(status.details) - len(details)
                if hidden > 0:
                    details.append(
                        _("%(count)s more errors. See manifest.json.", count=hidden)
                    )
                payload["warning_details"] = details
        elif status.state == "failed":
            payload["error"] = _("Export failed. Check the host logs.")
        response = jsonify(payload)
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @blueprint.get("/jobs/<job_id>/export/download")
    def download_export(job_id: str) -> Response:
        _valid_job_id(job_id)
        _authorize("download_export", job_id)
        source = _input_dir() / job_id / "source.pdf"
        if not source.is_file() or not source.resolve().is_relative_to(_input_dir()):
            abort(404)
        try:
            archive = _export_adapter().export_path(job_id)
        except KeyError:
            abort(404)
        except LookupError:
            abort(409)
        if not archive.is_file() or not archive.resolve().is_relative_to(_output_dir()):
            abort(404)
        response = send_file(
            archive,
            mimetype="application/zip",
            as_attachment=True,
            download_name="export.zip",
        )
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @blueprint.post("/jobs/<job_id>/plan")
    def save_plan(job_id: str) -> tuple[Response, int] | Response:
        _valid_job_id(job_id)
        _authorize("save_plan", job_id)
        if _export_active(job_id):
            return jsonify(error=_("Wait for the active operation to finish.")), 409
        plan = request.get_json(silent=True)
        if not isinstance(plan, dict):
            return jsonify(error=_("The plan must be a JSON object.")), 400
        try:
            source = _document_path(job_id)
            validate_plan(plan, source)
        except KeyError:
            abort(404)
        except PlanValidationError:
            return jsonify(error=_("The plan is not valid.")), 400

        destination = _plan_path(job_id)
        if not destination.resolve().is_relative_to(_output_dir()):
            abort(404)
        timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        created = timestamp
        if destination.is_file():
            try:
                existing = json.loads(destination.read_text(encoding="utf-8"))
                if isinstance(existing, dict) and isinstance(
                    existing.get("created"), str
                ):
                    created = existing["created"]
            except (OSError, ValueError):
                pass
        plan = document_with_pdf_type(plan, source)
        plan["created"] = created
        plan["updated"] = timestamp
        destination.parent.mkdir(parents=True, exist_ok=True)
        staged = destination.with_suffix(".json.tmp")
        staged.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        staged.replace(destination)
        return jsonify(
            message=_("The approved plan was saved."),
            download_url=url_for("pdfwtf_gui.download_plan", job_id=job_id),
        )

    @blueprint.delete("/jobs/<job_id>/plan")
    def delete_plan(job_id: str) -> tuple[Response, int] | Response:
        _valid_job_id(job_id)
        _authorize("delete_plan", job_id)
        if _export_active(job_id):
            return jsonify(error=_("Wait for the active operation to finish.")), 409
        try:
            _document_path(job_id)
        except KeyError:
            abort(404)
        destination = _plan_path(job_id)
        if not destination.resolve().is_relative_to(_output_dir()):
            abort(404)
        destination.unlink(missing_ok=True)
        return jsonify(message=_("The plan was deleted."))

    @blueprint.get("/jobs/<job_id>/plan")
    def download_plan(job_id: str) -> Response:
        _valid_job_id(job_id)
        _authorize("download_plan", job_id)
        try:
            _document_path(job_id)
        except KeyError:
            abort(404)
        destination = _plan_path(job_id)
        if (
            not destination.resolve().is_relative_to(_output_dir())
            or not destination.is_file()
        ):
            abort(404)
        return send_file(
            destination,
            mimetype="application/json",
            as_attachment=request.args.get("inline") != "1",
            download_name="approved.plan.json",
        )

    @blueprint.post("/jobs/<job_id>/prepare")
    def start_prepare(job_id: str) -> tuple[str, int] | str:
        _valid_job_id(job_id)
        _authorize("prepare", job_id)
        adapter = _prepare_adapter_or_none()
        if adapter is None:
            abort(503, description="The host has not configured preparation.")
        pdf_type = request.form.get("pdf_type", "auto")
        languages = request.form.get("languages", "eng").strip() or "eng"
        if pdf_type not in {"auto", "born-digital", "scanned"} or not (
            _LANGUAGES.fullmatch(languages)
        ):
            return (
                render_template(
                    "pdfwtf_gui/_job_error.html",
                    message=_("Select valid preparation options."),
                ),
                400,
            )
        try:
            partition_status = _adapter().status(job_id)
        except KeyError:
            partition_status = None
        if (
            _export_active(job_id)
            or _prepare_active(job_id)
            or (
                partition_status is not None
                and partition_status.state in {"queued", "processing"}
            )
        ):
            return (
                render_template(
                    "pdfwtf_gui/_job_error.html",
                    message=_("Wait for the active operation to finish."),
                ),
                409,
            )
        source = _input_dir() / job_id / "source.pdf"
        options = {
            "pdf_type": pdf_type,
            "languages": languages,
            "remove_background_flag": (
                pdf_type != "born-digital"
                and request.form.get("remove_background") == "1"
            ),
        }
        try:
            # Preparation always starts from the original upload.
            adapter.start_prepare(
                job_id, source, source.parent / "prepared.pdf", options=options
            )
        except LookupError:
            return (
                render_template(
                    "pdfwtf_gui/_job_error.html",
                    message=_("Wait for the active operation to finish."),
                ),
                409,
            )
        except Exception:
            current_app.logger.exception("Cannot start GUI preparation")
            return (
                render_template(
                    "pdfwtf_gui/_job_error.html",
                    message=_("Preparation could not start. Try again."),
                ),
                500,
            )
        return render_template("pdfwtf_gui/_prepare_status.html", job_id=job_id)

    @blueprint.get("/jobs/<job_id>/prepare/status")
    def prepare_status(job_id: str) -> str | Response:
        _valid_job_id(job_id)
        _authorize("prepare_status", job_id)
        adapter = _prepare_adapter_or_none()
        if adapter is None:
            abort(404)
        try:
            status = adapter.prepare_status(job_id)
        except KeyError:
            abort(404)
        if status.state == "failed":
            return render_template(
                "pdfwtf_gui/_job_error.html",
                message=_("Preparation failed. Check the host logs."),
            )
        if status.state == "complete":
            response = Response(
                render_template("pdfwtf_gui/_prepare_complete.html", job_id=job_id)
            )
            # Reload the file list so the active PDF and actions update.
            response.headers["HX-Refresh"] = "true"
            return response
        return render_template(
            "pdfwtf_gui/_prepare_status.html", job_id=job_id, state=status.state
        )

    @blueprint.post("/jobs/<job_id>/analyze")
    def start_analysis(job_id: str) -> tuple[str, int] | str:
        _valid_job_id(job_id)
        _authorize("analyze", job_id)
        adapter = _analyze_adapter_or_none()
        if adapter is None:
            abort(503, description="The host has not configured analysis.")
        if _prepare_active(job_id):
            return (
                render_template(
                    "pdfwtf_gui/_job_error.html",
                    message=_("Wait for the active operation to finish."),
                ),
                409,
            )
        try:
            # Analyze the active PDF: the prepared PDF when it exists.
            adapter.start_analysis(job_id, _document_path(job_id))
        except LookupError:
            return (
                render_template(
                    "pdfwtf_gui/_job_error.html",
                    message=_("Wait for the active operation to finish."),
                ),
                409,
            )
        except Exception:
            current_app.logger.exception("Cannot start GUI analysis")
            return (
                render_template(
                    "pdfwtf_gui/_job_error.html",
                    message=_("Analysis could not start. Try again."),
                ),
                500,
            )
        return render_template("pdfwtf_gui/_analysis_status.html", job_id=job_id)

    @blueprint.get("/jobs/<job_id>/analyze/status")
    def analysis_status(job_id: str) -> str:
        _valid_job_id(job_id)
        _authorize("analyze_status", job_id)
        adapter = _analyze_adapter_or_none()
        if adapter is None:
            abort(404)
        try:
            status = adapter.analysis_status(job_id)
        except KeyError:
            abort(404)
        if status.state == "failed":
            return render_template(
                "pdfwtf_gui/_job_error.html",
                message=_("Analysis failed. Check the host logs."),
            )
        if status.state == "complete":
            return render_template(
                "pdfwtf_gui/_analysis_complete.html",
                job_id=job_id,
                has_analysis=_has_analysis(job_id),
                update_actions=True,
            )
        return render_template(
            "pdfwtf_gui/_analysis_status.html", job_id=job_id, state=status.state
        )

    @blueprint.get("/jobs/<job_id>/analysis/summary")
    def analysis_summary(job_id: str) -> str:
        _valid_job_id(job_id)
        _authorize("analysis_summary", job_id)
        try:
            report = _analysis_report(job_id)
        except KeyError:
            abort(404)
        return render_template(
            "pdfwtf_gui/_analysis_summary.html",
            job_id=job_id,
            report=report,
            summary=report["summary"],
            analyzed_pdf=(
                "prepared"
                if report["source"].get("filename") == "prepared.pdf"
                else "source"
            ),
            pages=format_page_ranges,
            labels=_analysis_labels(),
        )

    @blueprint.get("/jobs/<job_id>/analysis.json")
    def download_analysis_report(job_id: str) -> Response:
        _valid_job_id(job_id)
        _authorize("download_analysis_report", job_id)
        try:
            report = _analysis_report(job_id)
        except KeyError:
            abort(404)
        filename = _upload_metadata(_input_dir() / job_id / "source.pdf").get(
            "filename", "source.pdf"
        )
        if not isinstance(filename, str) or not filename:
            filename = "source.pdf"
        response = jsonify(report)
        # Werkzeug encodes non-ASCII filenames as RFC 2231 parameters.
        response.headers.set(
            "Content-Disposition",
            "attachment",
            filename=f"{Path(filename).stem or 'source'}.analysis.json",
        )
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @blueprint.app_errorhandler(RequestEntityTooLarge)
    def upload_too_large(_error: RequestEntityTooLarge) -> tuple[str, int]:
        return (
            render_template(
                "pdfwtf_gui/_job_error.html",
                message=_("The PDF is larger than the configured upload limit."),
            ),
            413,
        )

    return blueprint


def _adapter() -> PartitionAdapter:
    adapter = current_app.config.get("PDFWTF_GUI_PARTITION_ADAPTER")
    if adapter is None:
        raise RuntimeError("PDFWTF_GUI_PARTITION_ADAPTER is required.")
    return adapter


def _export_adapter() -> ExportAdapter:
    adapter = current_app.config.get("PDFWTF_GUI_EXPORT_ADAPTER")
    if adapter is None:
        abort(503, description="The host has not configured export processing.")
    return adapter


def _prepare_adapter_or_none() -> PrepareAdapter | None:
    return current_app.config.get("PDFWTF_GUI_PREPARE_ADAPTER")


def _llm_task_choices(command: str = COMMAND_EXPORT) -> list[dict[str, str]]:
    """Return localized LLM task choices that can run for one operation."""
    key = (
        "PDFWTF_GUI_PREPARE_ADAPTER"
        if command == COMMAND_ENHANCE
        else "PDFWTF_GUI_EXPORT_ADAPTER"
    )
    adapter = current_app.config.get(key)
    list_tasks = getattr(adapter, "llm_tasks", None)
    if not callable(list_tasks):
        return []
    try:
        tasks = list_tasks(command)
    except TypeError:
        # Older adapters list export tasks only.
        tasks = list_tasks() if command == COMMAND_EXPORT else []
    locale = get_locale()
    language = locale.language if locale is not None else "en"
    return [{"name": task.name, "label": task.label(language)} for task in tasks]


def _analyze_adapter_or_none() -> AnalyzeAdapter | None:
    return current_app.config.get("PDFWTF_GUI_ANALYZE_ADAPTER")


def _analysis_active(job_id: str) -> bool:
    adapter = _analyze_adapter_or_none()
    if adapter is None:
        return False
    try:
        return adapter.analysis_status(job_id).state in {"queued", "processing"}
    except KeyError:
        return False


def _analysis_report(job_id: str) -> dict[str, Any]:
    """Return the saved diagnostic report for the active PDF.

    Raise KeyError when the report is missing, invalid, or for another PDF.
    """
    destination = _output_dir() / job_id / "analysis.json"
    if not destination.resolve().is_relative_to(_output_dir()):
        raise KeyError("Unknown analysis.")
    try:
        report = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise KeyError("No saved analysis.") from None
    if (
        not isinstance(report, dict)
        or report.get("kind") != ANALYSIS_KIND
        or not isinstance(report.get("summary"), dict)
        or not isinstance(report.get("document"), dict)
    ):
        raise KeyError("Invalid saved analysis.")
    if not _matches_active_pdf(job_id, report):
        raise KeyError("The analysis does not match the active PDF.")
    return report


def _has_analysis(job_id: str) -> bool:
    try:
        _analysis_report(job_id)
    except KeyError:
        return False
    return True


def _analysis_labels() -> dict[str, dict[str, str]]:
    """Return localized labels for analysis enumerations."""
    return {
        "composition": {
            "born_digital": _("Born-digital"),
            "scanned": _("Scanned"),
            "mixed": _("Mixed content"),
            "blank": _("Blank"),
            "uncertain": _("Uncertain"),
            "heterogeneous": _("Different page types"),
        },
        "need": {
            "likely": _("OCR likely needed"),
            "unlikely": _("OCR unlikely needed"),
            "uncertain": _("OCR need uncertain"),
        },
        "action": {
            "run_ocr": _("Run OCR"),
            "review_ocr_quality": _("Review text quality"),
            "check_page_content": _("Check page content"),
            "review_blank_page": _("Review blank pages"),
        },
    }


def _prepare_active(job_id: str) -> bool:
    adapter = _prepare_adapter_or_none()
    if adapter is None:
        return False
    try:
        return adapter.prepare_status(job_id).state in {"queued", "processing"}
    except KeyError:
        return False


def _export_active(job_id: str) -> bool:
    adapter = current_app.config.get("PDFWTF_GUI_EXPORT_ADAPTER")
    if adapter is None:
        return False
    try:
        return adapter.export_status(job_id).state in {"queued", "processing"}
    except KeyError:
        return False


def _has_plan(job_id: str) -> bool:
    """Return whether a saved plan exists for the active PDF."""
    path = _plan_path(job_id)
    if not path.resolve().is_relative_to(_output_dir()) or not path.is_file():
        return False
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return _matches_active_pdf(job_id, plan)


def _configured_directory(name: str) -> Path:
    raw_path = current_app.config.get(name)
    if raw_path is None:
        raise RuntimeError(f"{name} is required.")
    return Path(raw_path).resolve()


def _input_dir() -> Path:
    return _configured_directory("PDFWTF_GUI_INPUT_DIR")


def _output_dir() -> Path:
    return _configured_directory("PDFWTF_GUI_OUTPUT_DIR")


def _plan_path(job_id: str) -> Path:
    return _output_dir() / job_id / "approved.plan.json"


def _authorize(action: str, job_id: str | None) -> None:
    callback: AccessCheck | None = current_app.config.get("PDFWTF_GUI_ACCESS_CHECK")
    if callback is not None and not callback(action, job_id):
        abort(403)


def _valid_job_id(job_id: str) -> None:
    if _JOB_ID.fullmatch(job_id) is None:
        abort(404)
    source = _input_dir() / job_id / "source.pdf"
    if not source.is_file() or not source.resolve().is_relative_to(_input_dir()):
        abort(404)


def _delete_error(job_id: str, message: str, status: int) -> Response:
    return Response(
        render_template("pdfwtf_gui/_job_error.html", message=message),
        status=status,
        headers={
            "HX-Retarget": f"#job-state-{job_id}",
            "HX-Reswap": "innerHTML",
        },
    )


def _partition_result(job_id: str) -> dict[str, Any]:
    try:
        partition = _adapter().result(job_id)
    except (KeyError, LookupError) as error:
        destination = _output_dir() / job_id / "source.partition.json"
        if not destination.resolve().is_relative_to(_output_dir()):
            raise KeyError("Unknown partition.") from None
        try:
            partition = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise error from None
        if (
            not isinstance(partition, dict)
            or partition.get("kind") != "partition"
            or not isinstance(partition.get("pages"), list)
            or not isinstance(partition.get("units"), list)
        ):
            raise KeyError("Invalid saved partition.") from None
    if not _matches_active_pdf(job_id, partition):
        # A partition of another PDF, for example the original after preparation.
        raise KeyError("The partition does not match the active PDF.")
    return partition


def _document_path(job_id: str) -> Path:
    """Return the active PDF: the prepared PDF when it exists, else the upload."""
    # Routes already validate the upload path and authorize the operation.
    directory = _input_dir() / job_id
    prepared = directory / "prepared.pdf"
    if prepared.is_file() and prepared.resolve().is_relative_to(_input_dir()):
        return prepared
    return directory / "source.pdf"


def _active_sha256(job_id: str) -> str:
    path = _document_path(job_id)
    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    digest = _IDENTITY_CACHE.get(key)
    if digest is None:
        digest = source_identity(path)["sha256"]
        _IDENTITY_CACHE[key] = digest
    return digest


def _matches_active_pdf(job_id: str, document: Any) -> bool:
    """Compare a partition or plan source identity with the active PDF."""
    source = document.get("source") if isinstance(document, dict) else None
    if not isinstance(source, dict):
        return False
    try:
        return source.get("sha256") == _active_sha256(job_id)
    except OSError:
        return False


def _save_document_type(source: Path, document_type: str) -> None:
    metadata_path = source.parent / "upload.json"
    metadata = _upload_metadata(source)
    metadata["document_type"] = document_type
    staged = metadata_path.with_suffix(".json.tmp")
    try:
        staged.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        staged.replace(metadata_path)
    finally:
        staged.unlink(missing_ok=True)


def _upload_metadata(source: Path) -> dict[str, Any]:
    metadata_path = source.parent / "upload.json"
    if not metadata_path.resolve().is_relative_to(_input_dir()):
        return {"filename": "source.pdf"}
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if isinstance(metadata, dict):
            return metadata
    except (OSError, ValueError):
        pass
    return {"filename": "source.pdf"}


def _uploaded_files() -> list[dict[str, Any]]:
    uploads = []
    callback: AccessCheck | None = current_app.config.get("PDFWTF_GUI_ACCESS_CHECK")
    for source in _input_dir().glob("*/source.pdf"):
        job_id = source.parent.name
        if _JOB_ID.fullmatch(job_id) is None or not source.is_file():
            continue
        if not source.resolve().is_relative_to(_input_dir()):
            continue
        if callback is not None and not callback("view", job_id):
            continue
        metadata = _upload_metadata(source)
        filename = metadata.get("filename", "source.pdf")
        if not isinstance(filename, str):
            filename = "source.pdf"
        document_type = metadata.get("document_type", "auto")
        if document_type not in _DOCUMENT_TYPES:
            document_type = "auto"
        try:
            _partition_result(job_id)
            has_partition = True
        except (KeyError, LookupError):
            has_partition = False
        prepared = source.parent / "prepared.pdf"
        uploads.append(
            {
                "job_id": job_id,
                "filename": filename,
                "pdf_type": metadata.get("pdf_type", "born-digital"),
                "active_pdf": "prepared" if prepared.is_file() else "source",
                "has_partition": has_partition,
                "has_analysis": _has_analysis(job_id),
                "has_plan": _has_plan(job_id),
                "document_type": document_type,
                "uploaded_at": source.stat().st_mtime_ns,
            }
        )
    uploads.sort(key=lambda upload: (upload["uploaded_at"], upload["job_id"]))
    return uploads


def _upload_error(upload: FileStorage | None) -> str | None:
    if upload is None or not upload.filename:
        return _("Select a PDF document.")
    if not upload.filename.lower().endswith(".pdf"):
        return _("The selected file must have a .pdf extension.")
    return None
