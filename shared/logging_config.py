"""Structured JSON logging — القسم 9.3: لا تُسجّل كلمات المرور أو رموز الجلسة."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

_REDACT_KEYS = {"password", "token", "cookie", "storage_state", "session"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_fields", None)
        if extra:
            for k, v in extra.items():
                if any(bad in k.lower() for bad in _REDACT_KEYS):
                    v = "***redacted***"
                payload[k] = v
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
