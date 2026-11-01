"""Unit tests for Emergency Hamburg monitor helpers."""
import unittest
from unittest.mock import Mock, patch

import emergency_hamburg


class EmergencyHamburgTests(unittest.TestCase):
    def test_free_plan_server_limit(self):
        with patch("emergency_hamburg.guild_plan", return_value="free"):
            self.assertEqual(emergency_hamburg.server_limit_for_guild({}), 1)

    def test_premium_plan_server_limit(self):
        with patch("emergency_hamburg.guild_plan", return_value="premium"):
            self.assertEqual(emergency_hamburg.server_limit_for_guild({}), 5)

    def test_premium_plus_server_limit(self):
        with patch("emergency_hamburg.guild_plan", return_value="premium_plus"):
            self.assertEqual(emergency_hamburg.server_limit_for_guild({}), 20)

    def test_public_server_pagination_and_target_stop(self):
        first = Mock()
        first.status_code = 200
        first.json.return_value = {
            "data": [{"id": "job-1", "playing": 12, "maxPlayers": 40, "ping": 50, "fps": 60}],
            "nextPageCursor": "next",
        }
        second = Mock()
        second.status_code = 200
        second.json.return_value = {
            "data": [{"id": "job-2", "playing": 8, "maxPlayers": 40}],
            "nextPageCursor": "later",
        }
        with patch.object(emergency_hamburg._HTTP, "get", side_effect=[first, second]) as get:
            rows = emergency_hamburg.fetch_public_servers(max_pages=5, target_ids=["job-1", "job-2"])
        self.assertEqual({row["id"] for row in rows}, {"job-1", "job-2"})
        self.assertEqual(get.call_count, 2)

    def test_embed_shows_selected_server_and_totals(self):
        guild = Mock()
        settings = {"servers": [{"job_id": "job-1", "name": "Police"}]}
        embed = emergency_hamburg.build_status_embed(
            guild, settings,
            {"job-1": {"playing": 10, "maxPlayers": 40, "ping": 45, "fps": 60}},
        )
        self.assertIn("Police", embed.fields[0].name)
        self.assertIn("10/40", embed.fields[0].value)
        self.assertIn("Spieler:** 10", embed.fields[-1].value)

    def test_online_offline_thresholds_use_hysteresis(self):
        guild = Mock()
        settings = {
            "servers": [{"job_id": "job-1", "name": "Testserver"}],
            "online_min_players": 5,
            "offline_max_players": 1,
        }
        online_embed = emergency_hamburg.build_status_embed(
            guild, settings, {"job-1": {"playing": 5, "maxPlayers": 40}}
        )
        self.assertIn("🟢 Online", online_embed.fields[0].value)
        middle_embed = emergency_hamburg.build_status_embed(
            guild, settings, {"job-1": {"playing": 3, "maxPlayers": 40}}
        )
        self.assertIn("🟢 Online", middle_embed.fields[0].value)
        offline_embed = emergency_hamburg.build_status_embed(
            guild, settings, {"job-1": {"playing": 1, "maxPlayers": 40}}
        )
        self.assertIn("🔴 Offline", offline_embed.fields[0].value)

    def test_join_link_and_optional_status_message_are_in_embed(self):
        guild = Mock()
        settings = {
            "servers": [{
                "job_id": "",
                "server_key": "link-abc",
                "name": "Privatserver",
                "join_link": "https://www.roblox.com/share?code=abc&type=Server",
            }],
            "status_message": "Bitte Regeln beachten!",
        }
        embed = emergency_hamburg.build_status_embed(guild, settings, {})
        self.assertIn("Bitte Regeln beachten!", embed.description)
        self.assertIn("Live-Spielerdaten nicht verfügbar", embed.fields[0].value)
        self.assertIn("Roblox-Server beitreten", embed.fields[0].value)

    def test_server_owner_is_displayed(self):
        guild = Mock()
        settings = {"servers": [{"job_id": "job-1", "name": "Testserver", "owner": "RobloxOwner"}]}
        embed = emergency_hamburg.build_status_embed(
            guild, settings, {"job-1": {"playing": 2, "maxPlayers": 40}}
        )
        self.assertIn("Besitzer: RobloxOwner", embed.fields[0].value)


if __name__ == "__main__":
    unittest.main()
