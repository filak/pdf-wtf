# Container analysis and unit export

## Purpose and scope

This specification defines born-digital container analysis, reviewed unit plans,
and per-unit HTML export. `specs/PROJECT.md` owns the general processing and
security rules. This specification owns only the container workflow and its JSON
contracts.

This iteration supports PDFs with extractable text. A unit starts and ends at an
input page boundary. Scanned-document segmentation and within-page unit
boundaries are not supported. A consuming application, such as BMF, owns plan
review and editing.

## Workflow

Use this sequence:

1. Run `--analysis` to write machine proposals and evidence.
2. Review the analysis in the consuming application.
3. Write a plan with confirmed whole-page boundaries.
4. Run `--plan PATH` to process selected units.
5. Add `--get-html` to write one HTML fragment for each selected unit.

An analysis proposes units and extracts DOI candidates for each proposed unit.
It does not generate unit result files. Plan processing never repeats partition
analysis. `--doctype unit --get-html` can process the complete PDF without a
plan.

The source PDF is always the required positional argument. `--doctype`
describes document structure. It does not describe whether the PDF is scanned.
The container workflow currently requires `--born-digital`.

## Common JSON fields

All workflow JSON documents use UTF-8 and `schema_version` value `"1.2"`.
The `source` object has these fields:

```json
{
  "sha256": "64 lowercase hexadecimal characters",
  "page_count": 12,
  "bibliographic": {
    "kind": "journal",
    "journal_title": "Journal title",
    "issn": "1234-567X",
    "year": "2026",
    "volume": "12",
    "issue": "3"
  }
}
```

For a book, `bibliographic.kind` is `book`. The bibliographic object then has
`book_title`, `isbn`, and `volume`. Unknown values are `null`. ISBN values use
digits and an optional final `X`. ISSN values use the `1234-567X` form.

`page_count` counts input PDF pages. All `input_pages` ranges are 1-based and
inclusive. Printed page numbers are strings or `null`. They never replace input
page indices.

A unit has these common fields:

```json
{
  "id": "unit-001",
  "title": "Article title",
  "type": "article",
  "input_pages": {"start": 3, "end": 8}
}
```

`type` is `preface`, `editorial`, `table-of-contents`, `programme`, `article`,
`chapter`, `abstract-or-poster`, `book-review`, or `unknown`.

Unit IDs contain only ASCII letters, digits, period, underscore, and hyphen.
They start with a letter or digit. They are unique in one document. Windows
reserved filenames are not valid unit IDs.

Ranges can have gaps. A gap identifies excluded material. Ranges cannot
overlap. A plan cannot assign one page to two units. A possible shared page must
remain unresolved until a reviewer selects a supported whole-page boundary.

## Analysis contract

The analysis filename is `<source-stem>.analysis.json`. It has this shape:

```json
{
  "schema_version": "1.2",
  "kind": "analysis",
  "source": {
    "sha256": "...",
    "page_count": 1,
    "bibliographic": {
      "kind": "journal",
      "journal_title": "Journal title",
      "issn": "1234-567X",
      "year": "2026",
      "volume": "12",
      "issue": "3"
    }
  },
  "document_type": "journal-issue",
  "coordinate_system": {
    "origin": "top-left",
    "units": "PDF points",
    "x_direction": "right",
    "y_direction": "down"
  },
  "classification_evidence": [],
  "pages": [
    {
      "input_page": 1,
      "printed_page_number": "1",
      "page_type": "normal",
      "page_type_evidence": ["The page contains substantial extractable text."],
      "main_title": {
        "text": "Article title",
        "bbox": [72.0, 80.0, 420.0, 104.0]
      },
      "sections": [
        {
          "title": "Introduction",
          "level": 2,
          "bbox": [72.0, 180.0, 220.0, 198.0]
        }
      ],
      "figures": [
        {
          "id": "figure-001-001",
          "bbox": [72.0, 220.0, 420.0, 480.0],
          "xref": 17,
          "occurrence": 1,
          "width": 1200,
          "height": 900,
          "colorspace": 3,
          "caption": "Figure 1. Example.",
          "caption_bbox": [72.0, 486.0, 420.0, 502.0]
        }
      ],
      "unit_ids": ["unit-001"],
      "warnings": []
    }
  ],
  "units": [
    {
      "id": "unit-001",
      "title": "Article title",
      "type": "article",
      "input_pages": {"start": 1, "end": 1},
      "doi": ["10.1234/example"],
      "boundary_status": "proposed",
      "boundary_evidence": ["prominent title typography", "author pattern"]
    }
  ],
  "overlaps": [],
  "suspected_shared_page_boundaries": [],
  "warnings": []
}
```

`document_type` is `unit`, `journal-issue`, `book`, `proceedings`, or `unknown`.
`--doctype auto` uses document-level evidence to propose the value. It can
produce `unknown`. An uncertain result requires review.

`pages` contains one entry for each input page in input order. `input_page` is
the absolute 1-based input index. `printed_page_number` is a string or `null`.
`page_type` is `normal`, `full-page-advertisement`, or `unknown`.
`page_type_evidence` explains the machine proposal.

