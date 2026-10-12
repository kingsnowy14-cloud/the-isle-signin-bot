"""Shared durable data paths for IronWolf.

Set DATA_DIR to the Railway Volume mount path (normally /data).
Existing local data files are copied once if the destination does not exist.
"""
import os
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR))).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)


def data_path(filename: str) -> Path:
    """Return a persistent data path, preserving any legacy file on first migration."""
    target = DATA_DIR / filename
    legacy = BASE_DIR / filename
    if target.resolve() != legacy.resolve() and not target.exists() and legacy.is_file():
        shutil.copy2(legacy, target)
    return target
