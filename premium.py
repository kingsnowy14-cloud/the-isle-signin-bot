"""Account-based Premium entitlements for IronWolf.

Premium access can be granted through Railway allowlists, server boosts, or the
single dashboard administrator. Legacy Premium+ records are normalized to Premium.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from storage import data_path

GRANTS_FILE = data_path("premium_grants.json")
PREMIUM_ADMIN_ID = "1493110334280564807"


def _now():
    return datetime.now(timezone.utc)


def _read_grants():
    try:
        value = json.loads(GRANTS_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_grants(grants):
    GRANTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    GRANTS_FILE.write_text(json.dumps(grants, ensure_ascii=False, indent=2), encoding="utf-8")


def sync_manual_grants():
    """Sync both current and legacy Railway allowlists as Premium grants."""
    now = _now()
    active = set()
    for variable in ("PREMIUM_USER_IDS", "PREMIUM_PLUS_USER_IDS"):
        active.update(
            uid for uid in (item.strip() for item in (os.getenv(variable) or "").split(","))
            if uid.isdigit()
        )
    grants = _read_grants()
    for uid, grant in list(grants.items()):
        if not isinstance(grant, dict):
            grants.pop(uid, None)
            continue
        # Migrate persisted Premium+ users before allowlist cleanup so their
        # existing unexpired entitlement survives removal of the legacy env var.
        if grant.get("plan") == "premium_plus":
            grant["plan"] = "premium"
            if grant.get("source") == "manual_allowlist":
                grant["source"] = "legacy_migration"
        # Keep migrated grants, dashboard grants, and server-boost rewards.
        if uid not in active and grant.get("source") not in {"server_boost", "dashboard_admin", "legacy_migration"}:
            grants.pop(uid, None)
    for uid in active:
        grant = grants.get(uid)
        if not isinstance(grant, dict) or grant.get("source") != "manual_allowlist":
            grants[uid] = {
                "plan": "premium", "source": "manual_allowlist",
                "granted_at": now.isoformat(),
                "expires_at": (now + timedelta(days=365)).isoformat(),
                "expiry_dm_sent": False,
            }
        elif grant.get("plan") != "premium":
            grant["plan"] = "premium"
    _write_grants(grants)
    return grants


def grant_manual_premium(user_id, granted_by):
    """Grant 365 days of Premium; only the configured dashboard admin may do so."""
    actor = str(granted_by or "")
    uid = str(user_id or "").strip()
    if actor != PREMIUM_ADMIN_ID:
        raise PermissionError("Keine Berechtigung für manuelle Premium-Freischaltungen.")
    if not uid.isdigit() or len(uid) < 17 or len(uid) > 20:
        raise ValueError("Bitte eine gültige Discord-Benutzer-ID eingeben.")
    grants = sync_manual_grants()
    now = _now()
    existing = grants.get(uid)
    expiry = now + timedelta(days=365)
    if isinstance(existing, dict):
        try:
            old_expiry = datetime.fromisoformat(str(existing.get("expires_at", "")).replace("Z", "+00:00"))
            if old_expiry.tzinfo is None:
                old_expiry = old_expiry.replace(tzinfo=timezone.utc)
            if old_expiry > now:
                expiry = old_expiry + timedelta(days=365)
        except (TypeError, ValueError):
            pass
    grant = {
        "plan": "premium", "source": "dashboard_admin",
        "granted_by": actor, "granted_at": now.isoformat(),
        "expires_at": expiry.isoformat(), "expiry_dm_sent": False,
    }
    grants[uid] = grant
    _write_grants(grants)
    return grant



def revoke_manual_premium(user_id, revoked_by):
    """Revoke only dashboard-admin grants; preserve boosts and allowlist entitlements."""
    if str(revoked_by or "") != PREMIUM_ADMIN_ID:
        raise PermissionError("Keine Berechtigung zum Widerrufen von Premium.")
    uid = str(user_id or "").strip()
    if not uid.isdigit() or not 17 <= len(uid) <= 20:
        raise ValueError("Ungültige Discord-Benutzer-ID.")
    grants = sync_manual_grants()
    grant = grants.get(uid)
    if not isinstance(grant, dict):
        return False
    if grant.get("source") != "dashboard_admin":
        raise PermissionError("Diese Freischaltung stammt nicht aus der Admin-Verwaltung und bleibt unverändert.")
    grants.pop(uid, None)
    _write_grants(grants)
    return True

def grant_server_boost_premium(user_id, guild_id, days=183):
    """Grant or extend account Premium for boosting the configured server."""
    uid = str(user_id)
    if not uid.isdigit():
        return None
    grants = sync_manual_grants()
    now = _now()
    existing = grants.get(uid)
    if isinstance(existing, dict) and existing.get("source") in {"manual_allowlist", "dashboard_admin"}:
        return existing
    expiry = now
    if isinstance(existing, dict) and existing.get("source") == "server_boost":
        try:
            previous_expiry = datetime.fromisoformat(str(existing.get("expires_at", "")).replace("Z", "+00:00"))
            if previous_expiry.tzinfo is None:
                previous_expiry = previous_expiry.replace(tzinfo=timezone.utc)
            if previous_expiry > now:
                expiry = previous_expiry
        except (TypeError, ValueError):
            pass
    grant = {
        "plan": "premium", "source": "server_boost", "guild_id": str(guild_id),
        "granted_at": now.isoformat(),
        "expires_at": (expiry + timedelta(days=days)).isoformat(),
        "expiry_dm_sent": False,
    }
    grants[uid] = grant
    _write_grants(grants)
    return grant


def expired_grants():
    grants = sync_manual_grants()
    now = _now()
    result = []
    for uid, grant in grants.items():
        try:
            expiry = datetime.fromisoformat(str(grant.get("expires_at", "")).replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if expiry <= now and not grant.get("expiry_dm_sent", False):
            result.append((uid, grant))
    return result


def mark_expiry_dm_sent(user_id):
    grants = _read_grants()
    if str(user_id) in grants:
        grants[str(user_id)]["expiry_dm_sent"] = True
        _write_grants(grants)


PREMIUM_FEATURES = {
    "ticket": "Ticket-System Pro",
    "branding": "Custom Branding",
    "custom_bot": "Custom Bot Profile (server-specific)",
    "security": "Security Pro",
    "automation": "Automation Pro",
    "invite_tracker": "Invite Tracker",
    "server_boost": "Server-Boost-System",
    "applications": "Pro application system (unlimited panels and questions)",
    "shift_tracker": "Schicht- und Zeiterfassungssystem",
    "wolfanalytics": "WolfAnalytics",
}

PLAN_FEATURES = {
    "free": frozenset(),
    "premium": frozenset(PREMIUM_FEATURES),
}


def account_plan(user_id):
    """Return the non-expired Premium entitlement for a Discord account."""
    if not user_id or not str(user_id).isdigit():
        return "free"
    grant = sync_manual_grants().get(str(user_id))
    if not isinstance(grant, dict):
        return "free"
    try:
        expiry = datetime.fromisoformat(str(grant.get("expires_at", "")).replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return "free"
    if expiry <= _now():
        return "free"
    return "premium" if grant.get("plan") in {"premium", "premium_plus"} else "free"


def has_feature(user_id, feature):
    if feature not in PREMIUM_FEATURES:
        return False
    return feature in PLAN_FEATURES[account_plan(user_id)]


def feature_label(feature):
    return PREMIUM_FEATURES.get(feature, "Unbekanntes Premium-Modul")


def guild_trial_active(guild_config):
    if not isinstance(guild_config, dict) or not guild_config.get("premium_trial_used"):
        return False
    try:
        expiry = datetime.fromisoformat(str(guild_config.get("premium_trial_expires_at") or "").replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return False
    return expiry > _now()


def guild_has_feature(guild_config, feature):
    if not isinstance(guild_config, dict) or feature not in PREMIUM_FEATURES:
        return False
    override_plan = guild_config.get("premium_server_override")
    if override_plan in {"premium", "premium_plus"}:
        return feature in PLAN_FEATURES["premium"]
    if guild_trial_active(guild_config):
        return True
    return account_plan(guild_config.get("premium_owner_id")) == "premium"


def guild_plan(guild_config):
    if not isinstance(guild_config, dict):
        return "free"
    override = guild_config.get("premium_server_override")
    if override in {"premium", "premium_plus"}:
        return "premium"
    if guild_trial_active(guild_config):
        return "premium"
    return account_plan(guild_config.get("premium_owner_id"))
