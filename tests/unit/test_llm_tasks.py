"""Tests for configured LLM tasks. Gemini calls are always mocked."""

import json

from click.testing import CliRunner
import pytest

from pdfwtf import cli, llm_tasks
from pdfwtf.configuration import load_config
from pdfwtf.llm_tasks import (
    LLMResponseError,
    LLMTaskError,
    MAX_ERROR_CHARS,
    available_tasks,
    describe_error,
    parse_response,
    run_after_unit_html,
    validate_task_selection,
)
from test_container_analysis import make_structured_pdf, reviewed_plan, unit

# The conftest guard replaces this wrapper in every test.
_REAL_CALL_GEMINI = llm_tasks._call_gemini


def _staged_units(tmp_path, unit_ids=("unit-001", "unit-002")):
    staged = tmp_path / "staged"
    staged.mkdir()
    units = []
    for unit_id in unit_ids:
        (staged / f"{unit_id}.html").write_text(f"<p>{unit_id}</p>", encoding="utf-8")
        units.append({"id": unit_id, "html": f"{unit_id}.html", "metadata": None})
    (staged / "manifest.json").write_text(
        json.dumps({"kind": "container-manifest", "units": units}), encoding="utf-8"
    )
    return staged


def _manifest(directory):
    return json.loads((directory / "manifest.json").read_text(encoding="utf-8"))


# Selection and availability


def test_tasks_are_available_with_valid_config_and_key(llm_home):
    config = load_config()
    assert [task.name for task in available_tasks(config, "export")] == ["clean_html"]
    assert available_tasks(config, "analyze") == []


