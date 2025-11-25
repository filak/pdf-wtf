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
- required by OCRmyPDF with some params: --clean etc. 
- https://github.com/unpaper/unpaper

**pngquant** 
- required by OCRmyPDF with optimize > 0 
- https://pngquant.org/

## Local dev installation

Prerequisites: Python 3.12+, Git

Open terminal/command line
- create the repo dir
- go to the dir

1. Clone the repo:

    git clone https://github.com/filak/pdf-wtf

2. Install uv package manager and create the virtual environment:

    ```
    pip install uv

    uv venv
    ```    
    
2. Activate the environment:

    ```
    .venv\Scripts\activate
    ```    
    
3. Install dependecies:

    ```
    uv sync
    ```    

4. Test - run:

    ```
    pytest
    ```

## Using unpaper on Windows

> OCRmyPDF requires unpaper installed to be able to use --clean and --clean_final params

Install and start Docker Desktop

Build the Docker image - run:

     docker build -t unpaper-alpine -f .Dockerfile-unpaper .

Test run:

     docker run --rm unpaper-alpine --version

Create ENV vars:

     setx PDFWTF_HOME_DIR d:\Decko\pdf-wtf
     setx PDFWTF_TEMP_DIR %PDFWTF_HOME_DIR%\instance\temp

Add %PDFWTF_HOME_DIR% to PATH so OCRmyPDF can find the unpaper.cmd

Check:

     echo  %PDFWTF_HOME_DIR%  %PDFWTF_TEMP_DIR%

     unpaper.cmd --version

Patch for ocrmypdf to use unpaper on Windows using Docker

    \venv\Lib\site-packages\ocrmypdf\subprocess\_windows.py#180  

    def fix_windows_args():
    ...
    # Patch for Windows - ".\\unpaper"
    if sys.platform.startswith("win"):
        if args[0].startswith(".\\unpaper."):
            args[0] = args[0].lstrip(".\\")

> **If you reinstall the package - you MUST insert the patch again !**

