import os
import subprocess
from unittest.mock import Mock

import pytest

from pdfwtf import unpaper_run
from importlib import util
from pathlib import Path

_spec = util.spec_from_file_location(
    "unpaper_wrap", Path(__file__).resolve().parents[2] / "src/tools/unpaper_wrap.py"
)
unpaper_wrap = util.module_from_spec(_spec)
_spec.loader.exec_module(unpaper_wrap)


@pytest.mark.parametrize("code, available", [(0, True), (1, False)])
def test_availability_uses_exit_status(monkeypatch, code, available):
    monkeypatch.setattr(
        unpaper_run.subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], code, "unavailable")),
    )
    assert unpaper_run.get_unpaper_version()[0] is available


@pytest.mark.parametrize(
    "failure", [FileNotFoundError(), subprocess.TimeoutExpired("unpaper", 1)]
)
def test_missing_or_slow_tool_is_unavailable(monkeypatch, failure):
    monkeypatch.setattr(unpaper_run.subprocess, "run", Mock(side_effect=failure))
    assert unpaper_run.get_unpaper_version()[0] is False


def test_default_clean_arguments_are_retained():
    args = unpaper_run.get_unpaper_args(get_default=True, unpaper_ok=True)
    assert "--mask-scan-size" in args
    assert unpaper_run.get_unpaper_args(layout="none", unpaper_ok=True) is None


def test_command_failure_does_not_expose_output(monkeypatch, tmp_path):
    monkeypatch.setattr(
        unpaper_run.subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], 1, "private text")),
    )
    with pytest.raises(RuntimeError) as error:
        unpaper_run.run_unpaper_simple(
            tmp_path / "in.png", tmp_path / "out.pnm", tmp_path
        )
    assert "private text" not in str(error.value)


def test_same_directory_wrapper_uses_distinct_filenames(tmp_path):
    command = unpaper_wrap.build_command(
        [str(tmp_path / "in.png"), str(tmp_path / "out.pnm")]
    )
    assert command[-2:] == ["/data0/in.png", "/data0/out.pnm"]


def test_separate_directory_mounts_and_options(tmp_path):
    command = unpaper_wrap.build_command(
        [
            "--output-pages",
            "2",
            str(tmp_path / "in/page.png"),
            str(tmp_path / "out/page_%03d.pnm"),
        ]
    )
    assert command[-2:] == ["/data0/page.png", "/data1/page_%03d.pnm"]
    assert "--output-pages" in command
    assert command.count("-v") == 2


@pytest.mark.parametrize("args", [["--version"], ["--help"], ["in.png", "out.pnm"]])
def test_wrapper_propagates_docker_failure(monkeypatch, tmp_path, args):
    args = [
        str(tmp_path / arg) if arg.endswith((".png", ".pnm")) else arg for arg in args
    ]
    monkeypatch.setattr(unpaper_wrap.sys, "argv", ["wrapper", *args])
    monkeypatch.setattr(
        unpaper_wrap.subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], 7)),
    )
    with pytest.raises(SystemExit) as error:
        unpaper_wrap.main()
    assert error.value.code == 7


def test_wrapper_timeout_returns_failure(monkeypatch):
    monkeypatch.setattr(unpaper_wrap.sys, "argv", ["wrapper", "--version"])
    monkeypatch.setattr(
        unpaper_wrap.subprocess,
        "run",
        Mock(side_effect=subprocess.TimeoutExpired("docker", 1)),
    )
    with pytest.raises(SystemExit) as error:
        unpaper_wrap.main()
    assert error.value.code == 1


def test_wrapper_rejects_same_path(tmp_path):
    path = str(tmp_path / "page.png")
    with pytest.raises(ValueError, match="differ"):
        unpaper_wrap.build_command([path, path])


@pytest.mark.skipif(os.name != "nt", reason="Windows command wrapper")
@pytest.mark.parametrize("code", [0, 7])
def test_windows_batch_wrapper_propagates_python_status(configured_home, code):
    script = configured_home / "src/tools/unpaper_wrap.py"
    script.parent.mkdir(parents=True)
    script.write_text(f"raise SystemExit({code})\n")
    wrapper = Path(__file__).resolve().parents[2] / "unpaper.cmd"
    result = subprocess.run(
        [os.environ["COMSPEC"], "/d", "/c", str(wrapper), "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == code, result.stderr
