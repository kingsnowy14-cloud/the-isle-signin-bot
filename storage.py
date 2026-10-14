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
if not DATA_DIR.is_absolute():
    DATA_DIR = BASE_DIR / DATA_DIR
DATA_DIR = DATA_DIR.resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

if IS_RAILWAY:
    if not os.getenv("DATA_DIR"):
        print(f"[STORAGE] WARNING: DATA_DIR is not set; using {DATA_DIR}.", flush=True)
    if not DATA_DIR.is_mount():
        print(
            f"[STORAGE] WARNING: {DATA_DIR} is not detected as a mounted volume. "
            "A DATA_DIR variable alone is not persistent storage. Attach a Railway Volume "
            "and set its Mount Path to the same value as DATA_DIR.",
            flush=True,
        )

try:
    probe = DATA_DIR / f".ironwolf-storage-check-{os.getpid()}"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()
except OSError as exc:
    raise RuntimeError(
        f"[STORAGE] DATA_DIR={DATA_DIR} is not writable; check the Railway Volume mount and permissions."
    ) from exc


def data_path(filename: str) -> Path:
    """Return a persistent data path, preserving any legacy file on first migration."""
    if Path(filename).name != filename:
        raise ValueError("data_path expects a filename, not a path")
    target = DATA_DIR / filename
    legacy = BASE_DIR / filename
    if target.resolve() != legacy.resolve() and not target.exists() and legacy.is_file():
        try:
            shutil.copy2(legacy, target)
            print(f"[STORAGE] Migrated existing {filename} to {target}.", flush=True)
        except OSError as exc:
            raise RuntimeError(
                f"[STORAGE] Could not migrate existing {filename} to {target}; "
                "refusing to start with potentially empty settings."
            ) from exc
    return target
