# Container partitioning and unit export

## Purpose and scope

This specification defines born-digital container partitioning, reviewed unit plans,
and per-unit HTML export. `specs/PROJECT.md` owns the general processing and
security rules. This specification owns only the container workflow and its JSON
contracts.

This iteration supports PDFs with extractable text. A unit starts and ends at an
input page boundary. Scanned-document segmentation and within-page unit
boundaries are not supported. A consuming application owns plan
review and editing.

## Workflow

Use this sequence:

1. Optional: run `pdfwtf enhance INPUT_PDF` to write `<stem>.prepared.pdf`.
   Use this step when the PDF needs cleanup, page selection, or OCR.
2. Run `pdfwtf partition PDF` on the PDF that you will export, normally the
   prepared PDF. This writes machine proposals and evidence.
3. Review the partition in the consuming application.
4. Write a plan with confirmed whole-page boundaries.
5. Run `pdfwtf export PDF --plan PATH` on the same PDF to process selected units.
6. Add `--get-html` to write one HTML fragment for each selected unit. Add
   `--get-unit-pdf` to write one PDF for each selected unit.

Partitioning is optional. `pdfwtf export PDF --doc_type unit --get-html`
exports the complete PDF as one unit without a plan. Repeat export as
necessary. It does not repeat enhancement or partitioning.

A partition proposes units and extracts DOI candidates for each proposed unit.
It does not generate unit result files. Plan processing never repeats
partitioning. `--doc_type unit --get-html` can process the complete PDF without a
plan.

The source PDF is always the required positional argument. `--doc_type`
describes document structure. It does not describe whether the PDF is scanned.
The `partition` and `export` actions use existing PDF text and do not run OCR.
Image-only pages have no extracted text in these workflows. To get text from
scanned pages, enhance the PDF first, then partition and export the prepared PDF.

## Source and plan compatibility

A plan is valid only for the exact PDF that was partitioned. Plan validation
compares `source.sha256` and `source.page_count` with the exported PDF.
A mismatch fails with this first error: "The plan was created for a different
PDF. Partition the PDF that you export, normally the prepared PDF."
The identity uses file content, not paths. A moved or renamed copy of an
unchanged PDF is accepted. A new enhancement can change the content and the
page structure. Partition the new prepared PDF again before export.
Physical page splitting during enhancement changes the page count. It is
separate from logical unit partitioning.

The `export` action accepts repeatable `--unit_type TYPE` filters.
Without a filter, selection is unchanged. Export filters the selected units from
`--plan`, or the direct unit from `--get-html`. It does not change the saved plan
or filter page derivatives. A filter requires one of these unit export options.
If no types match, export writes an empty unit manifest.

Type detection during partitioning uses title keywords and document-type defaults. These
are proposals for operator review. Journal and magazine units default to
`article` when no type keyword matches. Book units default to `chapter` when
no type keyword matches. Operators assign the correct types in
the reviewed plan before export. The `partition` action does not filter unit types.

## Common JSON fields

All workflow JSON documents use UTF-8 and `schema_version` value `"1.4"`.
Plan validation rejects plans with another version.
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

## Partition contract

The partition filename is `<source-stem>.partition.json`. Version `"1.4"`
renamed this document from `<source-stem>.analysis.json` with `kind` value
`analysis`. Version `"1.4"` does not read files from earlier versions.
The current `<stem>.analysis.json` is the read-only diagnostic report of
`pdfwtf analyze`, with `kind` `pdf-analysis`. It is not a partition and
not a plan. See `specs/ANALYSIS.md`.
It has this shape:

