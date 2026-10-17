import unittest

from isle_registration import get_registration, remove_registration, set_registration


class IsleRegistrationTests(unittest.TestCase):
    def test_set_get_and_remove_are_guild_scoped(self):
        data = {}
        record = {"dinosaur": "T. rex", "skin": "default", "location": "North", "signed_in_at": "now"}
        set_registration(data, 10, 20, record)
        self.assertEqual(get_registration(data, 10, 20), record)
        self.assertIsNone(get_registration(data, 10, 21))
        self.assertEqual(remove_registration(data, 10, 20), record)
        self.assertIsNone(get_registration(data, 10, 20))

    def test_corrupt_containers_are_repaired_on_write(self):
        data = {"registrations": ["broken"]}
        set_registration(data, 10, 20, {"dinosaur": "Rex", "location": "North", "skin": ""})
        self.assertEqual(get_registration(data, 10, 20)["dinosaur"], "Rex")

    def test_corrupt_or_incomplete_records_are_ignored(self):
        data = {"registrations": {"20": {"10": [], "11": {"dinosaur": "", "location": "North"}}}}
        self.assertIsNone(get_registration(data, 10, 20))
        self.assertIsNone(get_registration(data, 11, 20))
        self.assertIsNone(get_registration({}, 10, 20))

    def test_remove_handles_missing_and_corrupt_data(self):
        self.assertIsNone(remove_registration({}, 10, 20))
        self.assertIsNone(remove_registration({"registrations": None}, 10, 20))


if __name__ == "__main__":
    unittest.main()
