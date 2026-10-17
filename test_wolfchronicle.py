import unittest

from wolfchronicle import (
    INCIDENT_TYPES,
    MAX_INCIDENTS,
    create_incident,
    list_incidents,
    update_incident,
)


class WolfChronicleTests(unittest.TestCase):
    def test_create_and_update_shared_record(self):
        config = {}
        item = create_incident(
            config,
            incident_type="raid",
            severity="high",
            title="  Raid   detected ",
            summary=" Several new accounts joined. ",
            reporter_id=123,
            source="discord",
        )
        self.assertEqual(item["title"], "Raid detected")
        self.assertEqual(item["status"], "open")
        self.assertEqual(item["source"], "discord")
        self.assertEqual(list_incidents(config)[0]["id"], item["id"])
        updated = update_incident(config, item["id"], status="resolved", resolution_note="Reviewed by staff")
        self.assertEqual(updated["status"], "resolved")
        self.assertEqual(updated["resolution_note"], "Reviewed by staff")

    def test_rejects_invalid_type_severity_and_status(self):
        with self.assertRaises(ValueError):
            create_incident({}, incident_type="bad", severity="high", title="X", summary="Y", reporter_id=1, source="discord")
        with self.assertRaises(ValueError):
            create_incident({}, incident_type="raid", severity="urgent", title="X", summary="Y", reporter_id=1, source="discord")
        with self.assertRaises(ValueError):
            update_incident({}, "A", status="deleted")

    def test_rejects_empty_title_or_summary(self):
        args = dict(incident_type="other", severity="low", title=" ", summary="ok", reporter_id=1, source="discord")
        with self.assertRaises(ValueError):
            create_incident({}, **args)
        args.update(title="Valid", summary=" ")
        with self.assertRaises(ValueError):
            create_incident({}, **args)

    def test_limits_fields_and_retained_records(self):
        config = {"wolfchronicle_incidents": []}
        first = create_incident(
            config, incident_type="other", severity="low",
            title="T" * 150, summary="S" * 600, reporter_id="x" * 80, source="unknown",
        )
        self.assertEqual(len(first["title"]), 120)
        self.assertEqual(len(first["summary"]), 500)
        self.assertEqual(len(first["reporter_id"]), 32)
        self.assertEqual(first["source"], "discord")
        for _ in range(MAX_INCIDENTS + 5):
            create_incident(config, incident_type="raid", severity="medium", title="Raid", summary="Test", reporter_id=1, source="discord")
        self.assertEqual(len(config["wolfchronicle_incidents"]), MAX_INCIDENTS)

    def test_unknown_update_and_malformed_storage_are_safe(self):
        self.assertIsNone(update_incident({}, "missing", status="resolved"))
        self.assertEqual(list_incidents({"wolfchronicle_incidents": "invalid"}), [])
        self.assertEqual(list_incidents({"wolfchronicle_incidents": [{"id": "x"}]}), [])

    def test_supported_types_are_documented(self):
        self.assertIn("raid", INCIDENT_TYPES)
        self.assertIn("other", INCIDENT_TYPES)


if __name__ == "__main__":
    unittest.main()
