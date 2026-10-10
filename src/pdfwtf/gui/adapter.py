"""Analysis adapter boundary for the PDF-WTF GUI."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from threading import Lock
from typing import Any, Literal, Protocol

from pdfwtf.container_analysis import analyze_container, write_json_document

JobState = Literal["queued", "processing", "complete", "failed"]


@dataclass(frozen=True)
class JobStatus:
    """Public state returned by an analysis adapter."""

    state: JobState
    message: str | None = None
    demo: bool = False


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

    def start(self, job_id: str, source: Path, document_type: str) -> None:
        with self._lock:
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
            result = analyze_container(job.source, job.document_type)
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
        except Exception:
            with self._lock:
                job.state = "failed"
                job.message = "The demo analysis failed. Check the host logs."
            return
        with self._lock:
            job.result = result
            job.state = "complete"

    def close(self) -> None:
        """Stop accepting work and release worker threads."""
        self._executor.shutdown(wait=False, cancel_futures=True)
