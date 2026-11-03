import copy
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest

try:
    import numpy as np
    from harness import coupled, grid
except ModuleNotFoundError as error:
    raise unittest.SkipTest('Install harness/requirements-option2.txt to test the coupled runner') from error
from src.beam_sim import Config, Edge, Plan, assert_plan_safe, distributed_deferred_acceptance, evaluate_plan, global_greedy, load_orbital_snapshot, regional_greedy


class CoupledTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.path = Path(cls.directory.name) / 'grid.npz'
        grid.build(cls.path, 1)
        cls.grid = grid.load(cls.path)
        cls.snapshot = load_orbital_snapshot()
        cls.config = Config(service_cells=len(cls.grid['ids']), h3_resolution=1, satellites=12, epochs=5)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def workers(self, number=3, config=None):
        config = config or self.config
        return [coupled.Local(coupled.Shard(config, 7, self.snapshot, self.grid, i, number)) for i in range(number)]

    def test_complete_grid_and_single_owner(self):
        workers = self.workers()
        ids = np.concatenate([worker.shard.ids for worker in workers])
        self.assertEqual(len(ids), len(np.unique(ids)))
        np.testing.assert_array_equal(np.sort(ids), self.grid['ids'])
        self.assertEqual(len(ids), 842)

    def test_vector_candidates_and_both_schedulers_match_scalar(self):
        workers = self.workers()
        reference = coupled.reference_scenario(self.config, 7, self.snapshot, self.grid)
        parts = coupled.all_call(workers, {'action': 'prepare', 'target': 4})
        counts = sum((part['sat_counts'] for part in parts), np.zeros(self.config.satellites, dtype=np.int64))
        for observation, noise in ((4, 0.), (1, .012), (2, .006)):
            expected_edges = reference.candidate_edges(4, observation, noise)
            self.assertEqual(sum(p['edges'] for p in parts), len(expected_edges))
            actual_edges = []
            for worker in workers:
                worker.shard.sort(observation, noise)
                actual_edges.extend(coupled.edge(row) for row in worker.shard.page(10000)['records'])
            expected_map = {(e.cell, e.sat, e.beam): e for e in expected_edges}
            self.assertEqual(set(expected_map), {(e.cell, e.sat, e.beam) for e in actual_edges})
            for e in actual_edges:
                self.assertAlmostEqual(e.score, expected_map[(e.cell, e.sat, e.beam)].score, places=11)
            plan = coupled.greedy(workers, self.config, observation, noise, counts, page_size=2)
            self.assertAssignmentsEqual(plan, global_greedy(expected_edges, self.config))
        plan = coupled.deferred(workers, self.config, 2)
        self.assertAssignmentsEqual(plan, distributed_deferred_acceptance(reference.candidate_edges(4, 2, .006), self.config))
        # Returning to a prior observation after proposals must recompute its
        # scores; cached regional sort scores must not alias distributed scores.
        plan = coupled.greedy(workers, self.config, 1, .012, counts, page_size=2)
        self.assertAssignmentsEqual(plan, global_greedy(reference.candidate_edges(4, 1, .012), self.config))

    def assertAssignmentsEqual(self, first, second, config=None):
        self.assertEqual({cid: (e.sat, e.beam) for cid, e in first.assignments.items()},
                         {cid: (e.sat, e.beam) for cid, e in second.assignments.items()})
        for cid, e in first.assignments.items():
            self.assertAlmostEqual(e.score, second.assignments[cid].score, places=11)
        assert_plan_safe(first, config or self.config)

    def test_global_capacity_with_contested_cross_shard_cells(self):
        config = replace(self.config, satellites=1, beams_per_satellite=1)
        class Fake:
            def __init__(self, cid, score):
                self.cid, self.score = cid, score
            def call(self, command):
                if command['action'] == 'sort':
                    self.sent = False
                    return {}
                rows = [] if self.sent else [(self.cid, 0, 0, self.score, .9)]
                self.sent = True
                return {'records': np.array(rows, dtype=coupled.EDGE_DTYPE)}
        plan = coupled.greedy([Fake(0, .7), Fake(1, .9)], config, 1, 0, np.array([2]), 1)
        self.assertEqual(list(plan.assignments), [1])

    def test_lazy_stream_refills_after_conflicts_and_orders_ties(self):
        shard = coupled.Shard.__new__(coupled.Shard)
        shard.c = replace(self.config, satellites=2, beams_per_satellite=40)
        shard.ids = np.arange(80, dtype=np.uint32)
        shard.cell = np.r_[np.arange(80), np.arange(40)].astype(np.uint32)
        shard.sat = np.r_[np.zeros(80), np.ones(40)].astype(np.uint32)
        shard.beam = np.zeros(120, dtype=np.uint8)
        shard.sat_starts = np.array([0, 80, 120])
        scores = np.r_[np.ones(80), np.full(40, 2.)]
        shard.scores = lambda observation, noise: (scores, np.ones(120))
        shard.sort(1, 0)
        actual = []
        while True:
            page = shard.page(7)['records']
            if not len(page):
                break
            actual.extend(coupled.edge(row) for row in page)
        expected = [Edge(int(shard.ids[c]), int(s), 0, float(score), 1.)
                    for c, s, score in zip(shard.cell, shard.sat, scores)]
        self.assertEqual(actual, sorted(expected, key=lambda e: (-e.score, e.cell, e.sat, e.beam)))
        plan = coupled.greedy([coupled.Local(shard)], shard.c, 1, 0, np.array([80, 40]), 2)
        self.assertAssignmentsEqual(plan, global_greedy(expected, shard.c), shard.c)
        self.assertEqual(sum(e.sat == 0 for e in plan.assignments.values()), 40)

    def test_implicit_rejections_preserve_later_proposal_round(self):
        shard = coupled.Shard.__new__(coupled.Shard)
        shard.c = replace(self.config, satellites=3, beams_per_satellite=1, service_cells=3)
        shard.ids = np.arange(3, dtype=np.uint32)
        shard.cell = np.array([0, 2, 1, 2, 2], dtype=np.uint32)
        shard.sat = np.array([0, 0, 1, 1, 2], dtype=np.uint32)
        shard.beam = np.zeros(5, dtype=np.uint8)
        scores = np.array([.9, .7, .9, .6, .5])
        shard.scores = lambda observation, noise: (scores, np.ones(5))
        result = coupled.deferred([coupled.Local(shard)], shard.c, 1)
        self.assertEqual(result.assignments[2].sat, 2)

    def test_metrics_match_reference_including_stale_plan(self):
        workers = self.workers()
        reference = coupled.reference_scenario(self.config, 7, self.snapshot, self.grid)
        state = coupled.initial_state()
        periodic = None
        for current in range(5):
            row = coupled.step(workers, reference, state)
            target = current + self.config.horizon_epochs
            truth = reference.candidate_edges(target)
            oracle = global_greedy(truth, self.config)
            fresh = global_greedy(reference.candidate_edges(target, max(0, current - 1), .012), self.config)
            if periodic is None or current % self.config.central_cadence_epochs == 0:
                periodic = fresh
            dist = distributed_deferred_acceptance(reference.candidate_edges(target, current, .006), self.config)
            regional = regional_greedy(reference.candidate_edges(target, max(0, current - 1), .012), self.config, reference.cell_regions)
            for name, plan in zip(coupled.NAMES, (fresh, periodic, regional, dist, oracle)):
                expected = evaluate_plan(reference, plan, oracle, target)
                for metric, value in expected.items():
                    self.assertAlmostEqual(row['systems'][name][metric], value, places=10)
            self.assertEqual(row['candidate_edges'], len(truth))

    def test_resume_preserves_periodic_state_across_chunk_boundary(self):
        reference = coupled.reference_scenario(self.config, 7, self.snapshot, self.grid)
        full = coupled.initial_state()
        workers = self.workers(1)
        for _ in range(5):
            coupled.step(workers, reference, full)
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            partial = coupled.initial_state()
            workers = self.workers(3)
            for _ in range(2):
                coupled.step(workers, reference, partial)
            coupled.save_checkpoint(directory, partial, 'frozen')
            partial = coupled.resume(directory, 'frozen')
            workers = self.workers(2)
            for _ in range(3):
                coupled.step(workers, reference, partial)
            coupled.save_checkpoint(directory, partial, 'frozen')
            resumed = coupled.resume(directory, 'frozen')
            self.assertEqual(resumed['next_epoch'], 5)
            for name, metrics in coupled.summarize(full, self.config).items():
                for key, value in metrics.items():
                    self.assertAlmostEqual(value, coupled.summarize(partial, self.config)[name][key], places=10)
            with self.assertRaisesRegex(ValueError, 'inputs differ'):
                coupled.resume(directory, 'different')
            checkpoint = directory / 'chunk-000005.json'
            entry = json.loads(checkpoint.read_text())
            entry['state']['next_epoch'] = 6
            checkpoint.write_text(json.dumps(entry))
            with self.assertRaisesRegex(ValueError, 'checksum'):
                coupled.resume(directory, 'frozen')

    def test_binary_protocol_roundtrip_no_pickle(self):
        stream = io.BytesIO()
        message = {'action': 'page', 'records': np.array([(1, 2, 0, .9, .8)], dtype=coupled.EDGE_DTYPE)}
        coupled.send(stream, message)
        stream.seek(0)
        actual = coupled.receive(stream)
        self.assertEqual(actual['action'], 'page')
        np.testing.assert_array_equal(actual['records'], message['records'])
        with self.assertRaises(EOFError):
            coupled.receive(io.BytesIO(b'abc'))

    def test_mask_grid_preserves_master_ids_and_requires_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            mask = {'type': 'FeatureCollection', 'features': [{'type': 'Feature',
                    'properties': {'products': ['land', 'aviation'], 'status': 'available'},
                    'geometry': {'type': 'Polygon', 'coordinates': [[[-20, -20], [20, -20], [20, 20], [-20, 20], [-20, -20]]]}}]}
            path = directory / 'mask.json'
            path.write_text(json.dumps(mask))
            with self.assertRaisesRegex(ValueError, 'complete land'):
                grid.build(directory / 'masked.npz', 1, path)
            mask['coverage_provenance'] = {'complete_products': list(grid.PRODUCTS), 'observed_at': 'fixture',
                'sources': ['fixture'], 'review_notes': 'test fixture only', 'verified': False}
            path.write_text(json.dumps(mask))
            result = grid.build(directory / 'masked.npz', 1, path)
            masked = grid.load(directory / 'masked.npz')
            self.assertFalse(result['production_ready'])
            np.testing.assert_array_equal(self.grid['indexes'][masked['ids']], masked['indexes'])
            np.testing.assert_array_equal(coupled.phases(masked['ids'], 7), coupled.phases(self.grid['ids'], 7)[masked['ids']])


if __name__ == '__main__':
    unittest.main()
