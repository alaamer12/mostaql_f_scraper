"""Logging setup shared across scraper modules."""

import logging


def setup_logging(log_file: str = "scraper.log") -> logging.Logger:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[logging.FileHandler(log_file, encoding="utf-8")],
        )
    return logging.getLogger("mostaql")
