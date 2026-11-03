from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

try:
    import numpy as np
    from harness import coupled, grid
except ModuleNotFoundError as error:
    raise unittest.SkipTest('Install harness/requirements-option2.txt') from error
from src.beam_sim import (Config, Edge, Point, assert_plan_safe, global_greedy,
                          load_orbital_snapshot, region_for, regional_greedy,
                          regional_quotas, regional_control_cost)


class RegionalTests(unittest.TestCase):
    def assertPlansEqual(self, a, b):
        self.assertEqual({c: (e.sat, e.beam) for c, e in a.assignments.items()},
                         {c: (e.sat, e.beam) for c, e in b.assignments.items()})
        for cid, e in a.assignments.items():
            self.assertAlmostEqual(e.score, b.assignments[cid].score, places=11)

    def test_quotas_empty_regions_small_populations_and_deterministic_remainders(self):
        self.assertEqual(regional_quotas([[2, 0, 1], [8, 0, 0], [0, 0, 0]], 2),
                         [[0, 0, 1], [2, 0, 0], [0, 0, 0]])
        self.assertEqual(regional_quotas([[1], [1], [1]], 2), [[1], [1], [0]])
        for regions in (1, 2, 6, 12):
            counts = [[(r * 7 + s * 11) % 19 for s in range(8)] for r in range(regions)]
            quotas = regional_quotas(counts, 4)
            for sid in range(8):
                self.assertEqual(sum(q[sid] for q in quotas), min(4, sum(c[sid] for c in counts)))
                self.assertTrue(all(0 <= q[sid] <= c[sid] for q, c in zip(quotas, counts)))

    def test_regions_are_spherical_and_boundary_ties_are_worker_independent(self):
        controllers = [Point(10, .25, .5), Point(20, .75, .5)]
        points = [Point(i, x, y) for i, (x, y) in enumerate([(0., .5), (1., .5), (.25, .5), (.75, .5), (.9, 0.)])]
        owners = coupled.region_ids(np.array([(p.x, p.y) for p in points]), controllers)
        self.assertEqual(owners.tolist(), [region_for(p, controllers) for p in points])
        self.assertEqual(owners.tolist(), [0, 0, 0, 1, 0])

    def test_cross_region_satellite_budget_and_unused_quota_are_explicit(self):
        config = Config(satellites=2, beams_per_satellite=2, regional_controllers=2)
        owners = {0: 0, 1: 1, 2: 1}
        edges = [Edge(0, 0, 0, 1., 1.), Edge(0, 1, 0, .9, .9),
                 Edge(1, 1, 0, .8, .8), Edge(2, 1, 0, .7, .7)]
        reference = regional_greedy(edges, config, owners)
        self.assertEqual(set(reference.assignments), {0, 1})
        self.assertEqual(set(global_greedy(edges, config).assignments), {0, 1, 2})
        workers = []
        for ids in ([0, 2], [1]):
            shard = coupled.Shard.__new__(coupled.Shard)
            shard.c = config
            shard.ids = np.array(ids, dtype=np.uint32)
            shard.regions = np.array([owners[cid] for cid in ids], dtype=np.uint16)
            subset = sorted([e for e in edges if e.cell in ids], key=lambda e: e.sat)
            shard.cell = np.array([ids.index(e.cell) for e in subset], dtype=np.uint32)
            shard.sat = np.array([e.sat for e in subset], dtype=np.uint32)
            shard.beam = np.array([e.beam for e in subset], dtype=np.uint8)
            shard.sat_starts = np.searchsorted(shard.sat, np.arange(config.satellites + 1))
            score = np.array([e.score for e in subset])
            quality = np.array([e.quality for e in subset])
            shard.scores = lambda observation, noise, s=score, q=quality: (s, q)
            workers.append(coupled.Local(shard))
        actual, stats = coupled.regional(workers, config, 1, 0, np.array([[1, 1], [0, 2]]), page_size=1)
        self.assertPlansEqual(actual, reference)
        assert_plan_safe(actual, config)
        self.assertEqual(stats['unused_capacity_shares'], 1)
        self.assertEqual(stats['assigned_cells'], [1, 1])

    def test_scalar_parity_and_resume_across_worker_counts(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'grid.npz'
            grid.build(path, 1)
            cells = grid.load(path)
            snapshot = load_orbital_snapshot()
            config = Config(satellites=12, service_cells=842, h3_resolution=1, epochs=4)
            scenario = coupled.reference_scenario(config, 3, snapshot, cells)
            def workers(number, conf=config):
                return [coupled.Local(coupled.Shard(conf, 3, snapshot, cells, i, number)) for i in range(number)]
            full = coupled.initial_state()
            one = workers(1)
            for _ in range(4):
                row = coupled.step(one, scenario, full)
                target = row['target_epoch']
                expected = regional_greedy(scenario.candidate_edges(target, max(0, row['current_epoch'] - 1), .012),
                                           config, scenario.cell_regions)
                observed = coupled.all_call(one, {'action': 'prepare', 'target': target})
                counts = sum((p['regional_counts'] for p in observed), np.zeros((6, 12), dtype=np.int64))
                plan, stats = coupled.regional(one, config, max(0, row['current_epoch'] - 1), .012, counts, 2)
                self.assertPlansEqual(plan, expected)
                self.assertEqual(sum(row['regional']['cells']), 842)
                self.assertEqual(sum(stats['candidate_edges']), row['candidate_edges'])
            partial = coupled.initial_state()
            coupled.step(workers(3), scenario, partial)
            checkpoint = Path(temporary) / 'state'
            checkpoint.mkdir()
            coupled.save_checkpoint(checkpoint, partial, 'frozen')
            partial = coupled.resume(checkpoint, 'frozen')
            two = workers(2)
            for _ in range(3):
                coupled.step(two, scenario, partial)
            for a, b in zip(full['rows'], partial['rows']):
                self.assertEqual(a['regional'], b['regional'])
                for metric, value in a['systems']['regional_centralized'].items():
                    self.assertAlmostEqual(value, b['systems']['regional_centralized'][metric], places=10)
            single = replace(config, regional_controllers=1)
            reference = coupled.reference_scenario(single, 3, snapshot, cells)
            row = coupled.step(workers(2, single), reference, coupled.initial_state())
            for metric, value in row['systems']['centralized'].items():
                self.assertAlmostEqual(value, row['systems']['regional_centralized'][metric], places=10)
            one_region = regional_greedy(reference.candidate_edges(2, 0, .012), single, reference.cell_regions)
            self.assertPlansEqual(one_region, global_greedy(reference.candidate_edges(2, 0, .012), single))


if __name__ == '__main__':
    unittest.main()
