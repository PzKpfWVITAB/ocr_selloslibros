import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from src.core.settings_manager import SettingsManager

_LOGGER_NAME = "ocr_selloslibros"
_INITIALIZED = False


def setup_logger(
    log_level: int = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB por archivo
    backup_count: int = 5,
) -> logging.Logger:
    """
    Configura y devuelve el logger centralizado del sistema.
    Escribe logs rotativos en logs/ocr_selloslibros.log y en la consola.
    """
    global _INITIALIZED
    logger = logging.getLogger(_LOGGER_NAME)

    if _INITIALIZED:
        return logger

    logger.setLevel(log_level)
    logger.propagate = False

    # Crear carpeta logs/ en el directorio base del proyecto
    try:
        base_dir = SettingsManager.get_base_dir()
        log_dir = base_dir / "logs"
    except Exception:
        log_dir = Path(__file__).resolve().parent.parent.parent / "logs"

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "ocr_selloslibros.log"

        file_handler = RotatingFileHandler(
            str(log_file),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)
        file_formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [%(threadName)s] %(name)s (%(filename)s:%(lineno)d): %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
    except Exception as exc:
        print(f"[WARN] No se pudo inicializar RotatingFileHandler en {log_dir}: {exc}", file=sys.stderr)

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.WARNING)  # Solo advertencias y errores en stdout de consola
    console_formatter = logging.Formatter(
        fmt="[%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    _INITIALIZED = True
    return logger


def get_logger(module_name: Optional[str] = None) -> logging.Logger:
    """Obtiene el logger raíz o un logger hijo del sistema."""
    if not _INITIALIZED:
        setup_logger()
    if module_name:
        return logging.getLogger(f"{_LOGGER_NAME}.{module_name}")
    return logging.getLogger(_LOGGER_NAME)
