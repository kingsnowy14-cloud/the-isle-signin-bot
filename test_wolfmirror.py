"""Unit tests for the pure WolfMirror tabletop simulation engine."""
import unittest

from wolfmirror import SCENARIOS, simulate_scenario


class WolfMirrorTests(unittest.TestCase):
    def test_all_supported_scenarios_are_dry_run(self):
        for scenario in SCENARIOS:
            report = simulate_scenario({}, {}, scenario)
            self.assertTrue(report["dry_run"])
            self.assertIn(report["scenario"], SCENARIOS)
            self.assertGreaterEqual(report["score"], 0)
            self.assertLessEqual(report["score"], 100)

    def test_unknown_scenario_is_rejected(self):
        with self.assertRaises(ValueError):
            simulate_scenario({}, {}, "ban_everyone")

    def test_raid_reflects_configured_threshold_and_action(self):
        report = simulate_scenario(
            {"anti_raid_enabled": True, "raid_threshold": 12,
             "raid_window_seconds": 20, "raid_action": "alert"},
            {"moderate_members": False},
            "raid",
        )
        details = [item["detail"] for item in report["controls"]]
        self.assertIn("12 Beitritte in 20 Sekunden", details)
        self.assertIn("Nur Alarmierung", details)
        self.assertNotIn("Timeout", details)

    def test_missing_moderation_permission_is_reported(self):
        report = simulate_scenario(
            {"anti_raid_enabled": True, "raid_action": "timeout"},
            {"moderate_members": False},
            "raid",
        )
        self.assertTrue(any("Berechtigung fehlt" in item["detail"] for item in report["controls"]))

    def test_missing_required_permission_changes_verdict(self):
        report = simulate_scenario(
            {"anti_raid_enabled": True, "raid_action": "timeout"},
            {"moderate_members": False},
            "raid",
        )
        self.assertIn("fehlender Bot-Rechte", report["verdict"])
        self.assertIn("Mitglieder moderieren", report["missing_permissions"])

    def test_simulation_does_not_mutate_config(self):
        config = {"anti_raid_enabled": True, "raid_threshold": 8}
        original = dict(config)
        simulate_scenario(config, {"moderate_members": True}, "raid")
        self.assertEqual(config, original)

    def test_invalid_numbers_are_bounded(self):
        report = simulate_scenario(
            {"anti_raid_enabled": True, "raid_threshold": 9999,
             "raid_window_seconds": -100, "raid_action": "untrusted"},
            {"moderate_members": True},
            "raid",
        )
        details = [item["detail"] for item in report["controls"]]
        self.assertIn("30 Beitritte in 5 Sekunden", details)
        self.assertIn("Timeout", details)


if __name__ == "__main__":
    unittest.main()
