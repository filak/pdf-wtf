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
The actions are `analyse`, `enhance`, and `export`. Both arguments are required.
`analyse` writes analysis JSON from existing PDF text. It does not run OCR.
`enhance` performs the existing PDF processing with optional derivative exports.
`export` writes explicitly selected derivatives from the input PDF. It does not
run scan cleanup or OCR and does not write a processed PDF.
Use `pdfwtf ACTION --help` for action-specific options. The `--analysis` flag
and the former command syntax without an action are not supported.
The input argument is required. The input file must exist. Filenames and
relative file paths resolve from the configured input directory. Its default is
`PDFWTF_HOME/instance/_data/in`. Absolute file paths select files directly.
The CLI does not search the working directory.

`--extract` selects input pages for the output PDF. `--remove` removes input
pages from that PDF. Both options use absolute input page indices starting at 1.
Removal takes precedence when an index occurs in both lists. Derivatives cover
all input pages. These options do not filter derivatives. With `--no-pdf-out`,
the selection lists still set metadata flags.

The `--pdf_type` option accepts `auto`, `born-digital`, and `scanned`.
The default is `auto`, which detects scanned or hybrid content.
Use `scanned` to force scan preparation and OCR.
Use `born-digital` to override automatic scan detection. It bypasses
scan preparation, page rasterization for processing, and OCR, even when the
input has no extractable text. Page selection and removal still apply. Image
and thumbnail exports still render pages without changing the output PDF.

With this option, text exports contain only existing extractable text.
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

For `enhance`, the default output is a processed PDF. `--no-pdf-out` disables final PDF output.
With this option, select at least one derivative output. The pipeline can still
create a temporary processed PDF for exports.

`--get-meta` writes JSON metadata with `input`, `output`, `doi`, and `pages`.
`doi` is empty unless DOI extraction is requested. `--get-doi` searches the
first derivative page for DOI candidates and writes JSON metadata. It accepts
`doi.org` and `dx.doi.org` resolver URLs, resolver names without a scheme,
`doi:` labels, and bare DOI names. Resolver URL paths are percent-decoded once.
Plain DOI names are not URL-decoded. The option does not write text exports
unless `--get-text` is selected.

DOI extraction removes soft hyphens and zero-width spaces inside candidates.
It joins a line continuation only when the next physical line contains one
isolated token. At a physical line break, it rejects a candidate fragment that
ends with a slash or hyphen when that continuation is not available. It does
not join general text lines. Clear surrounding brackets and quotation marks are
removed. Ambiguous trailing punctuation remains part of the candidate.

Extracted DOI candidates are not externally verified. Extraction can produce
false positives or omit DOI names with ambiguous multi-token line wrapping or
complex page layouts. A candidate does not establish that its DOI is registered
or that it identifies the processed document.

`input` is the absolute input PDF path. `output` is the absolute final output
PDF path, including preserved subdirectories. With `--no-pdf-out`, `output` is
`null`. Neither field refers to a temporary PDF.

Each `pages` key matches the derivative filename stem, such as `page_001`.
`index` is the absolute input page index starting at 1. `pgn` is the printed
page number as a string, or `null` when detection is uncertain. Detection uses
short header and footer lines and neighboring numeric candidates. It supports
decimal and Roman numbers. It does not infer missing numbers.

Add `skip: true` when the input index occurs in the removal list. Add
`keep: true` when it occurs in the extraction list. Omit absent flags.
Both flags can occur on one page.

Whole-document metadata uses this structure:

```json
{
  "input": "F:/Decko/pdf-wtf/instance/_data/in/input.pdf",
  "output": "F:/Decko/pdf-wtf/instance/_data/out/input.pdf",
  "doi": [],
  "pages": {
    "page_001": {"index": 1, "pgn": "105"},
    "page_002": {"index": 2, "pgn": "106", "skip": true},
    "page_003": {"index": 3, "pgn": null},
    "page_004": {"index": 4, "pgn": "108", "keep": true}
  }
}
```

Derivative processing preserves one page per input page. Page splitting applies
only to the output PDF. Removing a split input page removes all of its PDF pages.

Optional exports include PNG page images, JPEG thumbnails, per-page text,
and combined text. `--get-thumb` also writes page images.

