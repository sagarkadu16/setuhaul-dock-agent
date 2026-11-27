"""App logging: one line per event, tagged with the request it belongs to.

dev: readable text. staging/prod: JSON (one object per line) so CloudWatch can
search and filter by field, e.g. every line of one failing request.
"""

import json
import logging
import re
import uuid
from contextvars import ContextVar
from typing import TextIO

# Holds the current request's id. Each request (and the thread it runs on) sees its own.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_SAFE_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def new_request_id(incoming: str | None) -> str:
    """Reuse the caller's id (e.g. from a load balancer) if it's safe, else make one."""
    if incoming and _SAFE_ID.match(incoming):
        return incoming
    return uuid.uuid4().hex


class _RequestIdFilter(logging.Filter):
    """Stamps every record with the current request id."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(json_logs: bool, level: str = "INFO", stream: TextIO | None = None) -> None:
    """Send all app logs to one stream (default stderr) in the chosen format."""
    handler = logging.StreamHandler(stream)
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(
        JsonFormatter() if json_logs
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)