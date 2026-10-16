# pdf-wtf project

## Purpose

pdf-wtf processes PDF files and extracts document data. It supports documents
with existing text, scanned pages, and mixed text and image content. The current
product is a Python package with a command-line interface. It is in development.

## Primary goals

- Select pages before processing and remove selected pages after processing.
- Prepare scanned pages for optical character recognition (OCR).
- Produce PDF output with recognized text when OCR is required.
- Export page images, thumbnails, and UTF-8 text.
- Extract Digital Object Identifier (DOI) candidates from document text.
- Provide reusable processing functions and a command-line entry point.

## Scope and domain concepts

The `pdfwtf` command processes one input PDF per invocation. Options select
page ranges, OCR backend, languages, resolution, scan cleanup, and exports.
CLI page numbers start at 1.

Select an action before the input PDF: `pdfwtf ACTION INPUT_PDF`.
The actions are `analyze`, `enhance`, `partition`, and `export`. Both
arguments are required.
Each action is a separate step with one responsibility:

| Action | Responsibility | Output |
| --- | --- | --- |
| `analyze` | Diagnose PDF properties and preparation needs. Read-only. | Diagnostic report `<stem>.analysis.json` |
| `enhance` | Prepare or repair physical pages: cleanup, rotation, splitting, page selection and removal, and OCR. | Prepared PDF `<stem>.prepared.pdf` and report `<stem>.enhance.json` |
| `partition` | Identify logical units from existing PDF content. | Reviewable partition `<stem>.partition.json` |
| `export` | Produce selected derivatives from the supplied PDF, optionally with a reviewed plan. | Exported files |

`analyze`, `enhance`, and `partition` are optional. `export` is a complete step on any
PDF, original or prepared. No action checks that an earlier action ran.
The only rule between actions is plan compatibility: a plan must match the
exact PDF that is exported. Enhance once, then repeat partitioning or export
on the prepared PDF without another enhancement.

Analysis, partitioning, and export never run cleanup or OCR. Analysis
never changes the PDF, and its report is not a partition plan. See
`specs/ANALYSIS.md` for the report contract, the classification rules,
and the exit behavior. Export can render page
images and thumbnails and extract unit PDFs. Physical page splitting is part
of enhancement. Logical units are part of partitioning and export.
There is no action that chains all steps.

Use `pdfwtf ACTION --help` for action-specific options. Each action rejects
options of another action, and the error names the owning action.
The `--analysis` flag and the former command syntax without an action are
not supported. Before version 1.4, `analyze` wrote what `partition` writes
now. The current `analyze` action is the read-only diagnostic.

These former `enhance` options are removed: `--no-pdf-out`, `--plan`,
`--doc_type`, `--unit_type`, `--get-doi`, `--get-format`, `--get-html`,
`--get-img`, `--get-meta`, `--get-text`, and `--get-thumb`. The export
options are available on `export`. Enhancement always writes the prepared PDF.
The input argument is required. The input file must exist. Filenames and
relative file paths resolve from the configured input directory. Its default is
`PDFWTF_HOME/instance/_data/in`. Absolute file paths select files directly.
The CLI does not search the working directory.

`enhance --extract` selects input pages for the prepared PDF. `--remove`
removes input pages from that PDF. Both options use absolute input page
indices starting at 1. Removal takes precedence when an index occurs in both
lists. Export of a prepared PDF uses the page indices of the prepared PDF.

The `--pdf_type` option accepts `auto`, `born-digital`, and `scanned`.
The default is `auto`, which detects scanned or hybrid content.
Use `scanned` to force scan preparation and OCR.
Use `born-digital` to override automatic scan detection. It bypasses
scan preparation, page rasterization for processing, and OCR, even when the
input has no extractable text. Page selection and removal still apply. The
prepared PDF is then a copy of the selected pages.

Export always behaves like this mode. Text exports contain only existing
extractable text.
Per-page text files are empty when the corresponding pages have no extractable
text. The combined text file still contains page headings. DOI extraction
returns no candidates when the first exported page has no extractable text.
The option cannot be combined with background removal, scan layouts
`single` or `double`, page splitting, or pre-rotation.

