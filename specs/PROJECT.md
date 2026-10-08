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

Pass the input PDF as the first positional argument: `pdfwtf input.pdf`.
The input argument is required. The input file must exist. Filenames and
relative file paths resolve from `PDFWTF_HOME/instance/_data/in`. Absolute
file paths select files directly. The CLI does not search the working directory.

`--extract` selects input pages for the output PDF. `--remove` removes input
pages from that PDF. Both options use absolute input page indices starting at 1.
Removal takes precedence when an index occurs in both lists. Derivatives cover
all input pages. These options do not filter derivatives. With `--no-pdf-out`,
the selection lists still set metadata flags.

The `--born-digital` option overrides automatic scan detection. It bypasses
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

A text PDF contains extractable text. A scanned PDF requires OCR to obtain
text. A hybrid PDF contains mixed text and image content. Scan preparation
includes orientation correction and optional dark-background cropping.
The unpaper integration supports layout, page splitting, and pre-rotation.

The default output is a processed PDF. `--no-pdf-out` disables final PDF output.
With this option, select at least one derivative output. The pipeline can still
create a temporary processed PDF for exports.

`--get-json` writes JSON metadata with `input`, `output`, `doi`, and `pages`. `doi` is empty
unless DOI extraction is requested. `--get-doi` searches the first derivative
page for DOI links and writes JSON metadata. It does not write text exports unless `--get-text` is selected.
Extracted DOI candidates are not externally verified.

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

Derivative processing preserves one page per input page. Page splitting applies
only to the output PDF. Removing a split input page removes all of its PDF pages.

Optional exports include PNG page images, JPEG thumbnails, per-page text,
and combined text. `--get-thumb` also writes page images.

A web interface, HTTP API, database, job queue, and LLM integration are not
established components. Adding these components requires an approved scope
and architecture change.

## Component responsibilities

| Component | Responsibility |
| --- | --- |
| `src/pdfwtf/cli.py` | Define options with Click, collect settings with Pydantic, and invoke the pipeline. |
| `src/pdfwtf/pipeline.py` | Coordinate page selection, scan preparation, OCR, and exports. |
| `src/pdfwtf/utils/analyze.py` | Identify scanned or hybrid PDF content. |
| `src/pdfwtf/utils/common.py` | Provide page operations, paths, image preparation, text extraction, thumbnails, and metadata helpers. |
| `src/pdfwtf/unpaper_run.py` | Build arguments and execute unpaper. Select the Windows wrapper when required. |
| `src/tools/unpaper_wrap.py` and `unpaper.cmd` | Support Docker-based unpaper execution on Windows. |

Keep argument handling in the CLI. Keep processing coordination in the
pipeline. Keep document operations and tool invocation reusable outside the CLI.

### Background-processing boundary

The current CLI processes documents synchronously. A background-worker runtime
and queue technology are not selected.

Rendering, scan preparation, OCR, and export belong to the processing component.
If a request-serving component is approved later, preserve this boundary.
Define worker execution and job coordination before adding them.

## Technology

Use Python 3.12. Use `uv` for dependencies and environments. Use `uv_build` for
packaging. Use versions defined in `pyproject.toml` and `uv.lock`.

The established PDF libraries are pikepdf, PyMuPDF, and OCRmyPDF. Processing
code also uses pytesseract, Pillow, img2pdf, and cv3. Click provides the CLI.
Pydantic defines CLI settings. An existing import does not approve a new
production dependency. Follow the dependency rules in `AGENTS.md`.

Tesseract supplies OCR for both supported OCR paths. Selected operations can
also require Ghostscript, unpaper, pngquant, and installed OCR language data.
The Windows unpaper wrapper runs unpaper in Docker. It does not containerize
the application.

## Development and runtime model

Support Microsoft Windows for development. Run application and any future
approved background-worker processes on the Windows host during initial
development. Use Docker Desktop with the WSL 2 backend for local infrastructure.
Keep each development computer's Docker data independent.

Use direct `uv run --locked <tool> ...` commands. Run verification commands
separately. Use Black, Flake8, pytest, and pip-audit as specified in `AGENTS.md`.
Place tests without external services in `tests/unit`. Put shared fixtures in
`tests/conftest.py`. Existing scripts in `tests/misc` do not change this policy.

`specs/CONFIGURATION.md` defines configuration sources, validation, secrets,
`PDFWTF_HOME`, and instance directories. Runtime paths resolve from the validated
application root. Environment overrides include `PDFWTF_OUTPUT_DIR` and
`PDFWTF_TEMP_DIR`.

Support native Windows Server production deployment and container deployment
on Docker-capable hosts, including Linux. Do not require Docker Desktop in
production. Use a reverse proxy for public routing and TLS when public access
is introduced. `specs/DEPLOYMENT.md` owns production deployment details.

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

- Production installation, supervision, and container layouts remain undefined
  in `specs/DEPLOYMENT.md`.
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
