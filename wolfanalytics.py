"""WolfAnalytics: aggregate server activity counters."""
import json
import threading
from datetime import datetime, timedelta, timezone
from storage import data_path

_PATH = data_path("wolfanalytics.json")
_LOCK = threading.Lock()

def _load():
    try:
        value = json.loads(_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}

def _save(data):
    temp = _PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temp.replace(_PATH)

def record(guild_id: int, event: str) -> None:
    """Persist event type and timestamp only; never message content or user IDs."""
    if event not in {"join", "leave", "message"}:
        return
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=31)
    with _LOCK:
        data = _load()
        events = data.setdefault(str(guild_id), [])
        events.append({"at": now.isoformat(), "type": event})
        kept = []
        for item in events:
            try:
                stamp = datetime.fromisoformat(item["at"])
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                if stamp >= cutoff:
                    kept.append(item)
            except (TypeError, ValueError, KeyError):
                continue
        data[str(guild_id)] = kept[-50000:]
        _save(data)

def get_stats(guild_id: int, period: str) -> dict:
    hours = {"24h": 24, "7day": 168, "30day": 720}.get(period)
    if hours is None:
        raise ValueError("Period must be 24h, 7day or 30day.")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = {"joins": 0, "leaves": 0, "messages": 0}
    with _LOCK:
        events = list(_load().get(str(guild_id), []))
    for item in events:
        try:
            stamp = datetime.fromisoformat(item["at"])
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError, KeyError):
            continue
        if stamp >= cutoff:
            key = {"join": "joins", "leave": "leaves", "message": "messages"}.get(item.get("type"))
            if key:
                result[key] += 1
    return result

def build_report(guild_name: str, period: str, stats: dict) -> str:
    label = {"24h": "Last 24 hours", "7day": "Last 7 days", "30day": "Last 30 days"}[period]
    return (f"📊 **WolfAnalytics — {guild_name}**\n**Period:** {label}\n\n"
            f"👋 Members joined: **{stats['joins']:,}**\n"
            f"🚪 Members left: **{stats['leaves']:,}**\n"
            f"💬 Messages sent: **{stats['messages']:,}**")
