"""Console and file logging for application entry points."""

import logging
from pathlib import Path


class ContextFilter(logging.Filter):
    """Supply optional context fields for operational records."""

    def filter(self, record: logging.LogRecord) -> bool:
        for name in ("client_address", "correlation_id"):
            if not hasattr(record, name):
                setattr(record, name, "-")
        return True


def configure_logging(debug: bool = False, log_file: Path | None = None) -> None:
    """Configure the application namespace without changing the root logger."""
    file_handler = None
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
    logger = logging.getLogger("pdfwtf")
    for handler in list(logger.handlers):
        if getattr(handler, "_pdfwtf_console", False):
            logger.removeHandler(handler)
            handler.close()
    handler = logging.StreamHandler()
    handler._pdfwtf_console = True
    handler.addFilter(ContextFilter())
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(processName)s %(name)s "
            "client=%(client_address)s correlation=%(correlation_id)s %(message)s"
        )
    )
    logger.addHandler(handler)
    if file_handler is not None:
        file_handler._pdfwtf_console = True
        file_handler.addFilter(ContextFilter())
        file_handler.setFormatter(handler.formatter)
        logger.addHandler(file_handler)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False
