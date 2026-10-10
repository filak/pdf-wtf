from click.testing import CliRunner
import shutil
import json
import pikepdf
import pytest

from pdfwtf import cli


def test_input_is_required():
    result = CliRunner().invoke(cli.main, ["enhance"])
    assert result.exit_code == 2
    assert "INPUT_PDF" in result.output
    assert "Done!" not in result.output


def test_processing_failure_is_nonzero_and_has_no_payload(make_pdf, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("private document content")

    monkeypatch.setattr(cli, "process_pdf", fail)
    result = CliRunner().invoke(cli.main, ["enhance", str(make_pdf(["digital"]))])
    assert result.exit_code == 1
    assert "Done!" not in result.output
    assert "private document content" not in result.output


def test_missing_configuration_is_reported(make_pdf, monkeypatch):
    monkeypatch.delenv("PDFWTF_HOME")
    result = CliRunner().invoke(cli.main, ["enhance", str(make_pdf(["digital"]))])
    assert result.exit_code == 1
    assert "PDFWTF_HOME is required" in result.output


@pytest.mark.parametrize("angle", ["0", "90", "180", "270"])
def test_cli_preserves_rotation_options(make_pdf, monkeypatch, angle):
    options = {}
    monkeypatch.setattr(cli, "process_pdf", lambda *a, **kw: options.update(kw))
    result = CliRunner().invoke(
        cli.main, ["enhance", str(make_pdf(["digital"])), "--pre-rotate", angle]
    )
    assert result.exit_code == 0, result.output
    assert options["pre_rotate"] == int(angle)
    assert "Done!" in result.output


def test_actual_cli_processing(make_pdf, tmp_path):
    source = make_pdf(["digital", "digital"])
    result = CliRunner().invoke(
        cli.main,
        [
            "enhance",
            str(source),
            "--outdir",
            str(tmp_path / "out"),
            "--extract",
            "2",
            "--get-text",
            "--debug",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "out/input.pdf").is_file()
    assert "DEBUG" in result.output
    assert "correlation=pdfwtf-" in result.output
    assert "This document has enough" not in result.output


def test_help_shows_current_directories_without_creating_them(configured_home):
    result = CliRunner().invoke(cli.main, ["enhance", "--help"])
    assert result.exit_code == 0, result.output
    assert f"Input: {configured_home / 'instance/_data/in'}" in result.output
    assert (
        f"Output (default): {configured_home / 'instance/_data/out'}" in result.output
    )
    assert not (configured_home / "instance/_data/in").exists()
    assert not (configured_home / "instance/_data/out").exists()


@pytest.mark.parametrize("source", ["ini", "dotenv", "environment"])
def test_help_resolves_output_overrides(configured_home, monkeypatch, source):
    (configured_home / "instance/conf/pdf-wtf.ini").write_text(
        '[pdf-wtf]\noutput_dir = "ini-out"\n', encoding="utf-8"
    )
    if source in ("dotenv", "environment"):
        (configured_home / ".env").write_text("PDFWTF_OUTPUT_DIR=dotenv-out\n")
    if source == "environment":
        monkeypatch.setenv("PDFWTF_OUTPUT_DIR", "env-out")
    expected = {"ini": "ini-out", "dotenv": "dotenv-out", "environment": "env-out"}[
        source
    ]
    result = CliRunner().invoke(cli.main, ["enhance", "--help"])
    assert result.exit_code == 0, result.output
    assert str(configured_home / expected) in result.output


def test_help_resolves_input_override(configured_home, monkeypatch):
    monkeypatch.setenv("PDFWTF_INPUT_DIR", "imports")
    result = CliRunner().invoke(cli.main, ["enhance", "--help"])
    assert result.exit_code == 0, result.output
    assert f"Input: {configured_home / 'imports'}" in result.output


def test_help_remains_available_without_home(monkeypatch):
    monkeypatch.delenv("PDFWTF_HOME")
    result = CliRunner().invoke(cli.main, ["enhance", "--help"])
    assert result.exit_code == 0, result.output
    assert "INPUT_PDF" in result.output
    assert "Unavailable. PDFWTF_HOME is required." in result.output


def test_help_handles_invalid_output_directory(configured_home, monkeypatch):
    monkeypatch.setenv("PDFWTF_OUTPUT_DIR", "false")
    result = CliRunner().invoke(cli.main, ["enhance", "--help"])
    assert result.exit_code == 0, result.output
    assert str(configured_home / "instance/_data/in") in result.output
    assert "PDFWTF_OUTPUT_DIR must be a directory path." in result.output


def test_help_handles_invalid_input_directory(configured_home, monkeypatch):
    monkeypatch.setenv("PDFWTF_INPUT_DIR", "false")
    result = CliRunner().invoke(cli.main, ["enhance", "--help"])
    assert result.exit_code == 0, result.output
    assert "PDFWTF_INPUT_DIR must be a directory path." in result.output
    assert str(configured_home / "instance/_data/out") in result.output


def test_help_does_not_cache_paths_between_invocations(configured_home, monkeypatch):
    monkeypatch.setenv("PDFWTF_OUTPUT_DIR", "first")
    first = CliRunner().invoke(cli.main, ["enhance", "--help"])
    monkeypatch.setenv("PDFWTF_OUTPUT_DIR", "second")
    second = CliRunner().invoke(cli.main, ["enhance", "--help"])
    assert str(configured_home / "first") in first.output
    assert str(configured_home / "second") in second.output


@pytest.mark.parametrize("option", ["--remove"])
def test_cli_removes_pages_after_selection(make_pdf, tmp_path, option):
    source = make_pdf(["digital"] * 4)
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
            "--extract",
            "2-4",
            option,
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    with pikepdf.open(output / source.name) as pdf:
        assert len(pdf.pages) == 2
    with pikepdf.open(source) as pdf:
        assert len(pdf.pages) == 4


def test_legacy_input_option_is_rejected(make_pdf, monkeypatch):
    inputs = []
    monkeypatch.setattr(cli, "process_pdf", lambda *a, **kw: inputs.append(a))
    source = make_pdf(["digital"])
    result = CliRunner().invoke(cli.main, ["enhance", "--infile", str(source)])
    assert result.exit_code == 2, result.output
    assert "No such option" in result.output
    assert inputs == []


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_input_argument_requires_an_existing_file(tmp_path, kind, monkeypatch):
    inputs = []
    monkeypatch.setattr(cli, "process_pdf", lambda *a, **kw: inputs.append(a))
    path = tmp_path / "missing.pdf" if kind == "missing" else tmp_path
    result = CliRunner().invoke(cli.main, ["enhance", str(path)])
    assert result.exit_code == 2, result.output
    assert inputs == []


def test_help_shows_positional_input():
    result = CliRunner().invoke(cli.main, ["enhance", "--help"])
    assert result.exit_code == 0, result.output
    assert "[OPTIONS] INPUT_PDF" in result.output
    assert "--infile" not in result.output


@pytest.mark.parametrize(
    "relative", ["born_digital.pdf", "reports/paper with spaces.pdf"]
)
def test_relative_input_uses_default_directory(
    make_pdf, configured_home, tmp_path, monkeypatch, relative
):
    source = make_pdf(
        ["digital"], name=str(configured_home / "instance/_data/in" / relative)
    )
    other = tmp_path / "working"
    other.mkdir()
    make_pdf(["digital"], name=str(other / relative))
    monkeypatch.chdir(other)
    inputs = []
    monkeypatch.setattr(cli, "process_pdf", lambda path, *a, **kw: inputs.append(path))
    result = CliRunner().invoke(cli.main, ["enhance", relative])
    assert result.exit_code == 0, result.output
    assert inputs == [str(source.resolve())]


def test_relative_input_uses_configured_directory(
    make_pdf, configured_home, monkeypatch
):
    input_dir = configured_home / "imports"
    source = make_pdf(["digital"], name=str(input_dir / "input.pdf"))
    monkeypatch.setenv("PDFWTF_INPUT_DIR", str(input_dir))
    inputs = []
    monkeypatch.setattr(cli, "process_pdf", lambda path, *a, **kw: inputs.append(path))
    result = CliRunner().invoke(cli.main, ["enhance", "input.pdf"])
    assert result.exit_code == 0, result.output
    assert inputs == [str(source.resolve())]


def test_relative_input_does_not_fall_back_to_working_directory(
    make_pdf, configured_home, tmp_path, monkeypatch
):
    make_pdf(["digital"])
    monkeypatch.chdir(tmp_path)
    inputs = []
    monkeypatch.setattr(cli, "process_pdf", lambda *a, **kw: inputs.append(a))
    result = CliRunner().invoke(cli.main, ["enhance", "input.pdf"])
    assert result.exit_code == 2, result.output
    assert repr(str(configured_home / "instance/_data/in/input.pdf")) in result.output
    assert inputs == []
    assert not (configured_home / "instance/_data/in").exists()


def test_absolute_input_works_outside_default_directory(make_pdf, monkeypatch):
    source = make_pdf(["digital"], name="outside input.pdf")
    inputs = []
    monkeypatch.setattr(cli, "process_pdf", lambda path, *a, **kw: inputs.append(path))
    result = CliRunner().invoke(cli.main, ["enhance", str(source)])
    assert result.exit_code == 0, result.output
    assert inputs == [str(source.resolve())]


def test_relative_input_reports_missing_configuration(monkeypatch):
    monkeypatch.delenv("PDFWTF_HOME")
    result = CliRunner().invoke(cli.main, ["enhance", "born_digital.pdf"])
    assert result.exit_code == 1, result.output
    assert "PDFWTF_HOME is required" in result.output


def test_relative_input_processes_pdf_and_exports(make_pdf, configured_home):
    source = make_pdf(
        ["digital"],
        name=str(configured_home / "instance/_data/in/born_digital.pdf"),
    )
    result = CliRunner().invoke(
        cli.main,
        [
            "enhance",
            source.name,
            "--pdf_type",
            "born-digital",
            "--get-doi",
            "--get-img",
            "--get-text",
            "--get-thumb",
            "--dpi",
            "72",
        ],
    )
    assert result.exit_code == 0, result.output
    output = configured_home / "instance/_data/out"
    with pikepdf.open(output / source.name) as pdf:
        assert len(pdf.pages) == 1
    assert (output / "born_digital.meta.json").is_file()
    metadata = json.loads((output / "born_digital.meta.json").read_text())
    assert metadata["input"] == str(source.resolve())
    assert metadata["output"] == str((output / source.name).resolve())
    assert (output / "born_digital.txt").is_file()
    assert list(output.rglob("*.png"))
    assert list(output.rglob("*.jpg"))


@pytest.mark.parametrize("level", [None, 0, 1, 2, 3])
def test_cli_forwards_optimization_to_ocrmypdf(make_pdf, tmp_path, monkeypatch, level):
    from pdfwtf import pipeline

    options = {}

    def fake_ocr(source, destination, **kwargs):
        options.update(kwargs)
        shutil.copy2(source, destination)

    monkeypatch.setattr(pipeline, "is_scanned_or_hybrid", lambda path: True)
    monkeypatch.setattr(
        pipeline, "_process_scanned", lambda *a, **kw: (tmp_path, False)
    )
    monkeypatch.setattr(pipeline.ocrmypdf, "ocr", fake_ocr)
    args = [str(make_pdf(["scan"])), "--outdir", str(tmp_path / "out")]
    if level is not None:
        args += ["--optimize", str(level)]
    result = CliRunner().invoke(cli.main, ["enhance", *(args)])
    assert result.exit_code == 0, result.output
    assert options["optimize"] == (0 if level is None else level)
    assert (tmp_path / "out/input.pdf").is_file()


@pytest.mark.parametrize("level", ["-1", "4", "1.5", "invalid"])
def test_cli_rejects_invalid_optimization(make_pdf, monkeypatch, level):
    calls = []
    monkeypatch.setattr(cli, "process_pdf", lambda *a, **kw: calls.append(a))
    result = CliRunner().invoke(
        cli.main, ["enhance", str(make_pdf(["digital"])), "--optimize", level]
    )
    assert result.exit_code == 2, result.output
    assert "--optimize" in result.output
    assert calls == []


def test_cli_rejects_old_get_json_flag(make_pdf):
    result = CliRunner().invoke(
        cli.main, ["enhance", str(make_pdf(["digital"])), "--get-json"]
    )
    assert result.exit_code == 2, result.output
    assert "No such option '--get-json'" in result.output


@pytest.mark.parametrize("no_pdf", [False, True])
@pytest.mark.parametrize(
    "flags",
    [
        ["--get-meta"],
        ["--get-doi"],
        ["--get-img"],
        ["--get-text"],
        ["--get-thumb"],
        ["--get-meta", "--get-doi", "--get-img", "--get-text", "--get-thumb"],
    ],
)
def test_cli_output_flows(make_pdf, tmp_path, configured_home, no_pdf, flags):
    source = make_pdf(["digital"])
    import pymupdf as fitz

    with fitz.open(source) as doc:
        doc[0].insert_text((20, 100), "https://doi.org/10.1234/example")
        doc.saveIncr()
    original = source.read_bytes()
    output = tmp_path / "out"
    args = [
        str(source),
        "--outdir",
        str(output),
        "--dpi",
        "72",
        "--pdf_type",
        "born-digital",
    ]
    if no_pdf:
        args += ["--no-pdf-out"]
    result = CliRunner().invoke(cli.main, ["enhance", *(args + flags)])
    assert result.exit_code == 0, result.output
    expected = set()
    if not no_pdf:
        expected.add("input.pdf")
    if "--get-meta" in flags or "--get-doi" in flags:
        expected.add("input.meta.json")
        metadata = json.loads((output / "input.meta.json").read_text())
        assert metadata == {
            "input": str(source.resolve()),
            "output": None if no_pdf else str((output / source.name).resolve()),
            "doi": ["10.1234/example"] if "--get-doi" in flags else [],
            "pages": {"page_001": {"index": 1, "pgn": None}},
        }
    if "--get-img" in flags or "--get-thumb" in flags:
        expected.add("_images_input")
        assert list((output / "_images_input").glob("*.png"))
    if "--get-thumb" in flags:
        expected.add("_thumbs_input")
        assert list((output / "_thumbs_input").glob("*.jpg"))
    if "--get-text" in flags:
        expected.update({"_texts_input", "input.txt"})
        assert list((output / "_texts_input").glob("*.txt"))
    assert {p.name for p in output.iterdir()} == expected
    assert source.read_bytes() == original
    assert not list((configured_home / "instance/temp").iterdir())


def test_cli_rejects_no_output(make_pdf, tmp_path):
    output = tmp_path / "out"
    result = CliRunner().invoke(
        cli.main,
        [
            "enhance",
            str(make_pdf(["digital"])),
            "--outdir",
            str(output),
            "--no-pdf-out",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "No output selected." in result.output
    assert "Done!" not in result.output
    assert not output.exists()


@pytest.mark.parametrize("action", ["analyze", "enhance", "export"])
def test_action_help_has_input_and_current_directories(action, configured_home):
    result = CliRunner().invoke(cli.main, [action, "--help"])
    assert result.exit_code == 0
    assert "INPUT_PDF" in result.output
    assert "Current directories" in result.output


def test_root_help_lists_actions_and_old_syntax_is_rejected(make_pdf):
    help_result = CliRunner().invoke(cli.main, ["--help"])
    assert help_result.exit_code == 0
    assert all(
        action in help_result.output for action in ("analyze", "enhance", "export")
    )
    result = CliRunner().invoke(cli.main, [str(make_pdf(["digital"]))])
    assert result.exit_code == 2
    assert "No such command" in result.output


@pytest.mark.parametrize(
    "action, flags",
    [
        ("analyze", ["--analysis"]),
        ("enhance", ["--analysis"]),
        ("analyze", ["--get-text"]),
        ("analyze", ["--optimize", "1"]),
        ("export", ["--optimize", "1"]),
        ("export", ["--lang", "ces"]),
        ("export", ["--remove-bg"]),
        ("export", ["--pdf_type", "born-digital"]),
        ("export", ["--no-pdf-out"]),
    ],
)
def test_actions_reject_irrelevant_options(make_pdf, monkeypatch, action, flags):
    calls = []
    monkeypatch.setattr(cli, "process_pdf", lambda *args, **kwargs: calls.append(args))
    result = CliRunner().invoke(cli.main, [action, str(make_pdf(["digital"])), *flags])
    assert result.exit_code == 2
    assert "No such option" in result.output
    assert not calls


def test_export_reads_scanned_input_without_ocr(make_pdf, tmp_path, monkeypatch):
    from pdfwtf import pipeline

    def forbidden(*args, **kwargs):
        pytest.fail("Export must not run scan detection, preparation, or OCR.")

    monkeypatch.setattr(pipeline, "is_scanned_or_hybrid", forbidden)
    monkeypatch.setattr(pipeline, "_process_scanned", forbidden)
    monkeypatch.setattr(pipeline, "run_ocr", forbidden)
    source = make_pdf(["scan"])
    original = source.read_bytes()
    output = tmp_path / "export"
    result = CliRunner().invoke(
        cli.main,
        [
            "export",
            str(source),
            "--outdir",
            str(output),
            "--dpi",
            "72",
            "--get-meta",
            "--get-text",
            "--get-thumb",
        ],
    )
    assert result.exit_code == 0, result.output
    assert not (output / source.name).exists()
    assert json.loads((output / "input.meta.json").read_text())["output"] is None
    assert list((output / "_images_input").glob("*.png"))
    assert list((output / "_thumbs_input").glob("*.jpg"))
    assert source.read_bytes() == original


def test_export_requires_explicit_output(make_pdf, tmp_path):
    output = tmp_path / "out"
    result = CliRunner().invoke(
        cli.main, ["export", str(make_pdf(["digital"])), "--outdir", str(output)]
    )
    assert result.exit_code == 1
    assert "No output selected" in result.output
    assert not output.exists()


@pytest.mark.parametrize("action", ["analyze", "enhance", "export"])
def test_document_type_flag_name(action):
    runner = CliRunner()
    result = runner.invoke(cli.main, [action, "--help"])
    assert result.exit_code == 0
    assert "--doc_type" in result.output
    assert "--doctype" not in result.output
    legacy = runner.invoke(cli.main, [action, "--doctype", "book"])
    assert legacy.exit_code == 2
    assert "No such option" in legacy.output


def test_analysis_doc_type_book(make_pdf, tmp_path):
    source = make_pdf(["digital"])
    output = tmp_path / "analysis"
    result = CliRunner().invoke(
        cli.main,
        ["analyze", str(source), "--doc_type", "book", "--outdir", str(output)],
    )
    assert result.exit_code == 0, result.output
    analysis = json.loads((output / "input.analysis.json").read_text("utf-8"))
    assert analysis["document_type"] == "book"
    assert all(unit["type"] == "chapter" for unit in analysis["units"])


@pytest.mark.parametrize("pdf_type", [None, "auto", "born-digital", "scanned"])
def test_cli_pdf_type_values(make_pdf, monkeypatch, pdf_type):
    received = {}
    monkeypatch.setattr(
        cli, "process_pdf", lambda *args, **kwargs: received.update(kwargs)
    )
    args = ["enhance", str(make_pdf(["digital"]))]
    if pdf_type is not None:
        args.extend(["--pdf_type", pdf_type])
    result = CliRunner().invoke(cli.main, args)
    assert result.exit_code == 0, result.output
    assert received["pdf_type"] == (pdf_type or "auto")
    assert "born_digital_flag" not in received


@pytest.mark.parametrize("options", [["--pdf_type", "invalid"], ["--born-digital"]])
def test_cli_rejects_invalid_or_obsolete_pdf_type(make_pdf, monkeypatch, options):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid options must not start processing.")

    monkeypatch.setattr(cli, "process_pdf", unexpected)
    result = CliRunner().invoke(
        cli.main, ["enhance", str(make_pdf(["digital"])), *options]
    )
    assert result.exit_code == 2