The `--optimize` option sets OCRmyPDF optimization from 0 to 3.
The default is 0, which disables optional optimization. Level 1 uses lossless
optimization. Levels 2 and 3 permit lossy optimization. Level 3 is more aggressive.
This option applies only when OCRmyPDF runs. It does not change PyMuPDF OCR,
born-digital processing, or image and thumbnail exports.
Levels 2 and 3 can show a warning if the optional `jbig2` executable is
unavailable.

A text PDF contains extractable text. A scanned PDF requires OCR to obtain
text. A hybrid PDF contains mixed text and image content. Scan preparation
includes orientation correction and optional dark-background cropping.
The unpaper integration supports layout, page splitting, and pre-rotation.

A successful `enhance` saves the prepared PDF. It never modifies the
original PDF, and the prepared PDF must not be the input file. It publishes
the prepared PDF atomically, so a failure leaves an earlier prepared PDF
unchanged. OCR runs at most once for each page. Born-digital and text pages
are copied, not rasterized.

The `<stem>.enhance.json` report has `schema_version`, `kind` (`enhancement`),
`created`, `input` and `output` (each with `filename`, `sha256`, and
`page_count`), `settings`, `scan_processing`, and `pages` (`selected`,
`removed`, and `pages_per_input_page`). It does not contain document text or
absolute paths.

`export` requires at least one output. It does not write a processed PDF.

`--get-meta` writes JSON metadata with `input`, `output`, `doi`, and `pages`.
`doi` is empty unless DOI extraction is requested. `--get-doi` searches the
first derivative page for DOI candidates and writes JSON metadata. It accepts
`doi.org` and `dx.doi.org` resolver URLs, resolver names without a scheme,
`doi:` labels, and bare DOI names. Resolver URL paths are percent-decoded once.
Plain DOI names are not URL-decoded. The option does not write text exports
unless `--get-text` is selected.

DOI extraction removes soft hyphens and zero-width spaces inside candidates.
A DOI is never split across lines. A line break always ends a candidate, and
DOI extraction does not join lines. Clear surrounding brackets and quotation marks are
removed. Ambiguous trailing punctuation remains part of the candidate.

Extracted DOI candidates are not externally verified. Extraction can produce
false positives or omit DOI names in complex page layouts. A candidate does not establish that its DOI is registered
or that it identifies the processed document.

`input` is the absolute path of the exported PDF. `output` is always `null`
because export does not write a processed PDF. The field remains for
compatibility.

Each `pages` key matches the derivative filename stem, such as `page_001`.
`index` is the absolute input page index starting at 1. `pgn` is the printed
page number as a string, or `null` when detection is uncertain. Detection uses
short header and footer lines and neighboring numeric candidates. It supports
decimal and Roman numbers. It does not infer missing numbers.

With `--plan`, add `keep: true` when the plan selects the page. Omit absent
flags. Export has no page removal, so it does not write `skip`.

Whole-document metadata uses this structure:

```json
{
  "input": "F:/Decko/pdf-wtf/instance/_data/out/input.prepared.pdf",
  "output": null,
  "doi": [],
  "pages": {
    "page_001": {"index": 1, "pgn": "105", "keep": true},
    "page_002": {"index": 2, "pgn": "106"},
    "page_003": {"index": 3, "pgn": null},
    "page_004": {"index": 4, "pgn": "108", "keep": true}
  }
}
```

Export writes one derivative page for each page of the supplied PDF.
Page splitting applies only to the prepared PDF. Removing a split input page
removes all of its prepared PDF pages.

Optional exports include PNG page images, JPEG thumbnails, per-page text,
and combined text. `--get-thumb` also writes page images.

Born-digital container partitioning describes each input page and proposes
whole-page unit partitions from text structure, bookmarks, links, and raster
image occurrences. Journal and proceedings partitioning combines local opening
signals and matching contents navigation. It does not require a large title,
author, abstract, or DOI individually. Ambiguous shared-page starts require
review. The container specification defines the evidence and diagnostics. Page descriptions include printed page numbers, page types,
titles, sections, substantive figure positions, captions, bibliographic source
metadata, and unit links. Each proposed unit includes DOI candidates extracted
from the pages in its proposed range. Extracted strings use Unicode compatibility
normalization. The persisted partition does not contain the full extracted page
text or text-span structure. A separate reviewed plan owns corrections, page
selection, confirmed ranges, and unit selection. Reviewed whole-page ranges
can overlap. Each selected unit includes the entire shared page in its results.
Within-page splitting is not supported. The partition file remains
unchanged. Per-unit metadata and optional HTML remain separate from existing
whole-document derivatives. See
`specs/CONTAINER_PARTITION.md` for the workflow and JSON contracts.

