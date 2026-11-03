#!/usr/bin/env python3
"""SSH adapter for existing CloudLab nodes; use the shared seed runner."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
SIM_KEYS = ('SERVICE_CELLS', 'H3_RESOLUTION', 'EPOCHS', 'EPOCH_SECONDS', 'SATELLITES', 'PROVIDERS', 'SEEDS', 'SIM_WORKERS')
MARKER = '.bratos-cloudlab-root'


def invoke(args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs)


def ssh(settings, host, command, timeout=120):
    # CloudLab accounts may default to tcsh, which cannot parse our multiline
    # Bash scripts. Send scripts on stdin so the login shell only sees bash -s.
    return invoke([settings['ssh'], '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', host, 'bash -s'],
                  input=command + '\n', timeout=timeout)


def copy_to(settings, source, host, target):
    invoke([settings['rsync'], '-az', '--checksum', '-e', shlex.join([settings['ssh'], '-o', 'BatchMode=yes']),
            str(source), f'{host}:{target}'])


def config(require_hosts=True):
    hosts = shlex.split(os.environ.get('CLOUDLAB_HOSTS', ''))
    if require_hosts and not hosts:
        raise ValueError('Set CLOUDLAB_HOSTS to your experiment SSH destinations or aliases')
    if len(hosts) != len(set(hosts)) or any(not re.fullmatch(r'[A-Za-z0-9_@.-]+', host) or host.startswith('-') for host in hosts):
        raise ValueError('Hosts must be unique SSH aliases or user@host; put ports/keys in ~/.ssh/config')
    remote = os.environ.get('CLOUDLAB_REMOTE_DIR', '')
    if require_hosts and (not re.fullmatch(r'/[A-Za-z0-9_./-]+', remote) or '..' in remote.split('/')
                          or remote.rstrip('/') in ('/', '/home', '/users', '/local', '/tmp', '/var', '/usr', '/data')):
        raise ValueError('CLOUDLAB_REMOTE_DIR must be a dedicated absolute directory')
    simulation = {key: os.environ[key] for key in SIM_KEYS}
    for key in ('SERVICE_CELLS', 'EPOCHS', 'EPOCH_SECONDS'):
        if int(simulation[key]) < 1:
            raise ValueError(f'{key} must be positive')
    if not 0 <= int(simulation['H3_RESOLUTION']) <= 15:
        raise ValueError('H3_RESOLUTION must be between 0 and 15')
    if simulation['SATELLITES'] != 'all' and int(simulation['SATELLITES']) < 1:
        raise ValueError('SATELLITES must be all or a positive integer')
    seeds = [int(value) for value in simulation['SEEDS'].split(',')]
    if not seeds or len(seeds) != len(set(seeds)):
        raise ValueError('SEEDS must contain unique integers')
    snapshot = Path(os.environ.get('ORBITAL_SNAPSHOT', 'data/orbital_snapshot_full.json'))
    if not snapshot.is_absolute():
        snapshot = ROOT / snapshot
    catalog = json.loads(snapshot.read_text())
    providers = list(catalog['providers']) if simulation['PROVIDERS'] == 'all' else simulation['PROVIDERS'].split(',')
    if not providers or len(set(providers)) != len(providers):
        raise ValueError('PROVIDERS must name unique providers')
    counts = {}
    for provider in providers:
        records = catalog['providers'][provider]['sampled_orbits']
        if simulation['SATELLITES'] == 'all':
            entry = catalog['providers'][provider]
            if entry.get('selection') != 'all' or len(records) != entry['records_available'] or len({r['norad_catalog_id'] for r in records}) != len(records):
                raise ValueError(f'{provider}: all satellites requires a complete, unique catalog')
            counts[provider] = len(records)
        else:
            counts[provider] = int(simulation['SATELLITES'])
        if not 0 < counts[provider] <= len(records):
            raise ValueError(f'{provider}: invalid satellite count')
    jobs = len(seeds) * len(providers)
    cpus = int(os.environ.get('CLOUDLAB_CPUS_PER_NODE', '1'))
    workers = int(simulation['SIM_WORKERS'])
    limit = int(os.environ.get('CLOUDLAB_TIME_LIMIT_SECONDS', '3600'))
    poll = int(os.environ.get('CLOUDLAB_POLL_SECONDS', '30'))
    if cpus < 1 or not 0 <= workers <= cpus or limit < 0 or poll < 1:
        raise ValueError('Invalid CPU, worker, time-limit, or poll setting')
    if require_hosts and len(hosts) > jobs:
        raise ValueError(f'{len(hosts)} nodes exceed {jobs} independent provider/seed runs')
    state = Path(os.environ.get('CLOUDLAB_STATE_DIR', 'cloudlab/state'))
    if not state.is_absolute():
        state = ROOT / state
    return {'hosts': hosts, 'remote': remote.rstrip('/'), 'cpus': cpus, 'time_limit': limit, 'poll': poll,
            'ssh': os.environ.get('CLOUDLAB_SSH', 'ssh'), 'rsync': os.environ.get('CLOUDLAB_RSYNC', 'rsync'),
            'python': os.environ.get('CLOUDLAB_PYTHON', 'python3'), 'simulation': simulation,
            'snapshot': str(snapshot), 'counts': counts, 'jobs': jobs, 'state_dir': str(state)}


def state_root():
    path = Path(os.environ.get('CLOUDLAB_STATE_DIR', 'cloudlab/state'))
    return path if path.is_absolute() else ROOT / path


def load_run(run_id=None):
    state = state_root()
    run_id = run_id or os.environ.get('CLOUDLAB_RUN_ID') or (state / 'latest').read_text().strip()
    if not re.fullmatch(r'run-[A-Za-z0-9-]+', run_id):
        raise ValueError('Invalid CloudLab run ID')
    local = state / run_id
    return local, json.loads((local / 'run.json').read_text())


def remote_run(settings):
    return f"{settings['remote']}/runs/{settings['id']}"


def node_command(settings, action):
    base = remote_run(settings)
    return shlex.join([f"{settings['remote']}/.venv/bin/python", f'{base}/cloudlab/node.py', action, f'{base}/node.json'])


def guard(settings, host):
    target = shlex.quote(settings['remote'])
    script = f'''set -e
mkdir -p {target}
if [ ! -f {target}/{MARKER} ]; then
  if [ -n "$(find {target} -mindepth 1 -maxdepth 1 -print -quit)" ]; then
    echo 'Refusing non-empty unmarked CloudLab directory' >&2; exit 2
  fi
  touch {target}/{MARKER}
fi
'''
    ssh(settings, host, 'bash -c ' + shlex.quote(script))


def setup(settings):
    def install(host):
        guard(settings, host)
        copy_to(settings, ROOT / 'requirements.txt', host, settings['remote'] + '/requirements.txt')
        base = shlex.quote(settings['remote'])
        python = shlex.quote(settings['python'])
        script = f'''set -e
{python} -c 'import sys; assert sys.version_info >= (3,10), "Python >=3.10 required"'
if [ ! -x {base}/.venv/bin/python ]; then {python} -m venv {base}/.venv; fi
{base}/.venv/bin/python -m pip install -r {base}/requirements.txt
{base}/.venv/bin/python -c 'import sys,h3,sgp4; assert sys.version_info >= (3,10), "Existing venv needs Python >=3.10"; print("CloudLab dependencies ready")'
'''
        # Dependency installation can exceed the short SSH command timeout.
        output = ssh(settings, host, script, timeout=900)
        print(host + ': ' + output.stdout.strip())
    with ThreadPoolExecutor(max_workers=len(settings['hosts'])) as pool:
        list(pool.map(install, settings['hosts']))


def start(settings):
    previous = os.environ.get('CLOUDLAB_RESUME_RUN_ID', '')
    old_shards = None
    if previous:
        old, _ = load_run(previous)
        old_shards = old / 'shards'
        if not old_shards.is_dir() or not list(old_shards.glob('*.json')):
            raise ValueError('Fetch completed checkpoints from the previous run before resuming')
    run_id = 'run-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    local = Path(settings['state_dir']) / run_id
    bundle = local / 'inputs'
    for path in ('src', 'data', 'harness', 'cloudlab'):
        (bundle / path).mkdir(parents=True, exist_ok=True)
    files = ('src/beam_sim.py', 'requirements.txt', 'harness/distributed.py', 'harness/preflight.py', 'cloudlab/node.py')
    for path in files:
        shutil.copy2(ROOT / path, bundle / path)
    shutil.copy2(settings['snapshot'], bundle / 'data/orbital_snapshot.json')
    settings.update(id=run_id, resume_from=previous, state='preparing',
                    input_sha256={str(path.relative_to(bundle)): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in bundle.rglob('*') if path.is_file()})
    (local / 'run.json').write_text(json.dumps(settings, indent=2) + '\n')
    (local.parent / 'latest').write_text(run_id + '\n')
    base = remote_run(settings)
    versions = []
    for rank, host in enumerate(settings['hosts']):
        guard(settings, host)
        ssh(settings, host, shlex.join(['mkdir', '-p', base]))
        copy_to(settings, str(bundle) + '/', host, base + '/')
        node = {'simulation': settings['simulation'], 'rank': rank, 'nodes': len(settings['hosts']),
                'cpus': settings['cpus'], 'time_limit': settings['time_limit'], 'input_sha256': settings['input_sha256']}
        node_path = local / f'node-{rank}.json'
        node_path.write_text(json.dumps(node) + '\n')
        copy_to(settings, node_path, host, base + '/node.json')
        env = {**settings['simulation'], 'BEAM_NODES': str(len(settings['hosts'])),
               'ORBITAL_SNAPSHOT': base + '/data/orbital_snapshot.json'}
        prefix = 'env ' + ' '.join(shlex.quote(key + '=' + value) for key, value in env.items()) + ' '
        python = f"{settings['remote']}/.venv/bin/python"
        check = ("import json,sys,os,h3,sgp4,hashlib; from pathlib import Path; "
                 f"root=Path({base!r}); expected=json.loads((root/'node.json').read_text())['input_sha256']; "
                 "assert all(hashlib.sha256((root/key).read_bytes()).hexdigest()==value for key,value in expected.items()), 'Frozen input checksum mismatch'; "
                 "print(json.dumps([sys.version.split()[0],h3.__version__,sgp4.__version__,"
                 "len(os.sched_getaffinity(0)) if hasattr(os,'sched_getaffinity') else os.cpu_count()]))")
        version = json.loads(ssh(settings, host, shlex.join([python, '-c', check])).stdout)
        if version[3] < settings['cpus']:
            raise ValueError(f'{host}: only {version[3]} CPUs available')
        versions.append(version[:3])
        output = ssh(settings, host, prefix + shlex.join([python, base + '/harness/preflight.py', base]))
        print(host + ': ' + output.stdout.strip())
        if old_shards is not None:
            ssh(settings, host, shlex.join(['mkdir', '-p', base + '/resume']))
            copy_to(settings, str(old_shards) + '/', host, base + '/resume/')
            ssh(settings, host, prefix + shlex.join([python, base + '/harness/distributed.py', 'prepare', base + '/shards', base + '/resume']))
    if len({tuple(version) for version in versions}) != 1:
        raise ValueError('Every node must use identical Python, H3, and SGP4 versions')
    settings['versions'] = versions[0]
    started = []
    try:
        for host in settings['hosts']:
            started.append(host)
            print(host + ': ' + ssh(settings, host, node_command(settings, 'start')).stdout.strip())
        settings['state'] = 'running'
    except Exception:
        settings['state'] = 'launch_failed'
        for host in started:
            try:
                ssh(settings, host, node_command(settings, 'cancel'))
            except Exception:
                pass
        raise
    finally:
        (local / 'run.json').write_text(json.dumps(settings, indent=2) + '\n')
    print('Started ' + run_id + '; workers continue after disconnect. Use status, fetch, or wait.')


def statuses(settings):
    output = []
    for host in settings['hosts']:
        try:
            info = json.loads(ssh(settings, host, node_command(settings, 'status')).stdout)
        except Exception as error:
            info = {'state': 'unreachable', 'error': str(error)}
        print(host + ': ' + json.dumps(info))
        output.append(info)
    return output


def fetch(local, settings):
    shards = local / 'shards'
    shards.mkdir(exist_ok=True)
    base = remote_run(settings)
    errors = []
    for rank, host in enumerate(settings['hosts']):
        target = local / f'node-{rank}'
        target.mkdir(exist_ok=True)
        try:
            invoke([settings['rsync'], '-az', '-e', shlex.join([settings['ssh'], '-o', 'BatchMode=yes']),
                    '--include=shards/', '--include=shards/*.json', '--include=status.json', '--include=worker.log', '--exclude=*',
                    f'{host}:{base}/', str(target) + '/'])
            for source in (target / 'shards').glob('*.json'):
                destination = shards / source.name
                if destination.exists() and destination.read_bytes() != source.read_bytes():
                    raise ValueError('Conflicting checkpoint: ' + source.name)
                shutil.copy2(source, destination)
        except (subprocess.SubprocessError, OSError) as error:
            errors.append(f'{host}: {error}')
    print(f"Cached {len(list(shards.glob('*.json')))}/{settings['jobs']} checkpoints in {shards}")
    infos = statuses(settings)
    if errors or any(info['state'] in ('failed', 'lost', 'timed_out', 'cancelled', 'unreachable', 'not_started') for info in infos):
        raise RuntimeError('Incomplete/failed run; cached checkpoints retained. ' + '; '.join(errors))
    if all(info['state'] == 'completed' for info in infos):
        host = settings['hosts'][0]
        collected = base + '/collected'
        ssh(settings, host, shlex.join(['mkdir', '-p', collected]))
        copy_to(settings, str(shards) + '/', host, collected + '/')
        env = {**settings['simulation'], 'ORBITAL_SNAPSHOT': base + '/data/orbital_snapshot.json'}
        prefix = 'env ' + ' '.join(shlex.quote(key + '=' + value) for key, value in env.items()) + ' '
        print(ssh(settings, host, prefix + shlex.join([settings['remote'] + '/.venv/bin/python', base + '/harness/distributed.py',
                                                   'merge', collected, base + '/results.json'])).stdout.strip())
        invoke([settings['rsync'], '-az', '-e', shlex.join([settings['ssh'], '-o', 'BatchMode=yes']),
                f'{host}:{base}/results.json', str(local / 'results.json')])
        settings['state'] = 'completed'
        (local / 'run.json').write_text(json.dumps(settings, indent=2) + '\n')
        print('Result: ' + str(local / 'results.json'))
        return True
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['setup', 'dry-run', 'run', 'smoke', 'status', 'logs', 'cancel', 'fetch', 'wait'])
    action = parser.parse_args().action
    if action in ('setup', 'dry-run', 'run', 'smoke'):
        if action == 'smoke':
            os.environ.update(SERVICE_CELLS='240', EPOCHS='1', SEEDS='1', SATELLITES='all', SIM_WORKERS='1', PROVIDERS='starlink,kuiper')
            hosts = shlex.split(os.environ.get('CLOUDLAB_HOSTS', ''))
            os.environ['CLOUDLAB_HOSTS'] = shlex.join(hosts[:2])
        settings = config()
        if action == 'dry-run':
            print(json.dumps(settings, indent=2))
            print('Preview only: no SSH, file transfer, or worker launch')
        elif action == 'setup':
            setup(settings)
        else:
            start(settings)
    else:
        local, settings = load_run()
        if action == 'status':
            infos = statuses(settings)
            if any(info['state'] in ('failed', 'lost', 'timed_out', 'unreachable') for info in infos):
                raise RuntimeError('One or more nodes failed or are unreachable')
        elif action == 'fetch':
            fetch(local, settings)
        elif action == 'wait':
            while not fetch(local, settings):
                time.sleep(settings['poll'])
        else:
            errors = []
            for host in settings['hosts']:
                command = node_command(settings, 'cancel') if action == 'cancel' else shlex.join(['tail', '-n', '100', remote_run(settings) + '/worker.log'])
                try:
                    print(host + ': ' + ssh(settings, host, command).stdout.strip())
                except Exception as error:
                    errors.append(f'{host}: {error}')
            if errors:
                raise RuntimeError('; '.join(errors))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.SubprocessError, RuntimeError) as error:
        print('CloudLab: ' + str(error), file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stderr, file=sys.stderr)
        sys.exit(1)
