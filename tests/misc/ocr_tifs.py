from pathlib import Path
import io
import ocrmypdf
import pikepdf


def tiff_folder_to_pdf_pikepdf(
    tiff_dir: Path, output_pdf: Path, lang: str = "eng"
) -> None:
    """
    Convert all TIFF files in a folder to a single searchable PDF using OCR.
    Uses pikepdf to merge PDFs efficiently.
    """
    tiff_files = sorted(tiff_dir.glob("*.tif"))
    if not tiff_files:
        print("No TIFF files found!")
        return

    temp_pdfs = []

    # Step 1: OCR each TIFF into an in-memory PDF
    for tiff_file in tiff_files:
        pdf_bytes = io.BytesIO()
        ocrmypdf.ocr(
            input_file=str(tiff_file),
            output_file=pdf_bytes,
            language=lang,
            force_ocr=True,
            output_type="pdfa",  # ensures PDF/A
        )
        pdf_bytes.seek(0)
        temp_pdfs.append(pdf_bytes)

    # Step 2: Merge PDFs using pikepdf
    with pikepdf.Pdf.new() as merged_pdf:
        for pdf_bytes in temp_pdfs:
            with pikepdf.open(pdf_bytes) as single_pdf:
                merged_pdf.pages.extend(single_pdf.pages)
        merged_pdf.save(output_pdf)

    print(f"Searchable PDF created: {output_pdf}")


if __name__ == "__main__":
    tiff_folder_to_pdf_pikepdf(
        tiff_dir=Path("output_tiff"),
        output_pdf=Path("final_ocr.pdf"),
        lang="eng+ces",
    )
