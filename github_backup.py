"""Daily backups of Railway's persistent /data volume to rotating GitHub Release assets."""
import asyncio
import os
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

DATA_DIR = Path(os.getenv("DATA_DIR", "/data")).expanduser()
REPOSITORY = os.getenv("GITHUB_BACKUP_REPOSITORY", "kingsnowy14-cloud/the-isle-signin-bot")
RELEASE_TAG = "ironwolf-backups"
MAX_ARCHIVE_BYTES = 1_900_000_000
KEEP_ASSETS = 2
API = "https://api.github.com"


def _headers(token):
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _sqlite_snapshot(source_path, destination_path):
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True, timeout=30)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def _make_archive(archive_path):
    if not DATA_DIR.is_dir():
        raise RuntimeError(f"Persistent data directory does not exist: {DATA_DIR}")
    with tempfile.TemporaryDirectory(prefix="ironwolf-sqlite-") as snapshot_dir:
        snapshots = Path(snapshot_dir)
        with tarfile.open(archive_path, "w:gz", compresslevel=6) as archive:
            for path in sorted(DATA_DIR.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(DATA_DIR)
                if path.name.endswith(("-wal", "-shm")):
                    continue
                if path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
                    snapshot = snapshots / (str(relative).replace("/", "__") + ".snapshot")
                    _sqlite_snapshot(path, snapshot)
                    archive.add(snapshot, arcname=relative.as_posix(), recursive=False)
                else:
                    archive.add(path, arcname=relative.as_posix(), recursive=False)
    size = archive_path.stat().st_size
    if size <= 0 or size > MAX_ARCHIVE_BYTES:
        raise RuntimeError(f"Backup archive size outside supported range: {size} bytes")
    return size


def _get_release(token):
    headers = _headers(token)
    url = f"{API}/repos/{REPOSITORY}/releases/tags/{RELEASE_TAG}"
    response = requests.get(url, headers=headers, timeout=30)
    if response.status_code == 200:
        return response.json()
    if response.status_code != 404:
        response.raise_for_status()
    response = requests.post(
        f"{API}/repos/{REPOSITORY}/releases",
        headers=headers,
        json={
            "tag_name": RELEASE_TAG,
            "target_commitish": "main",
            "name": "IronWolf automatic backups",
            "body": "Private rotating backups of the Railway /data volume.",
            "draft": False,
            "prerelease": True,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _upload_and_rotate(token, archive_path):
    release = _get_release(token)
    upload_url = release["upload_url"].split("{", 1)[0]
    filename = f"ironwolf-backup-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.tar.gz"
    with archive_path.open("rb") as archive_file:
        response = requests.post(
            upload_url,
            params={"name": filename},
            headers={**_headers(token), "Content-Type": "application/gzip"},
            data=archive_file,
            timeout=(30, 600),
        )
    response.raise_for_status()
    uploaded = response.json()

    assets_url = release["assets_url"].split("{", 1)[0]
    response = requests.get(assets_url, headers=_headers(token), timeout=30)
    response.raise_for_status()
    assets = sorted(response.json(), key=lambda item: item.get("created_at", ""), reverse=True)
    for old_asset in assets[KEEP_ASSETS:]:
        response = requests.delete(old_asset["url"], headers=_headers(token), timeout=30)
        response.raise_for_status()
    return uploaded.get("name", filename)


def _backup_once():
    token = os.getenv("GITHUB_BACKUP_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GITHUB_BACKUP_TOKEN is not configured")
    with tempfile.TemporaryDirectory(prefix="ironwolf-backup-") as work_dir:
        archive_path = Path(work_dir) / "ironwolf-backup.tar.gz"
        size = _make_archive(archive_path)
        asset_name = _upload_and_rotate(token, archive_path)
    (DATA_DIR / ".github_backup_last_success").write_text(
        datetime.now(timezone.utc).date().isoformat(), encoding="utf-8"
    )
    print(
        f"[GITHUB BACKUP] Success: {asset_name} ({size} bytes) in private repository {REPOSITORY}.",
        flush=True,
    )


async def github_backup_worker():
    """Run at 03:00 UTC daily and avoid duplicate uploads after a restart."""
    if not os.getenv("GITHUB_BACKUP_TOKEN", "").strip():
        print("[GITHUB BACKUP] Disabled: GITHUB_BACKUP_TOKEN is not configured.", flush=True)
        return
    marker = DATA_DIR / ".github_backup_last_success"
    while True:
        now = datetime.now(timezone.utc)
        scheduled = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if now >= scheduled:
            last_success = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
            if last_success != now.date().isoformat():
                try:
                    print("[GITHUB BACKUP] Starting daily /data backup.", flush=True)
                    await asyncio.to_thread(_backup_once)
                except Exception as exc:
                    print(f"[GITHUB BACKUP] Failed: {type(exc).__name__}: {exc}", flush=True)
            scheduled = datetime.now(timezone.utc).replace(
                hour=3, minute=0, second=0, microsecond=0
            ) + timedelta(days=1)
        await asyncio.sleep(max(30, (scheduled - datetime.now(timezone.utc)).total_seconds()))
