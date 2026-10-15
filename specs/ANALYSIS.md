# PDF analysis (diagnostics)

## Purpose

`pdfwtf analyze INPUT_PDF` diagnoses PDF properties and preparation needs.
It writes a JSON report with one entry for each page and advisory
recommendations. Analysis is optional. No other action requires it.

| Action | Responsibility |
| --- | --- |
| `analyze` | Diagnose PDF properties and preparation needs. |
| `enhance` | Prepare the PDF: cleanup, page selection, and OCR. |
| `partition` | Identify logical units. |
| `export` | Generate outputs. |

The analysis report is not a partition plan. `partition` and `export` do not
read it.

## GUI

The PDF-WTF-GUI file row has an Analyze button. It analyzes the active
PDF in the background and stores the report as
`<output-dir>/<job-id>/analysis.json`. With a current report, the button
opens a modal with the summary, Download report, and Run again.
`specs/PROJECT.md` describes the routes and states.

## Boundaries

Analysis opens the PDF read-only with PyMuPDF. It does not modify the PDF,
render pages, run cleanup or OCR, call an external OCR or LLM service,
partition, or export. The service is `analyze_document` in
`src/pdfwtf/diagnostics.py`. It does not depend on CLI or GUI code. The
pipeline operation `analyze_pdf` writes the report.

## Command

```bash
uv run --locked pdfwtf analyze issue.pdf
uv run --locked pdfwtf analyze /data/in/issue.pdf --outdir /data/out
```

The command accepts `--outdir`, `--prefix`, `--debug`, `--llm-task`, and
`--list-llm-tasks`. Input and output paths resolve as for the other actions.
It inspects all pages. It writes `<input-stem>.analysis.json` in the resolved
output directory. It stages the report in the same directory and then
replaces the destination, so a reader never sees a partial report.

The command prints the page count, the document composition, the page counts
by composition and OCR need, each recommendation with its pages, the number of
warnings, and the report path.

### Exit behavior

- Exit status 0: inspection finished and the report was written. This also
  applies when the report has recommendations or warnings, and when some
  pages could not be inspected. In that case, the report has
  `summary.inspection` `incomplete`, and the command prints the affected pages.
- Exit status 1: the file is unreadable, is not a valid PDF, has no pages, or
  is encrypted and needs a password. No report is written. A selected LLM
  task also causes exit status 1 before inspection starts.
- Exit status 2: invalid command-line usage, for example an option of
  another action.

### LLM tasks

`analyze` accepts the shared LLM options. No LLM stage supports analysis yet.
A selected task is rejected before processing starts, and it is never
skipped silently. Without selected tasks, analysis needs no API key.

## Report contract

The report is UTF-8 JSON. All page numbers start at 1. A property that cannot
be determined is `null`. A report with schema version `"1.0"` has these
top-level fields, in this order:

| Field | Content |
| --- | --- |
| `schema_version` | `"1.0"`. This version is independent of the workflow schema version. |
| `kind` | `"pdf-analysis"` |
| `created_at` | UTC timestamp in ISO 8601 format, ending in `Z` |
| `source` | `filename`, `size_bytes`, and `sha256` of the file content |
| `document` | `page_count`, `pdf_version`, `metadata`, `outline_entries`, and `encryption` |
| `summary` | Aggregate findings and recommendations |
| `pages` | One entry for each page, in page order |
| `warnings` | Document-level limitations and inspection problems |

Before workflow schema version 1.4, partition files were also named
`<stem>.analysis.json`, with `kind` `analysis`. A diagnostic report always
has `kind` `pdf-analysis`.

### `document`

- `metadata` has `title`, `author`, `subject`, `keywords`, `creator`,
  `producer`, `creation_date`, and `modification_date`. An empty value is
  `null`. Dates keep the PDF date format.
- `encryption` has `encrypted`, `method` (or `null`), and `permissions`. For
  an encrypted PDF that opens without a password, `permissions` has the
  Boolean fields `print`, `copy`, `modify`, and `annotate`. For an
  unencrypted PDF, `permissions` is `null`.
- `outline_entries` is the number of bookmarks, or `null` if they cannot be
  read.

### Page entry

| Field | Content |
| --- | --- |
| `number` | One-based page number |
| `status` | `ok` or `error` |
| `size` | `width` and `height` in PDF points of the unrotated crop box, and `label` (`A3`, `A4`, `A5`, `Letter`, `Legal`, or `null`) |
| `rotation` | Declared `/Rotate` value in degrees |
| `composition` | `born_digital`, `scanned`, `mixed`, `blank`, or `uncertain` |
| `text` | `characters` (non-whitespace), `words`, `visible_characters`, `invisible_characters`, `replacement_characters`, `control_characters`, `private_use_characters`, `suspect_character_ratio`, `meaningful`, and `fonts` |
| `images` | `count` (including inline images) and `coverage` from 0 to 1 |
| `drawings` | Number of vector drawing paths |
| `ocr` | `need`, `text_layer`, `quality`, and `reasons` |
| `findings` | List of `code`, `severity` (`info`, `warning`, or `error`), and `message` |
| `recommendations` | List of `action` and `reason` |

