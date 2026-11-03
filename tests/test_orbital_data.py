import copy
import unittest
from unittest import mock

from scripts import update_orbital_data as updater
from src.beam_sim import load_orbital_snapshot


class OrbitalCatalogTests(unittest.TestCase):
    def records(self):
        orbit = load_orbital_snapshot()["providers"]["starlink"]["sampled_orbits"][0]
        records = []
        for index in range(3):
            record = copy.deepcopy(orbit["omm"])
            record.update(OBJECT_NAME=f"STARLINK-TEST-{index}", NORAD_CAT_ID=100 + index)
            records.append(record)
        # A record outside every configured shell must survive full-catalog mode.
        records[-1]["INCLINATION"] = 12.345
        return records

    def test_full_catalog_keeps_unmatched_shells_and_all_unique_records(self):
        with mock.patch.object(updater, "fetch", return_value=self.records()) as fetch:
            result = updater.build_provider("starlink", updater.PROVIDERS["starlink"], 1, True)
        self.assertEqual(result["selection"], "all")
        self.assertEqual(result["records_available"], 3)
        self.assertEqual(result["records_matching_authorized_shells"], 2)
        self.assertEqual([orbit["norad_catalog_id"] for orbit in result["sampled_orbits"]], [100, 101, 102])
        self.assertEqual(result["sampled_orbits"][-1]["shell_id"], "unmatched")
        self.assertIn("gp.php?GROUP=starlink", result["source"])
        fetch.assert_called_once_with("starlink", True)

    def test_sample_mode_still_filters_and_limits_records(self):
        with mock.patch.object(updater, "fetch", return_value=self.records()):
            result = updater.build_provider("starlink", updater.PROVIDERS["starlink"], 1)
        self.assertEqual(len(result["sampled_orbits"]), 1)
        self.assertNotEqual(result["sampled_orbits"][0]["shell_id"], "unmatched")
        self.assertIn("supplemental/sup-gp.php", result["source"])

    def test_empty_or_duplicate_catalog_is_rejected(self):
        records = self.records()
        for raw in ([], records + [records[0]]):
            with self.subTest(count=len(raw)), mock.patch.object(updater, "fetch", return_value=raw):
                with self.assertRaises(ValueError):
                    updater.build_provider("starlink", updater.PROVIDERS["starlink"], 1, True)
