"""Command-line entry point."""

import logging
from pathlib import Path
from typing import Any, Callable

import click

from pdfwtf.configuration import (
    COMMAND_ANALYZE,
    COMMAND_ENHANCE,
    COMMAND_EXPORT,
    COMMAND_PARTITION,
    AppConfig,
    ConfigurationError,
    load_config,
)
from pdfwtf.container_partition import UNIT_TYPES
from pdfwtf.diagnostics import format_page_ranges
from pdfwtf.llm_tasks import LLMReport, unavailable_reason
from pdfwtf.logging_utils import configure_logging
from pdfwtf.pipeline import (
    AnalysisResult,
    EnhanceResult,
    ProcessingError,
    analyze_pdf,
    enhance_pdf,
    export_pdf,
    partition_pdf,
)

# Help sections for each command. Options not listed appear under "Options".
_HELP_GROUPS: dict[str, dict[str, set[str]]] = {
    COMMAND_ANALYZE: {
        "Common options": {"output_dir", "input_path_prefix", "debug_flag", "help"},
        "LLM options": {"llm_tasks", "list_llm_tasks"},
    },
    COMMAND_ENHANCE: {
        "Common options": {"output_dir", "input_path_prefix", "debug_flag", "help"},
        "Page selection": {"extract_pages_str", "skip_pages_str"},
        "Enhancement options": {
            "pdf_type",
            "languages",
            "dpi",
            "ocrlib",
            "optimize",
            "layout",
            "output_pages",
            "pre_rotate",
            "remove_background_flag",
        },
        "LLM options": {"llm_tasks", "list_llm_tasks"},
    },
    COMMAND_PARTITION: {
        "Common options": {"output_dir", "input_path_prefix", "debug_flag", "help"},
        "Partition options": {"document_type"},
        "LLM options": {"llm_tasks", "list_llm_tasks"},
    },
    COMMAND_EXPORT: {
        "Common options": {"output_dir", "input_path_prefix", "debug_flag", "help"},
        "Unit options": {
            "document_type",
            "plan_path",
            "export_html_flag",
            "export_unit_pdfs_flag",
            "unit_types",
        },
        "Page derivatives": {
            "dpi",
            "get_doi_flag",
            "export_format",
            "export_images_flag",
            "export_json_flag",
            "export_texts_flag",
            "export_thumbs_flag",
        },
        "LLM options": {"llm_tasks", "list_llm_tasks"},
    },
}

# Hints for removed options that no current command owns.
_REMOVED_OPTIONS = {
    "--no-pdf-out": (
        "Enhancement always writes the prepared PDF. Use 'pdfwtf export' "
        "to write derivatives without a PDF."
    ),
}


