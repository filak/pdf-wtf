"""Analysis adapter boundary for the PDF-WTF GUI."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
import json
import logging
from pathlib import Path
from threading import Lock
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZIP_DEFLATED
from typing import Any, Literal, Protocol

from pdfwtf.container_analysis import analyze_container, write_json_document
from pdfwtf.pipeline import process_pdf

JobState = Literal["queued", "processing", "complete", "failed"]


def document_with_pdf_type(document: dict[str, Any], source: Path) -> dict[str, Any]:
    """Copy the upload PDF type immediately after the document type."""
    metadata_path = source.parent / "upload.json"
    metadata = (
        json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata_path.is_file()
        else {}
    )
    pdf_type = metadata.get("pdf_type", "born-digital")
    result = {}
    for key, value in document.items():
        if key != "pdf_type":
            result[key] = value
        if key == "document_type":
            result["pdf_type"] = pdf_type
    return result


@dataclass(frozen=True)
class JobStatus:
    """Public state returned by an analysis adapter."""

    state: JobState
    message: str | None = None
    demo: bool = False


class ExportAdapter(Protocol):
    """Background export operations supplied by the GUI host."""

    def start_export(
        self,
        job_id: str,
        source: Path,
        plan: dict[str, Any],
        *,
        no_pdf_out: bool = False,
        get_html: bool = False,
        get_meta: bool = False,
        include_source: bool = False,
        debug: bool = False,
    ) -> None:
        """Schedule export from a snapshot of the saved plan."""

    def export_status(self, job_id: str) -> JobStatus:
        """Return the current export state."""

    def export_path(self, job_id: str) -> Path:
        """Return the completed export archive."""


class AnalysisAdapter(Protocol):
    """Operations required by the GUI without selecting a queue technology."""

    def start(self, job_id: str, source: Path, document_type: str) -> None:
        """Schedule analysis, including a rerun after a finished job."""

    def status(self, job_id: str) -> JobStatus:
        """Return the current job state."""

    def result(self, job_id: str) -> dict[str, Any]:
        """Return completed analysis data."""

    def document_path(self, job_id: str) -> Path:
        """Return the controlled source document path."""


@dataclass
class _DemoJob:
    source: Path
    document_type: str
    state: JobState = "queued"
    message: str | None = None
    result: dict[str, Any] | None = None


class DemoAnalysisAdapter:
    """In-process demo adapter for local scaffolding, not a durable job queue."""

    def __init__(self, max_workers: int = 2, *, output_dir: Path | None = None) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="pdfwtf-gui-demo"
        )
        self._output_dir = output_dir.resolve() if output_dir is not None else None
        self._jobs: dict[str, _DemoJob] = {}
        self._lock = Lock()
        self._exports: dict[str, JobStatus] = {}

    def start(self, job_id: str, source: Path, document_type: str) -> None:
        with self._lock:
            export = self._exports.get(job_id)
            if export is not None and export.state in {"queued", "processing"}:
                raise LookupError("Wait for export to finish.")
            existing = self._jobs.get(job_id)
            if existing is not None and existing.state in {"queued", "processing"}:
                return
            self._jobs[job_id] = _DemoJob(source, document_type)
        self._executor.submit(self._analyze, job_id)

    def status(self, job_id: str) -> JobStatus:
        with self._lock:
            job = self._get(job_id)
            return JobStatus(job.state, job.message, demo=True)

    def result(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._get(job_id)
            if job.state != "complete" or job.result is None:
                raise LookupError("The analysis result is not ready.")
            return deepcopy(job.result)

    def document_path(self, job_id: str) -> Path:
        with self._lock:
            return self._get(job_id).source

    def _get(self, job_id: str) -> _DemoJob:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise KeyError("Unknown job.") from None

    def _analyze(self, job_id: str) -> None:
        with self._lock:
            job = self._get(job_id)
            job.state = "processing"
        try:
            result = document_with_pdf_type(
                analyze_container(job.source, job.document_type), job.source
            )
            if self._output_dir is not None:
                destination = self._output_dir / job_id / "source.analysis.json"
                destination.parent.mkdir(parents=True, exist_ok=True)
                staged = destination.with_suffix(".json.tmp")
                try:
                    write_json_document(result, staged)
                    staged.replace(destination)
                finally:
                    staged.unlink(missing_ok=True)
            detected_type = result["document_type"]
            if job.document_type == "auto" and detected_type in {
                "unit",
                "journal-issue",
                "magazine-issue",
                "book",
                "proceedings",
            }:
                metadata_path = job.source.parent / "upload.json"
                if metadata_path.is_file():
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    if (
                        isinstance(metadata, dict)
                        and metadata.get("document_type") == "auto"
                    ):
                        metadata["document_type"] = detected_type
                        staged_metadata = metadata_path.with_suffix(".json.tmp")
                        try:
                            staged_metadata.write_text(
                                json.dumps(metadata, ensure_ascii=False),
                                encoding="utf-8",
                            )
                            staged_metadata.replace(metadata_path)
                        finally:
                            staged_metadata.unlink(missing_ok=True)
        except Exception as error:
            logging.getLogger(__name__).error(
                "GUI analysis failed (%s).",
                type(error).__name__,
                extra={"correlation_id": job_id},
            )
            with self._lock:
                job.state = "failed"
                job.message = "The demo analysis failed. Check the host logs."
            return
        with self._lock:
            job.result = result
            job.state = "complete"

    def start_export(
        self,
        job_id: str,
        source: Path,
        plan: dict[str, Any],
        *,
        no_pdf_out: bool = False,
        get_html: bool = False,
        get_meta: bool = False,
        include_source: bool = False,
        debug: bool = False,
    ) -> None:
        """Schedule saved-plan export without processing inside an HTTP request."""
        if self._output_dir is None:
            raise RuntimeError("Export output directory is not configured.")
        snapshot = deepcopy(plan)
        with self._lock:
            analysis = self._jobs.get(job_id)
            if analysis is not None and analysis.state in {"queued", "processing"}:
                raise LookupError("Wait for analysis to finish.")
            existing = self._exports.get(job_id)
            if existing is not None and existing.state in {"queued", "processing"}:
                raise LookupError("Wait for export to finish.")
            self._exports[job_id] = JobStatus("queued", demo=True)
        try:
            self._executor.submit(
                self._export,
                job_id,
                source,
                snapshot,
                no_pdf_out,
                get_html,
                include_source,
                debug,
                get_meta,
            )
        except Exception:
            with self._lock:
                self._exports[job_id] = JobStatus("failed", demo=True)
            raise

    def export_status(self, job_id: str) -> JobStatus:
        """Return the state of an export job."""
        with self._lock:
            return self._exports[job_id]

    def export_path(self, job_id: str) -> Path:
        """Return the archive only after the latest export succeeds."""
        if self.export_status(job_id).state != "complete":
            raise LookupError("The export is not ready.")
        assert self._output_dir is not None
        return self._output_dir / job_id / "export.zip"

    def _export(
        self,
        job_id: str,
        source: Path,
        plan: dict[str, Any],
        no_pdf_out: bool,
        get_html: bool,
        include_source: bool,
        debug: bool,
        get_meta: bool,
    ) -> None:
        with self._lock:
            self._exports[job_id] = JobStatus("processing", demo=True)
        try:
            assert self._output_dir is not None
            upload_path = source.parent / "upload.json"
            if upload_path.resolve().parent != source.parent.resolve():
                raise ValueError("Upload metadata must remain in the source directory.")
            upload = json.loads(upload_path.read_text(encoding="utf-8"))
            if not isinstance(upload, dict):
                raise ValueError("Upload metadata must be a JSON object.")
            destination = self._output_dir / job_id
            destination.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="export-", dir=destination) as temporary:
                workspace = Path(temporary)
                plan_path = workspace / "approved.plan.json"
                write_json_document(plan, plan_path)
                debug_files = []
                if debug:
                    for name in ("approved.plan.json", "source.analysis.json"):
                        original = destination / name
                        if original.resolve().parent != destination.resolve():
                            raise ValueError(
                                "Debug files must remain in the job directory."
                            )
                        copied = workspace / "debug" / name
                        copied.parent.mkdir(exist_ok=True)
                        copied.write_bytes(original.read_bytes())
                        debug_files.append(copied)
                results = workspace / "results"
                process_pdf(
                    source,
                    results,
                    plan_path=plan_path,
                    pdf_type="born-digital",
                    no_pdf_flag=True,
                    export_unit_pdfs_flag=not no_pdf_out,
                    export_html_flag=get_html,
                )
                units = results / f"_units_{source.stem}"
                manifest_path = units / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not get_meta:
                    for unit in manifest["units"]:
                        unit["metadata"] = None
                enriched_manifest = {}
                for key, value in manifest.items():
                    enriched_manifest[key] = value
                    if key == "kind":
                        enriched_manifest["upload"] = upload
                write_json_document(enriched_manifest, manifest_path)
                units.rename(results / "_units")
                archive = workspace / "export.zip"
                with ZipFile(archive, "w", compression=ZIP_DEFLATED) as bundle:
                    for file in sorted(results.rglob("*")):
                        if file.is_file() and (
                            get_meta or not file.name.endswith(".metadata.json")
                        ):
                            bundle.write(file, file.relative_to(results).as_posix())
                    if include_source:
                        bundle.write(source, "source.pdf")
                    for file in debug_files:
                        bundle.write(file, file.name)
                archive.replace(destination / "export.zip")
        except Exception:
            logging.getLogger(__name__).exception(
                "GUI export failed", extra={"job_id": job_id}
            )
            with self._lock:
                self._exports[job_id] = JobStatus("failed", demo=True)
            return
        with self._lock:
            self._exports[job_id] = JobStatus("complete", demo=True)

    def close(self) -> None:
        """Stop accepting work and release worker threads."""
        self._executor.shutdown(wait=False, cancel_futures=True)