A reusable Flask blueprint provides the PDF-WTF-GUI review interface. A small
standalone Flask host supports local use. The blueprint does not own host
authentication, sessions, logging, secrets, global extensions, URL prefixes,
or processing infrastructure. A database, durable job queue, and HTTP API
are not established components. Adding these components requires an approved
scope and architecture change.

### LLM tasks

Configured LLM tasks are optional post-processing steps. They send unit HTML
to Gemini through the `gemini-call` dependency. `specs/CONFIGURATION.md`
defines the `LLM_TASKS` configuration and the API key requirements.

A task runs only when it is explicitly selected. Every action accepts the
repeatable `--llm-task NAME` option and `--list-llm-tasks`. GUI adapters
accept `llm_tasks` for every operation. Ordinary processing does not require
an API key.

Task selection is separate from stage implementation. Each task declares its
stage and the actions that accept it. Stages and their supported actions are
defined in code. Export is currently the only implemented LLM stage:
`after_unit_html` supports `export` and requires `--get-html`. Future stages
can assist enhancement, for example OCR, or partitioning. LLM OCR and LLM
partitioning are not implemented.

Before processing starts, each action rejects unknown tasks, unsupported
stages, incompatible action and task combinations, a missing key, and unmet
stage prerequisites. A selected task is never ignored. A configuration that
lists an action that its stage does not support is invalid.

The shared runner in `src/pdfwtf/llm_tasks.py` runs the selected tasks one
after another. It runs after ordinary unit HTML generation and before export
publication or ZIP creation. It makes one call for each exported unit and task.
It does not split input, retry calls, or run calls concurrently. Unit HTML
larger than 500 000 bytes is rejected for that unit.

The runner calls `gemini-call` with `response_format="json"`. It reads the
response text and the token usage separately. A response must contain
non-empty HTML with at least one element and no Markdown fence. The runner
rejects other responses. It writes `<unit-id><suffix>`, for example
`<unit-id>.clean.html`, next to the original HTML. The original HTML does not
change.

The manifest records each task result for each unit under `llm_tasks` with
`status`, `output`, `usage`, and `error`. The top-level `llm` object records
`status` (`complete` or `incomplete`), `tasks`, and `failed_units`. These
fields occur only when tasks were selected. A failed call does not stop other
units. Ordinary exports are still published. The CLI then exits with status 1
and reports the failed units. The GUI export completes with a warning. Logs
contain the task name, the unit identifier, and the error class. Logs do not
contain prompts, HTML, or responses.

The standalone GUI stores uploaded source PDFs below the default input directory,
independent of the configured CLI input directory. It stores approved plans below
the configured output directory. A job identifier keeps each upload and plan in
a separate subdirectory.

Upload, preparation, and partitioning use separate requests. Upload a PDF
with the upload form.
Select the PDF type: `born-digital` (default) or `scanned`.
Upload metadata stores this value as `pdf_type` in `upload.json`.
The GUI copies this value into `source.partition.json` and `approved.plan.json`,
immediately after `document_type`. Older uploads without `pdf_type` use
`born-digital`. The server sets the plan value from upload metadata.
The uploaded-file list appears below the form. Select the document type in the
file row. Select Partition to start partitioning for that file. Upload does not start
partitioning. Saved uploads remain listed after the host restarts. The standalone
demo adapter writes the completed partition to
`<output-dir>/<job-id>/source.partition.json` before reporting completion.
Select Download partition to download the JSON result. Active job state remains
local to the configured adapter. Restarting the host does not restore active
jobs. Saved partition remains available through Review in each file row. Select Partition to run partitioning again. An active job is not started twice.
After partitioning finishes, the completion widget appears below the file row.
A successful rerun replaces the saved machine partition.
Select the filename to open the PDF in a new browser tab. Upload metadata
stores the last document type submitted with Partition. Each file row restores
this value when the page loads. New uploads use `auto`.
After successful automatic partitioning, the detected document type replaces
`auto` in upload metadata. An unknown result keeps `auto`. Saving an approved
plan does not change upload metadata.

