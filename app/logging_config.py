from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import Settings


def setup_logging(settings: Settings) -> None:
    Path(settings.log_dir).mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    app_file = RotatingFileHandler(
        settings.log_dir / "app.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    app_file.setFormatter(formatter)

    payments_file = RotatingFileHandler(
        settings.log_dir / "payments.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    payments_file.setFormatter(formatter)

    logging.basicConfig(level=settings.log_level.upper(), handlers=[stream_handler, app_file])

    payments_logger = logging.getLogger("payments")
    payments_logger.setLevel(settings.log_level.upper())
    payments_logger.addHandler(payments_file)
