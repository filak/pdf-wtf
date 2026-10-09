"""Analysis adapter boundary for the PDF-WTF GUI."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Literal, Protocol

from pdfwtf.container_analysis import analyze_container

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
        """Schedule analysis and return without doing the work inline."""

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

    def __init__(self, max_workers: int = 2) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="pdfwtf-gui-demo"
        )
        self._jobs: dict[str, _DemoJob] = {}
        self._lock = Lock()

    def start(self, job_id: str, source: Path, document_type: str) -> None:
        with self._lock:
            if job_id in self._jobs:
                raise ValueError("The job already exists.")
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
