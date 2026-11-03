import contextlib
import copy
import io
import unittest
from unittest import mock

import h3

from src.beam_sim import (
    Config,
    Scenario,
    assert_plan_safe,
    distributed_deferred_acceptance,
    global_greedy,
    h3_service_cells,
    load_orbital_snapshot,
    resolve_satellite_count,
    run_seed,
)


class BeamSimulationTests(unittest.TestCase):
    def setUp(self):
        self.config = Config(epochs=8, satellites=10, ground_stations=8, replica_group_size=3)
        self.scenario = Scenario(self.config, 7)

    def test_all_plans_respect_per_satellite_capacity(self):
        edges = self.scenario.candidate_edges(4)
        for plan in (global_greedy(edges, self.config), distributed_deferred_acceptance(edges, self.config)):
            assert_plan_safe(plan, self.config)

    def test_each_cell_has_at_most_one_assignment(self):
        plan = distributed_deferred_acceptance(self.scenario.candidate_edges(6), self.config)
        self.assertEqual(len(plan.assignments), len(set(plan.assignments)))

    def test_seed_is_reproducible(self):
        self.assertEqual(run_seed(self.config, 13), run_seed(self.config, 13))

    def test_benchmark_has_required_metrics(self):
        result = run_seed(self.config, 17)
        for name in ("centralized", "centralized_periodic", "distributed"):
            self.assertIn("pair_accuracy", result["systems"][name])
            self.assertIn("latency_p95_ms", result["systems"][name])
            self.assertIn("backhaul_mb", result["systems"][name])

    def test_snapshot_contains_both_public_constellations(self):
        snapshot = load_orbital_snapshot()
        self.assertEqual(set(snapshot["providers"]), {"starlink", "kuiper"})
        for provider in snapshot["providers"].values():
            self.assertGreaterEqual(len(provider["sampled_orbits"]), 10)
            self.assertTrue(provider["source"].startswith("https://celestrak.org/"))
            self.assertIn("BSTAR", provider["sampled_orbits"][0]["omm"])

    def test_default_scenario_covers_one_hundred_minutes(self):
        config = Config()
        self.assertEqual(config.epochs * config.epoch_seconds, 100 * 60)

    def test_all_satellites_rejects_sampled_or_incomplete_catalog(self):
        snapshot = load_orbital_snapshot()
        with self.assertRaisesRegex(ValueError, "full catalog"):
            resolve_satellite_count("all", "starlink", snapshot)
        snapshot = copy.deepcopy(snapshot)
        snapshot["providers"]["starlink"]["selection"] = "all"
        with self.assertRaisesRegex(ValueError, "full catalog"):
            resolve_satellite_count("all", "starlink", snapshot)

    def test_all_satellites_resolves_provider_counts_and_preserves_ids_across_seeds(self):
        snapshot = copy.deepcopy(load_orbital_snapshot())
        for provider, count in (("starlink", 3), ("kuiper", 5)):
            catalog = snapshot["providers"][provider]
            catalog.update(selection="all", records_available=count)
            catalog["sampled_orbits"] = catalog["sampled_orbits"][:count]
            resolved = resolve_satellite_count("all", provider, snapshot)
            self.assertEqual(resolved, count)
            config = Config(provider=provider, satellites=resolved)
            expected = [record["norad_catalog_id"] for record in catalog["sampled_orbits"]]
            for seed in (1, 19):
                actual = Scenario(config, seed, snapshot)
                self.assertEqual([orbit.norad_catalog_id for orbit in actual.orbits], expected)
            catalog["sampled_orbits"][-1] = catalog["sampled_orbits"][0]
            with self.assertRaisesRegex(ValueError, "duplicate"):
                resolve_satellite_count("all", provider, snapshot)

    def test_cluster_cli_all_satellites_uses_each_providers_own_count(self):
        snapshot = copy.deepcopy(load_orbital_snapshot())
        for provider, count in (("starlink", 3), ("kuiper", 5)):
            catalog = snapshot["providers"][provider]
            catalog.update(selection="all", records_available=count)
            catalog["sampled_orbits"] = catalog["sampled_orbits"][:count]
        with mock.patch("src.beam_sim.load_orbital_snapshot", return_value=snapshot), \
             mock.patch("src.beam_sim.aggregate", return_value={"systems": {}, "comparisons": {}}) as aggregate, \
             mock.patch("src.beam_sim.Path.write_text"), \
             mock.patch("sys.argv", ["beam_sim.py", "--satellites", "all", "--epochs", "1200", "--demo-out", ""]), \
             contextlib.redirect_stdout(io.StringIO()):
            # Keep the test focused on the configuration passed to each provider.
            with mock.patch("src.beam_sim.benchmark_result", return_value={}):
                from src.beam_sim import main
                main()
        self.assertEqual([(call.args[0].provider, call.args[0].satellites, call.args[0].epochs)
                          for call in aggregate.call_args_list], [("starlink", 3, 1200), ("kuiper", 5, 1200)])

    def test_service_cells_are_unique_h3_resolution_five_cells(self):
        cells = h3_service_cells(5, 240)
        self.assertEqual(len(cells), 240)
        self.assertEqual(len({cell.h3_index for cell in cells}), 240)
        self.assertTrue(all(h3.get_resolution(cell.h3_index) == 5 for cell in cells))
        self.assertTrue(all(0 <= cell.x <= 1 and 0 <= cell.y <= 1 for cell in cells))
        self.assertLess(min(cell.y for cell in cells), 0.1)
        self.assertGreater(max(cell.y for cell in cells), 0.9)

    def test_real_orbits_produce_normalized_ground_tracks(self):
        for provider in ("starlink", "kuiper"):
            scenario = Scenario(Config(epochs=2, satellites=10, provider=provider), 7)
            points = scenario.satellites(1)
            self.assertTrue(all(0 <= point.x <= 1 and 0 <= point.y <= 1 for point in points))
            self.assertTrue(all(scenario.orbits[point.id].name.upper().startswith(("STARLINK", "KUIPER")) for point in points))

    def test_cluster_cli_can_select_one_provider_and_disable_demo_output(self):
        with mock.patch(
            "sys.argv",
            [
                "beam_sim.py",
                "--providers",
                "kuiper",
                "--epochs",
                "2",
                "--satellites",
                "10",
                "--service-cells",
                "12",
                "--seeds",
                "7",
                "--workers",
                "1",
                "--out",
                "/tmp/beam-sim-cli-test.json",
                "--demo-out",
                "",
            ],
        ), mock.patch("src.beam_sim.Path.write_text") as write_text, contextlib.redirect_stdout(io.StringIO()):
            from src.beam_sim import main

            main()

        self.assertEqual(write_text.call_count, 1)
        payload = write_text.call_args.args[0]
        self.assertIn('"kuiper"', payload)
        self.assertNotIn('"starlink"', payload)


if __name__ == "__main__":
    unittest.main()
