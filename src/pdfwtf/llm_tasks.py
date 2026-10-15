"""Selection, validation, and sequential execution of configured LLM tasks."""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .configuration import COMMANDS, LLM_STAGES, LLMTaskConfig, AppConfig
from .container_partition import write_json_document

log = logging.getLogger(__name__)

# Reject larger unit HTML instead of splitting it.
MAX_LLM_INPUT_BYTES = 500_000
_ELEMENT = re.compile(r"<[A-Za-z]")
MAX_ERROR_CHARS = 1000
_API_KEY = re.compile(r"AIza[0-9A-Za-z_-]{20,}")


class LLMTaskError(ValueError):
    """The LLM task selection cannot run."""


class LLMResponseError(ValueError):
    """The LLM response is malformed or unusable."""


@dataclass
class LLMReport:
    """Outcome of the selected LLM tasks for one run."""

    tasks: tuple[str, ...]
    failed_units: dict[str, list[str]] = field(default_factory=dict)
    # Task name, unit ID, and bounded error message for each failure.
    errors: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not any(self.failed_units.values())

    def summary(self) -> str:
        failures = "; ".join(
            f"{name} failed for {', '.join(units)}"
            for name, units in self.failed_units.items()
            if units
        )
        return f"LLM processing incomplete: {failures}."


def unavailable_reason(config: AppConfig) -> str | None:
    """Return why LLM tasks are unavailable, or None when they are available."""
    if not config.gemini_api_key_loaded:
        return "GEMINI_API_KEY is not set."
    if not config.gemini_api_key_present:
        return (
            "GEMINI_API_KEY is only in .env. gemini-call reads only the process "
            "environment. Set GEMINI_API_KEY in the operating-system environment."
        )
    return None


def available_tasks(
    config: AppConfig, command: str | None = None
) -> list[LLMTaskConfig]:
    """Return configured tasks that can run, optionally for one command."""
    if unavailable_reason(config) is not None:
        return []
    return [
        task
        for task in config.llm_tasks.values()
        if command is None or command in task.commands
    ]


def validate_task_selection(
    config: AppConfig,
    names: Iterable[str],
    command: str,
    *,
    get_html: bool,
) -> tuple[LLMTaskConfig, ...]:
    """Check explicitly selected tasks before processing starts.

    A selected task is never ignored. It runs, or the selection is rejected.
    """
    if command not in COMMANDS:
        raise LLMTaskError(f"Unknown command '{command}'.")
    names = tuple(dict.fromkeys(names))
    if not names:
        return ()
    configured = config.llm_tasks
    tasks = []
    for name in names:
        task = configured.get(name)
        if task is None:
            raise LLMTaskError(f"Unknown LLM task '{name}'.")
        if command not in task.commands or command not in LLM_STAGES.get(
            task.stage, frozenset()
        ):
            raise LLMTaskError(
                f"LLM task '{name}' does not support the '{command}' command."
            )
        if task.stage == "after_unit_html" and not get_html:
            raise LLMTaskError(f"LLM task '{name}' requires --get-html.")
        tasks.append(task)
    reason = unavailable_reason(config)
    if reason is not None:
        raise LLMTaskError(f"LLM tasks are unavailable. {reason}")
    return tuple(tasks)


def _call_gemini(task: LLMTaskConfig, html_path: Path) -> Any:
    """Send one unit HTML file to Gemini. gemini-call reads the API key itself."""
    from gemini_call import call

    return call(
        task.prompt,
        files=[str(html_path)],
        system=task.system,
        response_format="json",
    )


def parse_response(envelope: Any) -> tuple[str, dict[str, Any] | None]:
    """Separate the HTML response from token usage and validate both."""
    if not isinstance(envelope, dict):
        raise LLMResponseError("The response envelope is not a JSON object.")
    response = envelope.get("response")
    usage = envelope.get("usage")
    if not isinstance(response, str):
        raise LLMResponseError("The response field is not text.")
    if usage is not None and not isinstance(usage, dict):
        raise LLMResponseError("The usage field is not a JSON object.")
    html = response.strip()
    if not html:
        raise LLMResponseError("The response HTML is empty.")
    if html.startswith("```"):
        raise LLMResponseError("The response HTML is wrapped in a Markdown fence.")
    if not _ELEMENT.search(html):
        raise LLMResponseError("The response contains no HTML element.")
    return html, usage


def describe_error(error: Exception) -> str:
    """Return a bounded one-line error text without API keys."""
    text = " ".join(str(error).split()) or "No error message."
    text = _API_KEY.sub("[REDACTED]", text)
    if len(text) > MAX_ERROR_CHARS:
        text = text[:MAX_ERROR_CHARS] + "…"
    return f"{type(error).__name__}: {text}"


def _run_unit(task: LLMTaskConfig, unit_id: str, html_path: Path) -> dict[str, Any]:
    if html_path.stat().st_size > MAX_LLM_INPUT_BYTES:
        raise LLMResponseError(
            f"The unit HTML is larger than {MAX_LLM_INPUT_BYTES} bytes."
        )
    html, usage = parse_response(_call_gemini(task, html_path))
    output_name = f"{unit_id}{task.output_suffix}"
    (html_path.parent / output_name).write_text(html + "\n", encoding="utf-8")
    return {"status": "complete", "output": output_name, "usage": usage, "error": None}


def run_after_unit_html(
    tasks: tuple[LLMTaskConfig, ...], staged_units: Path
) -> LLMReport:
    """Run tasks sequentially, one call per unit, and record results in the manifest."""
    manifest_path = staged_units / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = LLMReport(tuple(task.name for task in tasks))
    for task in tasks:
        failed = report.failed_units.setdefault(task.name, [])
        for unit in manifest["units"]:
            if not unit.get("html"):
                continue
            unit_id = unit["id"]
            try:
                result = _run_unit(task, unit_id, staged_units / unit["html"])
            except Exception as error:
                # Log identifiers and the bounded error text, never payloads.
                message = (
                    str(error)
                    if isinstance(error, LLMResponseError)
                    else describe_error(error)
                )
                log.error(
                    "LLM task failed. Task=%s Unit=%s Error=%s",
                    task.name,
                    unit_id,
                    message,
                )
                result = {
                    "status": "failed",
                    "output": None,
                    "usage": None,
                    "error": message,
                }
                failed.append(unit_id)
                report.errors.append((task.name, unit_id, message))
            unit.setdefault("llm_tasks", {})[task.name] = result
    manifest["llm"] = {
        "status": "complete" if report.complete else "incomplete",
        "tasks": list(report.tasks),
        "failed_units": report.failed_units,
    }
    write_json_document(manifest, manifest_path)
    return report
