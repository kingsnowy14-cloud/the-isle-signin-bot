# =========================================================
# TICKET STEUERN: ÜBERNEHMEN, FREIGEBEN, SCHLIESSEN
# Nur die Support-Rolle darf Tickets schließen.
# =========================================================

class TicketControlView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.channel_id = channel_id

        claim_button = discord.ui.Button(
            label="Ticket übernehmen",
            style=discord.ButtonStyle.success,
            custom_id=f"ticket:claim:{channel_id}"
        )
        release_button = discord.ui.Button(
            label="Ticket freigeben",
            style=discord.ButtonStyle.secondary,
            custom_id=f"ticket:release:{channel_id}"
        )
        close_button = discord.ui.Button(
            label="Ticket schließen",
            style=discord.ButtonStyle.danger,
            custom_id=f"ticket:close:{channel_id}"
        )

        claim_button.callback = self.claim_ticket
        release_button.callback = self.release_ticket
        close_button.callback = self.close_ticket_button

        self.add_item(claim_button)
        self.add_item(release_button)
        self.add_item(close_button)

    async def claim_ticket(self, interaction: discord.Interaction):
        await self.handle_support_action(interaction, "claim")

    async def release_ticket(self, interaction: discord.Interaction):
        await self.handle_support_action(interaction, "release")

    async def handle_support_action(
        self,
        interaction: discord.Interaction,
        action: str
    ):
        guild = interaction.guild
        channel = interaction.channel

        if guild is None or channel is None:
            await reply(
                interaction,
                "Der Server oder Ticketkanal wurde nicht gefunden."
            )
            return

        config = get_config(guild.id)
        info = get_ticket_info(channel.id)

        if not info:
            await reply(
                interaction,
                "Für diesen Kanal wurden keine Ticketdaten gefunden."
            )
            return

        if not is_support(interaction.user, config):
            await reply(
                interaction,
                "Nur Mitglieder mit der Support-Rolle dürfen diese Aktion ausführen.",
                ephemeral=True
            )
            return

        if action == "claim":
            info["claimed_by"] = interaction.user.id
            save_ticket_info(channel.id, info)

            await reply(
                interaction,
                f"{interaction.user.mention} hat dieses Ticket übernommen.",
                ephemeral=False
            )

        elif action == "release":
            if info.get("claimed_by") not in (None, interaction.user.id):
                await reply(
                    interaction,
                    "Dieses Ticket wurde von einem anderen Support-Mitglied übernommen.",
                    ephemeral=True
                )
                return

            info["claimed_by"] = None
            save_ticket_info(channel.id, info)

            await reply(
                interaction,
                f"{interaction.user.mention} hat das Ticket freigegeben.",
                ephemeral=False
            )

    async def close_ticket_button(
        self,
        interaction: discord.Interaction
    ):
        guild = interaction.guild
        channel = interaction.channel

        if guild is None or channel is None:
            await reply(
                interaction,
                "Der Server oder Ticketkanal wurde nicht gefunden."
            )
            return

        config = get_config(guild.id)
        info = get_ticket_info(channel.id)

        if not info:
            await reply(
                interaction,
                "Für diesen Kanal wurden keine Ticketdaten gefunden."
            )
            return

        # WICHTIG: Ausschließlich die konfigurierte Support-Rolle darf schließen.
        if not is_support(interaction.user, config):
            await reply(
                interaction,
                "Du darfst dieses Ticket nicht schließen. "
                "Nur Mitglieder mit der Support-Rolle dürfen Tickets schließen.",
                ephemeral=True
            )
            return

        # Bestätigung an Discord senden, bevor das Ticket verarbeitet wird.
        await interaction.response.send_message(
            "Du hast das Schließen des Tickets angefordert. "
            "Das Transkript wird erstellt und im Ticket-Log gespeichert.",
            ephemeral=True
        )

        # Das Ticket wird protokolliert und danach geschlossen.
        await close_ticket(channel, guild, info, config)
