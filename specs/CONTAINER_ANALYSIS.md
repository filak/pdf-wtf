# Container analysis and unit export

## Purpose and scope

This specification defines born-digital container analysis, reviewed unit plans,
and per-unit HTML export. `specs/PROJECT.md` owns the general processing and
security rules. This specification owns only the container workflow and its JSON
contracts.

This iteration supports PDFs with extractable text. A unit starts and ends at an
input page boundary. Scanned-document segmentation and within-page unit
boundaries are not supported. A consuming application owns plan
review and editing.

## Workflow

Use this sequence:

1. Run `pdfwtf analyse INPUT_PDF` to write machine proposals and evidence.
2. Review the analysis in the consuming application.
3. Write a plan with confirmed whole-page boundaries.
4. Run `--plan PATH` to process selected units.
5. Add `--get-html` to write one HTML fragment for each selected unit.

An analysis proposes units and extracts DOI candidates for each proposed unit.
It does not generate unit result files. Plan processing never repeats partition
analysis. `--doc_type unit --get-html` can process the complete PDF without a
plan.

The source PDF is always the required positional argument. `--doc_type`
describes document structure. It does not describe whether the PDF is scanned.
The `analyse` and `export` actions use existing PDF text and do not run OCR.
When `enhance` processes a reviewed plan or exports unit HTML, use
`--pdf_type born-digital`. Image-only pages have no extracted text in these workflows.

The `export` action accepts repeatable `--unit_type TYPE` filters.
Without a filter, selection is unchanged. Export filters the selected units from
`--plan`, or the direct unit from `--get-html`. It does not change the saved plan
or filter page derivatives. A filter requires one of these unit export options.
If no types match, export writes an empty unit manifest.

Analysis type detection uses title keywords and document-type defaults. These
are proposals for operator review. Journal and magazine units default to
`article` when no type keyword matches. Book units default to `chapter` when
no type keyword matches. Operators assign the correct types in
the reviewed plan before export. The `analyse` action does not filter unit types.

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
`chapter`, `abstract`, `poster`, `book-review`, `full-page-advertisement`, `cover`, or `unknown`.

Unit IDs contain only ASCII letters, digits, period, underscore, and hyphen.
They start with a letter or digit. They are unique in one document. Windows
reserved filenames are not valid unit IDs.

Ranges can have gaps. A gap identifies excluded material. Whole-page ranges
can overlap. A plan can assign one page to more than one unit.
Each selected unit includes the entire shared page in its results, subject to
page selection. This can duplicate content from another article.
The plan does not split or crop content within a page.
A shared page lists every containing unit in `pages[].unit_ids`, in unit order.

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

`document_type` is `unit`, `journal-issue`, `magazine-issue`, `book`, `proceedings`, or `unknown`.
`--doc_type auto` uses document-level evidence to propose the value. It can
produce `unknown`. An uncertain result requires review.

`pages` contains one entry for each input page in input order. `input_page` is
the absolute 1-based input index. `printed_page_number` is a string or `null`.
`page_type` is `normal`, `full-page-advertisement`, `cover`, or `unknown`.
`page_type_evidence` explains the machine proposal.

`main_title`, section titles, figures, and captions use `coordinate_system`.
Rectangles contain finite coordinates in the order `[x0, y0, x1, y1]`.
The right and bottom coordinates must not precede the left and top coordinates.
Coordinates can be negative when content extends beyond the page boundary.
Preserve these positions. Do not clip them during plan validation.
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

The GUI host adds top-level `created` and `updated` ISO 8601 UTC timestamps
to its saved plans. These values describe plan saves. They do not affect
page or unit selection. Older plans without these fields remain valid.

The processor validates the complete plan before it writes result files. It
checks the schema version, source fingerprint, page count, bibliographic source,
document type, page records, unit IDs, titles, types, selection values,
confirmation states, ranges, and page-to-unit links.
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
supplies `--doc_type`, it must equal the plan's document type.

## Result contracts

Unit results are separate from whole-document derivatives. They are written to
`_units_<source-stem>`.
The manifest `created` value records export creation time in ISO 8601 UTC
format ending in `Z`. Each export writes a new creation timestamp.

