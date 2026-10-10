# pdf-wtf

PDF-WTF processes PDF files and extracts document data. It supports existing
PDF text, scanned pages, and mixed text and image content.

This project uses LLM-AI coding tools.

The current project status is: in development

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

## Installation

Use Python 3.12, Git, and uv.

Download or clone the repository:

```text
git clone https://github.com/filak/pdf-wtf.git
cd pdf-wtf
```

To update the repo run:

```text
git pull
```

## Configuration

Set `PDFWTF_HOME` to the absolute repository path in the operating-system
environment. The application requires this value.
Do not put `PDFWTF_HOME` in `.env`.

On Windows, run this command in CMD from the repository root:

```cmd
setx PDFWTF_HOME "%CD%"
```

> Restart the terminal or VS Code after setting this value.

On Linux, run this command in Bash from the repository root:

```bash
export PDFWTF_HOME="$(pwd -P)"
```

Review `instance/conf/pdf-wtf.ini`.
For persistent environment setup and path overrides, see the
[configuration specification](specs/CONFIGURATION.md).

## Docker

> If You do not want to install anything just spin up (or rebuild) the Docker

```text
docker buildx bake -f compose-pdf-wtf-gui.yaml --allow=fs.read=.Dockerfile-pdf-wtf-gui --load
docker compose -f compose-pdf-wtf-gui.yaml up -d
```

and open PDF-WTF-GUI in a browser http://127.0.0.1:5001

Try the client - run

```text
docker compose -f compose-pdf-wtf-gui.yaml exec pdf-wtf-gui pdfwtf --help
```

## Development

Run the following commands from the repository root.
Install the locked Python dependencies:

```text
uv sync --locked
```

For the Windows unpaper wrapper, see the
[development commands](specs/PROJECT.md#development-commands).

## Command-line interface

Select an action before the input PDF: `pdfwtf ACTION INPUT_PDF`.
Both arguments are required.

| Action | Result |
| --- | --- |
| `enhance` | Process a PDF with optional scan cleanup, OCR, and exports. |
| `analyze` | Write analysis proposals from existing PDF text without OCR. |
| `export` | Write selected exports without OCR or a processed PDF. |

List the actions and their options:

```text
uv run --locked pdfwtf --help
uv run --locked pdfwtf enhance --help
uv run --locked pdfwtf analyze --help
uv run --locked pdfwtf export --help
```

Use a filename or relative path below the configured input directory.
The default input directory is `PDFWTF_HOME/instance/_data/in`.
Use an absolute path to select a PDF outside this directory.
The CLI does not search the working directory.

The default output directory is `PDFWTF_HOME/instance/_data/out`.
Use `--outdir` to select another output directory.

### Process a PDF

Process a PDF and export its text:

```text
uv run --locked pdfwtf enhance input.pdf --outdir output --get-text
```

Use `--pdf_type auto` for automatic scan detection. This is the default.
Use `--pdf_type scanned` to force scan preparation and OCR.

If the PDF has existing text, use `--pdf_type born-digital` to bypass scan
preparation and OCR:

```text
uv run --locked pdfwtf enhance input.pdf --pdf_type born-digital --get-text
```

With `born-digital`, text exports contain only existing extractable text.
Do not combine this PDF type with `--remove-bg`, `--layout single`,
`--layout double`, `--output-pages`, or `--pre-rotate`.

### Select pages and exports

Use `--extract` to keep input pages in the processed PDF.
Use `--remove` to remove input pages from that PDF.
Both options use absolute input page indices starting at 1.
If a page occurs in both lists, removal takes precedence.
These options do not filter page exports.

```text
uv run --locked pdfwtf enhance input.pdf --extract 1-5 --remove 2
```

Select the required exports:

| Option | Output |
| --- | --- |
| `--get-doi` | DOI candidates from the first derivative page and JSON metadata |
| `--get-format` | Page image format. The only supported format is PNG. |
| `--get-html` | UTF-8 HTML fragments for selected units. |
| `--get-img` | PNG page images |
| `--get-meta` | JSON metadata |
| `--get-text` | Per-page text and combined text |
| `--get-thumb` | Page images and JPEG thumbnails |

DOI candidates are not externally verified.
If you also need text files, use `--get-text`.

To export existing PDF content without OCR, use `export`:

```text
uv run --locked pdfwtf export input.pdf --get-text
uv run --locked pdfwtf export input.pdf --get-meta --get-img --get-thumb
```

For OCR before export without a final PDF, use `enhance --no-pdf-out`:

```text
uv run --locked pdfwtf enhance input.pdf --no-pdf-out --get-text
```

### Analyze and export units

The container workflow uses existing PDF text and whole-page unit boundaries.
Write analysis proposals:

```text
uv run --locked pdfwtf analyze issue.pdf --doc_type journal-issue
```

Review the proposals in a consuming application, such as the demo GUI.
Save the confirmed page ranges, unit types, and selections in a separate plan.
Export the reviewed units as metadata and HTML:

```text
uv run --locked pdfwtf export issue.pdf --plan issue.plan.json --get-html
```

To select specific reviewed unit types, add `--unit_type`:

```text
uv run --locked pdfwtf export issue.pdf --plan issue.plan.json --get-html --unit_type article
```

Repeat `--unit_type` to select several types.
For schemas, output filenames, and analysis limits, see the
[container analysis specification](specs/CONTAINER_ANALYSIS.md).

## Demo GUI

PDF-WTF-GUI demonstrates PDF review and unit export.
Run the demo in Docker with Docker Desktop or Docker Engine and Docker Compose.

Open http://127.0.0.1:5001 in a browser.

1. Upload a PDF.
2. Select the document type in the file row.
3. Select Analyze.
4. Select Review to confirm unit titles, types, ranges, and inclusion.
5. Save the plan.
6. Select Export.
7. Download the results.

Upload does not start analysis.
Analysis and export use existing PDF text without OCR.

The standalone host uses the GUI demo adapter for analysis and export.
This adapter is not a production queue.
For host integration, see the
[GUI host configuration](specs/CONFIGURATION.md#gui-host-configuration).

After source, dependency, or configuration changes, rebuild the image.
Do not run `pdfwtf-gui` directly on the host.

Stop the demo from the repository root:

```text
docker compose -f compose-pdf-wtf-gui.yaml down
```

Uploaded documents and saved plans remain in `PDFWTF_HOME/instance/_data`.
For status checks, logs, and development commands, see the
[project specification](specs/PROJECT.md#development-commands).

## Specifications

- [Project](specs/PROJECT.md): scope, components, processing rules, and development commands.
- [Configuration](specs/CONFIGURATION.md): environment setup, paths, and host integration.
- [Container analysis](specs/CONTAINER_ANALYSIS.md): reviewed plans, schemas, and unit results.

## Application log files

The CLI appends operational records to `pdf-wtf-log.txt`.
The standalone GUI appends operational records to `pdf-wtf-gui-log.txt`.
Both files use UTF-8 and the configured `logs_dir`.
The default directory is `PDFWTF_HOME/instance/logs`.
Console logging remains enabled. CLI `--debug` enables debug severity.
The GUI logs request methods, endpoint names, status codes, and client addresses.
It does not log request query strings or bodies.
The demo container mounts `instance/logs` from the host.
Log files are not rotated automatically.
