import json
import logging
import sys
from datetime import datetime, timezone

from api.core.middleware import get_trace_context

# Atributos estándar de LogRecord — todo lo que no esté en este set y venga vía
# logger.info(msg, extra={...}) se trata como campo estructurado propio y se
# mezcla en el JSON de salida (ej. http_method, duration_ms en request_completed).
_RESERVED_RECORD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "taskName",
}


class JsonFormatter(logging.Formatter):
    """Formatter de logs JSON a stdout, compatible con la ingesta estructurada de Cloud Logging."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "logger": record.name,
        }

        trace_id, span_id, correlation_id = get_trace_context()
        if trace_id:
            payload["logging.googleapis.com/trace"] = trace_id
        if span_id:
            payload["logging.googleapis.com/spanId"] = span_id
        if correlation_id:
            payload["correlation_id"] = correlation_id

        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_RECORD_ATTRS and not key.startswith("_")
        }
        payload.update(extras)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Redirige el logger raíz (y los de uvicorn/gunicorn) a stdout en JSON."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "gunicorn", "gunicorn.access", "gunicorn.error"):
        noisy_logger = logging.getLogger(name)
        noisy_logger.handlers = [handler]
        noisy_logger.propagate = False
