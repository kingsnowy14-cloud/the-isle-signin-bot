"""Account-based premium entitlements for IronWolf.

No payment provider is connected here. Access is granted only by explicit
Discord user IDs configured in Railway environment variables.
"""
import os

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
    """Return the highest manually granted plan for a Discord account."""
    if not user_id or not str(user_id).isdigit():
        return "free"
    uid = str(user_id)
    if uid in _user_ids("PREMIUM_PLUS_USER_IDS"):
        return "premium_plus"
    if uid in _user_ids("PREMIUM_USER_IDS"):
        return "premium"
    return "free"


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
