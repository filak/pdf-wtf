import os
import pytest

from pdfwtf.configuration import (
    ConfigurationError,
    load_config,
    parse_value,
    validate_llm_tasks,
)
from pdfwtf.utils.common import get_output_dir, get_temp_dir


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("true", True),
        ("FALSE", False),
        ("NuLl", None),
        ("12", 12),
        ("2.5", 2.5),
        ("[True, false, NULL, 2]", [True, False, None, 2]),
        ('{"flag": TRUE, "label": "FALSE"}', {"flag": True, "label": "FALSE"}),
        ("plain string", "plain string"),
        ("50% complete", "50% complete"),
        ("'true'", "true"),
        ("[invalid", "[invalid"),
    ],
)
def test_safe_literal_parsing(raw, expected):
    assert parse_value(raw) == expected


def test_configuration_precedence(configured_home, monkeypatch):
    (configured_home / "instance/conf/pdf-wtf.ini").write_text(
        "[pdf-wtf]\ninput_dir = 'ini-in'\n"
        "output_dir = 'ini-out'\ntemp_dir = 'ini-temp'\n",
        encoding="utf-8",
    )
    (configured_home / ".env").write_text(
        "PDFWTF_INPUT_DIR=dotenv-in\n"
        "PDFWTF_OUTPUT_DIR=dotenv-out\nPDFWTF_TEMP_DIR=dotenv-temp\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PDFWTF_INPUT_DIR", "env-in")
    monkeypatch.setenv("PDFWTF_OUTPUT_DIR", "env-out")
    config = load_config()
    assert config.input_dir == configured_home / "env-in"
    assert config.output_dir == configured_home / "env-out"
    assert config.temp_dir == configured_home / "dotenv-temp"
    assert "PDFWTF_TEMP_DIR" not in os.environ


def test_empty_environment_is_unset(configured_home, monkeypatch):
    (configured_home / ".env").write_text("PDFWTF_OUTPUT_DIR=dotenv-out\n")
    monkeypatch.setenv("PDFWTF_OUTPUT_DIR", "")
    assert load_config().output_dir == configured_home / "dotenv-out"


def test_dotenv_cannot_supply_home(configured_home, monkeypatch):
    (configured_home / ".env").write_text(f"PDFWTF_HOME={configured_home}\n")
    monkeypatch.delenv("PDFWTF_HOME")
    with pytest.raises(ConfigurationError, match="PDFWTF_HOME is required"):
        load_config()


@pytest.mark.parametrize("home", ["", "relative/path", "missing-absolute"])
def test_invalid_home(configured_home, monkeypatch, home):
    if home == "missing-absolute":
        home = str(configured_home / "missing")
    monkeypatch.setenv("PDFWTF_HOME", home)
    with pytest.raises(ConfigurationError):
        load_config()


