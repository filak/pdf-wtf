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

## Licenses

See [Licenses](specs/LICENSES.md).

## Installation

Use Python 3.12, Git, and [uv](https://docs.astral.sh/uv/).

Download or clone the repository:

```bash
git clone https://github.com/filak/pdf-wtf.git
cd pdf-wtf
```

To update the repo run:

```bash
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

## Docker

> If You do not want to install anything just spin up the Docker

```bash
docker buildx bake -f compose-pdf-wtf-gui.yaml --allow=fs.read=.Dockerfile-pdf-wtf-gui --load
docker compose -f compose-pdf-wtf-gui.yaml up -d
```

and open PDF-WTF-GUI in a browser http://127.0.0.1:5001

To try the PDF-WTF client - run

```bash
docker compose -f compose-pdf-wtf-gui.yaml exec pdf-wtf-gui pdfwtf --help
```

To update the Docker use:

```bash
git pull --ff-only
docker compose -f compose-pdf-wtf-gui.yaml up -d --build
```

## Development

Run the following commands from the repository root.
Install the locked Python dependencies:

```bash
uv sync --locked --all-extras
```

Testing:

```bash
uv run pytest
```

For the Windows unpaper wrapper, see the
[development commands](specs/PROJECT.md#development-commands).

## Command-line interface

Select an action before the input PDF: `pdfwtf ACTION INPUT_PDF`.
Both arguments are required. Each action is a separate step.

| Action | Responsibility | Output |
| --- | --- | --- |
| `analyze` | Diagnose PDF properties and preparation needs. It does not change the PDF. | `<stem>.analysis.json` diagnostic report |
| `enhance` | Prepare physical pages: scan cleanup, rotation, splitting, page selection, and OCR. | `<stem>.prepared.pdf` and `<stem>.enhance.json` |
| `partition` | Identify logical units from existing PDF text. It does not run OCR. | `<stem>.partition.json` for review |
| `export` | Write selected derivatives from the supplied PDF. It does not run cleanup or OCR. | Text, metadata, images, thumbnails, unit HTML, and unit PDFs |

`analyze`, `enhance`, and `partition` are optional. `export` can run alone
on any PDF. Run `analyze` to find out whether a PDF needs `enhance`.
Run `enhance` only when the PDF needs cleanup, page selection, or OCR.
Run `partition` only when you need a reviewed plan with several units.
No action checks that an earlier action ran.

List the actions and their options:

```bash
uv run --locked pdfwtf --help
uv run --locked pdfwtf analyze --help
uv run --locked pdfwtf enhance --help
uv run --locked pdfwtf partition --help
uv run --locked pdfwtf export --help
```

Use a filename or relative path below the configured input directory.
The default input directory is `PDFWTF_HOME/instance/_data/in`.
Use an absolute path to select a PDF outside this directory.
The CLI does not search the working directory.

The default output directory is `PDFWTF_HOME/instance/_data/out`.
Use `--outdir` to select another output directory.

### Analyze a PDF

Analysis is a read-only diagnostic. It reports page composition, text
statistics, image coverage, an OCR assessment, and advisory
recommendations for each page:

```bash
uv run --locked pdfwtf analyze issue.pdf
```

The command writes `<stem>.analysis.json` and prints a summary:

```text
Pages        :  12
Composition  :  heterogeneous (born_digital=10, scanned=2)
OCR need     :  likely=2, unlikely=10, uncertain=0
Advice       :  run_ocr for pages 11-12
Warnings     :  3
Report       :  .../instance/_data/out/issue.analysis.json
```

The classifications are heuristics. Recommendations are advisory.
The report is not a partition plan.
The command exits with status 0 when the report is written, also with
warnings or pages that could not be inspected. It exits with status 1
for an unreadable, invalid, or password-protected PDF.
For the report structure and rules, see the
[analysis specification](specs/ANALYSIS.md).

### Staged workflow

1. Optional: enhance the source PDF once.

   ```bash
   uv run --locked pdfwtf enhance issue.pdf --outdir /data/out
   ```

   The result is `/data/out/issue.prepared.pdf`. The source PDF does not change.

2. Optional: partition the prepared PDF, and review the plan.

   ```bash
   uv run --locked pdfwtf partition /data/out/issue.prepared.pdf --outdir /data/out --doc_type journal-issue
   ```

   Give the prepared PDF as an absolute path. A relative path starts at the
   input directory, not at the output directory.

3. Export from the prepared PDF. Repeat this step as necessary.

   ```bash
   uv run --locked pdfwtf export /data/out/issue.prepared.pdf --outdir /data/out --get-text
   uv run --locked pdfwtf export /data/out/issue.prepared.pdf --outdir /data/out --plan issue.plan.json --get-html --get-unit-pdf
   ```

To export without the optional steps, run `export` on the original PDF:

```bash
uv run --locked pdfwtf export issue.pdf --get-text
uv run --locked pdfwtf export issue.pdf --doc_type unit --get-html
```

### Enhance a PDF

Use `--pdf_type auto` for automatic scan detection. This is the default.
Use `--pdf_type scanned` to force scan preparation and OCR.

If the PDF has existing text, use `--pdf_type born-digital` to bypass scan
preparation and OCR. The prepared PDF is then a copy of the selected pages.
Do not combine this PDF type with `--remove-bg`, `--layout single`,
`--layout double`, `--output-pages`, or `--pre-rotate`.

Use `--extract` to keep input pages in the prepared PDF.
Use `--remove` to remove input pages from that PDF.
Both options use absolute input page indices starting at 1.
If a page occurs in both lists, removal takes precedence.

```bash
uv run --locked pdfwtf enhance input.pdf --extract 1-5 --remove 2
```

The `<stem>.enhance.json` report records the settings, the identity of the
input and prepared PDFs, and whether scan processing ran.

These former `enhance` options are removed: `--no-pdf-out`, `--plan`,
`--doc_type`, `--unit_type`, `--get-doi`, `--get-format`, `--get-html`,
`--get-img`, `--get-meta`, `--get-text`, and `--get-thumb`.
If you use one of them, `enhance` stops and names the action that owns it.
Physical page splitting (`--output-pages`) is an enhancement. Logical units
are a partition and export concept.

### Export a PDF

Export uses the supplied PDF as it is. It renders page images and thumbnails
and can extract unit PDFs. It does not create text from scanned images.
To export text from a scan, enhance it first, then export the prepared PDF.

| Option | Output |
| --- | --- |
| `--get-doi` | DOI candidates and JSON metadata |
| `--get-format` | Page image format. The only supported format is PNG. |
| `--get-html` | UTF-8 HTML fragments for selected units |
| `--get-img` | PNG page images |
| `--get-meta` | JSON metadata |
| `--get-text` | Per-page text and combined text |
| `--get-thumb` | Page images and JPEG thumbnails |
| `--get-unit-pdf` | One PDF for each selected unit. Requires `--plan`. |

DOI candidates are not externally verified.
If you also need text files, use `--get-text`.

### Partition and export units

The container workflow uses existing PDF text and whole-page unit boundaries.
Write partition proposals:

```bash
uv run --locked pdfwtf partition /data/out/issue.prepared.pdf --doc_type journal-issue
```

Journal partitioning combines local title, author, affiliation, multilingual
abstract, keywords, and article DOI evidence. It also checks matching PDF
bookmarks, linked contents, and printed contents entries. Large titles and
abstracts are not required. Internal sections and repeated headers do not
establish starts by themselves. Page warnings report opening decisions and
evidence. Ambiguous shared-page starts still require review.

Review the proposals in a consuming application, such as the demo GUI.
Save the confirmed page ranges, unit types, and selections in a separate plan.
Export the reviewed units as metadata and HTML:

```bash
uv run --locked pdfwtf export /data/out/issue.prepared.pdf --plan issue.plan.json --get-html
```

A plan is valid only for the exact PDF that you partitioned. Export compares
the SHA-256 hash and page count of the PDF with the plan. If they differ,
export stops with this message: "The plan was created for a different PDF."
A moved or renamed copy of the same PDF is accepted.
If you enhance the PDF again, partition the new prepared PDF again.

To select specific reviewed unit types, add `--unit_type`:

```bash
uv run --locked pdfwtf export /data/out/issue.prepared.pdf --plan issue.plan.json --get-html --unit_type article
```

Repeat `--unit_type` to select several types.
For schemas, output filenames, and partitioning limits, see the
[container partitioning specification](specs/CONTAINER_PARTITION.md).

### LLM tasks

LLM tasks are optional. They send unit HTML to Gemini. Ordinary processing
does not need an API key.

All actions, including `analyze`, accept `--llm-task` and
`--list-llm-tasks`. Export is currently
the only action with an implemented LLM stage. If you select a task that
an action cannot run, the action stops before processing starts.

`gemini-call` reads `GEMINI_API_KEY` only from the operating-system
environment. It does not read `.env`. If the key is only in
`PDFWTF_HOME/.env`, LLM tasks are not available.

On Windows, open CMD and set the key for future processes:

```cmd
setx GEMINI_API_KEY "your-key"
```

Restart the terminal or VS Code after you set the key.

List the tasks for an action and their availability:

```bash
uv run --locked pdfwtf export --list-llm-tasks
```

Run a task with `--llm-task`. The `clean_html` task requires `--get-html`:

```bash
uv run --locked pdfwtf export /data/out/issue.prepared.pdf --plan issue.plan.json --get-html --llm-task clean_html
```

Repeat `--llm-task` to select several tasks. The task writes
`<unit-id>.clean.html` next to the original `<unit-id>.html`. The manifest
records the status and token usage of each task. If a task fails for a unit,
the other exports are still written. The command then exits with an error and
lists the failed units.

In the demo GUI, select LLM tasks in the Export modal.
For task configuration, see the
[configuration specification](specs/CONFIGURATION.md).

### Migration from the combined `enhance` action

- Move export options from `enhance` to `export`: `--get-*`, `--plan`,
  `--doc_type`, and `--unit_type`. Run `export` on the prepared PDF.
- Remove `--no-pdf-out`. Enhancement always writes the prepared PDF. Use
  `export` when you need derivatives without a PDF.
- Expect `<stem>.prepared.pdf` instead of `<stem>.pdf` from `enhance`.
- Export metadata now has `"output": null`. Page `skip` flags are no longer
  written, and `keep` flags come only from a reviewed plan.
- Change `"commands": ["export", "enhance"]` to `"commands": ["export"]` in
  `LLM_TASKS`. The earlier value now stops configuration loading.
- Partition the PDF that you export. A plan for the original PDF does not fit
  a prepared PDF with changed content or page structure.
- Python callers: use `enhance_pdf`, `partition_pdf`, or `export_pdf`.
  `process_pdf` remains as a dispatcher and rejects misplaced options.
- GUI hosts: rename nothing. Optionally set `PDFWTF_GUI_PREPARE_ADAPTER`.
  `start_export` can receive `plan=None`. See the
  [GUI host configuration](specs/CONFIGURATION.md#gui-host-configuration).

## Demo GUI

PDF-WTF-GUI demonstrates PDF preparation, review, and unit export.
Run the demo in Docker with Docker Desktop or Docker Engine and Docker Compose.

Open http://127.0.0.1:5001 in a browser.

1. Upload a PDF.
   Optional: select Analyze to diagnose the PDF. After the analysis,
   select Analyze again to see the report summary, download the report,
   or run the analysis again.
2. Optional: for a scanned PDF, open Prepare PDF in the file row, and
   select Prepare.
   The prepared PDF then becomes the active PDF.
3. Optional: select the document type, and select Partition.
4. Optional: select Review to confirm unit titles, types, ranges, and
   inclusion. Save the plan.
5. Select Export. Without a saved plan, export uses the whole active PDF as
   one unit.
6. Download the results.

After preparation, the file row shows "Active: prepared PDF". A row without
this badge uses the original PDF.
Preview, partitioning, review, and export use the active PDF.
Preparation always starts from the original PDF. After preparation, the GUI
deletes the saved partition, plan, and export archive of the earlier active PDF.
Upload does not start preparation or partitioning.
Partitioning and export do not run OCR.

The standalone host uses the GUI demo adapter for preparation, partitioning,
and export. This adapter is not a production queue.
For host integration, see the
[GUI host configuration](specs/CONFIGURATION.md#gui-host-configuration).

After source, dependency, or configuration changes, rebuild the image.
Do not run `pdfwtf-gui` directly on the host.

Stop the demo from the repository root:

```bash
docker compose -f compose-pdf-wtf-gui.yaml down
```

Uploaded documents and saved plans remain in `PDFWTF_HOME/instance/_data`.
For status checks, logs, and development commands, see the
[project specification](specs/PROJECT.md#development-commands).

## Specifications

- [Project](specs/PROJECT.md): scope, components, processing rules, and development commands.
- [Configuration](specs/CONFIGURATION.md): environment setup, paths, and host integration.
- [Container partitioning](specs/CONTAINER_PARTITION.md): reviewed plans, schemas, and unit results.
- [Analysis](specs/ANALYSIS.md): the diagnostic report, classification rules, and limitations.

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
