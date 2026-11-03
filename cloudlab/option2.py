#!/usr/bin/env python3
"""Deploy the shared coupled runner over existing SSH-config CloudLab aliases."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.grid import digest, load

FILES = ('src/beam_sim.py', 'requirements.txt', 'harness/requirements-option2.txt',
         'harness/grid.py', 'harness/coupled.py', 'harness/tls_transport.py')
MARKER = '.bratos-option2-root'


def ssh(host, command):
    return subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', host, 'bash -s'],
                          input=command + '\n', text=True, check=True, timeout=900)


def copy(source, host, target):
    subprocess.run(['rsync', '-az', '--checksum', '-e', 'ssh -o BatchMode=yes', str(source),
                    f'{host}:{target}'], check=True, timeout=900)


def install(host, base):
    root = shlex.quote(base)
    ssh(host, f'''set -e
mkdir -p {root}
if [ ! -f {root}/{MARKER} ]; then
  if [ -n "$(find {root} -mindepth 1 -maxdepth 1 -print -quit)" ]; then
    echo 'Refusing unmarked nonempty option2 directory' >&2; exit 2
  fi
  touch {root}/{MARKER}
fi
mkdir -p {root}/harness
if [ ! -x {root}/.venv/bin/python ]; then python3 -m venv {root}/.venv; fi
''')
    copy(ROOT / 'requirements.txt', host, base + '/requirements.txt')
    copy(ROOT / 'harness/requirements-option2.txt', host, base + '/harness/requirements-option2.txt')
    ssh(host, f'{root}/.venv/bin/python -m pip install -r {root}/harness/requirements-option2.txt')


def prepare(args):
    hosts = args.hosts
    if len(hosts) != len(set(hosts)) or any(not host or host.startswith('-') or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-@' for c in host) for host in hosts):
        raise ValueError('Use unique SSH-config aliases for hosts')
    if not args.remote_base.startswith('/') or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-' for c in args.remote_base) or '..' in args.remote_base.split('/') or args.remote_base.rstrip('/') in ('/', '/users', '/home', '/tmp', '/var', '/usr'):
        raise ValueError('Remote base must be a dedicated absolute directory')
    grid_path = args.resume.resolve() / 'inputs/data/grid.npz' if args.resume else args.grid
    grid = load(grid_path)
    if not args.calibration and (grid['metadata'].get('production_ready') is not True or
                                grid['metadata'].get('domain') not in ('commercial-union', 'global-study') or
                                grid['metadata']['resolution'] != 5):
        raise ValueError('Production requires a verified commercial union or an explicit complete-Earth study')
    if not args.resume:
        name = 'option2-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
        directory = ROOT / 'cloudlab/state' / name
        bundle = directory / 'inputs'
        bundle.mkdir(parents=True)
        for name in FILES:
            target = bundle / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        (bundle / 'data').mkdir()
        shutil.copy2(args.snapshot, bundle / 'data/orbital_snapshot_full.json')
        shutil.copy2(grid_path, bundle / 'data/grid.npz')
        manifest = {'hosts': hosts, 'remote_base': args.remote_base,
                    'remote_root': args.remote_base + '/runs/' + directory.name,
                    'sha256': {str(path.relative_to(bundle)): digest(path) for path in bundle.rglob('*') if path.is_file()},
                    'provider': args.provider, 'seed': args.seed, 'calibration': args.calibration,
                    'start_epoch': args.start_epoch,
                    'controller': 'local coordinator; SSH interruption resumes from completed-epoch checkpoint',
                    'grid': grid['metadata']}
        (directory / 'deployment.json').write_text(json.dumps(manifest, indent=2) + '\n')
    else:
        directory = args.resume.resolve()
        bundle = directory / 'inputs'
        manifest = json.loads((directory / 'deployment.json').read_text())
        if hosts != manifest['hosts'] or args.remote_base != manifest['remote_base']:
            raise ValueError('Resume must use the original deployment hosts/base')
        if args.provider != manifest['provider'] or args.seed != manifest['seed'] or args.calibration != manifest['calibration']:
            raise ValueError('Resume must use the original provider, seed, and calibration mode')
        if args.start_epoch != manifest.get('start_epoch', 0):
            raise ValueError('Resume must use the original calibration start epoch')
        for name, expected in manifest['sha256'].items():
            if digest(bundle / name) != expected:
                raise ValueError(f'Frozen deployment changed: {name}')
    def deploy(host):
        install(host, manifest['remote_base'])
        ssh(host, 'mkdir -p ' + shlex.quote(manifest['remote_root']))
        copy(str(bundle) + '/', host, manifest['remote_root'] + '/')
    with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        list(pool.map(deploy, hosts))
    return directory, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hosts', nargs='+', default=['bratos-cloudlab-node0', 'bratos-cloudlab-node1'])
    parser.add_argument('--remote-base', default='/users/ketanbj/bratos-option2')
    parser.add_argument('--grid', type=Path, default=ROOT / 'data/generated/global-option2-res5.npz')
    parser.add_argument('--snapshot', type=Path, default=ROOT / 'data/orbital_snapshot_full.json')
    parser.add_argument('--provider', choices=('starlink', 'kuiper'), default='starlink')
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--calibration', action='store_true')
    parser.add_argument('--epochs', type=int, default=1200)
    parser.add_argument('--start-epoch', type=int, default=0)
    parser.add_argument('--stop-after', type=int, default=None)
    parser.add_argument('--wall-seconds', type=int, default=43200)
    parser.add_argument('--chunk-epochs', type=int, default=120)
    parser.add_argument('--query-workers', type=int, default=1)
    parser.add_argument('--regional-controllers', type=int, default=6)
    parser.add_argument('--workers-per-host', type=int, default=8,
                        help='Geometry/sort processes per physical host; size from peak memory and CPU allocation')
    parser.add_argument('--resume', type=Path)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if min(args.workers_per_host, args.query_workers, args.chunk_epochs, args.epochs) < 1:
        parser.error('Worker and epoch counts must be positive')
    if not 1 <= args.regional_controllers <= 12:
        parser.error('--regional-controllers must be between 1 and 12')
    if not 0 <= args.start_epoch < args.epochs or (args.start_epoch and not args.calibration) or (args.stop_after is not None and args.stop_after < 1):
        parser.error('Invalid calibration start or stop count')
    directory, manifest = prepare(args)
    print('Frozen deployment: ' + str(directory), flush=True)
    if args.prepare_only:
        return
    command = [sys.executable, str(directory / 'inputs/harness/coupled.py'),
               '--grid', str(directory / 'inputs/data/grid.npz'),
               '--snapshot', str(directory / 'inputs/data/orbital_snapshot_full.json'),
               '--directory', str(directory / 'simulation'), '--provider', args.provider,
               '--seed', str(args.seed), '--epochs', str(args.epochs), '--chunk-epochs', str(args.chunk_epochs),
               '--query-workers', str(args.query_workers), '--hosts',
               *[host for host in args.hosts for _ in range(args.workers_per_host)],
               '--remote-root', manifest['remote_root'], '--remote-python', manifest['remote_base'] + '/.venv/bin/python']
    # Recovery executes the frozen runner, including its original CLI. Do not
    # require newly added operational flags from older checkpoints.
    frozen_source = (directory / 'inputs/harness/coupled.py').read_text()
    if '--regional-controllers' in frozen_source:
        command.extend(['--regional-controllers', str(args.regional_controllers)])
    if '--start-epoch' in frozen_source:
        command.extend(['--start-epoch', str(args.start_epoch)])
    elif args.start_epoch:
        raise ValueError('Frozen runner does not support calibration start offsets')
    if '--wall-seconds' in frozen_source:
        command.extend(['--wall-seconds', str(args.wall_seconds)])
    if args.calibration:
        command.append('--calibration')
    if args.stop_after:
        command.extend(['--stop-after', str(args.stop_after)])
    if args.resume:
        command.append('--resume')
    with (directory / 'controller.log').open('a') as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            for line in process.stdout:
                print(line, end='', flush=True)
                log.write(line)
                log.flush()
            if process.wait():
                raise subprocess.CalledProcessError(process.returncode, command)
        except BaseException:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=120)
            raise


if __name__ == '__main__':
    main()
