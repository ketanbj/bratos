import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / '.venv/bin/python'
WORKFLOW = ROOT / 'cloudlab/workflow.sh'


class CloudLabLauncherTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.temp = Path(self.directory.name)
        self.remote = self.temp / 'remote'
        self.state = self.temp / 'state'
        snapshot = json.loads((ROOT / 'data/orbital_snapshot.json').read_text())
        for provider, count in (('starlink', 10), ('kuiper', 12)):
            catalog = snapshot['providers'][provider]
            catalog.update(selection='all', records_available=count)
            catalog['sampled_orbits'] = catalog['sampled_orbits'][:count]
        self.snapshot = self.temp / 'full.json'
        self.snapshot.write_text(json.dumps(snapshot))
        self.ssh = self.temp / 'ssh'
        self.ssh.write_text(f'''#!{PYTHON}
import os,sys,subprocess
from pathlib import Path
args=sys.argv[1:]
while args and args[0]=='-o': args=args[2:]
host,command=args
if command=='bash -s': command=sys.stdin.read()
base=Path(os.environ['FAKE_CLOUD_ROOT'])/host/'bratos'
command=command.replace('/test/bratos',str(base))
if os.environ.get('FAKE_CLOUD_VERSION_HOST')==host:
    command=command.replace('sys.version.split()[0]', '"9.9.9"')
if '/node.py start ' in command:
    script=Path(command.split()[1]).parents[1]/'harness/distributed.py'
    if os.environ.get('FAKE_CLOUD_FAIL_HOST')==host: script.write_text('import sys; sys.exit(42)')
    if os.environ.get('FAKE_CLOUD_SLEEP')=='1': script.write_text('import time; time.sleep(20)')
sys.exit(subprocess.run(['bash','-c',command]).returncode)
''')
        self.ssh.chmod(0o755)
        self.rsync = self.temp / 'rsync'
        self.rsync.write_text(f'''#!{PYTHON}
import os,sys,shutil
from pathlib import Path
source,destination=sys.argv[-2:]
def location(value):
    if ':' in value:
        host,path=value.split(':',1)
        return Path(path.replace('/test/bratos',str(Path(os.environ['FAKE_CLOUD_ROOT'])/host/'bratos')))
    return Path(value)
src,dst=location(source),location(destination)
if src.is_dir():
    dst.mkdir(parents=True,exist_ok=True)
    filtered=any(arg.startswith('--include=') for arg in sys.argv)
    for path in src.iterdir():
        if filtered and path.name not in ('shards','status.json','worker.log'): continue
        if path.is_dir(): shutil.copytree(path,dst/path.name,dirs_exist_ok=True)
        else: shutil.copy2(path,dst/path.name)
else:
    if destination.endswith('/'): dst=dst/src.name
    dst.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(src,dst)
''')
        self.rsync.chmod(0o755)
        for host in ('node0', 'node1'):
            base = self.remote / host / 'bratos'
            (base / '.venv/bin').mkdir(parents=True)
            (base / '.bratos-cloudlab-root').touch()
            python = base / '.venv/bin/python'
            python.write_text(f'#!/bin/sh\nexec "{PYTHON}" "$@"\n')
            python.chmod(0o755)
        self.config = self.temp / 'cloudlab.env'
        self.config.write_text(
            f'CLOUDLAB_HOSTS="node0 node1"\nCLOUDLAB_REMOTE_DIR=/test/bratos\n'
            f'CLOUDLAB_SSH={self.ssh}\nCLOUDLAB_RSYNC={self.rsync}\n'
            f'CLOUDLAB_STATE_DIR={self.state}\nCLOUDLAB_CPUS_PER_NODE=1\n'
            'CLOUDLAB_TIME_LIMIT_SECONDS=60\nCLOUDLAB_POLL_SECONDS=1\n'
            f'ORBITAL_SNAPSHOT={self.snapshot}\nSATELLITES=all\n'
            'PROVIDERS=starlink,kuiper\nSEEDS=7,19\nSERVICE_CELLS=240\n'
            'EPOCHS=2\nEPOCH_SECONDS=5\nH3_RESOLUTION=5\nSIM_WORKERS=0\n'
        )
        self.environment = {**os.environ, 'ENV_FILE': str(self.config), 'FAKE_CLOUD_ROOT': str(self.remote)}
        self.addCleanup(self.stop_workers)

    def stop_workers(self):
        for path in self.remote.glob('*/bratos/runs/*/status.json'):
            info = json.loads(path.read_text())
            if info['state'] == 'running':
                try:
                    os.kill(info['pid'], signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def command(self, action, overrides=None, check=True):
        result = subprocess.run(['bash', str(WORKFLOW), action],
                                env={**self.environment, **(overrides or {})}, text=True, capture_output=True, timeout=60)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def latest(self):
        return (self.state / 'latest').read_text().strip()

    def test_dry_run_overrides_config_and_never_contacts_nodes(self):
        result = self.command('dry-run', {'CLOUDLAB_SSH': '/not/ssh', 'CLOUDLAB_RSYNC': '/not/rsync', 'EPOCHS': '1200'})
        self.assertIn('"EPOCHS": "1200"', result.stdout)
        self.assertIn('"starlink": 10', result.stdout)
        self.assertIn('"kuiper": 12', result.stdout)
        self.assertIn('Preview only', result.stdout)
        self.assertFalse(self.state.exists())
        rejected = self.command('dry-run', {'CLOUDLAB_HOSTS': 'node0 node1 node2 node3 node4'}, check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('exceed 4', rejected.stderr)

    def test_two_nodes_match_serial_and_resume_missing_checkpoint(self):
        self.command('run')
        old_id = self.latest()
        self.command('wait')
        old = self.state / old_id
        actual = json.loads((old / 'results.json').read_text())
        self.assertEqual({provider: value['config']['satellites'] for provider, value in actual['profiles'].items()},
                         {'starlink': 10, 'kuiper': 12})
        serial = self.temp / 'serial.json'
        subprocess.run([str(PYTHON), str(ROOT / 'src/beam_sim.py'), '--satellites', 'all', '--epochs', '2',
                        '--service-cells', '240', '--seeds', '7,19', '--workers', '1', '--out', str(serial), '--demo-out', ''],
                       env={**os.environ, 'ORBITAL_SNAPSHOT': str(self.snapshot)}, check=True, capture_output=True)
        self.assertEqual(actual, json.loads(serial.read_text()))
        # Resume from a locally cached partial set, after losing the old nodes.
        (old / 'shards/kuiper-seed-1.json').unlink()
        self.command('run', {'CLOUDLAB_RESUME_RUN_ID': old_id})
        resumed = self.state / self.latest()
        self.command('wait')
        self.assertEqual(json.loads((resumed / 'results.json').read_text()), actual)
        logs = ''.join(path.read_text() for path in resumed.glob('node-*/worker.log'))
        self.assertIn('Resumed starlink', logs)
        self.assertIn('Checkpointed kuiper seed 19', logs)
        self.assertNotIn('Checkpointed starlink', logs)
        rejected = self.command('run', {'CLOUDLAB_RESUME_RUN_ID': old_id, 'EPOCHS': '3'}, check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('checkpoint inputs do not match', rejected.stderr)
        bad_id = self.latest()
        self.assertFalse(any(self.remote.glob(f'*/bratos/runs/{bad_id}/status.json')))

    def test_worker_failure_retains_partial_checkpoints_and_no_result(self):
        self.command('run', {'FAKE_CLOUD_FAIL_HOST': 'node1'})
        result = self.command('wait', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('failed', result.stdout)
        run = self.state / self.latest()
        self.assertFalse((run / 'results.json').exists())
        self.assertTrue(list((run / 'shards').glob('*.json')))

    def test_dependency_mismatch_is_rejected_before_workers_start(self):
        result = self.command('run', {'FAKE_CLOUD_VERSION_HOST': 'node1'}, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('identical Python', result.stderr)
        self.assertFalse(any(self.remote.glob('*/bratos/runs/*/status.json')))

    def test_smoke_limits_large_inventory_and_keeps_both_full_catalogs(self):
        self.command('smoke', {'CLOUDLAB_HOSTS': 'node0 node1 unused-node', 'PROVIDERS': 'starlink'})
        self.command('wait')
        local = self.state / self.latest()
        settings = json.loads((local / 'run.json').read_text())
        self.assertEqual(settings['hosts'], ['node0', 'node1'])
        data = json.loads((local / 'results.json').read_text())
        self.assertEqual(set(data['profiles']), {'starlink', 'kuiper'})
        self.assertEqual(data['profiles']['starlink']['config']['satellites'], 10)
        self.assertEqual(data['profiles']['kuiper']['config']['satellites'], 12)
        self.assertTrue(all(profile['config']['epochs'] == 1 for profile in data['profiles'].values()))

    def test_cancel_stops_detached_workers(self):
        self.command('run', {'FAKE_CLOUD_SLEEP': '1'})
        self.command('cancel')
        deadline = time.monotonic() + 5
        states = []
        while time.monotonic() < deadline:
            states = [json.loads(path.read_text())['state'] for path in self.remote.glob('*/bratos/runs/*/status.json')]
            if states and all(state == 'cancelled' for state in states):
                break
            time.sleep(0.05)
        self.assertEqual(states, ['cancelled', 'cancelled'])

    def test_unmarked_nonempty_directory_is_rejected(self):
        (self.remote / 'node0/bratos/.bratos-cloudlab-root').unlink()
        result = self.command('run', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Refusing non-empty', result.stderr)
        self.assertFalse(any(self.remote.glob('*/bratos/runs/*/status.json')))

    def test_node_time_limit_stops_worker_group(self):
        spec = importlib.util.spec_from_file_location('cloudlab_node', ROOT / 'cloudlab/node.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        config = self.temp / 'node.json'
        config.write_text(json.dumps({'rank': 0, 'nodes': 1, 'cpus': 1, 'simulation': {}, 'time_limit': 1}))
        child = mock.Mock(pid=999)
        child.wait.side_effect = [subprocess.TimeoutExpired('worker', 1), 0]
        with mock.patch.object(module.subprocess, 'Popen', return_value=child), \
             mock.patch.object(module.signal, 'signal'), mock.patch.object(module.os, 'killpg') as killpg:
            module.run(config)
        info = json.loads((self.temp / 'status.json').read_text())
        self.assertEqual(info['state'], 'timed_out')
        self.assertEqual(info['exit_code'], 124)
        killpg.assert_called_once_with(999, signal.SIGTERM)


if __name__ == '__main__':
    unittest.main()
