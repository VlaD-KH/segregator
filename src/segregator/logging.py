"""JSON-логирование с обязательным маскированием чувствительных полей.

Реализует инварианты 3-4 из docs/DATA_BOUNDARY.md: PESEL, IBAN и номера
карт не должны попасть в лог. Значение оборачивается в ``Sensitive(...)``
явно в месте вызова, либо ловится по имени ключа из денай-листа — обе
проверки применяются процессором до рендеринга в JSON.
"""

from __future__ import annotations

import logging
from pathlib import Path

import structlog

MASK = "***"
SENSITIVE_KEYS = {"pesel", "iban", "card_number"}


class Sensitive:
    """Оборачивает значение, которое нельзя писать в лог как есть."""

    __slots__ = ("_value",)

    def __init__(self, value: object) -> None:
        self._value = value

    def __repr__(self) -> str:
        return MASK

    def __str__(self) -> str:
        return MASK


def _redact(logger: object, method_name: str, event_dict: dict) -> dict:
    for key, value in list(event_dict.items()):
        if isinstance(value, Sensitive) or key.lower() in SENSITIVE_KEYS or key.lower().endswith("_sensitive"):
            event_dict[key] = MASK
    return event_dict


def configure_logging(log_dir: Path) -> None:
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(log_dir / "segregator.ndjson", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
