"""JSON logs on stdout: Cloud Logging reads `severity` from them, so a logging.error in a
Cloud Run job lands as ERROR (plain stdout would be DEFAULT and never reach the alert)."""

from __future__ import annotations

import json
import logging
import sys


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(json_format: bool) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter() if json_format else logging.Formatter("%(levelname)s %(message)s")
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
