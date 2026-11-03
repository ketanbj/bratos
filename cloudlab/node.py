#!/usr/bin/env python3
"""Detached CloudLab worker supervision; all state belongs to one frozen run."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import signal
import shlex
import subprocess
import sys
import time


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def alive(info, config):
    pid = info.get('pid')
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        command = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        return os.fsencode(str(Path(__file__).resolve())) in command and os.fsencode(str(config)) in command
    except (FileNotFoundError, ProcessLookupError):
        # Allows local macOS integration checks; deployed Ubuntu uses /proc.
        command = subprocess.run(['ps', '-p', str(pid), '-o', 'args='], capture_output=True, text=True)
        arguments = shlex.split(command.stdout) if command.returncode == 0 else []
        return str(Path(__file__).resolve()) in arguments and str(config) in arguments


def status(config):
    path = config.parent / 'status.json'
    if not path.exists():
        return {'state': 'not_started'}
    info = json.loads(path.read_text())
    if info['state'] == 'running' and not alive(info, config):
        info.update(state='lost', observed_at=timestamp())
    return info


def run(config):
    settings = json.loads(config.read_text())
    root = config.parent
    info = {'state': 'running', 'pid': os.getpid(), 'started_at': timestamp(),
            'rank': settings['rank'], 'nodes': settings['nodes'], 'cpus': settings['cpus']}
    path = root / 'status.json'
    save(path, info)
    child = None
    cancelled = False

    def stop(_signum, _frame):
        nonlocal cancelled
        cancelled = True
        if child is not None and child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    env = {**os.environ, **settings['simulation'], 'PYTHONUNBUFFERED': '1',
           'BEAM_RANK': str(settings['rank']), 'BEAM_NODES': str(settings['nodes']),
           'BEAM_CPUS': str(settings['cpus']), 'ORBITAL_SNAPSHOT': str(root / 'data/orbital_snapshot.json')}
    try:
        if cancelled:
            info.update(state='cancelled', exit_code=143)
        else:
            child = subprocess.Popen([sys.executable, str(root / 'harness/distributed.py'), 'worker', str(root / 'shards')],
                                     cwd=root, env=env, start_new_session=True)
            if cancelled and child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
            try:
                code = child.wait(timeout=settings['time_limit'] or None)
                info.update(state='cancelled' if cancelled else ('completed' if code == 0 else 'failed'), exit_code=code)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
                info.update(state='timed_out', exit_code=124)
    except Exception as error:
        info.update(state='failed', exit_code=1, error=str(error))
        raise
    finally:
        info['finished_at'] = timestamp()
        save(path, info)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start', 'run', 'status', 'cancel'])
    parser.add_argument('config', type=Path)
    args = parser.parse_args()
    config = args.config.resolve()
    if args.action == 'run':
        run(config)
    elif args.action == 'start':
        if status(config)['state'] != 'not_started':
            raise ValueError('run already started; create a new run to resume')
        with (config.parent / 'worker.log').open('ab') as log:
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'run', str(config)],
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        for _ in range(100):
            info = status(config)
            if info.get('pid') == child.pid:
                print(json.dumps(info))
                return
            if child.poll() is not None:
                break
            time.sleep(0.05)
        if child.poll() is None:
            child.terminate()
        raise RuntimeError('worker did not report startup; inspect worker.log')
    elif args.action == 'status':
        print(json.dumps(status(config)))
    else:
        info = status(config)
        if info['state'] == 'running' and alive(info, config):
            os.kill(info['pid'], signal.SIGTERM)
        print(json.dumps(info))


if __name__ == '__main__':
    main()
