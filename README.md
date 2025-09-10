# pdf-wtf

PDF - what the file format ...

PDF files parsing and data extraction

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