Each upload has an original PDF (`source.pdf`) and can have a prepared PDF
(`prepared.pdf`) in the same input subdirectory. The active PDF is the
prepared PDF when it exists, otherwise the original PDF. When the prepared
PDF is active, the file row shows the badge "Active: prepared PDF", a link to
the original PDF, and a download of the prepared PDF. A row without the badge
uses the original PDF. Preview, partitioning, review, plan
validation, and export use the active PDF. `GET /jobs/<job_id>/document`
returns the active PDF. The `variant` query value `source` or `prepared`
selects one PDF. The value `download=1` sends it as an attachment.

Preparation is optional. When the host configures a prepare adapter, each
file row of a `scanned` upload has a Prepare PDF section with the PDF type,
the OCR languages, and background cropping. `POST /jobs/<job_id>/prepare` starts preparation, and
`GET /jobs/<job_id>/prepare/status` reports it. Both use CSRF protection
where required and the host access check with actions `prepare` and
`prepare_status`. Preparation runs the shared enhancement operation in the
background. It always starts from the original PDF. The adapter publishes
`prepared.pdf` atomically and writes the report to
`<output-dir>/<job-id>/enhance.json`. After success, the saved partition,
plan, and export archive of the earlier active PDF are deleted, and the page
reloads. The GUI also compares the `source.sha256` value of each saved
partition and plan with the active PDF. It does not show, review, or export
a partition or plan for another PDF. Export with such a plan returns 409.

Analysis is optional. When the host configures an analyze adapter, each
file row has an Analyze button before the document type selector. Without a
current report, the button uses `btn-info` and starts the read-only analysis
of the active PDF. `POST /jobs/<job_id>/analyze` starts it, and
`GET /jobs/<job_id>/analyze/status` reports it in the file row. The adapter
writes the report to `<output-dir>/<job-id>/analysis.json`. When the analysis
is complete, the button changes to `btn-outline-info`. It then opens a modal
with the report summary: the analyzed PDF, the document composition, the page
counts by composition and OCR need, the recommendations with their pages, and
the number of warnings. The modal has Download report
(`GET /jobs/<job_id>/analysis.json`) and Run again, which starts the analysis
again. `GET /jobs/<job_id>/analysis/summary` returns the modal content. These
routes use CSRF protection where required and the host access check with
actions `analyze`, `analyze_status`, `analysis_summary`, and
`download_analysis_report`. A report counts only when its `kind` is
`pdf-analysis` and its `source.sha256` matches the active PDF. After
preparation, the earlier report is deleted, and Analyze starts a new
analysis. Analysis is blocked while preparation is active. Preparation and
file deletion are blocked while analysis is active.

Select Delete in a file row to remove the uploaded PDF, the prepared PDF,
upload metadata, the enhancement report, the analysis report, the saved
plan, and the saved partition. Deletion is blocked while analysis,
preparation, partitioning, or export is queued or running. Deleted
uploads cannot be partitioned or reviewed. The delete endpoint is
`DELETE /uploads/<job_id>`. It uses CSRF protection and the host access check
with action `delete`.

