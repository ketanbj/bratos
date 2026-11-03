#!/usr/bin/env python3
"""Run independent seeds across nodes with durable seed checkpoints."""
import concurrent.futures
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys

import h3
import sgp4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.beam_sim import Config, aggregate_runs, benchmark_result, load_orbital_snapshot, orbital_snapshot_path, resolve_satellite_count, run_seed


def settings():
    seeds = [int(value) for value in os.environ['SEEDS'].split(',')]
    snapshot = load_orbital_snapshot()
    providers = list(snapshot['providers']) if os.environ['PROVIDERS'] == 'all' else os.environ['PROVIDERS'].split(',')
    configs = {provider: Config(
        provider=provider, epochs=int(os.environ['EPOCHS']),
        epoch_seconds=int(os.environ['EPOCH_SECONDS']),
        service_cells=int(os.environ['SERVICE_CELLS']),
        h3_resolution=int(os.environ['H3_RESOLUTION']),
        satellites=resolve_satellite_count(os.environ['SATELLITES'], provider, snapshot),
    ) for provider in providers}
    return seeds, configs


def fingerprint(provider, index, config, seed):
    inputs = {
        'provider': provider, 'index': index, 'config': asdict(config), 'seed': seed,
        'python': sys.version.split()[0], 'h3': h3.__version__, 'sgp4': sgp4.__version__,
        'sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                   for name in ('src/beam_sim.py', 'requirements.txt', 'harness/distributed.py')},
    }
    inputs['sha256']['orbital_snapshot'] = hashlib.sha256(orbital_snapshot_path().read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def checkpoint(directory, provider, index):
    return directory / f'{provider}-seed-{index}.json'


def read_checkpoint(path, expected):
    entry = json.loads(path.read_text())
    if entry['fingerprint'] != expected:
        raise ValueError(f'checkpoint inputs do not match this run: {path}')
    return entry


def run_job(job):
    provider, index, config, seed, directory = job
    path = checkpoint(directory, provider, index)
    expected = fingerprint(provider, index, config, seed)
    if path.exists():
        read_checkpoint(path, expected)
        print(f'Resumed {provider} seed {seed} from {path.name}', flush=True)
        return
    # A failed run writes no checkpoint; deterministic application errors
    # propagate rather than being silently retried.
    run = run_seed(config, seed, index == 0)
    if index != 0:
        run = {'systems': run['systems']}
    atomic_json(path, {'provider': provider, 'index': index, 'seed': seed,
                       'fingerprint': expected, 'run': run})
    print(f'Checkpointed {provider} seed {seed}', flush=True)


def worker(directory):
    seeds, configs = settings()
    rank = int(os.environ.get('BEAM_RANK', os.environ.get('SLURM_PROCID', '0')))
    nodes = int(os.environ.get('BEAM_NODES', os.environ.get('SLURM_NTASKS', '1')))
    jobs = [(provider, index, config, seed, directory) for provider, config in configs.items() for index, seed in enumerate(seeds)]
    assigned = jobs[rank::nodes]
    cpus = int(os.environ.get('BEAM_CPUS', os.environ.get('SLURM_CPUS_PER_TASK', '1')))
    requested = int(os.environ.get('SIM_WORKERS', '0')) or cpus
    workers = min(requested, cpus, len(assigned))
    print(f'Rank {rank}/{nodes}: {len(assigned)} provider/seed runs, {workers} CPU workers', flush=True)
    directory.mkdir(parents=True, exist_ok=True)
    if workers > 1:
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
            list(pool.map(run_job, assigned))
    else:
        for job in assigned:
            run_job(job)
    print(f'Rank {rank} completed', flush=True)


def prepare(directory, previous):
    seeds, configs = settings()
    if not previous.is_dir():
        raise ValueError(f'resume checkpoints not found: {previous}')
    directory.mkdir(parents=True, exist_ok=True)
    count = 0
    for provider, config in configs.items():
        for index, seed in enumerate(seeds):
            source = checkpoint(previous, provider, index)
            if source.exists():
                entry = read_checkpoint(source, fingerprint(provider, index, config, seed))
                atomic_json(checkpoint(directory, provider, index), entry)
                count += 1
    print(f'Prepared {count} completed seed checkpoints from {previous}', flush=True)


def merge(directory, output):
    seeds, configs = settings()
    profiles = {}
    for provider, config in configs.items():
        runs = []
        for index, seed in enumerate(seeds):
            path = checkpoint(directory, provider, index)
            entry = read_checkpoint(path, fingerprint(provider, index, config, seed))
            if (entry['provider'], entry['index'], entry['seed']) != (provider, index, seed):
                raise ValueError(f'incorrect provider/seed result: {path}')
            runs.append(entry['run'])
        profiles[provider] = aggregate_runs(config, seeds, runs)
    atomic_json(output, benchmark_result(profiles))
    print(f'Merged {len(configs) * len(seeds)} runs into {output}', flush=True)


if __name__ == '__main__':
    action, directory = sys.argv[1], Path(sys.argv[2])
    if action == 'worker':
        worker(directory)
    elif action == 'merge':
        merge(directory, Path(sys.argv[3]))
    elif action == 'prepare':
        prepare(directory, Path(sys.argv[3]))
    else:
        raise ValueError(f'unknown action: {action}')
