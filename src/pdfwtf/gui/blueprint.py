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
_DOCUMENT_TYPES = ("auto", "unit", "journal-issue", "book", "proceedings")
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
            document_types=(
                ("auto", _("Automatic")),
                ("unit", _("Single unit")),
                ("journal-issue", _("Journal issue")),
                ("book", _("Book")),
                ("proceedings", _("Proceedings")),
            ),
        )

    @blueprint.post("/jobs")
    def start_job() -> tuple[str, int] | str:
        _authorize("start", None)
        upload = request.files.get("document")
        document_type = request.form.get("document_type", "")
        error = _upload_error(upload, document_type)
        if error:
            return render_template("pdfwtf_gui/_job_error.html", message=error), 400

        job_id = secrets.token_urlsafe(24)
        job_dir = _input_dir() / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        source = job_dir / "source.pdf"
        assert upload is not None
        upload.save(source)
        try:
            with source.open("rb") as stream:
                if stream.read(5) != b"%PDF-":
                    source.unlink(missing_ok=True)
                    job_dir.rmdir()
                    return (
                        render_template(
                            "pdfwtf_gui/_job_error.html",
                            message=_("Select a valid PDF document."),
                        ),
                        400,
                    )
            _adapter().start(job_id, source, document_type)
        except Exception:
            source.unlink(missing_ok=True)
            job_dir.rmdir()
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
            )
        return render_template(
            "pdfwtf_gui/_job_status.html", job_id=job_id, state=status.state
        )

    @blueprint.get("/jobs/<job_id>/review")
    def review(job_id: str) -> str:
        _valid_job_id(job_id)
        _authorize("review", job_id)
        try:
            status = _adapter().status(job_id)
            analysis = _adapter().result(job_id)
        except KeyError:
            abort(404)
        except LookupError:
            return redirect(url_for("pdfwtf_gui.index"))
        return render_template(
            "pdfwtf_gui/review.html",
            job_id=job_id,
            analysis=analysis,
            analysis_json=json.dumps(analysis, ensure_ascii=False),
            demo=status.demo,
            unit_types=(
                "preface",
                "editorial",
                "table-of-contents",
                "programme",
                "article",
                "chapter",
                "abstract-or-poster",
                "book-review",
                "unknown",
            ),
            unit_type_labels={
                "preface": _("Preface"),
                "editorial": _("Editorial"),
                "table-of-contents": _("Table of contents"),
                "programme": _("Programme"),
                "article": _("Article"),
                "chapter": _("Chapter"),
                "abstract-or-poster": _("Abstract or poster"),
                "book-review": _("Book review"),
                "unknown": _("Unknown"),
            },
        )

    @blueprint.get("/jobs/<job_id>/document")
    def document(job_id: str) -> Response:
        _valid_job_id(job_id)
        _authorize("document", job_id)
        try:
            source = _adapter().document_path(job_id)
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
            source = _adapter().document_path(job_id)
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

    @blueprint.get("/jobs/<job_id>/plan")
    def download_plan(job_id: str) -> Response:
        _valid_job_id(job_id)
        _authorize("download_plan", job_id)
        try:
            _adapter().document_path(job_id)
        except KeyError:
            abort(404)
        destination = _plan_path(job_id)
        if not destination.is_file():
            abort(404)
        return send_file(
            destination,
            mimetype="application/json",
            as_attachment=True,
            download_name="approved-plan.json",
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
    return _output_dir() / job_id / "approved-plan.json"


def _authorize(action: str, job_id: str | None) -> None:
    callback: AccessCheck | None = current_app.config.get("PDFWTF_GUI_ACCESS_CHECK")
    if callback is not None and not callback(action, job_id):
        abort(403)


def _valid_job_id(job_id: str) -> None:
    if _JOB_ID.fullmatch(job_id) is None:
        abort(404)


def _upload_error(upload: FileStorage | None, document_type: str) -> str | None:
    if upload is None or not upload.filename:
        return _("Select a PDF document.")
    if not upload.filename.lower().endswith(".pdf"):
        return _("The selected file must have a .pdf extension.")
    if document_type not in _DOCUMENT_TYPES:
        return _("Select a valid document type.")
    return None
