# pdf-wtf

PDF - what the file format ...

PDF files parsing and data extraction

> Work in progress ...

## Local dev installation

1. Create virtualenv &amp; activate it:

    ```
    py -m venv venv
    py -m venv --upgrade venv
    ```
    
2. Upgrade the environment - run:

    ```
    venv\Scripts\activate
    python -m pip install --upgrade pip
    pip install setuptools wheel --upgrade
    ```
    
3. Install - run:
    
    ```
    pip install -e .[dev]
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

Patch  

    \venv\Lib\site-packages\ocrmypdf\subprocess\_windows.py#60  

    def fix_windows_args():
    ...
    # Patch for ".\\unpaper" on Windows
    if sys.platform.startswith("win"):
        if args[0].startswith(".\\unpaper."):
            args[0] = args[0].lstrip(".\\")