`manifest.json` has `schema_version`, `kind`, `created`, `source`, `document_type`, and a
`units` array. Each unit entry repeats its ID, title, type, input range, and
selected input pages. It links the unit metadata filename and the optional HTML
filename.

GUI plan export can also write `<unit-id>.pdf` for each selected unit.
The PDF contains only selected source pages inside that unit's reviewed range,
in input order. Overlapping units each include their selected shared pages.
Removed units produce no results. With `no-pdf-out` on, GUI export omits all
unit PDFs. When a unit PDF is written, its manifest entry includes a `pdf`
field with the filename. GUI export does not write a combined document PDF.
Inside the GUI ZIP, unit results use `_units`. Its manifest always includes
the complete upload metadata under `upload`, immediately after `kind`.
The GUI-only `include-source` switch optionally adds the original uploaded
`source.pdf` at the ZIP root. All GUI export switches default to off. The GUI `get-meta` switch controls
unit metadata JSON files in the ZIP. When off, these files are omitted and
manifest unit `metadata` fields are `null`. The manifest remains in the ZIP.

`<unit-id>.metadata.json` has `schema_version`, `kind`, `source`,
`document_type`, `unit`, `figures`, `warnings`, and `semantic_blocks`. Each
semantic block retains its absolute input page. Each figure has a stable figure
ID, input page, bounding box, image xref, and optional caption.

`<unit-id>.html` is a UTF-8 HTML fragment. It uses `h1` through `h6`, `p`,
`figure`, `figcaption`, and visible figure placeholder elements. Page markers
use detected printed numbers. If detection is uncertain, the marker explicitly
uses the input page index. Extracted text is HTML-escaped. The fragment has no
script, positioning CSS, or image URL.
A Figures section appears at the end of the HTML fragment.
The fragment has no `html`, `head`, or `body` wrapper tags.
It uses a standard `ul`/`li` list in figure occurrence order. Each item has
`id -- caption`. The ID is a local link to that figure placeholder.
The placeholder anchor uses the figure `id` value. Figure IDs must be
unique within the fragment. Captionless figures use an empty caption. With no figures, the
section contains an empty list. Figure IDs and captions are
HTML-escaped.

## Reconstruction limits

The reading-order implementation detects whitespace gutters for common single-,
two-, and three-column pages. It reads each column from top to bottom before
moving right. Full-width blocks divide the page into separate column regions.
Native text blocks that contain separate columns are split at line-level gutters.
Ambiguous overlaps use top-to-bottom order and require review. It uses native
font size and font names
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

Journal source detection checks the bottom 15 percent of every input page for
citation lines. It prefers repeated footer citations over first-page titles
and body text. Recognized footer patterns include `Journal. 2024;12(3):101-110`
and `Journal, 3/2024, vol. 12`. Other layouts use the existing source fallback.
Detected source metadata remains a proposal for review.

Unit title candidates exclude contact addresses, websites, academic credentials,
reference section labels, and footer text. DOI references and author contact
blocks on article-ending pages do not by themselves establish a new unit.

Journal and proceedings unit detection also checks citation footers in the
bottom 10 percent of each page. A citation repeated on consecutive pages
identifies an article run. A changed citation after a repeated run proposes a
new unit at the changed page. The signature includes the full article page
range. Standalone printed page numbers do not establish a boundary.

`magazine-issue` uses the journal source metadata contract. Select this type
explicitly. Automatic detection does not distinguish a magazine from an
academic journal. Magazine segmentation uses display titles of at least 24 PDF
points and at least 1.8 times the character-weighted median font size.
Adjacent large title lines are combined into `main_title`. Titles can occur
below photographs. Author, abstract, and DOI signals are not required.
Page numbers, footer text, websites, and repeated display titles do not create
new title-based boundaries. Proposed units still require review.

For magazine issues, photo captions can use unprefixed bold text directly below
an image. The detector checks horizontal alignment, a gap of at most 18 PDF
points, and text of at most 14 PDF points. It associates each caption line with
one nearest image and excludes page footers. Academic journal caption rules
continue to require explicit caption prefixes.
