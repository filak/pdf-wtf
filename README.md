# pdf-wtf

PDF - what the file format ...

PDF files parsing and data extraction

> Work in progress ...

[![CodeQL](https://github.com/filak/pdf-wtf/actions/workflows/codeql.yml/badge.svg)](https://github.com/filak/pdf-wtf/security/code-scanning)

[![Codacy Badge](https://app.codacy.com/project/badge/Grade/3a72061a5c0d4854885271f4e5e1bc2e)](https://app.codacy.com/gh/filak/pdf-wtf/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade)

## Built on top of

**pikepdf**
- PDF manipulation and content editing
- https://pikepdf.readthedocs.io/en/latest/installation.html

**PyMuPDF**
- PDF processing, text/image extraction, rendering
- https://pymupdf.readthedocs.io/en/latest/installation.html

**ocrmypdf**
- OCR wrapper (uses Tesseract + Ghostscript)
- https://ocrmypdf.readthedocs.io/en/latest/installation.html

## External non-Python dependencies

**Tesseract OCR**
- required by OCRmyPDF and PyMuPDF
- https://github.com/UB-Mannheim/tesseract

**Ghostscript**
- required by OCRmyPDF
- https://www.ghostscript.com/releases/gsdnld.html

**unpaper**
- used by the pipeline for scan cleanup, layout, splitting, and pre-rotation
- https://github.com/unpaper/unpaper

**pngquant**
- required by OCRmyPDF with optimize > 0
- https://pngquant.org/

## Local development installation

Use Python 3.12, Git, and uv. Run the following commands from the repository root.

1. Synchronize the locked dependencies:

    ```
    uv sync --locked
    ```

2. Supply `PDFWTF_HOME` in the operating-system environment.

    Use the repository root during development. Restart the terminal or VS Code
    after changing the environment. The application reports an error if this
    value is missing. Do not put `PDFWTF_HOME` in `.env`.

    Windows - go to the repo root folder open CMD and run:
    ```
    setx PDFWTF_HOME %CD%
    ```

    Linux (Bash) - open a terminal in the repository root.
    Set the value for the current terminal and processes launched from it:

    ```bash
    export PDFWTF_HOME="$(pwd -P)"
    ```

    For future interactive Bash terminals, add the following line to
    `~/.bashrc`. Replace the example with the absolute repository path:

    ```bash
    export PDFWTF_HOME="/home/your-user/pdf-wtf"
    ```

    Open a new terminal after saving `~/.bashrc`. Verify the value and
    configuration file:

    ```bash
    printf '%s\n' "$PDFWTF_HOME"
    test -f "$PDFWTF_HOME/instance/conf/pdf-wtf.ini" && echo "Configuration file found."
    ```

    To give VS Code the same environment, close all VS Code windows.
    Launch VS Code from the configured terminal:

    ```bash
    code .
    ```

3. Review `instance/conf/pdf-wtf.ini`.

    Relative configured paths resolve from `PDFWTF_HOME`. Environment values
    override `.env` values. Both override INI values. See
    [the configuration specification](specs/CONFIGURATION.md).

4. Run the tests:

    ```
    uv run --locked pytest
    ```

5. Run each quality check separately:

    ```
    uv run --locked black --check src tests
    ```

    ```
    uv run --locked flake8 src tests
    ```

    ```
    uv run --locked pip-audit
    ```

## Run PDF-WTF-GUI

Run PDF-WTF-GUI only in Docker. Use Docker Desktop or Docker Engine with the
Docker Compose plugin. Run these commands from the repository root.

Compose reads `PDFWTF_HOME` from the host environment. It bind-mounts
`PDFWTF_HOME/instance/_data` at `/app/instance/_data` in the container. Host and
container processes therefore use the same runtime data. The command stops with
an error if `PDFWTF_HOME` is missing.

Build and start the container:

```
docker compose -f dockers/pdf-wtf-gui-compose.yaml up --build -d
```

Open `http://127.0.0.1:5000` in a browser. Check the container state and
health:

```
docker compose -f dockers/pdf-wtf-gui-compose.yaml ps
```

View the application logs:

```
docker compose -f dockers/pdf-wtf-gui-compose.yaml logs -f pdf-wtf-gui
```

Stop and remove the container. Uploaded documents and approved plans remain in
`PDFWTF_HOME/instance/_data`:

```
docker compose -f dockers/pdf-wtf-gui-compose.yaml down
```

The GUI stores an uploaded PDF at
`PDFWTF_HOME/instance/_data/in/<job-id>/source.pdf`. It stores the approved plan
at `<output-dir>/<job-id>/approved-plan.json`. The default output directory is
`PDFWTF_HOME/instance/_data/out`. `PDFWTF_OUTPUT_DIR` can override it. The GUI
does not use `PDFWTF_INPUT_DIR`.

Do not run `pdfwtf-gui` directly on the host. Rebuild the image after a source,
dependency, or configuration change.

The standalone host uses the demo analysis adapter. Do not use this in-process
adapter as a production queue. The reusable blueprint is available as
`pdfwtf.gui.create_gui_blueprint`. A host must initialize Flask-Babel and CSRF
protection before it registers the blueprint. See `specs/CONFIGURATION.md` for
the namespaced integration settings and callbacks.

Extract, update, and compile English and Czech messages with these commands:

```
uv run --locked --extra gui pybabel extract -F babel.cfg -o messages.pot .
```

```
uv run --locked --extra gui pybabel update -i messages.pot -d src/pdfwtf/gui/translations
```

```
uv run --locked --extra gui pybabel compile -d src/pdfwtf/gui/translations
```

Process a PDF with the command-line interface:

```
uv run --locked pdfwtf input.pdf --outdir output --get-text
```

Use a filename or a relative path below the configured input directory. The
default is `PDFWTF_HOME/instance/_data/in`. Set `PDFWTF_INPUT_DIR` to override
it. Use an absolute path to select a file outside this directory.


The input PDF is required. The output PDF must differ from the input PDF.
Processing errors return a nonzero exit code. Each invocation removes its work
directory after success or failure. Debug mode enables debug logging and does
not retain document intermediates.

The pipeline stops if a requested unpaper operation fails or produces fewer
pages than expected. It also checks the OCR page count. Post-processing page
removal uses the processed PDF's page numbers.

## Born-digital input

If the input is born digital, use `--born-digital` to bypass automatic scan
detection, scan preparation, and OCR:

```
uv run --locked pdfwtf input.pdf --outdir output --born-digital --get-text
```

Page selection and removal still work. The output PDF retains the selected
pages without converting them to images. Requested image and thumbnail exports
still render separate files.

A born-digital PDF does not need to contain extractable text. Text can consist
of vector outlines, or the document can contain only graphics. In this case,
processing succeeds without OCR. Per-page text exports are empty. The combined
text file contains page headings. DOI extraction returns no candidates when
the first exported page has no extractable text.

Do not combine `--born-digital` with `--remove-bg`, `--layout single`,
`--layout double`, `--output-pages`, or `--pre-rotate`. The application reports
an error for these combinations.

## Using unpaper on Windows

The existing command wrapper runs unpaper in Docker. Install Docker Desktop
with the WSL 2 backend for local development. Start Docker Desktop before using
the wrapper.

Build the existing image:

```
docker build -t unpaper-alpine -f dockers/Dockerfile-unpaper .
```

Check the image:

```
docker run --rm unpaper-alpine --version
```

Add the repository root to `PATH` so the pipeline can find `unpaper.cmd`.
Run processing through `uv run --locked` so the wrapper uses the project
environment.

The pipeline invokes unpaper directly during scan preparation. It does not
require edits to installed OCRmyPDF files. Layout, page splitting, and
pre-rotation options require unpaper. If unpaper is unavailable, processing
without these options continues without optional unpaper cleaning.

### OCR optimization

Use `--optimize` to set OCRmyPDF optimization. The default is `0`.

- `0`: Disable optional optimization.
- `1`: Use lossless optimization.
- `2`: Permit lossy optimization.
- `3`: Use more aggressive lossy optimization.

```powershell
uv run --locked pdfwtf scan_with_text.pdf --optimize 1 --get-text
```

Levels `2` and `3` can show a warning if the optional `jbig2` executable is
unavailable. The option applies only when OCRmyPDF runs. It has no effect with
`--ocrlib pymupdf` or `--born-digital`.

### Output selection

The default output is a processed PDF. Add export options to write derivatives.
Use `--no-pdf-out` to write derivatives only. Select at least one export option.

```powershell
uv run --locked pdfwtf input.pdf
uv run --locked pdfwtf input.pdf --get-text
uv run --locked pdfwtf input.pdf --no-pdf-out --get-text
uv run --locked pdfwtf input.pdf --no-pdf-out --get-meta --get-img --get-thumb
```

Use `--get-meta` to write JSON metadata. JSON includes `doi` and `pages`. The
`doi` list is empty unless DOI extraction is requested. Use `--get-doi` to find
DOI candidates on the first output page and write JSON metadata. The extractor
accepts resolver URLs, `doi.org` names, `doi:` labels, and bare DOI names. It
decodes resolver URL paths once. It preserves ambiguous trailing punctuation.

The extractor joins a broken DOI only when the next physical line contains one
isolated token. At a line break, it rejects dangling slash or hyphen fragments.
It does not join general text lines. Complex layouts and ambiguous multi-token
wrapping can still cause missed or extra candidates. Candidates are not
externally verified. A candidate does not prove that a DOI is registered or
identifies the processed document. `--get-doi` writes text files only if
`--get-text` is also set.

`--no-pdf-out` disables final PDF output. Processing and OCR can still create
temporary PDFs. Use `--born-digital` to bypass scan preparation and OCR.

### Page metadata and selection

JSON includes all input pages. Page handles match derivative filenames.
The `index` starts at 1. The `pgn` is a detected printed number or `null`.

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

Use `--extract` and `--remove` with absolute input indices. These options filter
only the output PDF. All derivative pages remain available. The selection lists
also set `keep` and `skip` in JSON. Omit flags for indices absent from each list.
If an index occurs in both lists, removal takes precedence for the PDF.

Printed number detection checks header and footer text. Ambiguous numbers return
`null`. No text means no detected number. Derivative processing preserves input
indices. Page splitting applies only to the output PDF.

JSON `input` and `output` contain absolute PDF paths. With `--no-pdf-out`,
`output` is `null`.

## Container analysis and unit HTML

The container workflow supports born-digital PDFs and whole-page unit
boundaries. First, write a machine analysis:

```powershell
uv run --locked pdfwtf issue.pdf --born-digital --analysis --doctype journal-issue
```

The command writes `issue.analysis.json` in the output directory. The analysis
contains one review record for each input page. It proposes printed page
numbers, page types, titles, sections, substantive figures, captions,
bibliographic source metadata, and unit links. Each proposed unit has a `doi`
list with candidates from its proposed page range. Extracted strings are
normalized. The compact file does not contain full page text or text spans. A
consuming application keeps this file unchanged and writes corrections and
selections to `issue.plan.json`. Process the reviewed plan:

```powershell
uv run --locked pdfwtf issue.pdf --born-digital --plan issue.plan.json --get-html --no-pdf-out
```

The plan's page selections control the output PDF and the pages included in
unit results. The command writes `manifest.json`, one metadata JSON file for
each selected unit, and one HTML fragment for each selected unit below
`_units_issue`. Plan ranges can exclude pages by leaving gaps. They cannot
overlap.

Process a single unit without a reviewed plan:

```powershell
uv run --locked pdfwtf article.pdf --born-digital --doctype unit --get-html --no-pdf-out
```

See [the container analysis specification](specs/CONTAINER_ANALYSIS.md) for the
analysis schema, reviewed plan schema, result contracts, and heuristic limits.

# pdf-wtf deployment

## Standalone PDF-WTF-GUI container

Run the standalone PDF-WTF-GUI only in Docker. The approved Compose definition
is `dockers/pdf-wtf-gui-compose.yaml`. It supports Docker Desktop with the WSL 2
backend and Docker Engine on Docker-capable hosts.

The Compose service builds `dockers/Dockerfile-pdf-wtf-gui`. It publishes the
GUI on host loopback port 5000. It bind-mounts
`PDFWTF_HOME/instance/_data` at `/app/instance/_data`. This mount shares runtime
data with host processes that use the configured `PDFWTF_HOME`. The service runs
as a non-root user with a read-only root filesystem. It includes an HTTP health
check. The image receives no committed secret.

The standalone host uses the bounded in-process demo adapter. This container is
for local review and development. It is not an approved production processing
service or durable job queue.