```json
{
  "schema_version": "1.4",
  "kind": "partition",
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
notices. The partition does not persist full page text, text spans, bookmarks,
or links.

Extracted strings use Unicode compatibility normalization. The partitioner
converts compatibility ligatures and spaces, removes soft hyphens and format
characters, replaces invalid or private-use characters, and collapses
whitespace. It preserves valid letters and diacritics.

The partitioner excludes small raster decorations from `figures`. A raster
occurrence must cover at least 0.5 percent of the page, have displayed width
and height of at least 18 PDF points, and have source width and height of at
least 40 pixels. The partitioner retains the full internal structure only while it
classifies the document and builds the compact partition.

The partitioner combines evidence. A heading or DOI alone does not create a unit.
For journals and proceedings, a low title position does not establish a shared
page by itself. Compact branding above a title can precede a whole-page start.
Substantial preceding text, a reference section, or an unexplained title below
40 percent of the page height produces a `suspected_shared_page_boundaries`
entry. It is not converted to a proposed boundary.

Each `units[].doi` value is an array of unverified DOI candidates from that
unit's proposed page range. The array is empty when no candidate is found. DOI
identification uses the same candidate rules as `--get-doi`. Unlike
`--get-doi`, partitioning checks all pages in each proposed unit.

## Reviewed plan contract

A reviewed plan has this shape:

```json
{
  "schema_version": "1.4",
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
warnings, and unit links. The partition file remains unchanged.

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
filename. Version `"1.3"` added optional LLM task fields to the manifest: a
top-level `llm` object and a per-unit `llm_tasks` object. These fields occur only when
LLM tasks were selected. `specs/PROJECT.md` defines them.

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
font size, bold character coverage, numbering, and neighbouring spacing
for conservative HTML heading classification. The body-size baseline is the
most common font size by character count across the selected unit pages,
excluding recognized captions and repeated margin text. A single bold span
does not promote an entire body paragraph to a heading.

Direct unit export reconsiders automatic partition heading guesses. Explicit
plan headings retain their text and levels. Automatic heading fragments with
matching styles, aligned left edges, and a very small vertical gap can merge
on the same page. Numbered successors do not merge. One confident unit title
can use `h1`; other automatic heading styles map consistently to `h2` through
`h6` across the unit. A missing confident title does not require an `h1`.
Consecutive headings at the same or a higher level produce a review warning;
parent and subordinate headings can remain adjacent. Heading semantic blocks
include `heading_evidence` describing the classification signals.

Paragraph reconstruction still joins lines only within each native text block.
It removes a
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

## Academic journal opening detection

`journal-issue` and `proceedings` use local article-opening evidence. The
magazine detection path is unchanged. Automatic classification uses these
opening signals before the bookmark-based book fallback.

The detector evaluates title candidates rather than selecting the largest line
alone. It excludes recurring upper-margin text, internal section labels,
caption labels, DOI lines, affiliations, and contact credentials. Adjacent lines
with compatible title typography are combined. A translated title can occur
between the primary title and its authors without creating a second start.

Each candidate receives these heuristic weights:

| Evidence | Weight |
| --- | --- |
| Bold title or title font at least 1.15 times body size | +2 |
| Nearby compact author byline | +2 |
| Nearby affiliation | +1 |
| Nearby abstract label | +2 |
| Nearby keywords label | +1 |
| Nearby DOI | +1 |
| Editorial, letter, correspondence, or book-review label | +2 |
| Changed explicit article DOI in a margin | +1 |
| Continuing explicit article DOI without a new abstract | -3 |
| References on the previous page, with other opening evidence | +1 |

The body size is the character-weighted median font size on the page. Opening
evidence is limited to the next 230 PDF points or 35 percent of page height,
whichever is smaller. Byline evidence must occur within 60 points of the title
and among the first three following lines. Reference sections stop the evidence
window. Candidates inside references require title typography and a new
abstract or affiliation.

A score of at least 4 proposes a start when title typography, an abstract, or
an affiliation also supports it. No author, DOI, or abstract signal is mandatory.
Scores describe heuristic support. They are not calibrated probabilities.
Labels include `Abstract`, `Abstrakt`, `Souhrn`, `Súhrn`, `Résumé`, `Summary`,
`Keywords`, `Klíèová slova`, and `K¾úèové slová`. Label matches are anchored to
the start of a line. Byline checks support Unicode names. A mention of `and`
in prose is not byline evidence.

PDF bookmarks and links from recognized contents pages can recover a start
without opening typography. The normalized navigation title must match up to
four consecutive lines in the top 60 percent of the destination page. Known
internal section labels do not establish navigation boundaries. The matched
destination text supplies the title and its position. A matched navigation
entry contributes 4 points and can independently support a proposal.

Printed contents detection recognizes single-line entries with a title, optional
leader dots, and a final printed page number. The number must map to exactly
one detected input page. The destination title must also match. Missing or
ambiguous mappings do not create boundaries. Multiline contents entries and
unrecognized labels still require review.

Each journal page records a compact opening diagnostic in `pages[].warnings`:
the best candidate score, the accepted or insufficient-evidence decision, and
the supporting or opposing signals. Accepted boundaries retain their evidence
in `units[].boundary_evidence`. Shared-page candidates retain their evidence
separately. No extracted body text is persisted. The existing JSON schema and
reviewed whole-page plan contract remain unchanged.

Use manually reviewed scientific issues to assess boundary precision and recall
before tuning these weights. The synthetic regression cases verify individual
signals; they do not establish accuracy across publishers or historical issues.
