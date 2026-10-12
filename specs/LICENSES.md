# Licenses

**pdf-wtf** is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.

See [LICENSE](https://github.com/filak/pdf-wtf?tab=AGPL-3.0-1-ov-file) for the full text.

## Python Dependencies and Their Licenses

When **pdf-wtf** is distributed, all included Python dependencies become subject to the AGPL-3.0 license terms, as required by the copyleft nature of AGPL.

### Core PDF Processing Libraries

#### pikepdf
- **License**: Mozilla Public License 2.0 (MPL-2.0)
- **Used for**: PDF page manipulation, content editing, and page selection/extraction
- **Repository**: https://github.com/pikepdf/pikepdf
- **Purpose in pdf-wtf**:
  - Creating new PDFs from scratch
  - Appending individual pages to PDFs
  - Page selection and removal
  - PDF structural preservation during page operations
- **License Note**: MPL-2.0 is file-level copyleft; when distributed with AGPL-3.0 software, becomes subject to AGPL-3.0 terms

#### PyMuPDF (fitz)
- **License**: GNU Affero General Public License v3.0 (AGPL-3.0)
- **Used for**: PDF text/image extraction, rendering, OCR processing
- **Repository**: https://github.com/pymupdf/PyMuPDF
- **Documentation**: https://pymupdf.readthedocs.io/
- **Purpose in pdf-wtf**:
  - Extracting text and images from PDFs
  - Rendering pages as raster images
  - OCR processing via PyMuPDF's native Tesseract integration
  - Page metadata analysis
- **License Note**: Fully compatible with AGPL-3.0; no conflicts

#### ocrmypdf
- **License**: GNU Affero General Public License v3.0 (AGPL-3.0)
- **Used for**: OCR processing via Tesseract wrapper
- **Repository**: https://github.com/ocrmypdf/OCRmyPDF
- **Documentation**: https://ocrmypdf.readthedocs.io/
- **Purpose in pdf-wtf**:
  - Adding OCR text layers to scanned PDFs
  - Running Tesseract OCR with Ghostscript preprocessing
  - Optimizing PDF output after OCR processing
- **License Note**: Fully compatible with AGPL-3.0; no conflicts

### Supporting Python Libraries

#### pytesseract
- **License**: Apache License 2.0 (Apache-2.0)
- **Used for**: OCR image analysis and page orientation detection
- **Repository**: https://github.com/madmaze/pytesseract
- **Purpose in pdf-wtf**:
  - Detecting page orientation for automatic rotation
  - Image-to-text conversion (via Tesseract CLI)
  - Tesseract OSD (Orientation and Script Detection)
- **License Note**: When distributed with AGPL-3.0 software, becomes subject to AGPL-3.0 terms

#### Pillow (PIL)
- **License**: Historical Permission Notice and Disclaimer (HPND) / Historical BSD-like License
- **Used for**: Image processing
- **Repository**: https://github.com/python-pillow/Pillow
- **Purpose in pdf-wtf**:
  - Image manipulation and conversion
  - Thumbnail generation
  - Image format handling (PNG, JPEG)
- **License Note**: Permissive; when distributed with AGPL-3.0 software, becomes subject to AGPL-3.0 terms

#### img2pdf
- **License**: GNU General Public License v2.0 (GPL-2.0)
- **Used for**: Converting images to PDF
- **Repository**: https://github.com/josch/img2pdf
- **Purpose in pdf-wtf**:
  - Converting prepared images into PDF format with proper DPI settings
- **License Note**: Compatible with AGPL-3.0; when distributed together, becomes subject to AGPL-3.0 terms

#### cv3 (OpenCV Python bindings)
- **License**: Apache License 2.0 (Apache-2.0)
- **Used for**: Computer vision operations
- **Repository**: https://github.com/opencv/opencv-python
- **Purpose in pdf-wtf**:
  - Advanced image processing (dark background cropping)
  - Adaptive thresholding for scanned documents
  - Image analysis and contour detection
- **License Note**: When distributed with AGPL-3.0 software, becomes subject to AGPL-3.0 terms

#### Click
- **License**: BSD 3-Clause License (BSD-3-Clause)
- **Used for**: Command-line interface framework
- **Repository**: https://github.com/pallets/click
- **Purpose in pdf-wtf**: Building the CLI argument parser and help system
- **License Note**: Permissive; when distributed with AGPL-3.0 software, becomes subject to AGPL-3.0 terms

#### Pydantic
- **License**: MIT License
- **Used for**: Data validation and configuration management
- **Repository**: https://github.com/pydantic/pydantic
- **Purpose in pdf-wtf**: Validating CLI settings and configuration parameters
- **License Note**: Permissive; when distributed with AGPL-3.0 software, becomes subject to AGPL-3.0 terms

#### Flask, Flask-Babel, Flask-WTF
- **License**: BSD 3-Clause License (BSD-3-Clause)
- **Used for**: Web framework for the optional GUI
- **Repositories**:
  - https://github.com/pallets/flask
  - https://github.com/python-babel/flask-babel
  - https://github.com/wtforms/flask-wtf
- **Purpose in pdf-wtf**:
  - Web server for PDF-WTF-GUI
  - Internationalization (i18n) support
  - Web form handling and CSRF protection
- **License Note**: Permissive; marked as optional dependencies in `pyproject.toml` under `[project.optional-dependencies]`; when distributed with AGPL-3.0 software, become subject to AGPL-3.0 terms

#### Selenium
- **License**: Apache License 2.0 (Apache-2.0)
- **Used for**: Browser automation
- **Repository**: https://github.com/SeleniumHQ/selenium
- **Purpose in pdf-wtf**: Future or testing browser-based PDF interactions
- **License Note**: When distributed with AGPL-3.0 software, becomes subject to AGPL-3.0 terms

#### webdriver-manager
- **License**: Apache License 2.0 (Apache-2.0)
- **Used for**: Automated WebDriver management for Selenium
- **Repository**: https://github.com/SergeyPirogov/webdriver_manager
- **Purpose in pdf-wtf**: Managing ChromeDriver and other browser drivers for Selenium
- **License Note**: When distributed with AGPL-3.0 software, becomes subject to AGPL-3.0 terms

#### python-dotenv
- **License**: BSD 3-Clause License (BSD-3-Clause)
- **Used for**: Environment variable loading
- **Repository**: https://github.com/theskumar/python-dotenv
- **Purpose in pdf-wtf**: Loading configuration from `.env` files
- **License Note**: Permissive; when distributed with AGPL-3.0 software, becomes subject to AGPL-3.0 terms



### Development Dependencies

These are included in the development environment but not in production distributions:

#### Black
- **License**: MIT License
- **Used for**: Code formatting
- **Repository**: https://github.com/psf/black

#### Flake8
- **License**: MIT License
- **Used for**: Linting and code quality checks
- **Repository**: https://github.com/PyCQA/flake8

#### pytest
- **License**: MIT License
- **Used for**: Testing framework
- **Repository**: https://github.com/pytest-dev/pytest

#### pip-audit
- **License**: MIT License / Apache License 2.0
- **Used for**: Dependency vulnerability scanning
- **Repository**: https://github.com/pypa/pip-audit



## External Non-Python Dependencies

The following external tools are **required** for full functionality:

### Tesseract OCR
- **License**: Apache License 2.0 (Apache-2.0)
- **Project**: https://github.com/UB-Mannheim/tesseract
- **Required by**: ocrmypdf, PyMuPDF
- **Purpose**: OCR (Optical Character Recognition) engine
- **Installation**: See [README.md](README.md#external-non-python-dependencies)

### Ghostscript
- **License**: GNU Affero General Public License v3.0 (AGPL-3.0) / Proprietary Commercial License
- **Project**: https://www.ghostscript.com/
- **Required by**: ocrmypdf
- **Purpose**: PostScript and PDF processing
- **Installation**: See [README.md](README.md#external-non-python-dependencies)

### unpaper
- **License**: GNU General Public License v2.0 (GPL-2.0)
- **Project**: https://github.com/unpaper/unpaper
- **Used for**: Scan cleanup, layout detection, page splitting, and pre-rotation
- **Installation**: See [README.md](README.md#external-non-python-dependencies)

### pngquant
- **License**: GNU General Public License v3.0 (GPL-3.0)
- **Project**: https://pngquant.org/
- **Required by**: ocrmypdf (when `--optimize > 0`)
- **Purpose**: PNG color quantization and optimization
- **Installation**: See [README.md](README.md#external-non-python-dependencies)



## License Compatibility Summary

### AGPL-3.0 Compatibility

When pdf-wtf is distributed, all included dependencies become subject to AGPL-3.0 terms due to its copyleft nature:

| Dependency | Original License | Status When Distributed | Notes |
|||||
| pikepdf | MPL-2.0 | AGPL-3.0 | File-level copyleft; compatible when bundled |
| PyMuPDF | AGPL-3.0 | AGPL-3.0 | Native compatibility |
| ocrmypdf | AGPL-3.0 | AGPL-3.0 | Native compatibility |
| pytesseract | Apache-2.0 | AGPL-3.0 | Permissive; compatible |
| Pillow | HPND | AGPL-3.0 | Permissive; compatible |
| img2pdf | GPL-2.0 | AGPL-3.0 | GPL-compatible upgrade |
| cv3 | Apache-2.0 | AGPL-3.0 | Permissive; compatible |
| Click | BSD-3-Clause | AGPL-3.0 | Permissive; compatible |
| Pydantic | MIT | AGPL-3.0 | Permissive; compatible |
| Flask | BSD-3-Clause | AGPL-3.0 | Permissive; compatible |
| Selenium | Apache-2.0 | AGPL-3.0 | Permissive; compatible |

### Key Principle

**When you distribute pdf-wtf, all included software (Python packages, external tools, and bundled assets) must be made available under AGPL-3.0 terms or compatible terms.**

Users who receive pdf-wtf have the right to:
- Access the complete source code
- Modify the software
- Redistribute modified versions under AGPL-3.0
- Use the software as a networked service (with source code availability requirement)



## Third-Party Licenses

Full license texts for critical dependencies are available in their respective repositories:

- **PyMuPDF**: https://github.com/pymupdf/PyMuPDF/blob/main/LICENSE
- **ocrmypdf**: https://github.com/ocrmypdf/OCRmyPDF/blob/master/LICENSE
- **pikepdf**: https://github.com/pikepdf/pikepdf/blob/main/LICENSE
- **Tesseract**: https://github.com/UB-Mannheim/tesseract/blob/main/LICENSE
- **Ghostscript**: https://www.ghostscript.com/licensing/
- **unpaper**: https://github.com/unpaper/unpaper/blob/master/LICENSE



## Questions or Concerns?

If you have questions about license compliance or specific use cases, please:
1. Review the full AGPL-3.0 text in [LICENSE](LICENSE)
2. Consult the [GNU AGPL FAQ](https://www.gnu.org/licenses/agpl-3.0.html)
3. Contact the maintainers via GitHub Issues



**Last updated**: 2026-10-12
