"""Account-based premium entitlements for IronWolf.

No payment provider is connected here. Access is granted only by explicit
Discord user IDs configured in Railway environment variables.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from storage import data_path

GRANTS_FILE = data_path("premium_grants.json")


def _now():
    return datetime.now(timezone.utc)


def _read_grants():
    try:
        value = json.loads(GRANTS_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_grants(grants):
    GRANTS_FILE.write_text(json.dumps(grants, ensure_ascii=False, indent=2), encoding="utf-8")


def sync_manual_grants():
    """Track Railway allowlist grants with a persistent 365-day term."""
    now = _now()
    active = {}
    for plan, variable in (("premium", "PREMIUM_USER_IDS"), ("premium", "PREMIUM_PLUS_USER_IDS")):
        for raw_id in (os.getenv(variable) or "").split(","):
            uid = raw_id.strip()
            if uid.isdigit():
                active[uid] = plan
    grants = _read_grants()
    for uid in list(grants):
        grant = grants.get(uid)
        # Keep server-boost rewards even though they are not in the Railway allowlist.
        if uid not in active and not (isinstance(grant, dict) and grant.get("source") == "server_boost"):
            del grants[uid]
    for uid, plan in active.items():
        grant = grants.get(uid)
        if not isinstance(grant, dict) or grant.get("plan") != plan or grant.get("source") != "manual_allowlist":
            grants[uid] = {"plan": plan, "source": "manual_allowlist", "granted_at": now.isoformat(), "expires_at": (now + timedelta(days=365)).isoformat(), "expiry_dm_sent": False}
    _write_grants(grants)
    return grants


def grant_server_boost_premium(user_id, guild_id, days=183):
    """Grant or extend six months of account Premium for boosting the configured server."""
    uid = str(user_id)
    if not uid.isdigit():
        return None
    grants = sync_manual_grants()
    now = _now()
    existing = grants.get(uid)
    if isinstance(existing, dict) and existing.get("source") == "manual_allowlist":
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
        "plan": "premium",
        "source": "server_boost",
        "guild_id": str(guild_id),
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
    "security": "Security Pro",
    "automation": "Automation Pro",
    "invite_tracker": "Invite Tracker",
    "server_boost": "Server-Boost-System",
    "applications": "Bewerbungs-System Pro (unbegrenzte Panels)",
}

PLAN_FEATURES = {
    "free": frozenset(),
    "premium": frozenset(PREMIUM_FEATURES),
    "premium_plus": frozenset(PREMIUM_FEATURES),
}


def _user_ids(variable):
    """Parse a comma-separated allowlist without exposing it to the UI."""
    return {
        item.strip()
        for item in (os.getenv(variable) or "").split(",")
        if item.strip().isdigit()
    }


def account_plan(user_id):
    """Return the non-expired manually granted plan for a Discord account."""
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
    """Check a known feature against the account's current allowlisted plan."""
    if feature not in PREMIUM_FEATURES:
        return False
    return feature in PLAN_FEATURES[account_plan(user_id)]


def feature_label(feature):
    return PREMIUM_FEATURES.get(feature, "Unbekanntes Premium-Modul")


def guild_trial_active(guild_config):
    """Return whether this server's one-time 15-day Premium trial is active."""
    if not isinstance(guild_config, dict) or not guild_config.get("premium_trial_used"):
        return False
    raw_expiry = guild_config.get("premium_trial_expires_at")
    try:
        expiry = datetime.fromisoformat(str(raw_expiry or "").replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return False
    return expiry > _now()


def guild_has_feature(guild_config, feature):
    """Resolve a feature from a manual server grant, linked account, or active trial."""
    if not isinstance(guild_config, dict) or feature not in PREMIUM_FEATURES:
        return False
    override_plan = guild_config.get("premium_server_override")
    if override_plan in PLAN_FEATURES and override_plan != "free":
        return feature in PLAN_FEATURES[override_plan]
    if guild_trial_active(guild_config):
        return True
    owner_id = guild_config.get("premium_owner_id")
    owner_plan = account_plan(owner_id)
    if owner_plan != "free":
        return feature in PLAN_FEATURES[owner_plan]
    return False
