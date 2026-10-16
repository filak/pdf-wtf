"""Convert a PDF to Markdown or Text.

Install: uv pip install pymupdf4llm
Run:     uv run python pdf_to_output.py input.pdf -o output.md
"""

import argparse
from pathlib import Path
import sys

pymupdf_options = {
    "write_images": False,
    "embed_images": False,
    "show_progress": True,
    "force_ocr": False,
    "use_ocr": False,
}

to_choices = ["md", "txt"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="Input PDF")
    parser.add_argument("to", type=str, help="Convert PDF to", choices=to_choices)
    parser.add_argument(
        "-o", "--output", type=Path, help="Default: input filename with -to- extension"
    )
    parser.add_argument(
        "--layout",
        action="store_true",
        help="Enable trained layout detection (not an LLM)",
    )
    parser.add_argument("--force_ocr", action="store_true", help="Use OCR")
    parser.add_argument("--use_ocr", action="store_true", help="Use OCR where needed")
    parser.add_argument(
        "--overwrite", action="store_true", help="Replace an existing Markdown file"
    )
    args = parser.parse_args()
    source = args.pdf.resolve()
    output = (args.output or source.with_suffix(f".{args.to}")).resolve()
    if not source.is_file():
        parser.error(f"Input file does not exist: {source}")
    if source == output:
        parser.error("Input and output paths must differ")
    if output.exists() and not args.overwrite:
        parser.error(f"Output already exists; use --overwrite: {output}")

    try:
        import pymupdf
        import pymupdf4llm
        from pymupdf4llm.ocr import tesseract_api

        if args.use_ocr:
            pymupdf_options["use_ocr"] = True

        if args.force_ocr:
            pymupdf_options["use_ocr"] = True
            pymupdf_options["force_ocr"] = True

        pymupdf4llm.use_layout(args.layout)
        with pymupdf.open(source) as document:
            if document.needs_pass:
                parser.error("Password-protected PDFs are not supported by this script")
            page_count = len(document)

            if args.to == "md":
                pymupdf_output = pymupdf4llm.to_markdown(
                    document,
                    ocr_function=tesseract_api.exec_ocr,
                    **pymupdf_options,
                )
                write_output(
                    output, pymupdf_output, page_count, overwrite=args.overwrite
                )
            elif args.to == "txt":
                pymupdf_output = pymupdf4llm.to_text(
                    document,
                    ocr_function=tesseract_api.exec_ocr,
                    **pymupdf_options,
                )
                write_output(
                    output, pymupdf_output, page_count, overwrite=args.overwrite
                )
        return 0
    except ImportError as exc:
        print(
            f"Missing dependency: {exc}. Run: uv pip install pymupdf4llm",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(f"Conversion failed: {exc}", file=sys.stderr)
        return 1


def write_output(output, pymupdf_output, page_count, overwrite=False):
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open(
        "w" if overwrite else "x", encoding="utf-8", newline="\n"
    ) as handle:
        handle.write(pymupdf_output)
    print(f"Saved {page_count} pages to {output}")
    if not pymupdf_output.strip():
        print(
            "Warning: no text extracted. Scanned PDFs need OCR first.", file=sys.stderr
        )


if __name__ == "__main__":
    raise SystemExit(main())