class PdfCommand(click.Command):
    """Show grouped options, configured directories, and moved-option hints."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        try:
            return super().parse_args(ctx, args)
        except click.NoSuchOption as error:
            hint = _moved_option_hint(ctx, self.name, error.option_name)
            if hint is None:
                raise
            raise click.NoSuchOption(
                error.option_name, message=f"{error.message} {hint}", ctx=ctx
            ) from None

    def format_options(
        self, ctx: click.Context, formatter: click.HelpFormatter
    ) -> None:
        groups = _HELP_GROUPS.get(self.name or "")
        if groups is None:
            return super().format_options(ctx, formatter)
        params = self.get_params(ctx)
        grouped = set().union(*groups.values())
        sections = dict(groups)
        sections["Options"] = {p.name for p in params if p.name not in grouped}
        for title, names in sections.items():
            rows = [
                record
                for param in params
                if param.name in names
                and (record := param.get_help_record(ctx)) is not None
            ]
            if rows:
                with formatter.section(title):
                    formatter.write_dl(rows)

    def format_epilog(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        super().format_epilog(ctx, formatter)
        with formatter.section("Current directories"):
            try:
                config = ctx.obj if isinstance(ctx.obj, AppConfig) else load_config()
                _ = config.llm_tasks
            except ConfigurationError as error:
                formatter.write_text(f"Unavailable. {error}")
                return
            try:
                input_dir = str(config.input_dir)
            except ConfigurationError as error:
                input_dir = f"Unavailable. {error}"
            formatter.write_text(f"\b\nInput: {input_dir}")
            try:
                output = str(config.output_dir)
            except ConfigurationError as error:
                output = f"Unavailable. {error}"
            formatter.write_text(f"\b\nOutput (default): {output}")
            formatter.write_text(
                "Pass a filename or relative path below Input, or an absolute path. "
                "Use --outdir to change the output."
            )


def _moved_option_hint(
    ctx: click.Context, command: str | None, option: str
) -> str | None:
    """Name the commands that own an option that this command rejected."""
    if option in _REMOVED_OPTIONS:
        return _REMOVED_OPTIONS[option]
    group = ctx.parent.command if ctx.parent is not None else None
    if not isinstance(group, click.Group):
        return None
    owners = [
        name
        for name, other in sorted(group.commands.items())
        if name != command and any(option in param.opts for param in other.params)
    ]
    if not owners:
        return None
    names = " or ".join(f"'pdfwtf {name}'" for name in owners)
    return f"Use {names} for {option}."


def _list_llm_tasks(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    """Print configured LLM tasks for the current command and exit."""
    if not value or ctx.resilient_parsing:
        return
    try:
        config = ctx.obj if isinstance(ctx.obj, AppConfig) else load_config()
        tasks = [
            task for task in config.llm_tasks.values() if ctx.info_name in task.commands
        ]
    except ConfigurationError as error:
        raise click.ClickException(str(error)) from error
    reason = unavailable_reason(config)
    if not tasks:
        click.echo(f"No LLM tasks are configured for '{ctx.info_name}'.")
    for task in tasks:
        state = "available" if reason is None else f"unavailable: {reason}"
        click.echo(f"{task.name}  ({state})")
        click.echo(f"  {task.label('en')}")
        click.echo(
            f"  stage={task.stage} commands={','.join(task.commands)} "
            f"output=<unit-id>{task.output_suffix}"
        )
    ctx.exit(0)


def show_info(input_pdf: str, output_dir: str | Path | None, debug_flag: bool) -> None:
    click.echo(f"Input  :  {input_pdf}")
    if output_dir is not None:
        click.echo(f"Output :  {output_dir}")


def _resolve_input_pdf(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> str | None:
    """Resolve relative CLI inputs from the default input directory."""
    if value is None:
        return None
    path = Path(value)
    if not path.is_absolute():
        try:
            config = ctx.obj if isinstance(ctx.obj, AppConfig) else load_config()
            path = config.input_dir / path
        except ConfigurationError as error:
            raise click.ClickException(str(error)) from error
    return click.Path(exists=True, dir_okay=False, resolve_path=True).convert(
        str(path), param, ctx
    )


Decorator = Callable[[Callable[..., Any]], Callable[..., Any]]


def _options(*decorators: Decorator) -> Decorator:
    """Apply option decorators in the order that help lists them."""

    def apply(function: Callable[..., Any]) -> Callable[..., Any]:
        for decorator in reversed(decorators):
            function = decorator(function)
        return function

    return apply


_input_options = _options(
    click.argument(
        "input_pdf", required=True, type=click.Path(), callback=_resolve_input_pdf
    ),
    click.option(
        "--outdir",
        "output_dir",
        type=click.Path(file_okay=False, resolve_path=True),
        help="Write output files to this directory.",
    ),
    click.option(
        "--prefix",
        "input_path_prefix",
        type=click.Path(file_okay=False),
        help="Preserve input subdirectories below this path.",
    ),
)

_shared_options = _options(
    click.option(
        "--llm-task",
        "llm_tasks",
        multiple=True,
        metavar="NAME",
        help="Run a configured LLM task. Repeat to select several tasks.",
    ),
    click.option(
        "--list-llm-tasks",
        is_flag=True,
        is_eager=True,
        expose_value=False,
        callback=_list_llm_tasks,
        help="List configured LLM tasks for this action and exit.",
    ),
    click.option("--debug", "debug_flag", is_flag=True, help="Enable debug logging."),
)


def _dpi_option(help_text: str) -> Decorator:
    return click.option(
        "--dpi",
        default=300,
        type=click.IntRange(72, 1200),
        show_default=True,
        help=help_text,
    )


def _doc_type_option(help_text: str) -> Decorator:
    return click.option(
        "--doc_type",
        "document_type",
        type=click.Choice(
            ["unit", "journal-issue", "magazine-issue", "book", "proceedings", "auto"]
        ),
        help=help_text,
    )


def _execute(
    ctx: click.Context,
    operation: Callable[..., Any],
    input_pdf: str,
    output_dir: str | None,
    options: dict[str, Any],
) -> None:
    """Run one operation with the shared configuration and report the result."""
    config = ctx.obj if isinstance(ctx.obj, AppConfig) else load_config()
    debug_flag = bool(options.get("debug_flag"))
    # 1. Print input/output info FIRST before logging handlers pollute stdout
    show_info(input_pdf, output_dir, debug_flag)
    try:
        configure_logging(debug_flag, config.logs_dir / "pdf-wtf-log.txt")
        logging.getLogger(__name__).info("PDF processing started.")
        result = operation(input_pdf, output_dir, config=config, **options)
    except (ConfigurationError, ProcessingError, ValueError) as error:
        logging.getLogger(__name__).error("PDF processing failed.")
        raise click.ClickException(str(error)) from error
    except Exception as error:
        logging.getLogger(__name__).exception("PDF processing failed.")
        raise click.ClickException(
            "PDF processing failed. Check the input and required tools."
        ) from error

    llm_report = result.llm_report if isinstance(result, EnhanceResult) else result
    if isinstance(llm_report, LLMReport) and not llm_report.complete:
        logging.getLogger(__name__).error("LLM processing incomplete.")
        raise click.ClickException(
            f"{llm_report.summary()} Ordinary exports were written. "
            "See manifest.json for details."
        )
    if isinstance(result, EnhanceResult):
        click.echo(f"Prepared PDF :  {result.pdf}")
    if isinstance(result, AnalysisResult):
        _echo_analysis(result)
    logging.getLogger(__name__).info("PDF processing completed.")
    click.echo("Done!")


def _echo_analysis(result: AnalysisResult) -> None:
    """Print a concise summary of a diagnostic report."""
    summary = result.report["summary"]
    counts = ", ".join(
        f"{name}={count}"
        for name, count in summary["pages_by_composition"].items()
        if count
    )
    needs = ", ".join(
        f"{name}={count}" for name, count in summary["pages_by_ocr_need"].items()
    )
    click.echo(f"Pages        :  {result.report['document']['page_count']}")
    click.echo(f"Composition  :  {summary['document_composition']} ({counts})")
    click.echo(f"OCR need     :  {needs}")
    if summary["inspection"] != "complete":
        click.echo(
            "Inspection   :  incomplete. Pages not inspected: "
            f"{format_page_ranges(summary['error_pages'])}"
        )
    for item in summary["recommendations"]:
        click.echo(
            f"Advice       :  {item['action']} for pages {format_page_ranges(item['pages'])}"
        )
    if not summary["recommendations"]:
        click.echo("Advice       :  none")
    click.echo(f"Warnings     :  {len(result.report['warnings'])}")
    click.echo(f"Report       :  {result.report_path}")


class PdfGroup(click.Group, PdfCommand):
    """Show configured directories for the action selector."""


@click.group(cls=PdfGroup)
@click.pass_context
def main(ctx: click.Context) -> None:
    """Analyze, enhance, partition, or export one PDF. Select an action first.

    Each action is a separate step. Analysis, enhancement, and partitioning are
    optional. Export can run alone on any PDF.
    """
    if ctx.resilient_parsing:
        return
    try:
        config = load_config()
        _ = config.llm_tasks
        ctx.obj = config
    except ConfigurationError as error:
        ctx.obj = None
        if not ctx.invoked_subcommand:
            return
        if ctx.get_help_record(ctx) is None:
            raise click.ClickException(str(error)) from error


@main.command(COMMAND_ANALYZE, cls=PdfCommand)
@_input_options
@_shared_options
@click.pass_context
def analyze(
    ctx: click.Context, input_pdf: str, output_dir: str | None, **kwargs: Any
) -> None:
    """Diagnose PDF properties and preparation needs without changing the PDF.

    Write <stem>.analysis.json with per-page findings and advisory
    recommendations. Analysis does not run cleanup, OCR, partitioning, or
    export. The report is not a partition plan.
    """
    _execute(ctx, analyze_pdf, input_pdf, output_dir, kwargs)


@main.command(COMMAND_ENHANCE, cls=PdfCommand)
@_input_options
@click.option(
    "--extract",
    "extract_pages_str",
    default=None,
    help="Keep these input pages in the prepared PDF, for example 1-3,5.",
)
@click.option(
    "--remove",
    "skip_pages_str",
    default=None,
    help="Remove these input pages from the prepared PDF, for example 2,4-6.",
)
@click.option(
    "--pdf_type",
    type=click.Choice(["auto", "born-digital", "scanned"]),
    default="auto",
    show_default=True,
    help="Set the PDF type: auto detects scans, born-digital skips scan preparation "
    "and OCR, scanned forces scan processing.",
)
@click.option(
    "--lang",
    "languages",
    default="eng",
    show_default=True,
    help="Select OCR languages, for example eng+ces.",
)
@_dpi_option("Set the scan rendering resolution in DPI.")
@click.option(
    "--ocrlib",
    default="ocrmypdf",
    type=click.Choice(["ocrmypdf", "pymupdf"]),
    show_default=True,
    help="Select the OCR backend.",
)
@click.option(
    "--optimize",
    default=0,
    type=click.IntRange(0, 3),
    show_default=True,
    help="Set OCRmyPDF optimization: 0=off, 1=lossless, 2=lossy, 3=aggressive.",
)
@click.option(
    "--layout",
    default=None,
    type=click.Choice(["single", "double", "none"]),
    help="Select the unpaper page layout.",
)
@click.option(
    "--output-pages",
    default=None,
    type=click.Choice(["1", "2"]),
    help="Set unpaper output pages per input page.",
)
@click.option(
    "--pre-rotate",
    default=None,
    type=click.Choice([0, 90, 180, 270]),
    help="Rotate scanned pages before OCR, in degrees.",
)
@click.option(
    "--remove-bg",
    "remove_background_flag",
    is_flag=True,
    help="Crop dark page backgrounds.",
)
@_shared_options
@click.pass_context
def enhance(
    ctx: click.Context, input_pdf: str, output_dir: str | None, **kwargs: Any
) -> None:
    """Write a prepared PDF with scan cleanup, page selection, and OCR.

    The output is <stem>.prepared.pdf and a <stem>.enhance.json report.
    The input PDF is not changed. Run partition or export on the prepared PDF.
    """
    _execute(ctx, enhance_pdf, input_pdf, output_dir, kwargs)


@main.command(COMMAND_PARTITION, cls=PdfCommand)
@_input_options
@_doc_type_option("Describe the document structure.")
@_shared_options
@click.pass_context
def partition(
    ctx: click.Context, input_pdf: str, output_dir: str | None, **kwargs: Any
) -> None:
    """Write partition JSON from existing PDF text without OCR or unit exports.

    Partition the same PDF that you export, normally the prepared PDF.
    """
    _execute(ctx, partition_pdf, input_pdf, output_dir, kwargs)


@main.command(COMMAND_EXPORT, cls=PdfCommand)
@_input_options
@_doc_type_option(
    "Describe the document structure. The reviewed plan is authoritative."
)
@click.option(
    "--plan",
    "plan_path",
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
    help="Export units from a reviewed JSON plan for this PDF.",
)
@click.option(
    "--get-html",
    "export_html_flag",
    is_flag=True,
    help="Write an extracted UTF-8 HTML fragment for each selected unit.",
)
@click.option(
    "--get-html-normalized",
    "export_html_normalized_flag",
    is_flag=True,
    help="Write an editorially normalized UTF-8 HTML fragment for each selected unit.",
)
@click.option(
    "--get-unit-pdf",
    "export_unit_pdfs_flag",
    is_flag=True,
    help="Write a PDF for each selected unit. Requires --plan.",
)
@click.option(
    "--unit_type",
    "unit_types",
    type=click.Choice(sorted(UNIT_TYPES)),
    multiple=True,
    help="Select units of this type. Repeat to select several types.",
)
@_dpi_option("Set the page image resolution in DPI.")
@click.option(
    "--get-doi",
    "get_doi_flag",
    is_flag=True,
    help="Find DOI candidates and write JSON metadata.",
)
@click.option(
    "--get-format",
    "export_format",
    default="png",
    type=click.Choice(["png"]),
    show_default=True,
    help="Select the page image format.",
)
@click.option(
    "--get-img",
    "export_images_flag",
    is_flag=True,
    help="Export every page as an image.",
)
@click.option(
    "--get-meta",
    "export_json_flag",
    is_flag=True,
    help="Write JSON metadata.",
)
@click.option(
    "--get-text",
    "export_texts_flag",
    is_flag=True,
    help="Export page text and combined text.",
)
@click.option(
    "--get-thumb",
    "export_thumbs_flag",
    is_flag=True,
    help="Export page images and JPEG thumbnails.",
)
@_shared_options
@click.pass_context
def export(
    ctx: click.Context, input_pdf: str, output_dir: str | None, **kwargs: Any
) -> None:
    """Export selected derivatives from the supplied PDF without cleanup or OCR.

    Export can run alone on any PDF, or on a prepared PDF after enhancement.
    """
    _execute(ctx, export_pdf, input_pdf, output_dir, kwargs)


if __name__ == "__main__":
    main()
