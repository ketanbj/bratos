import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unittest

try:
    from harness import grid
    from harness.tls_transport import certificates
except ModuleNotFoundError as error:
    raise unittest.SkipTest('Install harness/requirements-option2.txt') from error
from src.beam_sim import load_orbital_snapshot

ROOT = Path(__file__).resolve().parents[1]


class TLSTransportTests(unittest.TestCase):
    def test_authenticated_transport_resume_and_untrusted_client(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            credentials = directory / 'tls'
            certificates(credentials, 1)
            state = directory / 'server.json'
            server = subprocess.Popen([
                sys.executable, str(ROOT / 'harness/tls_transport.py'), 'serve',
                '--ca', str(credentials / 'ca.crt'), '--certificate', str(credentials / 'worker-0.crt'),
                '--key', str(credentials / 'worker-0.key'), '--bind', '127.0.0.1', '--state', str(state)],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 20
                while not state.exists():
                    if server.poll() is not None or time.monotonic() > deadline:
                        self.fail('TLS service failed to become ready')
                    time.sleep(.05)
                port = json.loads(state.read_text())['port']
                # A trusted server certificate is insufficient: the caller
                # must also present the task-specific client certificate.
                context = ssl.create_default_context(cafile=str(credentials / 'ca.crt'))
                with self.assertRaises((ssl.SSLError, OSError)):
                    with socket.create_connection(('127.0.0.1', port), timeout=5) as plain:
                        with context.wrap_socket(plain, server_hostname='worker-0') as connection:
                            connection.sendall(b'no client certificate')
                            connection.recv(1)
                path = directory / 'grid.npz'
                grid.build(path, 1, global_study=True)
                metadata = grid.load(path)['metadata']
                self.assertEqual(metadata['domain'], 'global-study')
                self.assertEqual(metadata['cells'], metadata['global_cells'])
                self.assertEqual(metadata['product_cells'], {'land': 0, 'maritime': 0, 'aviation': 0})
                snapshot = load_orbital_snapshot()
                for provider in snapshot['providers'].values():
                    provider['sampled_orbits'] = provider['sampled_orbits'][:12]
                    provider['records_available'] = 12
                    provider['selection'] = 'all'
                catalog = directory / 'catalog.json'
                catalog.write_text(json.dumps(snapshot))
                settings = {'ca': str(credentials / 'ca.crt'),
                            'certificate': str(credentials / 'controller.crt'),
                            'key': str(credentials / 'controller.key'),
                            'endpoints': [{'host': '127.0.0.1', 'port': port, 'name': 'worker-0'}] * 2}
                configuration = directory / 'tls.json'
                configuration.write_text(json.dumps(settings))
                base = [sys.executable, str(ROOT / 'harness/coupled.py'), '--grid', str(path),
                        '--snapshot', str(catalog), '--epochs', '4', '--calibration']
                tls = base + ['--tls-config', str(configuration), '--directory', str(directory / 'remote')]
                for command in (tls + ['--stop-after', '2'], tls + ['--resume'],
                                base + ['--local-workers', '2', '--directory', str(directory / 'local')]):
                    result = subprocess.run(command, text=True, capture_output=True, timeout=60,
                                            env={**os.environ, 'OPENBLAS_NUM_THREADS': '1'})
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                remote = json.loads((directory / 'remote/results.json').read_text())
                local = json.loads((directory / 'local/results.json').read_text())
                self.assertEqual(remote['completed_epochs'], 4)
                self.assertEqual(remote['systems'], local['systems'])
                self.assertIsNone(server.poll(), 'A rejected client must not stop the service')
            finally:
                server.terminate()
                server.wait(timeout=15)
                server.stderr.close()


if __name__ == '__main__':
    unittest.main()
