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
                abort(403, "Du brauchst Administrator- oder Server-verwalten-Rechte.")
            premium_voice = guild_has_feature(cfg, "voice_support")
            waiting_raw = request.form.getlist("waiting_channels")
            if not waiting_raw:
                legacy = request.form.get("waiting_channel", "0")
                waiting_raw = [legacy] if legacy != "0" else []
            max_waiting = 5 if premium_voice else 1
            if len(waiting_raw) > max_waiting:
                abort(400, f"Dein Tarif erlaubt maximal {max_waiting} Warteraum/Warteräume.")
            try:
                waiting_channels = [guild.get_channel(int(value)) for value in waiting_raw]
                notification = guild.get_channel(int(request.form.get("notification_channel", "0")))
                role = guild.get_role(int(request.form.get("support_role", "0")))
                category_raw = request.form.get("support_category", "0")
                category = guild.get_channel(int(category_raw)) if category_raw else None
                existing_raw = request.form.get("existing_support_channel", "0")
                existing_channel = guild.get_channel(int(existing_raw)) if existing_raw and existing_raw != "0" else None
            except (TypeError, ValueError):
                abort(400, "Bitte gültige Kanäle und eine Rolle auswählen.")
            if not waiting_channels or any(not isinstance(ch, discord.VoiceChannel) for ch in waiting_channels):
                abort(400, "Bitte mindestens einen gültigen Warteraum auswählen.")
            if len({ch.id for ch in waiting_channels}) != len(waiting_channels):
                abort(400, "Ein Warteraum darf nur einmal ausgewählt werden.")
            waiting = waiting_channels[0]
            if not isinstance(notification, discord.TextChannel):
                abort(400, "Der Benachrichtigungskanal muss ein Textkanal sein.")
            if role is None or role.is_default() or role.managed:
                abort(400, "Bitte eine gültige Support-Rolle auswählen.")
            if category is not None and not isinstance(category, discord.CategoryChannel):
                abort(400, "Bitte eine gültige Kategorie auswählen oder das Feld leeren.")
            if existing_channel is not None and (not isinstance(existing_channel, discord.VoiceChannel) or existing_channel.id in {ch.id for ch in waiting_channels}):
                abort(400, "Bitte einen anderen Sprachkanal als den Warteraum als vorhandenen Support-Kanal auswählen.")
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
            d._audit_dashboard_change(guild.id, "Voice-Support", session.get("user_id"), "Einstellungen gespeichert")
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
            '<option value="' + str(ch.id) + '"' + (" selected" if ch.id in configured_ids else "") + '>' + html.escape(ch.name) + '</option>'
            for ch in guild.voice_channels
        )
        waiting_limit_note = (
            '<div class="notice">⭐ Premium Voice Support: bis zu 5 Warteräume.</div>'
            if premium_voice else
            '<div class="notice">Free: 1 Warteraum. Mit Premium Voice Support kannst du bis zu 5 Warteräume konfigurieren.</div>'
        )
        notification_opts = options(guild.text_channels, cfg.get("voice_support_notification_channel"), "— Textkanal auswählen —")
        role_opts = options([r for r in guild.roles if not r.is_default() and not r.managed], cfg.get("voice_support_role"), "— Support-Rolle auswählen —")
        category_opts = options(guild.categories, cfg.get("voice_support_category"), "— Kategorie des Warteraums verwenden —")
        existing_channel_opts = options([ch for ch in guild.voice_channels if ch.id not in configured_ids], cfg.get("voice_support_existing_channel"), "— Automatisch neuen Kanal erstellen —")
        checked = " checked" if cfg.get("voice_support_enabled", False) else ""
        prefix = html.escape(str(cfg.get("voice_support_prefix") or "Support"), quote=True)
        saved = '<div class="notice">Einstellungen gespeichert.</div>' if request.args.get("saved") == "1" else ""
        body = (
            '<section class="card"><span class="pill">IRONWOLF VOICE SUPPORT</span><h1>🎧 Sprach-Support</h1>'
            '<p>Mitglieder treten dem Warteraum bei. IronWolf benachrichtigt das Team; ein Supporter übernimmt per Button und erhält einen privaten Sprachkanal.</p>'
            + saved + '<form method="post">' + d._csrf_field()
            + '<div class="check"><input type="checkbox" id="enabled" name="enabled"' + checked + '><label for="enabled">Sprach-Support aktivieren</label></div>'
            + '<label for="waiting_channels">Warteräume (max. ' + str(max_waiting) + ')</label><select id="waiting_channels" name="waiting_channels" multiple size="6" required>' + waiting_opts + '</select>'
            + waiting_limit_note
            + '<p>Mehrere Warteräume gleichzeitig auswählen: Strg (Windows) bzw. Cmd (Mac) gedrückt halten.</p>'
            + '<label for="notification_channel">Benachrichtigungskanal</label><select id="notification_channel" name="notification_channel" required>' + notification_opts + '</select>'
            + '<label for="support_role">Support-Team-Rolle</label><select id="support_role" name="support_role" required>' + role_opts + '</select>'
            + '<label for="support_category">Kategorie für neu erstellte Räume (optional)</label><select id="support_category" name="support_category">' + category_opts + '</select>'
            + '<label for="existing_support_channel">Vorhandenen Support-Sprachkanal verwenden (optional)</label><select id="existing_support_channel" name="existing_support_channel">' + existing_channel_opts + '</select>'
            + '<p>Wenn ein vorhandener Kanal gewählt ist, nutzt der Bot diesen nur, wenn er leer ist. Ohne Auswahl wird für jede Anfrage ein neuer Kanal erstellt.</p>'
            + '<label for="support_prefix">Kanalnamen-Prefix (nur neue Kanäle)</label><input id="support_prefix" name="support_prefix" maxlength="40" value="' + prefix + '" required>'
            + '<button type="submit">Einstellungen speichern</button></form>'
            + '<div class="notice">Benötigte Rechte: Nachrichten senden, Sprachkanäle erstellen/löschen und Mitglieder verschieben.</div>'
            + '<p><a class="button secondary" href="/guild/' + gid + '">← Zur Serververwaltung</a></p></section>'
        )
        return d._render(guild.name + " · Sprach-Support", body)
