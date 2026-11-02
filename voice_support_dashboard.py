"""Dashboard routes for IronWolf voice support."""
import html
import discord
from premium import guild_has_feature
from flask import abort, redirect, request, session, url_for


def register():
    import dashboard as d

    @d.app.route("/guild/<guild_id>/voice-support", methods=["GET", "POST"])
    def guild_voice_support(guild_id):
        guild, response = d._authorized_guild(guild_id)
        if response:
            return response
        gid = str(guild.id)
        cfg = d._guild_config(guild.id)
        if request.method == "POST":
            if not d._csrf_ok():
                abort(400, "CSRF-Prüfung fehlgeschlagen.")
            actor = guild.get_member(int(session.get("user_id", "0")))
            if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
                abort(403, "You need Administrator or Manage Server permissions.")
            premium_voice = guild_has_feature(cfg, "voice_support")
            waiting_raw = request.form.getlist("waiting_channels")
            if not waiting_raw:
                legacy = request.form.get("waiting_channel", "0")
                waiting_raw = [legacy] if legacy != "0" else []
            max_waiting = 5 if premium_voice else 1
            if len(waiting_raw) > max_waiting:
                abort(400, f"Your plan allows a maximum of {max_waiting} waiting room(s).")
            try:
                waiting_channels = [guild.get_channel(int(value)) for value in waiting_raw]
                notification = guild.get_channel(int(request.form.get("notification_channel", "0")))
                role = guild.get_role(int(request.form.get("support_role", "0")))
                category_raw = request.form.get("support_category", "0")
                category = guild.get_channel(int(category_raw)) if category_raw else None
                existing_raw = request.form.get("existing_support_channel", "0")
                existing_channel = guild.get_channel(int(existing_raw)) if existing_raw and existing_raw != "0" else None
            except (TypeError, ValueError):
                abort(400, "Please select valid channels and a role.")
            if not waiting_channels or any(not isinstance(ch, discord.VoiceChannel) for ch in waiting_channels):
                abort(400, "Please select at least one valid waiting room.")
            if len({ch.id for ch in waiting_channels}) != len(waiting_channels):
                abort(400, "Ein Warteraum darf nur einmal ausgewählt werden.")
            waiting = waiting_channels[0]
            if not isinstance(notification, discord.TextChannel):
                abort(400, "The notification channel must be a text channel.")
            if role is None or role.is_default() or role.managed:
                abort(400, "Please select a valid support role.")
            if category is not None and not isinstance(category, discord.CategoryChannel):
                abort(400, "Please select a valid category or clear the field.")
            if existing_channel is not None and (not isinstance(existing_channel, discord.VoiceChannel) or existing_channel.id in {ch.id for ch in waiting_channels}):
                abort(400, "Please select an existing support voice channel that is different from the waiting room.")
            cfg.update({
                "voice_support_enabled": request.form.get("enabled") == "on",
                "voice_support_waiting_channel": waiting.id,
                "voice_support_waiting_channels": [ch.id for ch in waiting_channels],
                "voice_support_notification_channel": notification.id,
                "voice_support_role": role.id,
                "voice_support_category": category.id if category else None,
                "voice_support_existing_channel": existing_channel.id if existing_channel else None,
                "voice_support_prefix": request.form.get("support_prefix", "Support").strip()[:40] or "Support",
            })
            d._save_json(d.data_path("config.json"), d._config)
            d._audit_dashboard_change(guild.id, "Voice Support", session.get("user_id"), "Settings saved")
            return redirect(url_for("guild_voice_support", guild_id=gid, saved="1"))

        def options(items, selected, placeholder):
            out = f'<option value="0">{html.escape(placeholder)}</option>'
            for item in items:
                sel = " selected" if str(item.id) == str(selected or "") else ""
                out += f'<option value="{item.id}"{sel}>{html.escape(item.name)}</option>'
            return out

        configured_waiting = cfg.get("voice_support_waiting_channels")
        if not isinstance(configured_waiting, list) or not configured_waiting:
            configured_waiting = [cfg.get("voice_support_waiting_channel")] if cfg.get("voice_support_waiting_channel") else []
        premium_voice = guild_has_feature(cfg, "voice_support")
        max_waiting = 5 if premium_voice else 1
        configured_ids = {int(x) for x in configured_waiting if str(x).isdigit()}
        waiting_opts = "".join(
            '<label class="voice-waiting-option" style="display:flex;align-items:center;gap:10px;padding:9px 12px;cursor:pointer;">'
            '<input type="checkbox" name="waiting_channels" value="' + str(ch.id) + '"'
            + (" checked" if ch.id in configured_ids else "") + ' style="width:auto;margin:0;">'
            + '<span>' + html.escape(ch.name) + '</span></label>'
            for ch in guild.voice_channels
        )
        waiting_limit_note = (
            '<div class="notice">⭐ Premium Voice Support: bis zu 5 Warteräume.</div>'
            if premium_voice else
            '<div class="notice">Free: 1 Warteraum. Mit Premium Voice Support kannst du bis zu 5 Warteräume konfigurieren.</div>'
        )
        notification_opts = options(guild.text_channels, cfg.get("voice_support_notification_channel"), "— Select a text channel —")
        role_opts = options([r for r in guild.roles if not r.is_default() and not r.managed], cfg.get("voice_support_role"), "— Select a support role —")
        category_opts = options(guild.categories, cfg.get("voice_support_category"), "— Kategorie des Warteraums verwenden —")
        existing_channel_opts = options([ch for ch in guild.voice_channels if ch.id not in configured_ids], cfg.get("voice_support_existing_channel"), "— Automatically create a new channel —")
        checked = " checked" if cfg.get("voice_support_enabled", False) else ""
        prefix = html.escape(str(cfg.get("voice_support_prefix") or "Support"), quote=True)
        saved = '<div class="notice">Settings saved.</div>' if request.args.get("saved") == "1" else ""
        body = (
            '<section class="card"><span class="pill">IRONWOLF VOICE SUPPORT</span><h1>🎧 Sprach-Support</h1>'
            '<p>Members join the waiting room. IronWolf notifies the team; a support agent claims the request using a button and receives a private voice channel.</p>'
            + saved + '<form method="post">' + d._csrf_field()
            + '<div class="check"><input type="checkbox" id="enabled" name="enabled"' + checked + '><label for="enabled">Enable voice support</label></div>'
            + '<label for="waiting_channels_dropdown">Voice Support-Warteräume (max. ' + str(max_waiting) + ')</label>'
            + '<details id="waiting_channels_dropdown" style="position:relative;margin-bottom:10px;">'
            + '<summary id="waiting_channels_summary" style="list-style:none;cursor:pointer;padding:12px 14px;border:1px solid #30394b;border-radius:9px;background:#0d1422;color:inherit;">Select waiting rooms ▾</summary>'
            + '<div id="waiting_channels_menu" data-max-selections="' + str(max_waiting) + '" style="position:relative;margin-top:5px;border:1px solid #30394b;border-radius:9px;background:#111827;max-height:240px;overflow-y:auto;">'
            + (waiting_opts or '<p style="padding:10px 12px;">Keine Sprachkanäle vorhanden.</p>') + '</div></details>'
            + waiting_limit_note
            + '<p id="waiting_channels_help">Select up to ' + str(max_waiting) + ' waiting rooms.</p>'
            + '<script>(function(){const menu=document.getElementById("waiting_channels_menu");const summary=document.getElementById("waiting_channels_summary");const help=document.getElementById("waiting_channels_help");if(!menu||!summary)return;const max=Number(menu.dataset.maxSelections||1);const boxes=Array.from(menu.querySelectorAll("input[name=waiting_channels]"));function update(){const chosen=boxes.filter(b=>b.checked);summary.textContent=(chosen.length?chosen.map(b=>b.parentElement.querySelector("span").textContent.trim()).join(", "):"Select waiting rooms")+" ▾";help.textContent=chosen.length+" of "+max+" waiting room(s) selected."; }boxes.forEach(box=>box.addEventListener("change",function(){const chosen=boxes.filter(b=>b.checked);if(chosen.length>max){box.checked=false;help.textContent="Your plan allows a maximum of "+max+" waiting room(s). Premium Voice Support is required for up to 5.";}update();}));update();})();</script>'
            + '<label for="notification_channel">Notification channel</label><select id="notification_channel" name="notification_channel" required>' + notification_opts + '</select>'
            + '<label for="support_role">Support-Team-Rolle</label><select id="support_role" name="support_role" required>' + role_opts + '</select>'
            + '<label for="support_category">Category for newly created rooms (optional)</label><select id="support_category" name="support_category">' + category_opts + '</select>'
            + '<label for="existing_support_channel">Use an existing support voice channel (optional)</label><select id="existing_support_channel" name="existing_support_channel">' + existing_channel_opts + '</select>'
            + '<p>If an existing channel is selected, the bot will use it only when it is empty. Otherwise, a new channel is created for each request.</p>'
            + '<label for="support_prefix">Channel name prefix (new channels only)</label><input id="support_prefix" name="support_prefix" maxlength="40" value="' + prefix + '" required>'
            + '<button type="submit">Save settings</button></form>'
            + '<div class="notice">Required permissions: Send Messages, create/delete voice channels, and move members.</div>'
            + '<p><a class="button secondary" href="/guild/' + gid + '">← Zur Serververwaltung</a></p></section>'
        )
        return d._render(guild.name + " · Sprach-Support", body)


    @d.app.route("/guild/<guild_id>/security-center", methods=["GET"])
    def guild_security_center(guild_id):
        """Structured, read-only security hub for this Discord server."""
        guild, response = d._authorized_guild(guild_id)
        if response:
            return response
        cfg = d._guild_config(guild.id)
        from security_suite import guild_state as security_guild_state
        secure = security_guild_state(guild.id)
        me = guild.me
        permission_checks = [
            ("Audit-Log lesen", bool(me and me.guild_permissions.view_audit_log)),
            ("Nachrichten verwalten", bool(me and me.guild_permissions.manage_messages)),
            ("Mitglieder moderieren", bool(me and me.guild_permissions.moderate_members)),
            ("Rollen verwalten", bool(me and me.guild_permissions.manage_roles)),
            ("Kanäle verwalten", bool(me and me.guild_permissions.manage_channels)),
        ]
        ready_permissions = sum(1 for _, ok in permission_checks if ok)
        protections = [
            ("Anti-Raid", bool(cfg.get("anti_raid_enabled")), "Beitrittswellen erkennen", "/wolfshield"),
            ("Anti-Nuke", bool(cfg.get("anti_nuke_enabled")), "Schutzregel in den Servereinstellungen", "?section=security#settings-panel"),
            ("AutoMod", bool(cfg.get("automod_enabled")), "Spam-Signale und Nachrichtenmoderation", "?section=general#settings-panel"),
            ("Link-/Scam-Filter", bool(secure.get("scam_enabled")), "Gesperrte Domains und Einladungslinks", "?section=security#settings-panel"),
            ("Account-Alter-Warnungen", bool(secure.get("alt_alerts_enabled", True)), "Junge Accounts markieren, ohne sie automatisch zu sanktionieren", "?section=security#settings-panel"),
            ("Verifizierung", bool(secure.get("verified_role_id") and secure.get("verify_channel_id")), "Verifizierungsrolle und Kanal eingerichtet", "/verification"),
        ]
        active = sum(1 for _, enabled, _, _ in protections if enabled)
        def card(title, detail, href, status=None):
            status_html = ('<span class="pill">' + ("Aktiv" if status else "Nicht aktiv") + '</span>') if status is not None else ""
            return (
                '<a class="card" style="display:block;text-decoration:none;color:inherit" href="/guild/'
                + str(guild.id) + href + '"><div style="display:flex;justify-content:space-between;gap:12px;align-items:center">'
                + '<strong>' + html.escape(title) + '</strong>' + status_html
                + '</div><p class="muted">' + html.escape(detail) + '</p><span class="card-link">Öffnen →</span></a>'
            )
        protection_cards = ''.join(card(name, detail, href, enabled) for name, enabled, detail, href in protections)
        permission_rows = ''.join(
            '<div class="setting-row"><span><strong>' + html.escape(name) + '</strong><small>'
            + ('Discord-Recht vorhanden' if ok else 'Fehlt – betroffene Aktionen können blockiert sein')
            + '</small></span><strong>' + ('✅' if ok else '⚠️') + '</strong></div>'
            for name, ok in permission_checks
        )
        modules = [
            ("🧪 WolfMirror", "Read-only Sicherheits-Checkliste für Szenarien und fehlende Rechte", "/wolfmirror"),
            ("📚 WolfChronicle", "Vorfallverwaltung und Statusverlauf", "/wolfchronicle"),
            ("🧪 Security Lab", "Live-Konfigurations- und Berechtigungsprüfung ohne destructive Aktionen", "/security-lab"),
            ("🛡️ WolfShield", "Anti-Raid-Schwellen und Reaktionsmodus", "/wolfshield"),
            ("🍯 Honeypot", "Lockkanal und konfigurierte Moderationsreaktion", "/honeypot"),
            ("⚙️ Schutz-Einstellungen", "Anti-Nuke, Anti-Raid, Linkfilter und Sicherheitsrollen", "?section=security#settings-panel"),
            ("📜 Dashboard-Audit", "Nachvollziehbare Dashboard-Änderungen", "/audit-log"),
            ("🗄️ Sicherheits-Backup", "Serverstruktur und Berechtigungen sichern", "/backup"),
        ]
        tool_cards = ''.join(card(name, detail, href) for name, detail, href in modules)
        body = (
            '<section class="card"><span class="pill">IRONWOLF · SECURITY OPERATIONS</span>'
            '<h1 style="margin-top:12px">🛡️ Security Center</h1>'
            '<p class="muted">Alle Schutzfunktionen an einem Ort. Prüfe zuerst die Live-Berechtigungen, kontrolliere danach die aktiven Schutzregeln und dokumentiere Vorfälle zentral.</p>'
            '<div class="grid">'
            '<div class="card"><small class="muted">Aktive Schutzregeln</small><h2>' + str(active) + ' / ' + str(len(protections)) + '</h2></div>'
            '<div class="card"><small class="muted">Bot-Rechte vorhanden</small><h2>' + str(ready_permissions) + ' / ' + str(len(permission_checks)) + '</h2></div>'
            '<div class="card"><small class="muted">Server</small><h2>' + html.escape(guild.name) + '</h2></div>'
            '</div></section>'
            '<section class="card"><h2>1 · Schutzstatus</h2><p class="muted">Aktueller Konfigurationsstand dieses Servers. Aktiv bedeutet nicht automatisch, dass jede Schutzaktion ausführbar ist – prüfe deshalb auch die Bot-Rechte.</p>'
            '<div class="grid">' + protection_cards + '</div></section>'
            '<section class="card"><h2>2 · Discord-Berechtigungen</h2><p class="muted">Diese Rechte werden live vom verbundenen Bot-Mitglied gelesen.</p>'
            + permission_rows + '<p><a href="/guild/' + str(guild.id) + '/wolfpulse">Zusätzlichen Server-Check mit WolfPulse öffnen →</a></p></section>'
            '<section class="card"><h2>3 · Security-Werkzeuge</h2><p class="muted">Prüfen, simulieren, Vorfälle dokumentieren und Konfigurationen verwalten.</p>'
            '<div class="grid">' + tool_cards + '</div></section>'
            '<p><a class="button secondary" href="/guild/' + str(guild.id) + '">← Zur Serverübersicht</a></p>'
        )
        return d._render(guild.name + " · Security Center", body)

    @d.app.after_request
    def add_security_center_shortcut(response):
        """Add a persistent security entry point to the main server dashboard."""
        try:
            if request.method == "GET" and request.path.startswith("/guild/") and request.path.count("/") == 2 and response.mimetype == "text/html":
                guild_id = request.path.rsplit("/", 1)[-1]
                if guild_id.isdigit():
                    html_body = response.get_data(as_text=True)
                    marker = '<div class="iw-metrics">'
                    shortcut = (
                        '<section class="card" style="margin:18px 0;border:1px solid var(--border)">'
                        '<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">'
                        '<div><span class="pill">SECURITY OPERATIONS</span><h2 style="margin:8px 0">🛡️ Security Center</h2>'
                        '<p class="muted" style="margin:0">Schutzstatus, Live-Rechte, WolfMirror und Vorfälle zentral verwalten.</p></div>'
                        '<a class="button" href="/guild/' + guild_id + '/security-center">Security Center öffnen →</a>'
                        '</div></section>'
                    )
                    if marker in html_body and "/security-center" not in html_body:
                        html_body = html_body.replace(marker, shortcut + marker, 1)
                        response.set_data(html_body)
        except Exception:
            # A navigation shortcut must never break the dashboard response.
            pass
        return response