The report contains no extracted document text.

If a page cannot be inspected, its entry remains. Then `status` is `error`,
the inspected properties are `null`, and `findings` has an
`inspection_error` entry. The report is then incomplete.

### OCR assessment

The OCR assessment is separate from the composition. A scanned page can
already have a usable text layer. A mixed page can have digital text and
images whose text content is unknown.

| Field | Values |
| --- | --- |
| `need` | `likely`, `unlikely`, or `uncertain` |
| `text_layer` | `absent`, `present`, or `suspected_ocr` |
| `quality` | `apparently_usable`, `suspect`, `unknown`, or `not_applicable` |
| `reasons` | Evidence codes, for example `image_coverage_1.00`, `no_extractable_text`, `invisible_text_layer`, `ocr_font`, `meaningful_text`, `insufficient_text`, `suspect_characters`, `little_text`, and `images_may_contain_text` |

### `summary`

| Field | Content |
| --- | --- |
| `inspection` | `complete` or `incomplete` |
| `document_composition` | `born_digital`, `scanned`, `mixed`, `blank`, `uncertain`, or `heterogeneous` |
| `pages_by_composition` | Page count for each composition |
| `pages_by_ocr_need` | Page count for each OCR need |
| `pages_without_text` | Pages without extractable text |
| `pages_with_suspect_text` | Pages with `quality` `suspect` |
| `blank_pages`, `uncertain_pages`, `error_pages` | Page lists |
| `page_sizes` | List of `width`, `height`, `label`, and `pages` |
| `rotations` | Map from declared rotation to pages |
| `recommendations_are_advisory` | Always `true` |
| `recommendations` | List of `action`, `pages`, `reason`, and, for `run_ocr`, `suggested_command` |

`heterogeneous` means that the document has pages of different
compositions. Blank pages are not counted for this decision. `mixed` means
that the pages themselves combine digital text with images. When all pages
are blank, the composition is `blank`. When no page could be inspected, it is
`uncertain`.

## Classification rules

The rules use observable PDF properties and these constants in
`src/pdfwtf/diagnostics.py`:

| Constant | Value | Meaning |
| --- | --- | --- |
| `SCAN_COVERAGE` | 0.85 | Image coverage of a page image |
| `IMAGE_COVERAGE` | 0.05 | Minimum coverage that counts as image content |
| `BAD_CHARACTER_RATIO` | 0.01 | Maximum ratio of suspect characters |

**Text.** Extractable text comes from the PDF text layer. Text is
`meaningful` when, after page-number lines are removed, it has at least 30
characters and 5 words. Text is suspected OCR when invisible text (render
mode 3) is at least as frequent as visible text, or when a font name contains
`GlyphLessFont`.

**Image coverage.** Coverage is the union area of all image placement
rectangles, clipped to the page, divided by the page area. Overlapping images
are counted once.

**Composition**, in this order:

1. `blank`: no text, no images, and no drawings.
2. Coverage ≥ 0.85: `mixed` when the page has meaningful text that is not
   suspected OCR, otherwise `scanned`.
3. Coverage ≥ 0.05: `mixed` when the page has meaningful text that is not
   suspected OCR, otherwise `uncertain`.
4. Otherwise `born_digital`. This includes vector-only pages.

**Text layer.** `absent` without characters, `suspected_ocr` by the rule
above, otherwise `present`.

**Text quality**, in this order:

1. `not_applicable` without a text layer.
2. `suspect` when the suspect-character ratio is above 0.01.
3. `suspect` when the text is not meaningful on a `scanned`, `mixed`, or
   `uncertain` page.
4. `unknown` when the text is not meaningful on another page.
5. Otherwise `apparently_usable`.

**OCR need.**

- `unlikely` for blank pages.
- For scanned pages: `unlikely` with apparently usable text, otherwise
  `likely`.
- `unlikely` for born-digital pages with apparently usable text.
- Otherwise `uncertain`. This includes mixed pages, uncertain pages, and
  vector-only pages, where text can be drawn as outlines.

**Recommendations**, for each page:

- `run_ocr` when the OCR need is `likely`.
- `review_ocr_quality` when the quality is `suspect` and OCR is not already
  recommended.
- `check_page_content` for `uncertain` pages.
- `review_blank_page` for `blank` pages.

The summary groups the recommendations by action. OCR is recommended only for
the pages that need it, not for every page of a document with scans.
`enhance` rasterizes and runs OCR on all selected pages when it detects a
scan. When other pages have usable digital text, the `run_ocr` reason says
so and suggests `--extract`. Recommendations are advisory.

