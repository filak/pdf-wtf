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


def test_file_logging_appends_without_duplicate_records(tmp_path):
    path = tmp_path / "logs" / "pdf-wtf-log.txt"
    logger = logging.getLogger("pdfwtf.test")
    try:
        configure_logging(log_file=path)
        logger.info("First operation completed.")
        configure_logging(debug=True, log_file=path)
        logger.debug("Second operation completed: č.")
        text = path.read_text(encoding="utf-8")
        assert text.count("First operation completed.") == 1
        assert text.count("Second operation completed: č.") == 1
        assert "DEBUG" in text
        assert "client=- correlation=-" in text
    finally:
        configure_logging()


def test_gui_host_logs_requests(configured_home):
    from pdfwtf.gui.app import create_app

    app = create_app({"TESTING": True})
    try:
        response = app.test_client().get("/?private=hidden-value")
        assert response.status_code == 200
        text = (configured_home / "instance/logs/pdf-wtf-gui-log.txt").read_text(
            encoding="utf-8"
        )
        assert "GUI host initialized." in text
        assert "Request completed: GET" in text
        assert "status=200" in text
        assert "hidden-value" not in text
    finally:
        app.config["PDFWTF_GUI_ANALYSIS_ADAPTER"].close()
        configure_logging()
