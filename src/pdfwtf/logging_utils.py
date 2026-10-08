"""Console logging for application entry points."""

import logging


class ContextFilter(logging.Filter):
    """Supply optional context fields for operational records."""

    def filter(self, record: logging.LogRecord) -> bool:
        for name in ("client_address", "correlation_id"):
            if not hasattr(record, name):
                setattr(record, name, "-")
        return True


def configure_logging(debug: bool = False) -> None:
    """Configure the application namespace without changing the root logger."""
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
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False
