"""CLI command boundaries: options per command and staged processing."""

import json

from click.testing import CliRunner
import pymupdf as fitz
import pytest

from pdfwtf import cli
from pdfwtf.configuration import AppConfig
from test_container_partition import make_structured_pdf


@pytest.mark.parametrize(
    "option",
    [
        ["--get-text"],
        ["--get-meta"],
        ["--get-img"],
        ["--get-thumb"],
        ["--get-doi"],
        ["--get-html"],
        ["--get-unit-pdf"],
        ["--doc_type", "unit"],
        ["--plan", "plan.json"],
        ["--unit_type", "article"],
    ],
)
def test_enhance_rejects_moved_export_options(make_pdf, option):
    result = CliRunner().invoke(
        cli.main, ["enhance", str(make_pdf(["digital"])), *option]
    )
    assert result.exit_code == 2
    assert f"No such option '{option[0]}'" in result.output
    assert "'pdfwtf export'" in result.output


def test_enhance_rejects_no_pdf_out_with_hint(make_pdf):
    result = CliRunner().invoke(
        cli.main, ["enhance", str(make_pdf(["digital"])), "--no-pdf-out"]
    )
    assert result.exit_code == 2
    assert "Enhancement always writes the prepared PDF" in result.output


@pytest.mark.parametrize(
    "option", [["--lang", "eng"], ["--extract", "1"], ["--pdf_type", "scanned"]]
)
@pytest.mark.parametrize("command", ["analyze", "export", "partition"])
def test_other_commands_reject_enhancement_options(make_pdf, command, option):
    result = CliRunner().invoke(
        cli.main, [command, str(make_pdf(["digital"])), *option]
    )
    assert result.exit_code == 2
    assert "Use 'pdfwtf enhance'" in result.output


@pytest.mark.parametrize(
    "command, operation, options",
    [
        ("enhance", "enhance_pdf", ["--lang", "ces"]),
        ("partition", "partition_pdf", ["--doc_type", "book"]),
        ("export", "export_pdf", ["--get-text"]),
    ],
)
def test_cli_passes_config_to_its_own_operation(
    make_pdf, monkeypatch, command, operation, options
):
    received = []
    for name in ("enhance_pdf", "partition_pdf", "export_pdf"):
        monkeypatch.setattr(
            cli,
            name,
            lambda *args, _name=name, **kwargs: received.append((_name, kwargs)),
        )
    result = CliRunner().invoke(
        cli.main, [command, str(make_pdf(["digital"])), *options]
    )
    assert result.exit_code == 0, result.output
    assert [name for name, _kwargs in received] == [operation]
    kwargs = received[0][1]
    assert isinstance(kwargs["config"], AppConfig)
    assert kwargs["llm_tasks"] == ()


def test_staged_cli_workflow(tmp_path):
    source = make_structured_pdf(tmp_path / "issue.pdf", 3)
    output = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["enhance", str(source), "--outdir", str(output), "--pdf_type", "born-digital"],
    )
    assert result.exit_code == 0, result.output
    prepared = output / "issue.prepared.pdf"
    assert f"Prepared PDF :  {prepared}" in result.output
    result = runner.invoke(
        cli.main,
        ["partition", str(prepared), "--outdir", str(output), "--doc_type", "book"],
    )
    assert result.exit_code == 0, result.output
    partition = json.loads((output / "issue.prepared.partition.json").read_text())
    plan = {
        "schema_version": partition["schema_version"],
        "kind": "plan",
        "source": partition["source"],
        "document_type": partition["document_type"],
        "pages": [{**page, "selected": True} for page in partition["pages"]],
        "units": [
            {**item, "selected": True, "boundary_status": "confirmed"}
            for item in partition["units"]
        ],
    }
    plan_path = output / "issue.prepared.plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    result = runner.invoke(
        cli.main,
        [
            "export",
            str(prepared),
            "--outdir",
            str(output),
            "--plan",
            str(plan_path),
            "--get-html",
            "--get-unit-pdf",
        ],
    )
    assert result.exit_code == 0, result.output
    manifest = json.loads((output / "_units_issue.prepared/manifest.json").read_text())
    for item in manifest["units"]:
        with fitz.open(output / "_units_issue.prepared" / item["pdf"]) as unit_pdf:
            assert len(unit_pdf) >= 1
    # The original plan does not fit an enhanced PDF with other pages.
    result = runner.invoke(
        cli.main,
        ["enhance", str(source), "--outdir", str(tmp_path / "b"), "--remove", "3"],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        cli.main,
        [
            "export",
            str(tmp_path / "b/issue.prepared.pdf"),
            "--outdir",
            str(tmp_path / "b"),
            "--plan",
            str(plan_path),
            "--get-html",
        ],
    )
    assert result.exit_code == 1
    assert "The plan was created for a different PDF" in result.output


def test_export_alone_on_original_pdf(make_pdf, tmp_path):
    output = tmp_path / "out"
    result = CliRunner().invoke(
        cli.main,
        ["export", str(make_pdf(["digital"])), "--outdir", str(output), "--get-text"],
    )
    assert result.exit_code == 0, result.output
    assert (output / "input.txt").is_file()
    assert not list(output.glob("*.pdf"))
