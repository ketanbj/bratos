#!/usr/bin/env python3
"""CloudLab allocation-local coordinator, provisioned through SSH-config identities."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloudlab.option2 import prepare, ssh, copy
from harness.grid import digest
from harness.tls_transport import certificates


def capture(host, command):
    return subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', host, 'bash -s'],
                          input=command + '\n', text=True, capture_output=True, check=True, timeout=120).stdout


def pull(host, remote, local):
    subprocess.run(['rsync', '-az', '-e', 'ssh -o BatchMode=yes', f'{host}:{remote}', str(local)],
                   check=True, timeout=900)


def start(args):
    if args.resume:
        previous = json.loads((args.resume / 'deployment.json').read_text())
        for name, expected in previous['sha256'].items():
            if digest(args.resume / 'inputs' / name) != expected:
                raise ValueError(f'Frozen deployment changed: {name}')
        assert_idle(previous)
        stop(args.resume, previous)
    directory, deployment = prepare(args)
    base = deployment['remote_root']
    python = deployment['remote_base'] + '/.venv/bin/python'
    private = directory / '.tls'
    if not args.resume:
        certificates(private, len(args.hosts))
    def server(item):
        index, host = item
        secret = base + '/.tls'
        ssh(host, 'mkdir -p ' + shlex.quote(secret) + '; chmod 700 ' + shlex.quote(secret))
        for name in ('ca.crt', f'worker-{index}.crt', f'worker-{index}.key'):
            copy(private / name, host, secret + '/' + name)
        ssh(host, 'chmod 600 ' + shlex.quote(secret + f'/worker-{index}.key'))
        command = [python, base + '/harness/tls_transport.py', 'serve', '--ca', secret + '/ca.crt',
                   '--certificate', secret + f'/worker-{index}.crt', '--key', secret + f'/worker-{index}.key',
                   '--state', base + '/server-state.json', '--python', python]
        script = f'''{shlex.quote(python)} - <<'SCRIPT'
import json, os, subprocess, time
from pathlib import Path
base = Path({base!r})
state = base / 'server-state.json'
state.unlink(missing_ok=True)
log = (base / 'server.log').open('a')
process = subprocess.Popen({command!r}, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                           start_new_session=True, env={{**os.environ, 'OPENBLAS_NUM_THREADS':'1', 'OMP_NUM_THREADS':'1'}})
limit = time.monotonic() + 30
while not (base / 'server-state.json').exists():
    if process.poll() is not None: raise RuntimeError('TLS service failed; inspect server.log')
    if time.monotonic() > limit: raise TimeoutError('TLS service startup timeout')
    time.sleep(.1)
print((base / 'server-state.json').read_text())
SCRIPT'''
        state = json.loads(capture(host, script))
        expanded = subprocess.run(['ssh', '-G', host], text=True, capture_output=True, check=True).stdout
        hostname = next(line.split(maxsplit=1)[1] for line in expanded.splitlines() if line.startswith('hostname '))
        return {'host': hostname, 'port': state['port'], 'name': f'worker-{index}', 'ssh_host': host,
                'server_pid': state['pid']}
    with ThreadPoolExecutor(max_workers=len(args.hosts)) as pool:
        endpoints = list(pool.map(server, enumerate(args.hosts)))
    head = args.hosts[0]
    for name in ('controller.crt', 'controller.key'):
        copy(private / name, head, base + '/.tls/' + name)
    ssh(head, 'chmod 600 ' + shlex.quote(base + '/.tls/controller.key'))
    configuration = {'ca': base + '/.tls/ca.crt', 'certificate': base + '/.tls/controller.crt',
                     'key': base + '/.tls/controller.key', 'rpc_timeout_seconds': 600,
                     'endpoints': [endpoint for endpoint in endpoints for _ in range(args.workers_per_host)]}
    tls_path = directory / 'tls-config.json'
    tls_path.write_text(json.dumps(configuration, indent=2) + '\n')
    copy(tls_path, head, base + '/tls-config.json')
    command = ['--tls-config', base + '/tls-config.json', '--grid', base + '/data/grid.npz',
               '--snapshot', base + '/data/orbital_snapshot_full.json', '--directory', base + '/simulation',
               '--provider', args.provider, '--seed', str(args.seed), '--epochs', str(args.epochs),
               '--start-epoch', str(args.start_epoch), '--wall-seconds', str(args.wall_seconds),
               '--query-workers', str(args.query_workers)]
    if args.calibration: command.append('--calibration')
    if args.stop_after: command.extend(['--stop-after', str(args.stop_after)])
    if args.resume: command.append('--resume')
    if '--regional-controllers' in (directory / 'inputs/harness/coupled.py').read_text():
        command.extend(['--regional-controllers', str(args.regional_controllers)])
    path = directory / 'controller-args.json'
    path.write_text(json.dumps(command) + '\n')
    copy(path, head, base + '/controller-args.json')
    deployment.update(controller='CloudLab node0; persists independently of laptop', head=head,
                      tls_endpoints=endpoints, workers_per_host=args.workers_per_host)
    (directory / 'deployment.json').write_text(json.dumps(deployment, indent=2) + '\n')
    supervised = [python, base + '/harness/tls_transport.py', 'supervise', base, '--python', python]
    script = f'''{shlex.quote(python)} - <<'SCRIPT'
import os, subprocess
from pathlib import Path
base = Path({base!r})
log = (base / 'supervisor.log').open('a')
p = subprocess.Popen({supervised!r}, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                     start_new_session=True, env={{**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'}})
print('Detached coordinator PID', p.pid)
SCRIPT'''
    ssh(head, script)
    print('Cluster deployment: ' + str(directory), flush=True)


def assert_idle(deployment):
    """Reject duplicate coordinators even if a previous status file is stale."""
    base = deployment['remote_root']
    script = f'''python3 - <<'SCRIPT'
import json
from pathlib import Path
base = Path({base!r})
path = base / 'controller-status.json'
if path.exists():
    status = json.loads(path.read_text())
    for key in ('pid', 'child_pid'):
        command = Path('/proc') / str(status.get(key)) / 'cmdline'
        if command.exists() and str(base).encode() in command.read_bytes():
            raise RuntimeError('Coordinator is still active; use stop before recovery')
SCRIPT'''
    capture(deployment['head'], script)


def resume(args, directory, deployment):
    # The captured trajectory defines all scientific parameters. Only the
    # additional epoch count and wall budget are operational overrides.
    command = json.loads((directory / 'controller-args.json').read_text())
    def value(flag):
        return command[command.index(flag) + 1]
    args.resume = directory
    args.hosts = deployment['hosts']
    args.remote_base = deployment['remote_base']
    args.provider = deployment['provider']
    args.seed = deployment['seed']
    args.calibration = deployment['calibration']
    args.start_epoch = deployment.get('start_epoch', 0)
    args.epochs = int(value('--epochs'))
    args.query_workers = int(value('--query-workers'))
    args.workers_per_host = deployment['workers_per_host']
    if '--regional-controllers' in command:
        args.regional_controllers = int(value('--regional-controllers'))
    start(args)


def fetch(directory, deployment):
    head, base = deployment['head'], deployment['remote_root']
    pull(head, base + '/controller-status.json', directory / 'controller-status.json')
    pull(head, base + '/controller.log', directory / 'controller.log')
    (directory / 'simulation').mkdir(exist_ok=True)
    pull(head, base + '/simulation/', str(directory / 'simulation') + '/')
    return json.loads((directory / 'controller-status.json').read_text())


def stop(directory, deployment):
    base = deployment['remote_root']
    for host in deployment['hosts']:
        script = f'''python3 - <<'SCRIPT'
import json, os, signal
from pathlib import Path
base = Path({base!r})
for name in ('controller-status.json', 'server-state.json'):
    path = base / name
    if not path.exists(): continue
    pid = json.loads(path.read_text()).get('pid')
    command = Path('/proc') / str(pid) / 'cmdline'
    if command.exists() and str(base).encode() in command.read_bytes() and b'tls_transport.py' in command.read_bytes():
        os.killpg(pid, signal.SIGTERM)
        print('Stopped owned process group', pid)
SCRIPT'''
        ssh(host, script)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'resume', 'status', 'wait', 'fetch', 'stop'))
    parser.add_argument('--run', type=Path)
    parser.add_argument('--hosts', nargs='+', default=['bratos-cloudlab-node0', 'bratos-cloudlab-node1'])
    parser.add_argument('--remote-base', default='/users/ketanbj/bratos-option2')
    parser.add_argument('--grid', type=Path, default=ROOT / 'data/generated/global-option2-res5.npz')
    parser.add_argument('--snapshot', type=Path, default=ROOT / 'data/orbital_snapshot_full.json')
    parser.add_argument('--provider', choices=('starlink', 'kuiper'), default='starlink')
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--epochs', type=int, default=1200)
    parser.add_argument('--start-epoch', type=int, default=0)
    parser.add_argument('--workers-per-host', type=int, default=8)
    parser.add_argument('--query-workers', type=int, default=1)
    parser.add_argument('--regional-controllers', type=int, default=6)
    parser.add_argument('--wall-seconds', type=int, default=43200)
    parser.add_argument('--stop-after', type=int)
    parser.add_argument('--calibration', action='store_true')
    parser.add_argument('--resume', type=Path)
    args = parser.parse_args()
    if args.action == 'start':
        if min(args.epochs, args.workers_per_host, args.query_workers) < 1:
            parser.error('Epochs and worker counts must be positive')
        if not 1 <= args.regional_controllers <= 12:
            parser.error('--regional-controllers must be between 1 and 12')
        if args.resume:
            parser.error('Use resume --run PATH to recover the captured cluster trajectory')
        if not 0 <= args.start_epoch < args.epochs or (args.start_epoch and not args.calibration) or args.wall_seconds < 1 or (args.stop_after is not None and args.stop_after < 1):
            parser.error('Invalid calibration start, wall budget, or stop count')
        start(args)
        return
    if not args.run:
        parser.error('--run is required')
    directory = args.run.resolve()
    deployment = json.loads((directory / 'deployment.json').read_text())
    if args.action == 'resume':
        if args.wall_seconds < 1 or (args.stop_after is not None and args.stop_after < 1):
            parser.error('Wall budget and stop count must be positive')
        resume(args, directory, deployment)
        return
    if args.action == 'stop':
        stop(directory, deployment)
        return
    while True:
        status = fetch(directory, deployment)
        print(json.dumps(status), flush=True)
        if args.action != 'wait' or status['state'] != 'running':
            break
        time.sleep(10)
    if status['state'] == 'failed':
        raise RuntimeError('Cluster coordinator failed; inspect controller.log')


if __name__ == '__main__':
    main()