**Rotation.** The declared rotation is reported, and a `declared_rotation`
finding is added. Analysis does not infer skew or wrong visual orientation
from it.

## Limitations

- All classifications are heuristics. Extractable text does not prove
  complete or accurate OCR. An image does not prove that a page contains text.
- Image coverage ignores clip paths, soft masks, and transparency. It uses
  placement rectangles, not pixel content.
- Text drawn as vector outlines is not detected as text.
- Hidden text that is not OCR, for example white text, counts as visible.
- Page sizes use the crop box. Other page boxes are not reported.

## Representative report

```json
{
  "schema_version": "1.0",
  "kind": "pdf-analysis",
  "created_at": "2026-10-15T10:39:25.224330Z",
  "source": {
    "filename": "sample.pdf",
    "size_bytes": 1507227,
    "sha256": "ae53e3f0074bb8f110d302315611c1c4564b828237f81d8e48427cb205cb4a30"
  },
  "document": {
    "page_count": 2,
    "pdf_version": "PDF 1.7",
    "metadata": {
      "title": "Sample issue",
      "author": null,
      "subject": null,
      "keywords": null,
      "creator": null,
      "producer": "Example producer",
      "creation_date": null,
      "modification_date": null
    },
    "outline_entries": 0,
    "encryption": {"encrypted": false, "method": null, "permissions": null}
  },
  "summary": {
    "inspection": "complete",
    "document_composition": "heterogeneous",
    "pages_by_composition": {
      "born_digital": 1, "scanned": 1, "mixed": 0, "blank": 0, "uncertain": 0
    },
    "pages_by_ocr_need": {"likely": 1, "unlikely": 1, "uncertain": 0},
    "pages_without_text": [2],
    "pages_with_suspect_text": [],
    "blank_pages": [],
    "uncertain_pages": [],
    "error_pages": [],
    "page_sizes": [
      {"width": 595.28, "height": 841.89, "label": "A4", "pages": [1, 2]}
    ],
    "rotations": {"0": [1, 2]},
    "recommendations_are_advisory": true,
    "recommendations": [
      {
        "action": "run_ocr",
        "pages": [2],
        "suggested_command": "pdfwtf enhance INPUT_PDF --pdf_type scanned",
        "reason": "These pages appear scanned and have no usable text layer. Note: enhance rasterizes and runs OCR on all selected pages when it detects a scan. Other pages have usable digital text. Use --extract to limit the enhanced pages if necessary."
      }
    ]
  },
  "pages": [
    {
      "number": 1,
      "status": "ok",
      "size": {"width": 595.28, "height": 841.89, "label": "A4"},
      "rotation": 0,
      "composition": "born_digital",
      "text": {
        "characters": 67,
        "replacement_characters": 0,
        "control_characters": 0,
        "private_use_characters": 0,
        "suspect_character_ratio": 0.0,
        "words": 13,
        "visible_characters": 67,
        "invisible_characters": 0,
        "meaningful": true,
        "fonts": ["Helvetica"]
      },
      "images": {"count": 0, "coverage": 0.0},
      "drawings": 0,
      "ocr": {
        "need": "unlikely",
        "text_layer": "present",
        "quality": "apparently_usable",
        "reasons": ["image_coverage_0.00", "meaningful_text"]
      },
      "findings": [],
      "recommendations": []
    },
    {
      "number": 2,
      "status": "ok",
      "size": {"width": 595.28, "height": 841.89, "label": "A4"},
      "rotation": 0,
      "composition": "scanned",
      "text": {
        "characters": 0,
        "replacement_characters": 0,
        "control_characters": 0,
        "private_use_characters": 0,
        "suspect_character_ratio": 0.0,
        "words": 0,
        "visible_characters": 0,
        "invisible_characters": 0,
        "meaningful": false,
        "fonts": []
      },
      "images": {"count": 1, "coverage": 0.9994},
      "drawings": 0,
      "ocr": {
        "need": "likely",
        "text_layer": "absent",
        "quality": "not_applicable",
        "reasons": ["image_coverage_1.00", "no_extractable_text"]
      },
      "findings": [
        {"code": "full_page_image", "severity": "info", "message": "Images cover 100% of the page."},
        {"code": "no_extractable_text", "severity": "info", "message": "The page has no extractable text."}
      ],
      "recommendations": [
        {"action": "run_ocr", "reason": "These pages appear scanned and have no usable text layer."}
      ]
    }
  ],
  "warnings": [
    "Classifications and OCR assessments are heuristics. Extractable text does not prove complete or accurate OCR, and an image does not prove that a page contains text.",
    "Image coverage uses image placement rectangles clipped to the page. It ignores clip paths, transparency, and image content.",
    "Declared page rotation is reported as is. Physical skew and visual orientation are not inspected."
  ]
}
```
