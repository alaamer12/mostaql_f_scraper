"""Logging setup shared across scraper modules."""

import logging


def setup_logging(log_file: str = "scraper.log") -> logging.Logger:
    root = logging.getLogger()
    has_file = any(isinstance(h, logging.FileHandler) for h in root.handlers)
    if not has_file:
        try:
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            root.addHandler(fh)
        except Exception:
            pass
    root.setLevel(logging.INFO)
    return logging.getLogger("mostaql")
