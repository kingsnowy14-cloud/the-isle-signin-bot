"""WolfChronicle: shared, bounded incident records for the bot and dashboard.

The module is deliberately side-effect free: callers provide the per-guild config
and persist it through the repository's existing atomic JSON storage helpers.
No Discord moderation actions or message-content collection happen here.
"""
from __future__ import annotations

from datetime import datetime, timezone
import secrets
from typing import Any

INCIDENT_TYPES = {
    "raid": "Mitglieder-Raid",
    "spam": "Spam-Welle",
    "permissions": "Berechtigungsproblem",
    "nuke": "Channel-/Rollen-Angriff",
    "other": "Sonstiger Vorfall",
}
SEVERITIES = {"low": "Niedrig", "medium": "Mittel", "high": "Hoch", "critical": "Kritisch"}
STATUSES = {"open": "Offen", "investigating": "In Bearbeitung", "resolved": "Abgeschlossen"}
MAX_INCIDENTS = 100
MAX_HISTORY_EVENTS = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_incidents(config: dict[str, Any], limit: int = 20) -> list[dict[str, Any]]:
    """Return newest well-formed incident records without exposing mutable storage."""
    records = config.get("wolfchronicle_incidents", [])
    if not isinstance(records, list):
        return []
    safe = [
        dict(item) for item in records
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and isinstance(item.get("type"), str)
        and item.get("type") in INCIDENT_TYPES
        and isinstance(item.get("severity"), str)
        and item.get("severity") in SEVERITIES
        and isinstance(item.get("status"), str)
        and item.get("status") in STATUSES
    ]
    safe.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    try:
        count = max(1, min(int(limit), MAX_INCIDENTS))
    except (TypeError, ValueError):
        count = 20
    return safe[:count]


def create_incident(
    config: dict[str, Any],
    *,
    incident_type: str,
    severity: str,
    title: str,
    summary: str,
    reporter_id: str | int,
    source: str,
) -> dict[str, Any]:
    """Create a bounded, structured incident record in the supplied guild config."""
    if incident_type not in INCIDENT_TYPES:
        raise ValueError("Unbekannter Vorfalltyp.")
    if severity not in SEVERITIES:
        raise ValueError("Unbekannte Priorität.")
    clean_title = " ".join(str(title).split())[:120]
    clean_summary = " ".join(str(summary).split())[:500]
    if not clean_title:
        raise ValueError("Der Titel darf nicht leer sein.")
    if not clean_summary:
        raise ValueError("Die Beschreibung darf nicht leer sein.")

    records = config.get("wolfchronicle_incidents")
    if not isinstance(records, list):
        records = []
    now = _now()
    # 96 bits of randomness makes accidental collisions negligible even at scale.
    incident = {
        "id": secrets.token_hex(12).upper(),
        "created_at": now,
        "updated_at": now,
        "reporter_id": str(reporter_id)[:32],
        "source": "dashboard" if source == "dashboard" else "discord",
        "type": incident_type,
        "type_label": INCIDENT_TYPES[incident_type],
        "severity": severity,
        "severity_label": SEVERITIES[severity],
        "status": "open",
        "status_label": STATUSES["open"],
        "title": clean_title,
        "summary": clean_summary,
        "resolution_note": "",
        "history": [{
            "at": now,
            "event": "created",
            "status": "open",
            "actor_id": str(reporter_id)[:32],
        }],
    }
    config["wolfchronicle_incidents"] = [incident] + [
        item for item in records
        if isinstance(item, dict) and item.get("id") != incident["id"]
    ][: MAX_INCIDENTS - 1]
    return dict(incident)


def update_incident(
    config: dict[str, Any],
    incident_id: str,
    *,
    status: str,
    resolution_note: str = "",
    actor_id: str | int | None = None,
) -> dict[str, Any] | None:
    """Update workflow state and append a bounded status-change event."""
    if status not in STATUSES:
        raise ValueError("Unbekannter Vorfallstatus.")
    records = config.get("wolfchronicle_incidents")
    if not isinstance(records, list):
        return None
    target = str(incident_id or "").strip().upper()
    for item in records:
        if isinstance(item, dict) and str(item.get("id", "")).upper() == target:
            previous_status = item.get("status", "open")
            now = _now()
            item["status"] = status
            item["status_label"] = STATUSES[status]
            item["updated_at"] = now
            item["resolution_note"] = " ".join(str(resolution_note).split())[:500] if status == "resolved" else ""
            history = item.get("history")
            if not isinstance(history, list):
                history = []
            history.append({
                "at": now,
                "event": "status_changed",
                "from": previous_status if previous_status in STATUSES else "unknown",
                "status": status,
                "actor_id": str(actor_id)[:32] if actor_id is not None else "",
            })
            item["history"] = history[-MAX_HISTORY_EVENTS:]
            return dict(item)
    return None
