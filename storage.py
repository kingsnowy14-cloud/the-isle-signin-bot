"""Shared durable data paths for IronWolf.

Set DATA_DIR to the Railway Volume mount path (normally /data).
Existing local data files are copied once if the destination does not exist.
"""
import os
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
IS_RAILWAY = any(os.getenv(key) for key in ("RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID"))
DEFAULT_DATA_DIR = "/data" if IS_RAILWAY else str(BASE_DIR)
DATA_DIR = Path(os.getenv("DATA_DIR", DEFAULT_DATA_DIR)).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)

if IS_RAILWAY and not DATA_DIR.is_mount():
    print(
        f"[STORAGE] WARNING: {DATA_DIR} is not detected as a mounted volume. "
        "Attach a Railway Volume at this path and set DATA_DIR to the mount path; "
        "otherwise saved settings may be lost when the service is redeployed.",
        flush=True,
    )


def data_path(filename: str) -> Path:
    """Return a persistent data path, preserving any legacy file on first migration."""
    target = DATA_DIR / filename
    legacy = BASE_DIR / filename
    if target.resolve() != legacy.resolve() and not target.exists() and legacy.is_file():
        try:
            shutil.copy2(legacy, target)
            print(f"[STORAGE] Migrated existing {filename} to {target}.", flush=True)
        except OSError as exc:
            print(
                f"[STORAGE] WARNING: Could not migrate {filename} to {target}: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
    return target
