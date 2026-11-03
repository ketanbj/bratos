#!/usr/bin/env python3
"""Hourly, stdlib-only collection of frozen CloudLab Option 2 trajectories."""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def remote_status(host, base):
    script = f'''python3 - <<'SCRIPT'
import json
from pathlib import Path
base = Path({base!r})
output = {{}}
for key, name in [('controller', 'controller-status.json'), ('run', 'simulation/run.json'),
                  ('latest', 'simulation/latest.json')]:
    path = base / name
    output[key] = json.loads(path.read_text()) if path.exists() else None
print(json.dumps(output))
SCRIPT'''
    return remote_json(host, script)


def remote_json(host, script):
    result = subprocess.run(['/usr/bin/ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
                             '-o', 'ServerAliveInterval=20', '-o', 'ServerAliveCountMax=3',
                             host, 'bash -s'], input=script, text=True, capture_output=True,
                            check=True, timeout=1800)
    return json.loads(result.stdout)


def collect(host, base, local):
    local.mkdir(parents=True, exist_ok=True)
    simulation = local / 'simulation'
    simulation.mkdir(exist_ok=True)
    transport = '/usr/bin/ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=20 -o ServerAliveCountMax=3'
    for source, target in [(base + '/simulation/', str(simulation) + '/'),
                           (base + '/controller-status.json', str(local / 'controller-status.json')),
                           (base + '/controller.log', str(local / 'controller.log')),
                           (base + '/server.log', str(local / 'server.log')),
                           (base + '/supervisor.log', str(local / 'supervisor.log'))]:
        subprocess.run(['/usr/bin/rsync', '-az', '--exclude=*.tmp', '-e', transport,
                        host + ':' + shlex.quote(source), target], check=True,
                       capture_output=True, text=True, timeout=1800)


def remote_hashes(host, base):
    return remote_json(host, f'''python3 - <<'SCRIPT'
import hashlib, json
from pathlib import Path
base = Path({base!r}) / 'simulation'
output = {{}}
for path in sorted(base.iterdir()):
    if not path.is_file() or path.suffix == '.tmp': continue
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): result.update(block)
    output[path.name] = result.hexdigest()
print(json.dumps(output))
SCRIPT''')


def verify_final(local, expected, hashes):
    directory = local / 'simulation'
    for name, value in hashes.items():
        if Path(name).name != name or digest(directory / name) != value:
            raise ValueError(f'Output transfer checksum mismatch: {name}')
    run = json.loads((directory / 'run.json').read_text())
    result = json.loads((directory / 'results.json').read_text())
    latest = json.loads((directory / 'latest.json').read_text())
    if run['fingerprint'] != expected['fingerprint'] or run['calibration']:
        raise ValueError('Final outputs do not match the frozen production run')
    config = run['inputs']['config']
    if (run['state'] != 'completed' or result['completed_epochs'] != config['epochs'] or
            result['next_epoch'] != config['epochs'] or result['start_epoch'] != 0 or
            result['calibration'] or result['config'] != config):
        raise ValueError('Partial or mismatched trajectory cannot be marked collected')
    if config['service_cells'] != 2016842 or config['satellites'] != expected['satellites']:
        raise ValueError('Unexpected grid or catalog size')
    previous = None
    final = None
    files = sorted(directory.glob('chunk-*.json'))
    for path in files:
        entry = json.loads(path.read_text())
        body = {key: entry[key] for key in ('fingerprint', 'state')}
        checksum = hashlib.sha256(json.dumps(body, sort_keys=True, allow_nan=False).encode()).hexdigest()
        state = entry['state']
        if (entry['fingerprint'] != expected['fingerprint'] or checksum != entry['checksum'] or
                state['predecessor'] != previous or state['next_epoch'] != len(state['rows']) or
                path.name != f"chunk-{state['next_epoch']:06d}.json"):
            raise ValueError(f'Invalid checkpoint chain: {path.name}')
        previous, final = checksum, state
    if (not files or latest['file'] != files[-1].name or latest['checksum'] != previous or
            final['next_epoch'] != config['epochs']):
        raise ValueError('Final checkpoint is missing or incomplete')
    if [row['current_epoch'] for row in final['rows']] != list(range(config['epochs'])):
        raise ValueError('Final raw epoch sequence is incomplete')
    return {'collected_at': now(), 'completed_epochs': config['epochs'],
            'fingerprint': expected['fingerprint'], 'files_sha256': hashes,
            'checkpoint_chain_verified': True}


def check_run(expected):
    local = Path(expected['directory'])
    marker = local / 'collected.json'
    if marker.exists():
        return {'state': 'collected', **json.loads(marker.read_text()), 'directory': str(local)}
    deployment = json.loads((local / 'deployment.json').read_text())
    host, base = deployment['head'], deployment['remote_root']
    observed = remote_status(host, base)
    run = observed['run']
    if not run or run['fingerprint'] != expected['fingerprint']:
        raise ValueError('Remote run does not match the monitored fingerprint')
    # Hourly backups protect already-completed epochs even before the full
    # trajectory ends. Files remain on CloudLab; collection never deletes them.
    collect(host, base, local)
    controller = observed['controller'] or {}
    state = {'state': 'running', 'checked_at': now(), 'directory': str(local),
             'controller_state': controller.get('state'), 'run_state': run['state']}
    latest = observed['latest']
    if latest:
        state['checkpoint'] = latest['file']
    if controller.get('state') == 'failed' or run['state'] == 'failed':
        state.update(state='failed', action_needed='Inspect controller.log; outputs backed up; explicit recovery required')
    elif controller.get('state') == 'completed':
        if run['state'] != 'completed':
            state.update(state='incomplete', action_needed='Run stopped before the full orbit; outputs backed up; resume required')
        else:
            verified = verify_final(local, expected, remote_hashes(host, base))
            atomic_json(marker, verified)
            state.update(verified, state='collected')
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--status', type=Path, required=True)
    args = parser.parse_args()
    args.status.parent.mkdir(parents=True, exist_ok=True)
    with args.status.with_suffix('.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        configuration = json.loads(args.config.read_text())
        # Once verified locally, do not keep contacting expired allocations.
        if args.status.exists() and json.loads(args.status.read_text()).get('all_collected'):
            return
        status = {'checked_at': now(), 'runs': {}}
        for provider, expected in configuration['runs'].items():
            try:
                status['runs'][provider] = check_run(expected)
            except Exception as error:
                status['runs'][provider] = {'state': 'error', 'checked_at': now(),
                                            'error': f'{type(error).__name__}: {error}'}
            # Preserve the first provider's successful collection even if the
            # second provider is unreachable or the monitor is interrupted.
            atomic_json(args.status, status)
        status['all_collected'] = all(run['state'] == 'collected' for run in status['runs'].values())
        atomic_json(args.status, status)
        print(json.dumps(status), flush=True)
        if any(run['state'] == 'error' for run in status['runs'].values()):
            sys.exit(1)


if __name__ == '__main__':
    main()
