"""Standalone local host for the reusable PDF-WTF GUI blueprint."""

from __future__ import annotations

import atexit
from pathlib import Path
import secrets
from typing import Any, Mapping

from flask import Flask, request
from flask_babel import Babel, get_locale
from flask_wtf.csrf import CSRFProtect

from pdfwtf.configuration import load_config
from pdfwtf.gui.adapter import DemoAnalysisAdapter
from pdfwtf.gui.blueprint import create_gui_blueprint


def create_app(overrides: Mapping[str, Any] | None = None) -> Flask:
    """Create the standalone local host application."""
    shared = load_config()
    input_dir = (shared.home / "instance/_data/in").resolve()
    output_dir = shared.output_dir
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=secrets.token_urlsafe(32),
        MAX_CONTENT_LENGTH=100 * 1024 * 1024,
        BABEL_DEFAULT_LOCALE="en",
        BABEL_SUPPORTED_LOCALES=("en", "cs"),
        BABEL_TRANSLATION_DIRECTORIES=str(Path(__file__).parent / "translations"),
        PDFWTF_GUI_INPUT_DIR=input_dir,
        PDFWTF_GUI_OUTPUT_DIR=output_dir,
        PDFWTF_GUI_BASE_TEMPLATE="pdfwtf_gui/standalone_base.html",
        PDFWTF_GUI_ACCESS_CHECK=lambda _action, _job_id: True,
    )
    if overrides:
        app.config.from_mapping(overrides)

    adapter = app.config.get("PDFWTF_GUI_ANALYSIS_ADAPTER")
    if adapter is None:
        adapter = DemoAnalysisAdapter()
        app.config["PDFWTF_GUI_ANALYSIS_ADAPTER"] = adapter
        atexit.register(adapter.close)

    Babel(app, locale_selector=_select_locale)
    CSRFProtect(app)
    app.jinja_env.globals["get_locale"] = get_locale
    app.register_blueprint(create_gui_blueprint())
    return app


def _select_locale() -> str:
    supported = ("en", "cs")
    requested = request.args.get("lang")
    if requested in supported:
        return requested
    return request.accept_languages.best_match(supported) or "en"


def main() -> None:
    """Run the local development host on the loopback interface."""
    create_app().run(host="127.0.0.1", port=5000)


if __name__ == "__main__":
    main()
