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
    for plan, variable in (("premium", "PREMIUM_USER_IDS"), ("premium_plus", "PREMIUM_PLUS_USER_IDS")):
        for raw_id in (os.getenv(variable) or "").split(","):
            uid = raw_id.strip()
            if uid.isdigit():
                active[uid] = plan
    grants = _read_grants()
    for uid in list(grants):
        if uid not in active:
            del grants[uid]
    for uid, plan in active.items():
        grant = grants.get(uid)
        if not isinstance(grant, dict) or grant.get("plan") != plan:
            grants[uid] = {"plan": plan, "granted_at": now.isoformat(), "expires_at": (now + timedelta(days=365)).isoformat(), "expiry_dm_sent": False}
    _write_grants(grants)
    return grants


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
}

PLAN_FEATURES = {
    "free": frozenset(),
    "premium": frozenset({"ticket", "branding"}),
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
    return grant.get("plan", "free") if grant.get("plan") in {"premium", "premium_plus"} else "free"


def has_feature(user_id, feature):
    """Check a known feature against the account's current allowlisted plan."""
    if feature not in PREMIUM_FEATURES:
        return False
    return feature in PLAN_FEATURES[account_plan(user_id)]


def feature_label(feature):
    return PREMIUM_FEATURES.get(feature, "Unbekanntes Premium-Modul")


def guild_has_feature(guild_config, feature):
    """Resolve a server feature through its explicitly linked purchasing account."""
    if not isinstance(guild_config, dict):
        return False
    owner_id = guild_config.get("premium_owner_id")
    return has_feature(owner_id, feature)