Born-digital container analysis describes each input page and proposes
whole-page unit partitions from text structure, bookmarks, links, and raster
image occurrences. Page descriptions include printed page numbers, page types,
titles, sections, substantive figure positions, captions, bibliographic source
metadata, and unit links. Each proposed unit includes DOI candidates extracted
from the pages in its proposed range. Extracted strings use Unicode compatibility
normalization. The persisted analysis does not contain the full extracted page
text or text-span structure. A separate reviewed plan owns corrections, page
selection, confirmed ranges, and unit selection. Reviewed whole-page ranges
can overlap. Each selected unit includes the entire shared page in its results.
Within-page splitting is not supported. The analysis file remains
unchanged. Per-unit metadata and optional HTML remain separate from existing
whole-document derivatives. See
`specs/CONTAINER_ANALYSIS.md` for the workflow and JSON contracts.

A reusable Flask blueprint provides the PDF-WTF-GUI review interface. A small
standalone Flask host supports local use. The blueprint does not own host
authentication, sessions, logging, secrets, global extensions, URL prefixes,
or processing infrastructure. A database, durable job queue, HTTP API, and LLM
integration are not established components. Adding these components requires
an approved scope and architecture change.

The standalone GUI stores uploaded source PDFs below the default input directory,
independent of the configured CLI input directory. It stores approved plans below
the configured output directory. A job identifier keeps each upload and plan in
a separate subdirectory.

Upload and analysis use separate requests. Upload a PDF with the upload form.
Select the PDF type: `born-digital` (default) or `scanned`.
Upload metadata stores this value as `pdf_type` in `upload.json`.
The GUI copies this value into `source.analysis.json` and `approved.plan.json`,
immediately after `document_type`. Older uploads without `pdf_type` use
`born-digital`. The server sets the plan value from upload metadata.
The uploaded-file list appears below the form. Select the document type in the
file row. Select Analyze to start analysis for that file. Upload does not start
analysis. Saved uploads remain listed after the host restarts. The standalone
demo adapter writes completed analysis to
`<output-dir>/<job-id>/source.analysis.json` before reporting completion.
Select Download analysis to download the JSON result. Active job state remains
local to the configured adapter. Restarting the host does not restore active
jobs. Saved analysis remains available through Review in each file row. Select Analyze to run analysis again. An active job is not started twice.
After analysis finishes, the completion widget appears below the file row.
A successful rerun replaces the saved machine analysis.
Select the filename to open the PDF in a new browser tab. Upload metadata
stores the last document type submitted with Analyze. Each file row restores
this value when the page loads. New uploads use `auto`.
After successful automatic analysis, the detected document type replaces
`auto` in upload metadata. An unknown result keeps `auto`. Saving an approved
plan does not change upload metadata.

Select Delete in a file row to remove the uploaded PDF, upload metadata, and
saved plan, and saved analysis. Deletion is blocked while analysis is queued or running. Deleted
uploads cannot be analyzed or reviewed. The delete endpoint is
`DELETE /uploads/<job_id>`. It uses CSRF protection and the host access check
with action `delete`.

After a plan is saved, Export appears next to Review in the Process files list.
Export opens a modal with five switches: `no-pdf-out`, `get-html`,
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
The GUI-only `include-source` switch adds the original uploaded `source.pdf`
at the ZIP root. This switch is independent of `no-pdf-out`.
The `_units/manifest.json` file always includes the complete `upload.json`
object under `upload`, immediately after `kind`.
The GUI-only `debug` switch copies `approved.plan.json` and
`source.analysis.json` to the ZIP root. Both files must exist when debug
export starts. This switch does not enable diagnostic logging.
Closing the modal does not stop export.
Open Export again to see the active operation. An active export cannot be
started twice. Analysis and file deletion are blocked while export is active.
Plan saving and plan deletion are blocked while export is queued or running.
Export does not change the source, analysis, or saved plan.

New uploads store a `created` timestamp in `upload.json`. Saving a plan
stores `created` and `updated` timestamps in `approved.plan.json`. The host
sets these values in ISO 8601 format with UTC `Z`. The first save sets both
plan timestamps to the same value. Later saves preserve `created` and replace
`updated`. Client-supplied timestamp values do not override host values.
An older plan without `created` receives both timestamps when it is next saved.

## Component responsibilities

