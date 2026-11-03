import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cloudlab.monitor import check_run, digest, verify_final


class Option2MonitorTests(unittest.TestCase):
    def fixture(self, local):
        directory = local / 'simulation'
        directory.mkdir()
        expected = {'fingerprint': 'frozen-test-inputs', 'satellites': 12, 'directory': str(local)}
        config = {'epochs': 2, 'satellites': 12, 'service_cells': 2016842}
        (directory / 'run.json').write_text(json.dumps({'state': 'completed', 'calibration': False,
            'fingerprint': expected['fingerprint'], 'inputs': {'config': config}}))
        (directory / 'results.json').write_text(json.dumps({'config': config, 'completed_epochs': 2,
            'next_epoch': 2, 'start_epoch': 0, 'calibration': False}))
        previous = None
        for epoch in (1, 2):
            body = {'fingerprint': expected['fingerprint'], 'state': {'next_epoch': epoch,
                'rows': [{'current_epoch': i} for i in range(epoch)], 'predecessor': previous}}
            checksum = hashlib.sha256(json.dumps(body, sort_keys=True, allow_nan=False).encode()).hexdigest()
            name = f'chunk-{epoch:06d}.json'
            (directory / name).write_text(json.dumps({**body, 'checksum': checksum}))
            previous = checksum
        (directory / 'latest.json').write_text(json.dumps({'file': name, 'checksum': checksum}))
        (local / 'deployment.json').write_text(json.dumps({'head': 'test-node', 'remote_root': '/test/run'}))
        return expected, {p.name: digest(p) for p in directory.iterdir()}

    def test_finished_output_is_verified_once_and_marker_avoids_expired_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary)
            expected, hashes = self.fixture(local)
            run = json.loads((local / 'simulation/run.json').read_text())
            observed = {'controller': {'state': 'completed'}, 'run': run, 'latest': None}
            with patch('cloudlab.monitor.remote_status', return_value=observed) as remote, \
                    patch('cloudlab.monitor.collect') as collect, \
                    patch('cloudlab.monitor.remote_hashes', return_value=hashes):
                self.assertEqual(check_run(expected)['state'], 'collected')
                self.assertTrue((local / 'collected.json').exists())
                self.assertEqual(check_run(expected)['state'], 'collected')
                remote.assert_called_once()
                collect.assert_called_once()

    def test_transfer_tampering_or_missing_predecessor_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary)
            expected, hashes = self.fixture(local)
            self.assertTrue(verify_final(local, expected, hashes)['checkpoint_chain_verified'])
            path = local / 'simulation/chunk-000001.json'
            original = path.read_text()
            path.write_text(original + '\n')
            with self.assertRaisesRegex(ValueError, 'transfer checksum'):
                verify_final(local, expected, hashes)
            path.unlink()
            hashes.pop(path.name)
            with self.assertRaisesRegex(ValueError, 'checkpoint chain'):
                verify_final(local, expected, hashes)

    def test_exit_zero_with_partial_orbit_is_incomplete_and_failure_is_backed_up(self):
        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary)
            expected, _hashes = self.fixture(local)
            run = json.loads((local / 'simulation/run.json').read_text())
            run['state'] = 'checkpointed'
            observed = {'controller': {'state': 'completed'}, 'run': run, 'latest': None}
            with patch('cloudlab.monitor.remote_status', return_value=observed), \
                    patch('cloudlab.monitor.collect') as collect:
                self.assertEqual(check_run(expected)['state'], 'incomplete')
                self.assertFalse((local / 'collected.json').exists())
                observed['controller']['state'] = 'failed'
                self.assertEqual(check_run(expected)['state'], 'failed')
                self.assertEqual(collect.call_count, 2)


if __name__ == '__main__':
    unittest.main()
