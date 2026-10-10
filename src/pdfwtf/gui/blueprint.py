"""Routes for the reusable PDF-WTF GUI blueprint."""

from __future__ import annotations

import json
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
from flask_babel import gettext as _
from werkzeug.datastructures import FileStorage
from werkzeug.exceptions import RequestEntityTooLarge

from pdfwtf.container_analysis import PlanValidationError, validate_plan
from pdfwtf.gui.adapter import AnalysisAdapter

_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{20,128}$")
_DOCUMENT_TYPES = (
    "auto",
    "unit",
    "journal-issue",
    "magazine-issue",
    "book",
    "proceedings",
)
AccessCheck = Callable[[str, str | None], bool]


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
        )

    @blueprint.post("/uploads")
    def upload_document() -> tuple[str, int] | str | Response:
        _authorize("upload", None)
        upload = request.files.get("document")
        error = _upload_error(upload)
        if error:
            return render_template("pdfwtf_gui/_job_error.html", message=error), 400

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
                    {"filename": upload.filename, "document_type": "auto"},
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
        return render_template("pdfwtf_gui/_uploads.html", uploads=_uploaded_files())

    @blueprint.delete("/uploads/<job_id>")
    def delete_upload(job_id: str) -> Response:
        _valid_job_id(job_id)
        _authorize("delete", job_id)
        try:
            status = _adapter().status(job_id)
        except KeyError:
            status = None
        if status is not None and status.state in {"queued", "processing"}:
            return _delete_error(
                job_id, _("Wait for analysis to finish before deleting the file."), 409
            )
        source = _input_dir() / job_id / "source.pdf"
        metadata = source.parent / "upload.json"
        plan = _plan_path(job_id)
        analysis = plan.parent / "source.analysis.json"
        paths = (
            (source, _input_dir()),
            (metadata, _input_dir()),
            (plan, _output_dir()),
            (analysis, _output_dir()),
        )
        if any(not path.resolve().is_relative_to(root) for path, root in paths):
            abort(404)
        try:
            # Remove only the files owned by this upload, never an entire tree.
            analysis.unlink(missing_ok=True)
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

    @blueprint.post("/jobs/<job_id>/analyze")
    def start_job(job_id: str) -> tuple[str, int] | str:
        _valid_job_id(job_id)
        _authorize("start", job_id)
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
                _adapter().start(job_id, source, document_type)
            except Exception:
                current_app.logger.exception("Cannot start a GUI analysis job")
                return (
                    render_template(
                        "pdfwtf_gui/_job_error.html",
                        message=_("Analysis could not start. Try again."),
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
                    "Analysis failed. Upload the document again or contact support."
                ),
            )
        if status.state == "complete":
            return render_template(
                "pdfwtf_gui/_job_complete.html",
                job_id=job_id,
                demo=status.demo,
                update_actions=request.headers.get("HX-Request") == "true",
                document_type=_upload_metadata(
                    _input_dir() / job_id / "source.pdf"
                ).get("document_type", "auto"),
            )
        return render_template(
            "pdfwtf_gui/_job_status.html", job_id=job_id, state=status.state
        )

    @blueprint.get("/jobs/<job_id>/analysis")
    def download_analysis(job_id: str) -> Response:
        _valid_job_id(job_id)
        _authorize("download_analysis", job_id)
        try:
            analysis = _analysis_result(job_id)
        except KeyError:
            abort(404)
        except LookupError:
            abort(409)
        response = jsonify(analysis)
        disposition = "inline" if request.args.get("inline") == "1" else "attachment"
        response.headers["Content-Disposition"] = (
            f'{disposition}; filename="source.analysis.json"'
        )
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @blueprint.get("/jobs/<job_id>/review")
    def review(job_id: str) -> str:
        _valid_job_id(job_id)
        _authorize("review", job_id)
        try:
            analysis = _analysis_result(job_id)
        except KeyError:
            abort(404)
        except LookupError:
            return redirect(url_for("pdfwtf_gui.index"))
        plan = None
        destination = _plan_path(job_id)
        if destination.is_file():
            try:
                plan = json.loads(destination.read_text(encoding="utf-8"))
                validate_plan(plan, _document_path(job_id))
            except (OSError, ValueError, PlanValidationError):
                abort(400, description="The saved plan is not valid.")
        return render_template(
            "pdfwtf_gui/review.html",
            job_id=job_id,
            analysis=analysis,
            plan=plan,
            analysis_json=json.dumps(analysis, ensure_ascii=False),
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
        )

    @blueprint.get("/jobs/<job_id>/document")
    def document(job_id: str) -> Response:
        _valid_job_id(job_id)
        _authorize("document", job_id)
        try:
            source = _document_path(job_id)
        except KeyError:
            abort(404)
        response = send_file(
            source,
            mimetype="application/pdf",
            as_attachment=False,
            conditional=True,
            download_name="document.pdf",
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @blueprint.post("/jobs/<job_id>/plan")
    def save_plan(job_id: str) -> tuple[Response, int] | Response:
        _valid_job_id(job_id)
        _authorize("save_plan", job_id)
        plan = request.get_json(silent=True)
        if not isinstance(plan, dict):
            return jsonify(error=_("The plan must be a JSON object.")), 400
        try:
            source = _document_path(job_id)
            validate_plan(plan, source)
        except KeyError:
            abort(404)
        except PlanValidationError as error:
            return jsonify(error=_("The plan is not valid."), details=str(error)), 400

        destination = _plan_path(job_id)
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
    def delete_plan(job_id: str) -> Response:
        _valid_job_id(job_id)
        _authorize("delete_plan", job_id)
        try:
            _document_path(job_id)
        except KeyError:
            abort(404)
        _plan_path(job_id).unlink(missing_ok=True)
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
        if not destination.is_file():
            abort(404)
        return send_file(
            destination,
            mimetype="application/json",
            as_attachment=True,
            download_name="approved.plan.json",
        )

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


def _adapter() -> AnalysisAdapter:
    adapter = current_app.config.get("PDFWTF_GUI_ANALYSIS_ADAPTER")
    if adapter is None:
        raise RuntimeError("PDFWTF_GUI_ANALYSIS_ADAPTER is required.")
    return adapter


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


def _analysis_result(job_id: str) -> dict[str, Any]:
    try:
        return _adapter().result(job_id)
    except (KeyError, LookupError) as error:
        destination = _output_dir() / job_id / "source.analysis.json"
        if not destination.resolve().is_relative_to(_output_dir()):
            raise KeyError("Unknown analysis.") from None
        try:
            analysis = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise error from None
        if (
            not isinstance(analysis, dict)
            or analysis.get("kind") != "analysis"
            or not isinstance(analysis.get("pages"), list)
            or not isinstance(analysis.get("units"), list)
        ):
            raise KeyError("Invalid saved analysis.") from None
        return analysis


def _document_path(job_id: str) -> Path:
    try:
        return _adapter().document_path(job_id)
    except KeyError:
        # Routes already validate the upload path and authorize the operation.
        return _input_dir() / job_id / "source.pdf"


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
            _analysis_result(job_id)
            has_analysis = True
        except (KeyError, LookupError):
            has_analysis = False
        uploads.append(
            {
                "job_id": job_id,
                "filename": filename,
                "has_analysis": has_analysis,
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
