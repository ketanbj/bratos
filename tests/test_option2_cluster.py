import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

try:
    from harness import grid
except ModuleNotFoundError as error:
    raise unittest.SkipTest('Install harness/requirements-option2.txt to test the coupled runner') from error

from src.beam_sim import load_orbital_snapshot

ROOT = Path(__file__).resolve().parents[1]


class Option2ClusterTests(unittest.TestCase):
    def test_srun_transport_and_resumed_trajectory(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / 'grid.npz'
            grid.build(path, 1)
            snapshot = load_orbital_snapshot()
            for provider in snapshot['providers'].values():
                provider['sampled_orbits'] = provider['sampled_orbits'][:12]
                provider['records_available'] = 12
                provider['selection'] = 'all'
            catalog = directory / 'snapshot.json'
            catalog.write_text(json.dumps(snapshot))
            srun = directory / 'srun'
            srun.write_text(f'''#!{sys.executable}
import json, os, sys
with open(os.environ['SRUN_TEST_LOG'], 'a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\\n')
args = sys.argv[1:]
while args and args[0].startswith('--'):
    args.pop(0)
os.execv(args[0], args)
''')
            srun.chmod(0o755)
            log = directory / 'srun.jsonl'
            environment = {**os.environ, 'PATH': str(directory) + os.pathsep + os.environ['PATH'],
                           'SLURM_JOB_ID': 'test-only', 'SRUN_TEST_LOG': str(log)}
            base = [sys.executable, str(ROOT / 'harness/coupled.py'), '--slurm', '--hosts', 'node0', 'node1',
                    '--grid', str(path), '--snapshot', str(catalog), '--epochs', '4', '--calibration']
            partial = base + ['--directory', str(directory / 'partial'), '--stop-after', '2']
            for command in (partial, partial + ['--resume'], base + ['--directory', str(directory / 'full')]):
                result = subprocess.run(command, env=environment, text=True, capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
            full = json.loads((directory / 'full/results.json').read_text())
            resumed = json.loads((directory / 'partial/results.json').read_text())
            self.assertEqual(full['systems'], resumed['systems'])
            self.assertEqual(resumed['completed_epochs'], 4)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(len(commands), 6)
            for command in commands:
                self.assertIn('--exact', command)
                self.assertIn('--exclusive', command)
                self.assertIn('--cpus-per-task=1', command)
                self.assertIn('--worker', command)

    def test_production_rejects_global_calibration_grid_before_contacting_hosts(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'grid.npz'
            grid.build(path, 1)
            result = subprocess.run([sys.executable, str(ROOT / 'harness/coupled.py'), '--grid', str(path),
                                     '--hosts', 'not-a-host'], text=True, capture_output=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('verified commercial-union', result.stderr)
            self.assertNotIn('Could not resolve', result.stderr)


if __name__ == '__main__':
    unittest.main()