Export appears in each file row of the Process files list. Partitioning is
optional. Without a saved plan, export uses the whole active PDF as one unit.
With a saved plan for the active PDF, export uses that plan. Export opens a modal with five switches: `no-pdf-out`, `get-html`,
`get-meta`, `include-source`, and `debug`. All switches start off each time the modal opens. With all
switches off,
export writes one PDF for each included unit and the manifest. `no-pdf-out`
suppresses unit PDFs. `get-html` adds HTML for included units.
`get-meta` adds unit `*.metadata.json` files. With `get-meta` off, the ZIP
contains no unit metadata files and manifest `metadata` fields are `null`. Export uses existing PDF text
and does not run OCR. The saved plan controls page and unit selection.
The demo adapter runs export in the background. Download export downloads
the results as `export.zip`. With `no-pdf-out` off, each included unit has
a `<unit-id>.pdf` in `_units`. Each PDF uses that unit's reviewed range
and selected source pages in input order. Shared pages appear in each included
unit that contains them. The ZIP does not contain a combined document PDF.
The GUI-only `include-source` switch adds the active PDF at the ZIP root,
as `source.pdf` for the original PDF or `prepared.pdf` for the prepared PDF. This switch is independent of `no-pdf-out`.
The `_units/manifest.json` file always includes the complete `upload.json`
object under `upload`, immediately after `kind`.
When LLM tasks are available, the modal also shows one switch for each task.
The switch label uses the task description in the current language, or
English when no translation exists. All task switches start off. Selecting a
task turns on `get-html`. Turning off `get-html` clears the selected tasks.
Task results such as `<unit-id>.clean.html` are added to `_units`. If a task
fails for a unit, the export completes and shows a warning.
The GUI-only `debug` switch copies `approved.plan.json` and
`source.partition.json` to the ZIP root. With a saved plan, both files must
exist when debug export starts. Without a saved plan, the ZIP contains the
generated one-unit plan only. This switch does not enable diagnostic logging.
Closing the modal does not stop export.
Open Export again to see the active operation. An active export cannot be
started twice. Preparation, partitioning, and file deletion are blocked
while export is active. Partitioning, export, and file deletion are blocked
while preparation is active.
Plan saving and plan deletion are blocked while export is queued or running.
Export does not change the source, partition, or saved plan.

New uploads store a `created` timestamp in `upload.json`. Saving a plan
stores `created` and `updated` timestamps in `approved.plan.json`. The host
sets these values in ISO 8601 format with UTC `Z`. The first save sets both
plan timestamps to the same value. Later saves preserve `created` and replace
`updated`. Client-supplied timestamp values do not override host values.
An older plan without `created` receives both timestamps when it is next saved.

## Component responsibilities

| Component | Responsibility |
| --- | --- |
| `src/pdfwtf/cli.py` | Define the options of each action with Click and invoke the matching pipeline operation. |
| `src/pdfwtf/pipeline.py` | Provide the operations `enhance_pdf`, `partition_pdf`, and `export_pdf`, the shared helpers, and the compatibility dispatcher `process_pdf`. |
| `src/pdfwtf/utils/analyze.py` | Identify scanned or hybrid PDF content for enhancement. |
| `src/pdfwtf/diagnostics.py` | Inspect a PDF read-only and build the `analyze` diagnostic report. |
| `src/pdfwtf/utils/common.py` | Provide page operations, paths, image preparation, text extraction, thumbnails, and metadata helpers. |
| `src/pdfwtf/container_partition.py` | Extract born-digital page structure, propose container partitions, validate reviewed plans, reconstruct units, and export unit results. |
| `src/pdfwtf/llm_tasks.py` | Check LLM task selection and availability, run selected tasks, and record results in the manifest. |
| `src/pdfwtf/templates/llm/` | Provide `prompt` and `system` text for LLM task templates. |
| `src/pdfwtf/gui/` | Provide the optional PDF-WTF-GUI blueprint, local host, browser PDF review, and the preparation, partition, and export adapter boundaries. |
| `src/pdfwtf/unpaper_run.py` | Build arguments and execute unpaper. Select the Windows wrapper when required. |
| `src/tools/unpaper_wrap.py` and `unpaper.cmd` | Support Docker-based unpaper execution on Windows. |

Keep argument handling in the CLI. Keep processing coordination in the
pipeline. Keep document operations and tool invocation reusable outside the CLI.

`process_pdf(input_pdf, output_dir, *, command="enhance", **options)` remains
for older callers. It selects the operation from `command`, maps
`partition_flag=True` to partitioning and `born_digital_flag=True` to
`pdf_type="born-digital"`, and rejects an option that the operation does not
accept unless the value is inactive. `enhance_pdf` returns an `EnhanceResult`
with `pdf`, `report`, and `llm_report`. `partition_pdf` returns the partition
path. `export_pdf` returns the LLM report or `None`. Each operation receives
the application configuration and validates LLM tasks with its own action
name.

