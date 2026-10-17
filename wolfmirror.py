"""WolfMirror: deterministic, read-only security tabletop simulations.

This module never performs Discord actions. Both the web dashboard and bot use
it to explain which existing protections would be available in a scenario.
"""
from __future__ import annotations

from typing import Any

SCENARIOS = {
    "raid": {
        "label": "Mitglieder-Raid",
        "summary": "Viele neue Mitglieder treten in kurzer Zeit bei.",
        "signals": ("anti_raid_enabled",),
    },
    "spam": {
        "label": "Spam-Welle",
        "summary": "Ein Mitglied sendet wiederholt unerwünschte Nachrichten.",
        "signals": ("automod_enabled",),
    },
    "nuke": {
        "label": "Channel-/Rollen-Angriff",
        "summary": "Ein verdächtiger Account löscht oder erstellt viele Serverobjekte.",
        "signals": ("anti_nuke_enabled",),
    },
    "new_accounts": {
        "label": "Welle neuer Accounts",
        "summary": "Mehrere sehr junge Accounts treten innerhalb kurzer Zeit bei.",
        "signals": ("anti_raid_enabled",),
    },
}

def simulate_scenario(
    config: dict[str, Any],
    bot_permissions: dict[str, bool],
    scenario: str,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a safe, explainable scenario report; never mutate config or Discord."""
    if scenario not in SCENARIOS:
        raise ValueError("Unknown WolfMirror scenario")

    metrics = metrics or {}
    definition = SCENARIOS[scenario]
    controls: list[dict[str, Any]] = []
    enabled_key = definition["signals"][0]
    enabled = bool(config.get(enabled_key, False))
    controls.append({
        "name": {
            "anti_raid_enabled": "Anti-Raid",
            "automod_enabled": "AutoMod",
            "anti_nuke_enabled": "Anti-Nuke",
        }[enabled_key],
        "active": enabled,
        "detail": "Aktiviert" if enabled else "Nicht aktiviert",
    })

    if scenario in {"raid", "new_accounts"}:
        threshold = _bounded_int(config.get("raid_threshold", 8), 8, 3, 30)
        window = _bounded_int(config.get("raid_window_seconds", 10), 10, 5, 60)
        action = str(config.get("raid_action", "timeout")).lower()
        action = action if action in {"timeout", "alert"} else "timeout"
        controls.append({
            "name": "Raid-Schwelle",
            "active": enabled,
            "detail": f"{threshold} Beitritte in {window} Sekunden",
        })
        controls.append({
            "name": "Konfigurierte Reaktion",
            "active": enabled,
            "detail": "Timeout" if action == "timeout" else "Nur Alarmierung",
        })
        if action == "timeout":
            can_moderate = bool(bot_permissions.get("moderate_members", False))
            controls.append({
                "name": "Mitglieder moderieren",
                "active": can_moderate,
                "detail": "Berechtigung vorhanden" if can_moderate else "Berechtigung fehlt – Timeout wäre blockiert",
            })
        if scenario == "new_accounts":
            min_age = _bounded_int(config.get("raid_min_account_age_days", 0), 0, 0, 365)
            controls.append({
                "name": "Accountalter-Hinweis",
                "active": min_age > 0,
                "detail": f"Accounts unter {min_age} Tagen markieren" if min_age else "Nicht konfiguriert",
            })

    if scenario == "spam":
        controls.append({
            "name": "Nachrichten verwalten",
            "active": bool(bot_permissions.get("manage_messages", False)),
            "detail": "Berechtigung vorhanden" if bot_permissions.get("manage_messages") else "Berechtigung fehlt",
        })
    if scenario == "nuke":
        controls.append({
            "name": "Audit-Log lesen",
            "active": bool(bot_permissions.get("view_audit_log", False)),
            "detail": "Berechtigung vorhanden" if bot_permissions.get("view_audit_log") else "Berechtigung fehlt",
        })

    active_count = sum(1 for item in controls if item["active"])
    score = round(100 * active_count / max(1, len(controls)))
    gaps = [item["name"] + ": " + item["detail"] for item in controls if not item["active"]]
    if not enabled:
        verdict = "Schutzregel fehlt"
    elif gaps:
        verdict = "Teilweise vorbereitet"
    else:
        verdict = "Gute Ausgangslage"

    return {
        "scenario": scenario,
        "label": definition["label"],
        "summary": definition["summary"],
        "score": score,
        "verdict": verdict,
        "controls": controls,
        "gaps": gaps,
        "metrics": {
            "member_count": _bounded_int(metrics.get("member_count", 0), 0, 0, 10_000_000),
            "channel_count": _bounded_int(metrics.get("channel_count", 0), 0, 0, 100_000),
        },
        "dry_run": True,
        "disclaimer": "Simulation auf Basis gespeicherter Einstellungen und aktueller Bot-Berechtigungen. Keine Aktion wurde ausgeführt; dies ist keine Garantie gegen Angriffe.",
    }


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(minimum, min(maximum, parsed))
