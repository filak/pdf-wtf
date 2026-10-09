import os

import pytest

from pdfwtf.configuration import ConfigurationError, load_config, parse_value
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
