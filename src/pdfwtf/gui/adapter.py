"""Analysis, preparation, partition, and export adapters for the PDF-WTF GUI."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
from threading import Lock
from tempfile import TemporaryDirectory
import traceback
from zipfile import ZipFile, ZIP_DEFLATED
from typing import Any, Literal, Protocol

from pdfwtf.configuration import (
    COMMAND_ANALYZE,
    COMMAND_ENHANCE,
    COMMAND_EXPORT,
    COMMAND_PARTITION,
    AppConfig,
    LLMTaskConfig,
)
from pdfwtf.container_partition import (
    direct_unit_plan,
    partition_container,
    write_json_document,
)
from pdfwtf.llm_tasks import available_tasks, validate_task_selection
from pdfwtf.pipeline import analyze_pdf, enhance_pdf, export_pdf
from pdfwtf.utils.common import replace_file

# Export status message for a finished export with failed LLM tasks.
LLM_INCOMPLETE = "llm_incomplete"

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
    """Public state returned by an adapter operation."""

    state: JobState
    message: str | None = None
    demo: bool = False
    details: tuple[str, ...] = ()


class ExportAdapter(Protocol):
    """Background export operations supplied by the GUI host."""

    def start_export(
        self,
        job_id: str,
        source: Path,
        plan: dict[str, Any] | None,
        *,
        no_pdf_out: bool = False,
        get_html: bool = False,
        get_meta: bool = False,
        include_source: bool = False,
        debug: bool = False,
        llm_tasks: tuple[str, ...] = (),
    ) -> None:
        """Schedule export of the active PDF from a snapshot of the saved plan.

        Without a plan, export the whole PDF as one unit.
        Raise ValueError when the selected LLM tasks cannot run.
        """

    def export_status(self, job_id: str) -> JobStatus:
        """Return the current export state."""

    def export_path(self, job_id: str) -> Path:
        """Return the completed export archive."""

    # Optional: ``llm_tasks(command="export") -> list[LLMTaskConfig]`` lists
    # the selectable tasks for one operation.


class PartitionAdapter(Protocol):
    """Operations required by the GUI without selecting a queue technology."""

    def start(
        self,
        job_id: str,
        source: Path,
        document_type: str,
        *,
        llm_tasks: tuple[str, ...] = (),
    ) -> None:
        """Schedule partitioning of the active PDF, including a rerun.

        Raise ValueError when the selected LLM tasks cannot run.
        """

    def status(self, job_id: str) -> JobStatus:
        """Return the current job state."""

    def result(self, job_id: str) -> dict[str, Any]:
        """Return completed partition data."""

    def document_path(self, job_id: str) -> Path:
        """Return the controlled source document path."""


class AnalyzeAdapter(Protocol):
    """Optional background PDF analysis (diagnostics) supplied by the GUI host."""

    def start_analysis(
        self, job_id: str, source: Path, *, llm_tasks: tuple[str, ...] = ()
    ) -> None:
        """Schedule read-only analysis of the active PDF.

        Publish ``<output_dir>/<job-id>/analysis.json`` atomically. Raise
        LookupError while preparation is active and ValueError when the
        selected LLM tasks cannot run.
        """

    def analysis_status(self, job_id: str) -> JobStatus:
        """Return the current analysis state. Raise KeyError when unknown."""


class PrepareAdapter(Protocol):
    """Optional background preparation (enhancement) supplied by the GUI host."""

    def start_prepare(
        self,
        job_id: str,
        source: Path,
        destination: Path,
        *,
        options: dict[str, Any],
        llm_tasks: tuple[str, ...] = (),
    ) -> None:
        """Schedule enhancement of the original PDF into ``destination``.

        ``options`` contains ``pdf_type``, ``languages``, and
        ``remove_background_flag``. Publish ``destination`` atomically.
        Raise LookupError while another operation is active and ValueError
        when the selected LLM tasks cannot run.
        """

    def prepare_status(self, job_id: str) -> JobStatus:
        """Return the current preparation state. Raise KeyError when unknown."""


def _failure_detail(error: BaseException) -> str:
    """Describe an error for the host log without its message.

    The message can contain document text, so log only the error class, the
    names of affected files, and the code location.
    """
    parts = [type(error).__name__]
    if isinstance(error, OSError):
        names = [
            Path(os.fsdecode(name)).name
            for name in (error.filename, error.filename2)
            if isinstance(name, (str, bytes, os.PathLike))
        ]
        if names:
            parts.append("file=" + " -> ".join(names))
    frames = traceback.extract_tb(error.__traceback__)
    if frames:
        parts.append(f"at={Path(frames[-1].filename).name}:{frames[-1].lineno}")
    return ", ".join(parts)


@dataclass
class _DemoJob:
    source: Path
    document_type: str
    state: JobState = "queued"
    message: str | None = None
    result: dict[str, Any] | None = None


class DemoPartitionAdapter:
    """In-process demo adapter for local scaffolding, not a durable job queue."""

    def __init__(
        self,
        max_workers: int = 2,
        *,
        output_dir: Path | None = None,
        config: AppConfig | None = None,
    ) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="pdfwtf-gui-demo"
        )
        self._output_dir = output_dir.resolve() if output_dir is not None else None
        self._config = config
        self._jobs: dict[str, _DemoJob] = {}
        self._lock = Lock()
        self._exports: dict[str, JobStatus] = {}
        self._prepares: dict[str, JobStatus] = {}
        self._analyses: dict[str, JobStatus] = {}

    def _validate_tasks(
        self, llm_tasks: tuple[str, ...], command: str, *, get_html: bool = False
    ) -> tuple[str, ...]:
        """Reject selected tasks that cannot run, before scheduling."""
        llm_tasks = tuple(llm_tasks)
        if llm_tasks:
            if self._config is None:
                raise ValueError("LLM tasks require the application configuration.")
            validate_task_selection(self._config, llm_tasks, command, get_html=get_html)
        return llm_tasks

    def _active_operation(self, job_id: str) -> str | None:
        """Return the active operation for a job. Call with the lock held."""
        for name, status in (
            ("export", self._exports.get(job_id)),
            ("preparation", self._prepares.get(job_id)),
        ):
            if status is not None and status.state in {"queued", "processing"}:
                return name
        job = self._jobs.get(job_id)
        if job is not None and job.state in {"queued", "processing"}:
            return "partitioning"
        return None

    def start(
        self,
        job_id: str,
        source: Path,
        document_type: str,
        *,
        llm_tasks: tuple[str, ...] = (),
    ) -> None:
        self._validate_tasks(llm_tasks, COMMAND_PARTITION)
        with self._lock:
            active = self._active_operation(job_id)
            if active in {"export", "preparation"}:
                raise LookupError(f"Wait for {active} to finish.")
            existing = self._jobs.get(job_id)
            if existing is not None and existing.state in {"queued", "processing"}:
                return
            self._jobs[job_id] = _DemoJob(source, document_type)
        self._executor.submit(self._partition, job_id)

    def status(self, job_id: str) -> JobStatus:
        with self._lock:
            job = self._get(job_id)
            return JobStatus(job.state, job.message, demo=True)

    def result(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._get(job_id)
            if job.state != "complete" or job.result is None:
                raise LookupError("The partition result is not ready.")
            return deepcopy(job.result)

    def document_path(self, job_id: str) -> Path:
        with self._lock:
            return self._get(job_id).source

    def _get(self, job_id: str) -> _DemoJob:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise KeyError("Unknown job.") from None

    def _partition(self, job_id: str) -> None:
        with self._lock:
            job = self._get(job_id)
            job.state = "processing"
        try:
            result = document_with_pdf_type(
                partition_container(job.source, job.document_type), job.source
            )
            if self._output_dir is not None:
                destination = self._output_dir / job_id / "source.partition.json"
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
                "GUI partitioning failed (%s).",
                _failure_detail(error),
                extra={"correlation_id": job_id},
            )
            with self._lock:
                job.state = "failed"
                job.message = "The demo partitioning failed. Check the host logs."
            return
        with self._lock:
            job.result = result
            job.state = "complete"

    def start_export(
        self,
        job_id: str,
        source: Path,
        plan: dict[str, Any] | None,
        *,
        no_pdf_out: bool = False,
        get_html: bool = False,
        get_meta: bool = False,
        include_source: bool = False,
        debug: bool = False,
        llm_tasks: tuple[str, ...] = (),
    ) -> None:
        """Schedule export without processing inside an HTTP request.

        Without a saved plan, export the whole PDF as one unit.
        """
        if self._output_dir is None:
            raise RuntimeError("Export output directory is not configured.")
        llm_tasks = self._validate_tasks(llm_tasks, COMMAND_EXPORT, get_html=get_html)
        snapshot = deepcopy(plan) if plan is not None else None
        with self._lock:
            active = self._active_operation(job_id)
            if active is not None:
                raise LookupError(f"Wait for {active} to finish.")
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
                llm_tasks,
            )
        except Exception:
            with self._lock:
                self._exports[job_id] = JobStatus("failed", demo=True)
            raise

    def llm_tasks(self, command: str = COMMAND_EXPORT) -> list[LLMTaskConfig]:
        """Return LLM tasks that can run for one operation."""
        if self._config is None:
            return []
        return available_tasks(self._config, command)

    def start_analysis(
        self, job_id: str, source: Path, *, llm_tasks: tuple[str, ...] = ()
    ) -> None:
        """Schedule read-only analysis. It does not block other operations."""
        if self._output_dir is None:
            raise RuntimeError("Analysis output directory is not configured.")
        llm_tasks = self._validate_tasks(llm_tasks, COMMAND_ANALYZE)
        with self._lock:
            if self._active_operation(job_id) == "preparation":
                raise LookupError("Wait for preparation to finish.")
            existing = self._analyses.get(job_id)
            if existing is not None and existing.state in {"queued", "processing"}:
                return
            self._analyses[job_id] = JobStatus("queued", demo=True)
        try:
            self._executor.submit(self._analyze, job_id, source, llm_tasks)
        except Exception:
            with self._lock:
                self._analyses[job_id] = JobStatus("failed", demo=True)
            raise

    def analysis_status(self, job_id: str) -> JobStatus:
        """Return the state of an analysis job."""
        with self._lock:
            return self._analyses[job_id]

    def _analyze(self, job_id: str, source: Path, llm_tasks: tuple[str, ...]) -> None:
        with self._lock:
            self._analyses[job_id] = JobStatus("processing", demo=True)
        try:
            assert self._output_dir is not None
            job_output = self._output_dir / job_id
            job_output.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="analyze-", dir=job_output) as temporary:
                result = analyze_pdf(
                    source, Path(temporary), config=self._config, llm_tasks=llm_tasks
                )
                result.report_path.replace(job_output / "analysis.json")
        except Exception as error:
            # Log no error message. Reports and document text stay private.
            logging.getLogger(__name__).error(
                "GUI analysis failed (%s).",
                _failure_detail(error),
                extra={"correlation_id": job_id},
            )
            with self._lock:
                self._analyses[job_id] = JobStatus(
                    "failed", "The demo analysis failed. Check the host logs.", True
                )
            return
        with self._lock:
            self._analyses[job_id] = JobStatus("complete", demo=True)

    def start_prepare(
        self,
        job_id: str,
        source: Path,
        destination: Path,
        *,
        options: dict[str, Any],
        llm_tasks: tuple[str, ...] = (),
    ) -> None:
        """Schedule enhancement of the original PDF into the prepared PDF."""
        if self._output_dir is None:
            raise RuntimeError("Preparation output directory is not configured.")
        llm_tasks = self._validate_tasks(llm_tasks, COMMAND_ENHANCE)
        with self._lock:
            active = self._active_operation(job_id)
            analysis = self._analyses.get(job_id)
            if active is None and analysis is not None:
                # Analysis reads the active PDF that preparation replaces.
                if analysis.state in {"queued", "processing"}:
                    active = "analysis"
            if active is not None:
                raise LookupError(f"Wait for {active} to finish.")
            self._prepares[job_id] = JobStatus("queued", demo=True)
        try:
            self._executor.submit(
                self._prepare, job_id, source, destination, dict(options), llm_tasks
            )
        except Exception:
            with self._lock:
                self._prepares[job_id] = JobStatus("failed", demo=True)
            raise

    def prepare_status(self, job_id: str) -> JobStatus:
        """Return the state of a preparation job."""
        with self._lock:
            return self._prepares[job_id]

    def _prepare(
        self,
        job_id: str,
        source: Path,
        destination: Path,
        options: dict[str, Any],
        llm_tasks: tuple[str, ...],
    ) -> None:
        with self._lock:
            self._prepares[job_id] = JobStatus("processing", demo=True)
        try:
            assert self._output_dir is not None
            job_output = self._output_dir / job_id
            job_output.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(
                prefix="prepare-", dir=job_output, ignore_cleanup_errors=True
            ) as temporary:
                result = enhance_pdf(
                    source,
                    Path(temporary),
                    config=self._config,
                    llm_tasks=llm_tasks,
                    **options,
                )
                # Stage beside the destination, then replace it atomically.
                staged = destination.with_name(f".{destination.name}.tmp")
                try:
                    staged.write_bytes(result.pdf.read_bytes())
                    replace_file(staged, destination)
                finally:
                    staged.unlink(missing_ok=True)
                replace_file(result.report, job_output / "enhance.json")
            # Results for the previous active PDF no longer match.
            for name in (
                "source.partition.json",
                "approved.plan.json",
                "export.zip",
                "analysis.json",
            ):
                (job_output / name).unlink(missing_ok=True)
        except Exception as error:
            logging.getLogger(__name__).error(
                "GUI preparation failed (%s).",
                _failure_detail(error),
                extra={"correlation_id": job_id},
            )
            with self._lock:
                self._prepares[job_id] = JobStatus(
                    "failed", "The demo preparation failed. Check the host logs.", True
                )
            return
        with self._lock:
            self._jobs.pop(job_id, None)
            self._exports.pop(job_id, None)
            self._prepares[job_id] = JobStatus("complete", demo=True)

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
        plan: dict[str, Any] | None,
        no_pdf_out: bool,
        get_html: bool,
        include_source: bool,
        debug: bool,
        get_meta: bool,
        llm_tasks: tuple[str, ...] = (),
    ) -> None:
        with self._lock:
            self._exports[job_id] = JobStatus("processing", demo=True)
        llm_report = None
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
                direct = plan is None
                if plan is None:
                    # Export without partitioning treats the PDF as one unit.
                    plan = direct_unit_plan(source)
                write_json_document(plan, plan_path)
                debug_files = []
                if debug:
                    for name in ("approved.plan.json", "source.partition.json"):
                        original = destination / name
                        if direct:
                            # A direct export has no saved partition or plan.
                            if name != "approved.plan.json":
                                continue
                            original = plan_path
                        elif original.resolve().parent != destination.resolve():
                            raise ValueError(
                                "Debug files must remain in the job directory."
                            )
                        copied = workspace / "debug" / name
                        copied.parent.mkdir(exist_ok=True)
                        copied.write_bytes(original.read_bytes())
                        debug_files.append(copied)
                results = workspace / "results"
                llm_report = export_pdf(
                    source,
                    results,
                    plan_path=plan_path,
                    export_unit_pdfs_flag=not no_pdf_out,
                    export_html_flag=get_html,
                    unit_doi_flag=True,
                    config=self._config,
                    llm_tasks=llm_tasks,
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
                        # Name the active PDF so original and prepared stay distinct.
                        bundle.write(
                            source,
                            (
                                "prepared.pdf"
                                if source.name == "prepared.pdf"
                                else "source.pdf"
                            ),
                        )
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
        message = None
        details: tuple[str, ...] = ()
        if llm_report is not None and not llm_report.complete:
            logging.getLogger(__name__).warning(
                "GUI export LLM processing incomplete", extra={"job_id": job_id}
            )
            message = LLM_INCOMPLETE
            details = tuple(
                f"{task} / {unit}: {error}" for task, unit, error in llm_report.errors
            )
        with self._lock:
            self._exports[job_id] = JobStatus(
                "complete", message, demo=True, details=details
            )

    def close(self) -> None:
        """Stop accepting work and release worker threads."""
        self._executor.shutdown(wait=False, cancel_futures=True)
