"""Structured logging. Never logs secrets; redacts anything that looks like an API key."""
from __future__ import annotations

import logging
import re
import sys

_KEY_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|tvly-[A-Za-z0-9_\-]{8,})")


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if _KEY_RE.search(msg):
            record.msg = _KEY_RE.sub("[REDACTED]", msg)
            record.args = ()
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(_RedactFilter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "google_genai", "google.genai", "google_genai.models", "urllib3", "faiss", "fastembed"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
