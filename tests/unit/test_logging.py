import importlib
import logging
import os

from pdfwtf import pipeline
from pdfwtf.logging_utils import configure_logging


def test_import_does_not_modify_environment_or_root_logging(
    configured_home, monkeypatch
):
    monkeypatch.setenv("PDFWTF_TEMP_DIR", str(configured_home / "instance/temp"))
    environment = dict(os.environ)
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    importlib.reload(pipeline)
    assert dict(os.environ) == environment
    assert root.handlers == handlers
    assert root.level == level


def test_console_logging_has_context_and_no_duplicate_handlers(capsys):
    configure_logging()
    configure_logging()
    logging.getLogger("pdfwtf.test").info("Operation completed.")
    result = capsys.readouterr()
    assert result.err.count("Operation completed.") == 1
    assert "INFO" in result.err
    assert "MainProcess" in result.err
    assert "pdfwtf.test" in result.err
    assert "client=- correlation=-" in result.err
    with capsys.disabled():
        configure_logging()