| Component | Responsibility |
| --- | --- |
| `src/pdfwtf/cli.py` | Define options with Click, collect settings with Pydantic, and invoke the pipeline. |
| `src/pdfwtf/pipeline.py` | Coordinate page selection, scan preparation, OCR, and exports. |
| `src/pdfwtf/utils/analyze.py` | Identify scanned or hybrid PDF content. |
| `src/pdfwtf/utils/common.py` | Provide page operations, paths, image preparation, text extraction, thumbnails, and metadata helpers. |
| `src/pdfwtf/container_analysis.py` | Extract born-digital page structure, propose container partitions, validate reviewed plans, reconstruct units, and export unit results. |
| `src/pdfwtf/gui/` | Provide the optional PDF-WTF-GUI blueprint, local host, browser PDF review, and analysis adapter boundary. |
| `src/pdfwtf/unpaper_run.py` | Build arguments and execute unpaper. Select the Windows wrapper when required. |
| `src/tools/unpaper_wrap.py` and `unpaper.cmd` | Support Docker-based unpaper execution on Windows. |

Keep argument handling in the CLI. Keep processing coordination in the
pipeline. Keep document operations and tool invocation reusable outside the CLI.

### Background-processing boundary

The current CLI processes documents synchronously. A background-worker runtime
and queue technology are not selected. The local GUI demo adapter uses a
bounded in-process executor only for scaffolding. It is not a durable queue and
is not approved for production processing.

Rendering, scan preparation, OCR, and export belong to the processing component.
The GUI starts work through an adapter and polls status and result operations.
Its request handlers do not execute analysis. A host such as the integrating apps must provide
production access control and processing integration through explicit
configuration or callbacks. Define durable worker execution and job
coordination before production use.

## Technology

Use Python 3.12. Use `uv` for dependencies and environments. Use `uv_build` for
packaging. Use versions defined in `pyproject.toml` and `uv.lock`.

The established PDF libraries are pikepdf, PyMuPDF, and OCRmyPDF. Processing
code also uses pytesseract, Pillow, img2pdf, and cv3. Click provides the CLI.
Pydantic defines CLI settings. An existing import does not approve a new
production dependency. Follow the dependency rules in `AGENTS.md`.

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

The Review toolbar has a More menu with Show analysis and Delete plan.
Delete plan removes only `approved.plan.json` and restores the analysis units
when Review reloads. `DELETE /jobs/<job_id>/plan` uses CSRF protection and the
host access check with action `delete_plan`. Uploaded PDFs and analysis remain.

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

```text
uv sync --locked --extra gui
```

Run the unit tests without external services:

```text
uv run --locked pytest tests/unit
```

Check Python formatting:

```text
uv run --locked black --check src tests
```

Check Python code:

```text
uv run --locked flake8 src tests
```

Check dependency vulnerabilities:

```text
uv run --locked pip-audit
```

### Windows unpaper wrapper

Use Docker Desktop with the WSL 2 backend.
Start Docker Desktop before using the wrapper.
Build the unpaper image:

```text
docker build -t unpaper-alpine -f dockers/Dockerfile-unpaper .
```

Check the image:

```text
docker run --rm unpaper-alpine --version
```

Add the repository root to `PATH` so the pipeline can find `unpaper.cmd`.
Run processing through `uv run --locked`.
Layout, page splitting, and pre-rotation require unpaper.
If unpaper is unavailable, processing without these options continues without
optional unpaper cleaning.

### Demo container operations

Check the container state and health:

```text
docker compose -f dockers/pdf-wtf-gui-compose.yaml ps
```

Read the application logs:

```text
docker compose -f dockers/pdf-wtf-gui-compose.yaml logs -f pdf-wtf-gui
```

The GUI image includes unpaper.
To run the CLI inside the container, place the PDF in
`PDFWTF_HOME/instance/_data/in`.
Run this command:

```text
docker compose -f dockers/pdf-wtf-gui-compose.yaml exec pdf-wtf-gui pdfwtf enhance input.pdf --layout single --outdir /app/instance/_data/out
```

The command writes the processed PDF below `PDFWTF_HOME/instance/_data/out`.
Use `--output-pages` or `--pre-rotate` for other unpaper operations.
The web review interface does not run this CLI command.
Rebuild the GUI image after a source, dependency, or configuration change.

### GUI translations

Extract English and Czech messages:

```text
uv run --locked --extra gui pybabel extract -F babel.cfg -o messages.pot .
```

Update the translation catalogs:

```text
uv run --locked --extra gui pybabel update -i messages.pot -d src/pdfwtf/gui/translations
```

Compile the translation catalogs:

```text
uv run --locked --extra gui pybabel compile -d src/pdfwtf/gui/translations
```