### Background-processing boundary

The current CLI processes documents synchronously. A background-worker runtime
and queue technology are not selected. The local GUI demo adapter uses a
bounded in-process executor only for scaffolding. It is not a durable queue and
is not approved for production processing.

Rendering, scan preparation, OCR, and export belong to the processing component.
The GUI starts work through an adapter and polls status and result operations.
Its request handlers do not execute preparation, partitioning, or export. A host such as the integrating apps must provide
production access control and processing integration through explicit
configuration or callbacks. Define durable worker execution and job
coordination before production use.

## Technology

Use Python 3.12. Use `uv` for dependencies and environments. Use `uv_build` for
packaging. Use versions defined in `pyproject.toml` and `uv.lock`.

The established PDF libraries are pikepdf, PyMuPDF, and OCRmyPDF. Processing
code also uses pytesseract, Pillow, img2pdf, and cv3. Click provides the CLI.
Pydantic defines CLI settings. `gemini-call` provides optional Gemini calls
for LLM tasks. An existing import does not approve a new production dependency. Follow the dependency rules in `AGENTS.md`.

Flask, Flask-Babel, and Flask-WTF are optional `gui` dependencies. Bootstrap 5,
HTMX, and PDF.js are browser assets in the GUI package. The PDF.js library and
its matching worker are served locally. The browser renders page thumbnails
and previews. The server does not generate thumbnails for the review interface.

Tesseract supplies OCR for both supported OCR paths. Selected operations can
also require Ghostscript, unpaper, pngquant, and installed OCR language data.
The Windows unpaper wrapper runs unpaper in Docker. It does not containerize
the application.

## Development and runtime model

Support Microsoft Windows for development. Run the CLI and any future approved
background-worker processes on the Windows host during initial development.
Run the standalone PDF-WTF-GUI only in its approved Compose container. Use
Docker Desktop with the WSL 2 backend for local Docker execution. The same
Compose file supports Docker Engine on Docker-capable hosts. Keep each
development computer's Docker data independent.

Use direct `uv run --locked <tool> ...` commands. Run verification commands
separately. Use Black, Flake8, pytest, and pip-audit as specified in `AGENTS.md`.
Place tests without external services in `tests/unit`. Put shared fixtures in
`tests/conftest.py`. Existing scripts in `tests/misc` do not change this policy.

`specs/CONFIGURATION.md` defines configuration sources, validation, secrets,
`PDFWTF_HOME`, and instance directories. Runtime paths resolve from the validated
application root. Environment overrides include `PDFWTF_INPUT_DIR`,
`PDFWTF_OUTPUT_DIR`, and `PDFWTF_TEMP_DIR`.

## Trust boundaries and data principles

Treat input documents and extracted text as untrusted data. Pass document
content to PDF, image, and OCR tools as data. Do not execute document content
as commands or configuration.

PDFs, rendered pages, extracted text, and temporary files can contain private
information. Keep these files out of version control. Use runtime directories
from `specs/CONFIGURATION.md`. Keep secrets out of committed configuration.

Use standard Python logging. Follow the logging rules in `AGENTS.md`.
Do not log credentials, session identifiers, document contents, or complete
LLM payloads. Existing console output does not establish compliance with the
logging contract.

## Repository policy

Commit source code, documentation, safe example configuration, application
resources, `pyproject.toml`, and `uv.lock`. Commit Compose configuration only
for approved infrastructure. Exclude secrets, runtime data, logs, local
databases, build output, and local PyInstaller `.spec` files.

Make the smallest change that completes a task. Preserve public interfaces
unless the task requires a change. Obtain explicit approval before changing
the stack, adding production dependencies, adding Compose services, or
containerizing an application component. Record durable architecture decisions
in project specifications.

## Open decisions

- Background-worker execution and job coordination are not selected.
- Backup, retention, recovery, and logging and monitoring technologies are not
  selected.
- Coordination between concurrent invocations writing the same output is
  not defined.

Resolve these decisions through explicit approval before implementing the
affected architecture or operational policy.

## Processing safety

Each invocation uses a unique work directory under the configured temporary
directory. The pipeline removes that directory after success or failure.
Debug mode does not retain document intermediates.

