"""Reusable PDF-WTF GUI blueprint.

Importing :mod:`pdfwtf` does not import this optional GUI package.
"""

from pdfwtf.gui.blueprint import create_gui_blueprint

__all__ = ["create_gui_blueprint"]
