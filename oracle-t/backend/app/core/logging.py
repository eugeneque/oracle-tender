import sys
from pathlib import Path

from loguru import logger

from app.core.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()
    logger.add(sys.stdout, level=settings.log_level, backtrace=False, diagnose=False)
    logger.add(
        log_dir / "app.log",
        level=settings.log_level,
        rotation="1 day",
        retention="180 days",
        compression="zip",
        backtrace=False,
        diagnose=False,
    )