`main_title`, section titles, figures, and captions use `coordinate_system`.
`main_title` is an object or `null`. Each
section has a title, heading level from 1 through 6, and bounding box. Each
raster figure has a page-stable ID, bounding box, image metadata, optional
caption, and optional caption bounding box. `unit_ids` links the page to every
proposed unit whose range contains it. `warnings` contains page-specific review
notices. The analysis does not persist full page text, text spans, bookmarks,
or links.

Extracted strings use Unicode compatibility normalization. The analyzer
converts compatibility ligatures and spaces, removes soft hyphens and format
characters, replaces invalid or private-use characters, and collapses
whitespace. It preserves valid letters and diacritics.

The analyzer excludes small raster decorations from `figures`. A raster
occurrence must cover at least 0.5 percent of the page, have displayed width
and height of at least 18 PDF points, and have source width and height of at
least 40 pixels. The analyzer retains the full internal structure only while it
classifies the document and builds the compact analysis.

The analyzer combines evidence. A heading or DOI alone does not create a unit.
A possible start below the top region of a page is written to
`suspected_shared_page_boundaries`. It is not converted to a proposed boundary.

Each `units[].doi` value is an array of unverified DOI candidates from that
unit's proposed page range. The array is empty when no candidate is found. DOI
identification uses the same candidate rules as `--get-doi`. Unlike
`--get-doi`, analysis checks all pages in each proposed unit.

## Reviewed plan contract

A reviewed plan has this shape:

```json
{
  "schema_version": "1.2",
  "kind": "plan",
  "source": {
    "sha256": "...",
    "page_count": 1,
    "bibliographic": {
      "kind": "journal",
      "journal_title": "Journal title",
      "issn": "1234-567X",
      "year": "2026",
      "volume": "12",
      "issue": "3"
    }
  },
  "document_type": "journal-issue",
  "pages": [
    {
      "input_page": 1,
      "selected": true,
      "printed_page_number": "1",
      "page_type": "normal",
      "page_type_evidence": ["The page contains substantial extractable text."],
      "main_title": {
        "text": "Article title",
        "bbox": [72.0, 80.0, 420.0, 104.0]
      },
      "sections": [],
      "figures": [],
      "unit_ids": ["unit-001"],
      "warnings": []
    }
  ],
  "units": [
    {
      "id": "unit-001",
      "title": "Article title",
      "type": "article",
      "selected": true,
      "boundary_status": "confirmed",
      "input_pages": {"start": 1, "end": 1}
    }
  ]
}
```

The processor validates the complete plan before it writes result files. It
checks the schema version, source fingerprint, page count, bibliographic source,
document type, page records, unit IDs, titles, types, selection values,
confirmation states, ranges, and overlaps.
It rejects `start_offset`, `end_offset`, `start_bbox`, `end_bbox`, and
`shared_page`. These fields represent unsupported within-page boundaries.

The plan contains one reviewed record for each input page. The GUI can correct
the printed page number, page type, main title, sections, figures, captions,
warnings, and unit links. The analysis file remains unchanged.

`pages[].selected` is the authority for page inclusion. At least one page must
be selected. A generated PDF contains the selected pages in input order. A
selected unit's metadata and HTML contain only its selected pages.

`units[].selected` is the authority for unit-result generation. At least one
unit must be selected. Each selected unit must contain a selected page. The
processor follows confirmed ranges exactly. It does not infer a replacement
range. `pages[].unit_ids` must agree with those ranges. If the user also
supplies `--doctype`, it must equal the plan's document type.

## Result contracts

Unit results are separate from whole-document derivatives. They are written to
`_units_<source-stem>`.

`manifest.json` has `schema_version`, `kind`, `source`, `document_type`, and a
`units` array. Each unit entry repeats its ID, title, type, input range, and
selected input pages. It links the unit metadata filename and the optional HTML
filename.

`<unit-id>.metadata.json` has `schema_version`, `kind`, `source`,
`document_type`, `unit`, `figures`, `warnings`, and `semantic_blocks`. Each
semantic block retains its absolute input page. Each figure has a stable figure
ID, input page, bounding box, image xref, and optional caption.

`<unit-id>.html` is a UTF-8 HTML fragment. It uses `h1` through `h6`, `p`,
`figure`, `figcaption`, and visible figure placeholder elements. Page markers
use detected printed numbers. If detection is uncertain, the marker explicitly
uses the input page index. Extracted text is HTML-escaped. The fragment has no
script, positioning CSS, or image URL.

## Reconstruction limits

The reading-order implementation handles common single-column pages and two
columns separated by full-width blocks. It uses native font size and font names
for conservative heading classification. It joins wrapped lines and removes a
hyphen only before a lowercase continuation.

Repeated margin text on two or more pages is treated as a running header or
footer. Raster images produce figure placeholders. A nearby caption that starts
with `Figure`, `Fig.`, or `Image` is associated with the figure and is not also
exported as a paragraph.

Vector graphics and table rules produce review warnings. Complex layouts,
multi-page figures, vector-only figures, tables, sidebars, footnotes, and true
within-page unit boundaries require review. The processor does not silently
discard a detected text block.