def test_tasks_are_unavailable_without_key(llm_home, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    assert available_tasks(load_config(), "export") == []


@pytest.mark.parametrize(
    "names, command, get_html, message",
    [
        (["missing"], "export", True, "Unknown LLM task 'missing'"),
        (["clean_html"], "analyze", True, "does not support the 'analyze' command"),
        (["clean_html"], "export", False, "requires --get-html"),
    ],
)
def test_selection_rejects_invalid_requests(
    llm_home, names, command, get_html, message
):
    with pytest.raises(LLMTaskError, match=message):
        validate_task_selection(load_config(), names, command, get_html=get_html)


def test_selection_rejects_missing_key(llm_home, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(LLMTaskError, match="GEMINI_API_KEY is not set"):
        validate_task_selection(load_config(), ["clean_html"], "export", get_html=True)


def test_selection_rejects_key_only_in_dotenv(llm_home, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    (llm_home / ".env").write_text("GEMINI_API_KEY=dotenv-only\n", encoding="utf-8")
    with pytest.raises(LLMTaskError, match="only in .env"):
        validate_task_selection(load_config(), ["clean_html"], "export", get_html=True)


def test_selection_removes_duplicates_and_allows_no_tasks(llm_home):
    config = load_config()
    selected = validate_task_selection(
        config, ["clean_html", "clean_html"], "enhance", get_html=True
    )
    assert [task.name for task in selected] == ["clean_html"]
    assert validate_task_selection(config, [], "export", get_html=False) == ()


# Response handling


def test_response_is_separated_from_usage():
    html, usage = parse_response(
        {"response": "  <h1>Title</h1>\n", "usage": {"total_token_count": 3}}
    )
    assert html == "<h1>Title</h1>"
    assert usage == {"total_token_count": 3}


@pytest.mark.parametrize(
    "envelope",
    [
        "<h1>Text mode</h1>",
        {"usage": {}},
        {"response": 3, "usage": {}},
        {"response": "<p>x</p>", "usage": "many"},
        {"response": "   ", "usage": {}},
        {"response": "```html\n<p>x</p>\n```", "usage": {}},
        {"response": "Plain text only", "usage": {}},
    ],
)
def test_malformed_responses_are_rejected(envelope):
    with pytest.raises(LLMResponseError):
        parse_response(envelope)


def test_gemini_call_uses_json_response_format(llm_home, monkeypatch, tmp_path):
    import gemini_call

    received = {}

    def fake_call(prompt, **kwargs):
        received.update(kwargs, prompt=prompt)
        return {"response": "<p>ok</p>", "usage": {}}

    monkeypatch.setattr(gemini_call, "call", fake_call)
    task = load_config().llm_tasks["clean_html"]
    html_path = tmp_path / "unit.html"
    _REAL_CALL_GEMINI(task, html_path)
    assert received["response_format"] == "json"
    assert received["files"] == [str(html_path)]
    assert received["system"] == task.system
    assert "api_key" not in received


# Runner


def test_runner_writes_results_and_preserves_original(llm_home, fake_gemini, tmp_path):
    calls, _behaviour = fake_gemini
    staged = _staged_units(tmp_path)
    tasks = validate_task_selection(
        load_config(), ["clean_html"], "export", get_html=True
    )
    report = run_after_unit_html(tasks, staged)
    assert report.complete
    assert [path.name for _name, path in calls] == ["unit-001.html", "unit-002.html"]
    assert (staged / "unit-001.html").read_text(encoding="utf-8") == "<p>unit-001</p>"
    assert (staged / "unit-001.clean.html").read_text(encoding="utf-8") == (
        "<h1>Clean unit-001</h1>\n"
    )
    manifest = _manifest(staged)
    assert manifest["llm"] == {
        "status": "complete",
        "tasks": ["clean_html"],
        "failed_units": {"clean_html": []},
    }
    assert manifest["units"][0]["llm_tasks"]["clean_html"] == {
        "status": "complete",
        "output": "unit-001.clean.html",
        "usage": {"prompt_token_count": 10, "total_token_count": 15},
        "error": None,
    }


def test_runner_records_partial_failure(llm_home, fake_gemini, tmp_path, caplog):
    calls, behaviour = fake_gemini
    behaviour["fail_units"] = {"unit-001"}
    staged = _staged_units(tmp_path)
    tasks = validate_task_selection(
        load_config(), ["clean_html"], "export", get_html=True
    )
    report = run_after_unit_html(tasks, staged)
    assert not report.complete
    assert "clean_html failed for unit-001" in report.summary()
    assert report.errors == [
        ("clean_html", "unit-001", "RuntimeError: Gemini API Error: simulated")
    ]
    assert len(calls) == 2
    assert not (staged / "unit-001.clean.html").exists()
    assert (staged / "unit-002.clean.html").is_file()
    assert (staged / "unit-001.html").is_file()
    manifest = _manifest(staged)
    assert manifest["llm"]["status"] == "incomplete"
    assert manifest["llm"]["failed_units"] == {"clean_html": ["unit-001"]}
    failed = manifest["units"][0]["llm_tasks"]["clean_html"]
    assert failed["status"] == "failed"
    assert failed["output"] is None
    assert failed["error"] == "RuntimeError: Gemini API Error: simulated"
    assert any(
        "Unit=unit-001 Error=RuntimeError: Gemini API Error: simulated"
        in record.getMessage()
        for record in caplog.records
    )


def test_describe_error_is_bounded_one_line_without_api_key():
    key = "AIza" + "x" * 35
    text = describe_error(RuntimeError(f"Gemini API Error: 503\nkey={key}"))
    assert text == "RuntimeError: Gemini API Error: 503 key=[REDACTED]"
    long_text = describe_error(RuntimeError("y" * (MAX_ERROR_CHARS + 50)))
    assert long_text == "RuntimeError: " + "y" * MAX_ERROR_CHARS + "…"


def test_runner_rejects_malformed_response(llm_home, fake_gemini, tmp_path):
    _calls, behaviour = fake_gemini
    behaviour["response"] = {"response": "", "usage": {}}
    staged = _staged_units(tmp_path, ("unit-001",))
    tasks = validate_task_selection(
        load_config(), ["clean_html"], "export", get_html=True
    )
    assert not run_after_unit_html(tasks, staged).complete
    entry = _manifest(staged)["units"][0]["llm_tasks"]["clean_html"]
    assert entry["error"] == "The response HTML is empty."


def test_runner_rejects_oversized_input_without_call(
    llm_home, fake_gemini, tmp_path, monkeypatch
):
    calls, _behaviour = fake_gemini
    monkeypatch.setattr(llm_tasks, "MAX_LLM_INPUT_BYTES", 5)
    staged = _staged_units(tmp_path, ("unit-001",))
    tasks = validate_task_selection(
        load_config(), ["clean_html"], "export", get_html=True
    )
    assert not run_after_unit_html(tasks, staged).complete
    assert calls == []
    entry = _manifest(staged)["units"][0]["llm_tasks"]["clean_html"]
    assert "larger than 5 bytes" in entry["error"]


# CLI


def _plan_args(tmp_path):
    source = make_structured_pdf(tmp_path / "input.pdf", page_count=2)
    plan = reviewed_plan(source, [unit("first", 1, 1), unit("second", 2, 2)])
    plan_path = tmp_path / "approved.plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    output = tmp_path / "export"
    args = ["export", str(source), "--outdir", str(output), "--plan", str(plan_path)]
    return args, output / "_units_input"


def test_cli_export_runs_selected_task(llm_home, fake_gemini, tmp_path):
    calls, _behaviour = fake_gemini
    args, units = _plan_args(tmp_path)
    result = CliRunner().invoke(
        cli.main, [*args, "--get-html", "--llm-task", "clean_html"]
    )
    assert result.exit_code == 0, result.output
    assert len(calls) == 2
    assert (units / "first.html").is_file()
    assert (units / "first.clean.html").is_file()
    manifest = _manifest(units)
    assert manifest["schema_version"] == "1.3"
    assert manifest["llm"]["status"] == "complete"


def test_cli_enhance_accepts_task(llm_home, fake_gemini, tmp_path):
    source = make_structured_pdf(tmp_path / "input.pdf", page_count=1)
    output = tmp_path / "out"
    result = CliRunner().invoke(
        cli.main,
        [
            "enhance",
            str(source),
            "--outdir",
            str(output),
            "--pdf_type",
            "born-digital",
            "--no-pdf-out",
            "--doc_type",
            "unit",
            "--get-html",
            "--llm-task",
            "clean_html",
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(fake_gemini[0]) == 1
    assert list((output / "_units_input").glob("*.clean.html"))


@pytest.mark.parametrize(
    "extra, message",
    [
        (["--llm-task", "clean_html"], "requires --get-html"),
        (["--get-html", "--llm-task", "unknown"], "Unknown LLM task 'unknown'"),
    ],
)
def test_cli_rejects_invalid_task_before_processing(
    llm_home, fake_gemini, tmp_path, extra, message
):
    args, units = _plan_args(tmp_path)
    result = CliRunner().invoke(cli.main, [*args, *extra])
    assert result.exit_code == 1
    assert message in result.output
    assert fake_gemini[0] == []
    assert not units.exists()


def test_cli_rejects_missing_key_before_processing(
    llm_home, fake_gemini, tmp_path, monkeypatch
):
    monkeypatch.delenv("GEMINI_API_KEY")
    args, units = _plan_args(tmp_path)
    result = CliRunner().invoke(
        cli.main, [*args, "--get-html", "--llm-task", "clean_html"]
    )
    assert result.exit_code == 1
    assert "GEMINI_API_KEY is not set" in result.output
    assert fake_gemini[0] == []
    assert not units.exists()


def test_cli_partial_failure_keeps_exports_and_fails(llm_home, fake_gemini, tmp_path):
    fake_gemini[1]["fail_units"] = {"first"}
    args, units = _plan_args(tmp_path)
    result = CliRunner().invoke(
        cli.main, [*args, "--get-html", "--llm-task", "clean_html"]
    )
    assert result.exit_code == 1
    assert "LLM processing incomplete: clean_html failed for first" in result.output
    assert "Done!" not in result.output
    assert (units / "first.html").is_file()
    assert (units / "second.clean.html").is_file()
    assert _manifest(units)["llm"]["failed_units"] == {"clean_html": ["first"]}


def test_cli_ordinary_export_works_without_key(tmp_path):
    args, units = _plan_args(tmp_path)
    result = CliRunner().invoke(cli.main, [*args, "--get-html"])
    assert result.exit_code == 0, result.output
    manifest = _manifest(units)
    assert manifest["schema_version"] == "1.3"
    assert "llm" not in manifest
    assert not list(units.glob("*.clean.html"))


def test_cli_lists_tasks_without_input(llm_home, monkeypatch):
    result = CliRunner().invoke(cli.main, ["export", "--list-llm-tasks"])
    assert result.exit_code == 0, result.output
    assert "clean_html  (available)" in result.output
    assert "Clean HTML output" in result.output
    monkeypatch.delenv("GEMINI_API_KEY")
    result = CliRunner().invoke(cli.main, ["enhance", "--list-llm-tasks"])
    assert result.exit_code == 0, result.output
    assert "unavailable: GEMINI_API_KEY is not set." in result.output


def test_cli_analyze_has_no_llm_options():
    result = CliRunner().invoke(cli.main, ["analyze", "--llm-task", "clean_html"])
    assert result.exit_code == 2
    assert "No such option" in result.output
