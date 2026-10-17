"""Defensive storage helpers for The Isle player registrations."""


def _mapping(parent, key):
    if not isinstance(parent, dict):
        return {}
    value = parent.get(key)
    return value if isinstance(value, dict) else {}


def get_registration(data, user_id, guild_id):
    """Return a valid registration or None when stored data is missing/corrupt."""
    registrations = _mapping(data, "registrations")
    guild_registrations = _mapping(registrations, str(guild_id))
    record = guild_registrations.get(str(user_id))
    if not isinstance(record, dict):
        return None
    if not isinstance(record.get("dinosaur"), str) or not record["dinosaur"].strip():
        return None
    if not isinstance(record.get("location"), str) or not record["location"].strip():
        return None
    if not isinstance(record.get("skin", ""), str):
        return None
    return record


def set_registration(data, user_id, guild_id, registration):
    """Write a registration while repairing malformed parent containers."""
    if not isinstance(data, dict):
        raise TypeError("data must be a dictionary")
    registrations = data.get("registrations")
    if not isinstance(registrations, dict):
        registrations = {}
        data["registrations"] = registrations
    guild_key = str(guild_id)
    guild_registrations = registrations.get(guild_key)
    if not isinstance(guild_registrations, dict):
        guild_registrations = {}
        registrations[guild_key] = guild_registrations
    guild_registrations[str(user_id)] = dict(registration)


def remove_registration(data, user_id, guild_id):
    """Remove one registration safely; return the previous valid or raw record."""
    registrations = _mapping(data, "registrations")
    guild_registrations = _mapping(registrations, str(guild_id))
    if not isinstance(data, dict) or not isinstance(data.get("registrations"), dict):
        return None
    return guild_registrations.pop(str(user_id), None)