@pytest.mark.parametrize(
    "contents", ["no section", "[other]\n", "[pdf-wtf]\nx=1\nx=2\n"]
)
def test_invalid_ini_does_not_expose_values(configured_home, contents):
    secret = "private-value"
    path = configured_home / "instance/conf/pdf-wtf.ini"
    path.write_text(contents + f"\nsecret={secret}\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as error:
        load_config()
    assert secret not in str(error.value)


def test_missing_ini(configured_home):
    (configured_home / "instance/conf/pdf-wtf.ini").unlink()
    with pytest.raises(ConfigurationError, match="configuration"):
        load_config()


def test_no_interpolation_and_utf8(configured_home):
    (configured_home / "instance/conf/pdf-wtf.ini").write_text(
        "[pdf-wtf]\nlabel = 100% p\u0159\u00edli\u0161\n", encoding="utf-8"
    )
    assert load_config().values["label"] == "100% p\u0159\u00edli\u0161"


def test_only_required_settings_are_validated(configured_home, monkeypatch):
    monkeypatch.setenv("PDFWTF_TEMP_DIR", "false")
    config = load_config()
    assert config.input_dir == configured_home / "instance/_data/in"
    assert config.output_dir == configured_home / "instance/_data/out"
    with pytest.raises(ConfigurationError, match="PDFWTF_TEMP_DIR"):
        config.temp_dir


def test_runtime_directories_and_explicit_output(configured_home):
    assert get_output_dir() == configured_home / "instance/_data/out"
    assert get_temp_dir().is_dir()
    explicit = get_output_dir("exports/nested")
    assert explicit == configured_home / "exports/nested"
    assert explicit.is_dir()


def test_runtime_path_cannot_be_a_file(configured_home, monkeypatch):
    file = configured_home / "file"
    file.write_text("content")
    monkeypatch.setenv("PDFWTF_OUTPUT_DIR", str(file))
    with pytest.raises(ConfigurationError):
        get_output_dir()


def test_input_directory_cannot_be_a_file(configured_home, monkeypatch):
    file = configured_home / "file"
    file.write_text("content")
    monkeypatch.setenv("PDFWTF_INPUT_DIR", str(file))
    with pytest.raises(ConfigurationError, match="PDFWTF_INPUT_DIR"):
        load_config().input_dir


def _task(**changes):
    task = {
        "description": {"en": "Clean HTML output", "cs": "Vyčistit HTML výstup"},
        "commands": ["export"],
        "stage": "after_unit_html",
        "template": "wp_html",
        "output": {"suffix": ".clean.html"},
    }
    task.update(changes)
    return task


def test_valid_llm_task():
    validated = validate_llm_tasks({"clean_html": _task()})
    task = validated["clean_html"]
    assert task.commands == ("export",)
    assert task.stage == "after_unit_html"
    assert task.output_suffix == ".clean.html"
    assert task.label("cs") == "Vyčistit HTML výstup"
    assert task.label("de") == "Clean HTML output"
    assert "html" in task.prompt.lower()
    assert "html" in task.system.lower()


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"stage": "before_ocr"}, r"invalid stage 'before_ocr'"),
        ({"commands": ["partition"]}, r"does not support commands \['partition'\]"),
        # The HTML stage moved to export. Enhancement has no implemented stage.
        (
            {"commands": ["export", "enhance"]},
            r"does not support commands \['enhance'\]. Allowed values: \['export'\]",
        ),
        ({"commands": []}, r"'commands' must be a non-empty list"),
        ({"commands": ["export", "export"]}, r"'commands' must be a non-empty list"),
        ({"output": {"suffix": ".html"}}, "unsafe output suffix"),
        ({"output": {"suffix": "../x.html"}}, "unsafe output suffix"),
        ({"output": {"suffix": ".clean/x.html"}}, "unsafe output suffix"),
        ({"output": {"suffix": ".CLEAN.html"}}, "unsafe output suffix"),
        ({"output": {"suffix": ".clean.txt"}}, "unsafe output suffix"),
        ({"output": {"suffix": ".metadata.json"}}, "unsafe output suffix"),
        ({"output": {"suffix": ".clean.html", "dir": "x"}}, "only 'suffix'"),
        ({"description": {"cs": "Jen česky"}}, "must contain an 'en' entry"),
        ({"template": "../wp_html"}, "valid module name"),
        ({"template": "non_existent_template"}, "not found"),
    ],
)
def test_invalid_llm_task(changes, message):
    with pytest.raises(ConfigurationError, match=message):
        validate_llm_tasks({"clean_html": _task(**changes)})


def test_former_llm_task_keys_are_rejected():
    raw = _task(context="export", pipeline="export_html_flag", document_types=["unit"])
    with pytest.raises(ConfigurationError, match=r"Unknown: \['context'"):
        validate_llm_tasks({"clean_html": raw})


def test_llm_task_output_suffixes_must_be_unique():
    with pytest.raises(ConfigurationError, match="same output suffix"):
        validate_llm_tasks({"clean_html": _task(), "other": _task()})


def test_invalid_llm_task_name_is_rejected():
    with pytest.raises(ConfigurationError, match="LLM task name"):
        validate_llm_tasks({"Clean HTML": _task()})


def test_gemini_key_presence_uses_process_environment(llm_home, monkeypatch):
    config = load_config()
    assert config.gemini_api_key_loaded and config.gemini_api_key_present

    monkeypatch.delenv("GEMINI_API_KEY")
    (llm_home / ".env").write_text("GEMINI_API_KEY=dotenv-only\n", encoding="utf-8")
    config = load_config()
    assert config.gemini_api_key_loaded
    assert not config.gemini_api_key_present
    assert "GEMINI_API_KEY" not in os.environ

    (llm_home / ".env").unlink()
    config = load_config()
    assert not config.gemini_api_key_loaded and not config.gemini_api_key_present
