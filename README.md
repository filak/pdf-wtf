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

Process a PDF with the command-line interface:

```
uv run --locked pdfwtf input.pdf --outdir output --get-text
```

Use a filename or a relative path below `PDFWTF_HOME/instance/_data/in`.
Use an absolute path to select a file outside this directory.


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
docker build -t unpaper-alpine -f dockers/.Dockerfile-unpaper .
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

Production installation remains subject to
[the deployment specification](specs/DEPLOYMENT.md).

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
uv run --locked pdfwtf input.pdf --no-pdf-out --get-json --get-img --get-thumb
```

Use `--get-json` to write JSON metadata. JSON includes `doi` and `pages`. The `doi` list is empty unless DOI extraction
is requested. Use `--get-doi` to find DOI links on the first output page and write
JSON metadata. This option writes text files only if `--get-text` is also set.

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