When PDF output is enabled, the output PDF must differ from the input PDF. Export directories must not
contain the input PDF. Requested unpaper operations
must produce all expected pages. Processing stops if cleanup fails or OCR
changes the prepared page count.

When PDF output is enabled, the pipeline publishes the PDF after processing
and requested exports succeed.
Publication replaces an existing output PDF through a staged file on the output
filesystem. Generated export directories replace their existing files. Export
files and metadata are not published as one atomic transaction. Concurrent
writers to the same output remain unsupported.

The Review toolbar has a More menu with Show partition and Delete plan.
Delete plan removes only `approved.plan.json` and restores the partition units
when Review reloads. `DELETE /jobs/<job_id>/plan` uses CSRF protection and the
host access check with action `delete_plan`. Uploaded PDFs and partition remain.

The Units card header has an Included/Removed switch and a unit filter.
All shows every unit. Selected shows only units with checked selection boxes.
Included shows only included units. Removed shows only removed units.
The switch applies to the units that match the current filter.
With no matching units, the filter hides every unit and disables the switch.
Clear selection unticks every unit selection checkbox and returns to All.
Clear selection does not change unit inclusion.
Mixed unit inclusion shows Mixed. Selecting the mixed switch includes all target
units. Selecting the switch again removes all target units. Save plan persists
the inclusion changes.

Thumbnails for pages that belong only to removed units use a grey gradient
and a faded image. Pages shared with an included unit keep normal styling.
Pages outside all unit ranges keep normal styling. Inclusion and range changes
update the thumbnails immediately.

## Development commands

Run commands from the repository root unless another directory is specified.
Supply `PDFWTF_HOME` in the inherited operating-system environment.
Run each verification command separately.

### Quality checks

For GUI development, install the optional dependencies:

```bash
uv sync --locked --extra gui
```

Run the unit tests without external services:

```bash
uv run --locked pytest tests/unit
```

Check Python formatting:

```bash
uv run --locked black --check src tests
```

Check Python code:

```bash
uv run --locked flake8 src tests
```

Check dependency vulnerabilities:

```bash
uv run --locked pip-audit
```

### Windows unpaper wrapper

Use Docker Desktop with the WSL 2 backend.
Start Docker Desktop before using the wrapper.
Build the unpaper image:

```bash
docker build -t unpaper-alpine -f .Dockerfile-unpaper .
```

Check the image:

```bash
docker run --rm unpaper-alpine --version
```

Add the repository root to `PATH` so the pipeline can find `unpaper.cmd`.
Run processing through `uv run --locked`.
Layout, page splitting, and pre-rotation require unpaper.
If unpaper is unavailable, processing without these options continues without
optional unpaper cleaning.

### Demo container operations

Check the container state and health:

```bash
docker compose -f compose-pdf-wtf-gui.yaml ps
```

Read the application logs:

```bash
docker compose -f compose-pdf-wtf-gui.yaml logs -f pdf-wtf-gui
```

The GUI image includes unpaper.
To run the CLI inside the container, place the PDF in
`PDFWTF_HOME/instance/_data/in`.
Run this command:

```bash
docker compose -f compose-pdf-wtf-gui.yaml exec pdf-wtf-gui pdfwtf enhance input.pdf --layout single --outdir /app/instance/_data/out
docker compose -f compose-pdf-wtf-gui.yaml exec pdf-wtf-gui pdfwtf export /app/instance/_data/out/input.prepared.pdf --get-text --outdir /app/instance/_data/out
```

The command writes the processed PDF below `PDFWTF_HOME/instance/_data/out`.
Use `--output-pages` or `--pre-rotate` for other unpaper operations.
The web review interface does not run this CLI command.
Rebuild the GUI image after a source, dependency, or configuration change.

### GUI translations

Extract English and Czech messages:

```bash
uv run --locked --extra gui pybabel extract -F babel.cfg -o messages.pot .
```

Update the translation catalogs:

```bash
uv run --locked --extra gui pybabel update -i messages.pot -d src/pdfwtf/gui/translations
```

Compile the translation catalogs:

```bash
uv run --locked --extra gui pybabel compile -d src/pdfwtf/gui/translations
```
