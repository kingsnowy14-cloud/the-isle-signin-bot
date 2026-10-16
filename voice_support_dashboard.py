"""Dashboard routes for IronWolf voice support."""
import html
import discord
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
            try:
                waiting = guild.get_channel(int(request.form.get("waiting_channel", "0")))
                notification = guild.get_channel(int(request.form.get("notification_channel", "0")))
                role = guild.get_role(int(request.form.get("support_role", "0")))
                category_raw = request.form.get("support_category", "0")
                category = guild.get_channel(int(category_raw)) if category_raw else None
            except (TypeError, ValueError):
                abort(400, "Bitte gültige Kanäle und eine Rolle auswählen.")
            if not isinstance(waiting, discord.VoiceChannel):
                abort(400, "Der Warteraum muss ein Sprachkanal sein.")
            if not isinstance(notification, discord.TextChannel):
                abort(400, "Der Benachrichtigungskanal muss ein Textkanal sein.")
            if role is None or role.is_default() or role.managed:
                abort(400, "Bitte eine gültige Support-Rolle auswählen.")
            if category is not None and not isinstance(category, discord.CategoryChannel):
                abort(400, "Bitte eine gültige Kategorie auswählen oder das Feld leeren.")
            cfg.update({
                "voice_support_enabled": request.form.get("enabled") == "on",
                "voice_support_waiting_channel": waiting.id,
                "voice_support_notification_channel": notification.id,
                "voice_support_role": role.id,
                "voice_support_category": category.id if category else None,
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

        waiting_opts = options(guild.voice_channels, cfg.get("voice_support_waiting_channel"), "— Warteraum auswählen —")
        notification_opts = options(guild.text_channels, cfg.get("voice_support_notification_channel"), "— Textkanal auswählen —")
        role_opts = options([r for r in guild.roles if not r.is_default() and not r.managed], cfg.get("voice_support_role"), "— Support-Rolle auswählen —")
        category_opts = options(guild.categories, cfg.get("voice_support_category"), "— Kategorie des Warteraums verwenden —")
        checked = " checked" if cfg.get("voice_support_enabled", False) else ""
        prefix = html.escape(str(cfg.get("voice_support_prefix") or "Support"), quote=True)
        saved = '<div class="notice">Einstellungen gespeichert.</div>' if request.args.get("saved") == "1" else ""
        body = (
            '<section class="card"><span class="pill">IRONWOLF VOICE SUPPORT</span><h1>🎧 Sprach-Support</h1>'
            '<p>Mitglieder treten dem Warteraum bei. IronWolf benachrichtigt das Team; ein Supporter übernimmt per Button und erhält einen privaten Sprachkanal.</p>'
            + saved + '<form method="post">' + d._csrf_field()
            + '<div class="check"><input type="checkbox" id="enabled" name="enabled"' + checked + '><label for="enabled">Sprach-Support aktivieren</label></div>'
            + '<label for="waiting_channel">Warteraum</label><select id="waiting_channel" name="waiting_channel" required>' + waiting_opts + '</select>'
            + '<label for="notification_channel">Benachrichtigungskanal</label><select id="notification_channel" name="notification_channel" required>' + notification_opts + '</select>'
            + '<label for="support_role">Support-Team-Rolle</label><select id="support_role" name="support_role" required>' + role_opts + '</select>'
            + '<label for="support_category">Kategorie für private Räume (optional)</label><select id="support_category" name="support_category">' + category_opts + '</select>'
            + '<label for="support_prefix">Kanalnamen-Prefix</label><input id="support_prefix" name="support_prefix" maxlength="40" value="' + prefix + '" required>'
            + '<button type="submit">Einstellungen speichern</button></form>'
            + '<div class="notice">Benötigte Rechte: Nachrichten senden, Sprachkanäle erstellen/löschen und Mitglieder verschieben.</div>'
            + '<p><a class="button secondary" href="/guild/' + gid + '">← Zur Serververwaltung</a></p></section>'
        )
        return d._render(guild.name + " · Sprach-Support", body)
