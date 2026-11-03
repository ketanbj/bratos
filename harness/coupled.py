#!/usr/bin/env python3
"""Spatially partitioned simulation with one global admission coordinator."""
from __future__ import annotations

import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import hashlib
import heapq
import io
import json
import math
import os
from pathlib import Path
import resource
import shlex
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import h3
import numpy as np
import scipy
import sgp4
from scipy.spatial import cKDTree
from harness.grid import digest, load
from src.beam_sim import (Config, Edge, Plan, Scenario, ServiceCell, assert_plan_safe,
                          load_orbital_snapshot, network_latency_ms, percentile,
                          resolve_satellite_count, backhaul_bytes, region_for,
                          regional_quotas, regional_control_cost, regional_topology)

NAMES = ('centralized', 'centralized_periodic', 'regional_centralized', 'distributed', 'oracle')
EDGE_DTYPE = np.dtype([('cell', '<u4'), ('sat', '<u4'), ('beam', 'u1'),
                      ('score', '<f8'), ('quality', '<f8')])


def phases(ids, seed):
    """Stable synthetic phases keyed to master-cell identity, independent of shards."""
    values = ids.astype(np.uint64) ^ np.uint64(seed % (1 << 64))
    with np.errstate(over='ignore'):
        values += np.uint64(0x9E3779B97F4A7C15)
        values = (values ^ (values >> 30)) * np.uint64(0xBF58476D1CE4E5B9)
        values = (values ^ (values >> 27)) * np.uint64(0x94D049BB133111EB)
        values ^= values >> 31
    return (values >> 11).astype(np.float64) / (1 << 53)


def vectors(xy):
    lon = 2 * np.pi * xy[:, 0] - np.pi
    lat = np.pi / 2 - np.pi * xy[:, 1]
    return np.column_stack((np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)))


def region_ids(xy, controllers):
    """Same spherical Voronoi partition and rounded ties as the scalar model."""
    lat = np.pi / 2 - np.pi * xy[:, 1]
    distances = []
    for controller in controllers:
        other_lat = np.pi / 2 - np.pi * controller.y
        cosine = (np.sin(lat) * math.sin(other_lat) + np.cos(lat) * math.cos(other_lat)
                  * np.cos(2 * np.pi * (xy[:, 0] - controller.x)))
        distances.append(np.round(np.arccos(np.clip(cosine, -1, 1)) / np.pi, 12))
    return np.argmin(np.column_stack(distances), axis=1).astype(np.uint16)


def demands(ids, phase, seed, epoch):
    base = .5 + .28 * np.sin(2 * np.pi * (epoch / 31 + phase))
    center = (ids.astype(np.int64) * 7 + seed) % 43
    burst = np.minimum((epoch - center) % 43, (center - epoch) % 43) <= 2
    return np.clip(base + .27 * burst, .1, 1)


class GridScenario(Scenario):
    def _make_cells(self):
        # Grid arrays are owned by the shard. Do not invoke the legacy sampler.
        return []


def reference_scenario(config, seed, snapshot, grid):
    scenario = GridScenario(config, seed, snapshot)
    scenario.cells = [ServiceCell(int(cid), (lon + 180) / 360, (90 - lat) / 180,
                                 h3.int_to_str(int(index)))
                      for cid, index, (lat, lon) in zip(grid['ids'], grid['indexes'], grid['latlon'])]
    scenario.cell_phase = dict(zip(map(int, grid['ids']), phases(grid['ids'], seed)))
    scenario.cell_regions = {cell.id: region_for(cell, scenario.region_controllers) for cell in scenario.cells}
    return scenario


def beam_ids(cell_xy, sat_xy):
    dx = cell_xy[:, 0] - sat_xy[:, 0]
    dx = np.where(dx > .5, dx - 1, np.where(dx < -.5, dx + 1, dx))
    dy = cell_xy[:, 1] - sat_xy[:, 1]
    angle = (np.arctan2(dy, dx) + 2 * np.pi) % (2 * np.pi)
    return np.where(np.hypot(dx, dy) < .075, 0,
                    1 + ((angle + np.pi / 6) // (np.pi / 3)).astype(int) % 6).astype(np.uint8)


class Shard:
    def __init__(self, config, seed, snapshot, grid, rank=0, workers=1, query_workers=1):
        self.c = config
        self.seed = seed
        self.scenario = GridScenario(config, seed, snapshot)
        # Whole H3 base cells have a single owner. Ownership changes computation
        # placement, never cell identity or satellite capacity.
        tiles = np.unique(grid['parents'])
        owned = tiles[rank::workers]
        keep = np.isin(grid['parents'], owned)
        self.ids = grid['ids'][keep]
        self.xy = np.column_stack(((grid['latlon'][keep, 1] + 180) / 360,
                                   (90 - grid['latlon'][keep, 0]) / 180))
        self.units = vectors(self.xy)
        self.regions = region_ids(self.xy, self.scenario.region_controllers)
        self.tree = cKDTree(self.units)
        self.phase = phases(self.ids, seed)
        self.query_workers = query_workers
        self.radii = np.array([self.scenario.visibility_radius(type('S', (), {'id': i})())
                               for i in range(config.satellites)])
        self.geometry = np.empty(0)
        self.cell = np.empty(0, dtype=np.uint32)
        self.sat = np.empty(0, dtype=np.uint32)
        self.beam = np.empty(0, dtype=np.uint8)
        self.order = None
        self.choices = None

    def prepare(self, target):
        started = time.monotonic()
        self.target = target
        self.order = self.choices = None
        self.score_key = None
        positions = self.scenario.satellites(target)
        self.sat_xy = np.array([(s.x, s.y) for s in positions])
        sat_units = vectors(self.sat_xy)
        cells, sats, geometry, beams = [], [], [], []
        # Bound the temporary Python neighbor lists to 32 satellites per query.
        for start in range(0, len(positions), 32):
            end = min(start + 32, len(positions))
            chords = 2 * np.sin(self.radii[start:end] * np.pi / 2) + 1e-12
            neighbors = self.tree.query_ball_point(sat_units[start:end], chords, eps=0,
                                                   workers=self.query_workers, return_sorted=False)
            for sid, nearby in enumerate(neighbors, start):
                idx = np.asarray(nearby, dtype=np.uint32)
                if not len(idx):
                    continue
                # Same spherical formula as the scalar reference, after exact
                # Euclidean cap query. The final filter is never approximate.
                lon_c = 2 * np.pi * self.xy[idx, 0] - np.pi
                lat_c = np.pi / 2 - np.pi * self.xy[idx, 1]
                lon_s = 2 * math.pi * positions[sid].x - math.pi
                lat_s = math.pi / 2 - math.pi * positions[sid].y
                cosine = np.sin(lat_c) * math.sin(lat_s) + np.cos(lat_c) * math.cos(lat_s) * np.cos(lon_c - lon_s)
                d = np.arccos(np.clip(cosine, -1, 1)) / np.pi
                visible = d <= self.radii[sid]
                idx, d = idx[visible], d[visible]
                cells.append(idx)
                sats.append(np.full(len(idx), sid, dtype=np.uint32))
                geometry.append(np.maximum(0, 1 - (d / self.radii[sid]) ** 1.55))
                beams.append(beam_ids(self.xy[idx], np.broadcast_to(self.sat_xy[sid], (len(idx), 2))))
        self.cell = np.concatenate(cells) if cells else np.empty(0, dtype=np.uint32)
        self.sat = np.concatenate(sats) if sats else np.empty(0, dtype=np.uint32)
        self.geometry = np.concatenate(geometry) if geometry else np.empty(0)
        self.beam = np.concatenate(beams) if beams else np.empty(0, dtype=np.uint8)
        self.sat_starts = np.searchsorted(self.sat, np.arange(self.c.satellites + 1))
        regional_counts = np.bincount(self.regions[self.cell].astype(np.int64) * self.c.satellites + self.sat,
                                     minlength=self.c.regional_controllers * self.c.satellites)
        return {'edges': len(self.cell), 'visible_cells': len(np.unique(self.cell)),
                'sat_counts': np.bincount(self.sat, minlength=self.c.satellites),
                'regional_counts': regional_counts.reshape(self.c.regional_controllers, self.c.satellites),
                'regional_cells': np.bincount(self.regions, minlength=self.c.regional_controllers),
                'total_demand': float(demands(self.ids, self.phase, self.seed, target).sum()),
                'geometry_seconds': time.monotonic() - started, 'cells': len(self.ids)}

    def scores(self, observation, noise):
        cid = self.ids[self.cell].astype(np.float64)
        residual = .09 * np.sin(.71 * observation + 1.37 * cid + .83 * self.sat + self.seed)
        quality = np.clip(self.geometry + residual, 0, 1)
        jitter = noise * np.sin(3.11 * cid + 5.17 * self.sat + .91 * observation + self.seed)
        demand = demands(self.ids, self.phase, self.seed, observation)[self.cell]
        return .58 * quality + .42 * demand + jitter, quality

    def records(self, index):
        out = np.empty(len(index), dtype=EDGE_DTYPE)
        out['cell'] = self.ids[self.cell[index]]
        out['sat'], out['beam'] = self.sat[index], self.beam[index]
        out['score'], out['quality'] = self.score[index], self.quality[index]
        return out

    def sort(self, observation, noise, region=None):
        if getattr(self, 'score_key', None) != (observation, noise):
            self.score, self.quality = self.scores(observation, noise)
            self.score_key = (observation, noise)
        self.selection_region = region
        self.streams = {}
        self.heap = []
        for sid in range(self.c.satellites):
            if self.sat_starts[sid] != self.sat_starts[sid + 1]:
                self.streams[sid] = {'used': [], 'chunk': deque()}
                self.advance(sid)
        return {}

    def advance(self, sid):
        stream = self.streams[sid]
        if not stream['chunk']:
            start, end = self.sat_starts[sid:sid + 2]
            available = np.ones(end - start, dtype=bool)
            available[np.asarray(stream['used'], dtype=np.int64) - start] = False
            if self.selection_region is not None:
                available &= self.regions[self.cell[start:end]] == self.selection_region
            candidates = np.flatnonzero(available) + start
            if not len(candidates):
                return
            size = min(16, len(candidates))
            if len(candidates) > size:
                values = self.score[candidates]
                threshold = np.partition(values, len(values) - size)[len(values) - size]
                # Include boundary ties before deterministic sorting. Never
                # discard a candidate permanently: refill the exact next block.
                candidates = candidates[values >= threshold]
            order = np.lexsort((self.beam[candidates], self.ids[self.cell[candidates]], -self.score[candidates]))
            selected = candidates[order[:size]]
            stream['used'].extend(selected.tolist())
            stream['chunk'].extend(selected)
        index = int(stream['chunk'].popleft())
        heapq.heappush(self.heap, (-float(self.score[index]), int(self.ids[self.cell[index]]),
                                  sid, int(self.beam[index]), index))

    def page(self, size=4096, remaining=None, chosen=None):
        indexes = []
        chosen = set(map(int, chosen)) if chosen is not None else set()
        while self.heap and len(indexes) < size:
            _score, cid, sid, _beam, index = heapq.heappop(self.heap)
            if remaining is not None and remaining[sid] == 0:
                # Global admission is monotone within a greedy pass. A full
                # satellite can never admit another edge, so discard its stream.
                continue
            if cid not in chosen:
                indexes.append(index)
            self.advance(sid)
        return {'records': self.records(np.asarray(indexes, dtype=np.int64))}

    def proposals(self, observation, noise):
        self.order = None
        self.score, self.quality = self.scores(observation, noise)
        self.score_key = (observation, noise)
        # Only the first proposal_rounds choices can be proposed in this model.
        # This truncation is exact for deferred acceptance, never used for greedy.
        self.streams = self.heap = None
        self.choices = np.full((len(self.ids), self.c.proposal_rounds), -1, dtype=np.int64)
        values = self.score.copy()
        for turn in range(self.c.proposal_rounds):
            best = np.full(len(self.ids), -np.inf)
            np.maximum.at(best, self.cell, values)
            winners = np.flatnonzero((values == best[self.cell]) & np.isfinite(values))
            index = np.full(len(self.ids), len(values), dtype=np.int64)
            # Edges are generated in ascending satellite order, with one edge
            # per (cell,satellite), so index tie-breaking equals (sat,beam).
            np.minimum.at(index, self.cell[winners], winners)
            valid = index != len(values)
            selected = index[valid]
            self.choices[valid, turn] = selected
            values[selected] = -np.inf
        self.active = self.choices[:, 0] >= 0
        return {}

    def propose(self, turn, accepted, remaining=None):
        if turn and len(accepted):
            self.active &= ~np.isin(self.ids, accepted)
        indexes = self.choices[self.active, turn]
        indexes = indexes[indexes >= 0]
        if remaining is not None:
            indexes = indexes[remaining[self.sat[indexes]] != 0]
        return {'records': self.records(indexes)}

    def evaluate(self, records):
        # Evaluate only assigned pairs against current truth, not all candidates
        # four more times. This also checks visibility of carried periodic plans.
        if not len(records):
            return {'served': 0., 'utility': 0.}
        locations = np.searchsorted(self.ids, records['cell'])
        keep = locations < len(self.ids)
        safe = np.minimum(locations, max(0, len(self.ids) - 1))
        if not len(self.ids):
            return {'served': 0., 'utility': 0.}
        keep &= self.ids[safe] == records['cell']
        records, locations = records[keep], locations[keep]
        sid = records['sat']
        cell_xy, sat_xy = self.xy[locations], self.sat_xy[sid]
        lat_c, lat_s = np.pi / 2 - np.pi * cell_xy[:, 1], np.pi / 2 - np.pi * sat_xy[:, 1]
        lon_delta = 2 * np.pi * (cell_xy[:, 0] - sat_xy[:, 0])
        cosine = np.sin(lat_c) * np.sin(lat_s) + np.cos(lat_c) * np.cos(lat_s) * np.cos(lon_delta)
        d = np.arccos(np.clip(cosine, -1, 1)) / np.pi
        visible = (d <= self.radii[sid]) & (beam_ids(cell_xy, sat_xy) == records['beam'])
        geometry = np.maximum(0, 1 - (d / self.radii[sid]) ** 1.55)
        quality = np.clip(geometry + .09 * np.sin(.71 * self.target + 1.37 * records['cell'].astype(float)
                                                 + .83 * sid + self.seed), 0, 1)
        demand = demands(self.ids[locations], self.phase[locations], self.seed, self.target)
        return {'served': float(np.minimum(demand, 1.28 * quality)[visible].sum()),
                'utility': float((.58 * quality + .42 * demand)[visible].sum())}

    def handle(self, command):
        action = command.pop('action')
        if action == 'memory':
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return {'peak_rss_mb': peak / (1024 * 1024 if sys.platform == 'darwin' else 1024)}
        return getattr(self, action)(**command)


def send(stream, message):
    arrays, metadata = {}, {}
    for key, value in message.items():
        if isinstance(value, np.ndarray):
            arrays[key] = value
        else:
            metadata[key] = value
    arrays['_meta'] = np.frombuffer(json.dumps(metadata).encode(), dtype=np.uint8)
    buffer = io.BytesIO()
    np.savez(buffer, **arrays)
    raw = buffer.getvalue()
    stream.write(struct.pack('!Q', len(raw)))
    stream.write(raw)
    stream.flush()


def exact_read(stream, size):
    chunks = []
    while size:
        chunk = stream.read(size)
        if not chunk:
            raise EOFError('Worker connection closed before complete response')
        chunks.append(chunk)
        size -= len(chunk)
    return b''.join(chunks)


def receive(stream):
    size = struct.unpack('!Q', exact_read(stream, 8))[0]
    if size > 1024 * 1024 * 1024:
        raise ValueError('Worker message exceeds 1 GiB limit')
    with np.load(io.BytesIO(exact_read(stream, size)), allow_pickle=False) as data:
        out = json.loads(data['_meta'].tobytes())
        out.update({key: data[key] for key in data.files if key != '_meta'})
    return out


class Remote:
    def __init__(self, host, root, python, init, slurm=False):
        command = shlex.join([python, str(Path(root) / 'harness/coupled.py'), '--worker'])
        args = [python, str(Path(root) / 'harness/coupled.py'), '--worker'] if host == 'local' else [
            'ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
            '-o', 'ServerAliveInterval=20', '-o', 'ServerAliveCountMax=3', host, command]
        if slurm:
            args = ['srun', '--exact', '--exclusive', '--nodes=1', '--ntasks=1',
                    f"--cpus-per-task={init['query_workers']}", f'--nodelist={host}',
                    '--cpu-bind=cores', '--input=0', python, str(Path(root) / 'harness/coupled.py'), '--worker']
        environment = os.environ.copy()
        if slurm:
            # The framed binary protocol needs ordinary pipes. Inherited Slurm
            # I/O overrides must not add labels, redirect frames, or enable a PTY.
            for name in ('SLURM_LABELIO', 'SLURM_UNBUFFEREDIO', 'SLURM_OUTPUT', 'SLURM_ERROR', 'SLURM_INPUT'):
                environment.pop(name, None)
        self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=environment)
        try:
            response = self.call({'action': 'init', **init})
            expected = init.get('expected')
            if expected and any(response[key] != expected[key] for key in ('grid', 'catalog', 'source', 'dependencies')):
                raise ValueError(f'Worker {host} inputs or dependencies differ from coordinator')
        except BaseException:
            self.close()
            raise

    def call(self, message):
        send(self.process.stdin, message)
        response = receive(self.process.stdout)
        if 'error' in response:
            raise RuntimeError(response['error'])
        return response

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=10)


class Local:
    def __init__(self, shard):
        self.shard = shard

    def call(self, command):
        return self.shard.handle(dict(command))

    def close(self):
        pass


def all_call(workers, command):
    with ThreadPoolExecutor(max_workers=len(workers)) as pool:
        return list(pool.map(lambda worker: worker.call(dict(command)), workers))


def edge(row):
    return Edge(int(row['cell']), int(row['sat']), int(row['beam']), float(row['score']), float(row['quality']))


def records(plan):
    out = np.empty(len(plan.assignments), dtype=EDGE_DTYPE)
    for i, e in enumerate(plan.assignments.values()):
        out[i] = (e.cell, e.sat, e.beam, e.score, e.quality)
    return out


def greedy(workers, config, observation, noise, counts, page_size=1024, region=None, capacity=None):
    remaining = np.full(config.satellites, config.beams_per_satellite, dtype=int) if capacity is None else np.array(capacity, dtype=int)
    all_call(workers, {'action': 'sort', 'observation': observation, 'noise': noise, 'region': region})
    request = {'action': 'page', 'size': page_size}
    if capacity is not None:
        request['remaining'] = remaining
    pages = [deque(response['records']) for response in all_call(workers, request)]
    heap = []
    active = (counts != 0) & (remaining > 0)
    chosen = {}
    def push(rank):
        while True:
            if not pages[rank]:
                pages[rank].extend(workers[rank].call({'action': 'page', 'size': page_size,
                                                     'remaining': remaining, 'chosen': np.array(list(chosen), dtype=np.uint32)})['records'])
                if not pages[rank]:
                    return
            row = pages[rank].popleft()
            cid, sid = int(row['cell']), int(row['sat'])
            if cid not in chosen and remaining[sid]:
                heapq.heappush(heap, (-float(row['score']), cid, sid, int(row['beam']), rank, row))
                return
    for rank in range(len(workers)):
        push(rank)
    unfinished = int(np.count_nonzero(active))
    while heap and unfinished:
        _score, cid, sid, _beam, rank, row = heapq.heappop(heap)
        if cid not in chosen and remaining[sid]:
            chosen[cid] = edge(row)
            remaining[sid] -= 1
            unfinished -= remaining[sid] == 0
        push(rank)
    return Plan(chosen)


def regional(workers, config, observation, noise, counts, page_size=1024):
    """Merge each region's streams independently within its satellite quotas.

    Workers are compute shards, not controller regions: geography and quotas
    stay identical when the number or placement of workers changes.
    """
    quotas = regional_quotas(counts.tolist(), config.beams_per_satellite)
    chosen, used = {}, []
    for rid in range(config.regional_controllers):
        plan = greedy(workers, config, observation, noise, counts[rid], page_size,
                      region=rid, capacity=quotas[rid])
        chosen.update(plan.assignments)
        used.append(len(plan.assignments))
    statistics = {'candidate_edges': counts.sum(axis=1).tolist(),
                  'capacity_shares': [sum(row) for row in quotas], 'assigned_cells': used,
                  'unused_capacity_shares': sum(map(sum, quotas)) - sum(used)}
    return Plan(chosen), statistics


def deferred(workers, config, observation):
    all_call(workers, {'action': 'proposals', 'observation': observation, 'noise': .006})
    accepted = {}
    remaining = np.full(config.satellites, config.beams_per_satellite, dtype=int)
    for turn in range(config.proposal_rounds):
        parts = all_call(workers, {'action': 'propose', 'turn': turn,
                                  'accepted': np.array(list(accepted), dtype=np.uint32), 'remaining': remaining})
        proposals = np.concatenate([part['records'] for part in parts])
        if not len(proposals):
            # Proposals to already-full satellites are implicit rejections;
            # cells may still have a feasible next choice in the next round.
            continue
        order = np.lexsort((proposals['beam'], proposals['cell'], -proposals['score'], proposals['sat']))
        ordered = proposals[order]
        starts = np.flatnonzero(np.r_[True, ordered['sat'][1:] != ordered['sat'][:-1]])
        ends = np.r_[starts[1:], len(ordered)]
        for start, end in zip(starts, ends):
            sid = int(ordered[start]['sat'])
            admitted = ordered[start:min(end, start + remaining[sid])]
            for row in admitted:
                accepted[int(row['cell'])] = edge(row)
            remaining[sid] -= len(admitted)
        if not np.any(remaining):
            break
    return Plan(accepted)


def evaluate(workers, config, plan, oracle, total_demand):
    parts = all_call(workers, {'action': 'evaluate', 'records': records(plan)})
    exact, satellite = 0, 0
    for cid, best in oracle.assignments.items():
        picked = plan.assignments.get(cid)
        if picked and picked.sat == best.sat:
            satellite += 1
            exact += picked.beam == best.beam
    comparable = len(oracle.assignments)
    return {'demand_satisfaction': sum(p['served'] for p in parts) / total_demand if total_demand else 1.,
            'utility': sum(p['utility'] for p in parts),
            'pair_accuracy': exact / comparable if comparable else 1.,
            'satellite_accuracy': satellite / comparable if comparable else 1.,
            'outage_ratio': 1 - len(plan.assignments) / config.service_cells}


def initial_state(start_epoch=0):
    return {'start_epoch': start_epoch, 'next_epoch': start_epoch, 'central_plan': None, 'rows': [], 'predecessor': None}


def step(workers, scenario, state, progress=False):
    config = scenario.c
    current = state['next_epoch']
    target = current + config.horizon_epochs
    started = time.monotonic()
    timing = {}
    mark = started
    def measured(name):
        nonlocal mark
        now = time.monotonic()
        timing[name] = now - mark
        mark = now
        if progress:
            print(f'Epoch {current}: {name} {timing[name]:.2f}s', file=sys.stderr, flush=True)
    preparation = all_call(workers, {'action': 'prepare', 'target': target})
    measured('geometry')
    counts = sum((part['sat_counts'] for part in preparation), np.zeros(config.satellites, dtype=np.int64))
    edge_count = sum(part['edges'] for part in preparation)
    total_demand = sum(part['total_demand'] for part in preparation)
    oracle = greedy(workers, config, target, 0, counts)
    measured('oracle')
    fresh = greedy(workers, config, max(0, current - 1), .012, counts)
    measured('centralized')
    regional_counts = sum((part['regional_counts'] for part in preparation),
                          np.zeros((config.regional_controllers, config.satellites), dtype=np.int64))
    regional_plan, regional_stats = regional(workers, config, max(0, current - 1), .012, regional_counts)
    regional_stats['cells'] = sum((part['regional_cells'] for part in preparation),
                                 np.zeros(config.regional_controllers, dtype=np.int64)).tolist()
    measured('regional_centralized')
    refresh = state['central_plan'] is None or current % config.central_cadence_epochs == 0
    periodic = fresh if refresh else Plan({e[0]: Edge(*e) for e in state['central_plan']})
    dist = deferred(workers, config, current)
    measured('distributed')
    plans = dict(zip(NAMES, (fresh, periodic, regional_plan, dist, oracle)))
    systems = {}
    for name, plan in plans.items():
        assert_plan_safe(plan, config)
        metrics = evaluate(workers, config, plan, oracle, total_demand)
        if name != 'oracle':
            if name == 'regional_centralized':
                metrics['latency_ms'], metrics['backhaul_bytes'] = regional_control_cost(
                    scenario, regional_stats['candidate_edges'], regional_stats['assigned_cells'])
            else:
                metrics['latency_ms'] = network_latency_ms(scenario, target, name == 'distributed', edge_count)
                metrics['backhaul_bytes'] = backhaul_bytes(scenario, target, edge_count, name == 'distributed', len(plan.assignments))
            if name == 'centralized_periodic' and not refresh:
                metrics['backhaul_bytes'] = 0
        systems[name] = metrics
    measured('evaluation_and_modeled_latency')
    row = {'current_epoch': current, 'target_epoch': target, 'visible_cells': sum(p['visible_cells'] for p in preparation),
           'candidate_edges': edge_count, 'total_demand': total_demand, 'systems': systems,
           'wall_seconds': time.monotonic() - started,
           'geometry_seconds_by_worker': [part['geometry_seconds'] for part in preparation], 'timing': timing}
    row['regional'] = regional_stats
    carried = [[e.cell, e.sat, e.beam, e.score, e.quality] for e in periodic.assignments.values()]
    # Build the next state before replacing it. An interrupt must not persist
    # an appended metric row with the previous epoch or periodic plan.
    state.update(rows=state['rows'] + [row], central_plan=carried, next_epoch=current + 1)
    return row


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def fingerprint(config, seed, grid_path, snapshot_path, start_epoch=0, calibration=False):
    inputs = {'config': asdict(config), 'seed': seed, 'grid': digest(grid_path), 'catalog': digest(snapshot_path),
        'source': {name: digest(ROOT / name) for name in ('harness/coupled.py', 'harness/grid.py',
                        'src/beam_sim.py', 'harness/requirements-option2.txt', 'requirements.txt', 'harness/tls_transport.py')},
              'start_epoch': start_epoch, 'calibration': calibration,
              'dependencies': {'numpy': np.__version__, 'scipy': scipy.__version__,
                               'h3': h3.__version__, 'sgp4': sgp4.__version__}}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest(), inputs


def save_checkpoint(directory, state, expected):
    body = {'fingerprint': expected, 'state': state}
    checksum = hashlib.sha256(json.dumps(body, sort_keys=True, allow_nan=False).encode()).hexdigest()
    path = directory / f"chunk-{state['next_epoch']:06d}.json"
    atomic_json(path, {**body, 'checksum': checksum})
    atomic_json(directory / 'latest.json', {'file': path.name, 'checksum': checksum})
    state['predecessor'] = checksum


def resume(directory, expected):
    latest = json.loads((directory / 'latest.json').read_text())
    name = latest['file']
    if Path(name).name != name or not name.startswith('chunk-'):
        raise ValueError('Invalid checkpoint filename')
    entry = json.loads((directory / name).read_text())
    body = {key: entry[key] for key in ('fingerprint', 'state')}
    checksum = hashlib.sha256(json.dumps(body, sort_keys=True, allow_nan=False).encode()).hexdigest()
    if checksum != entry['checksum'] or checksum != latest['checksum']:
        raise ValueError('Checkpoint checksum mismatch')
    if entry['fingerprint'] != expected:
        raise ValueError('Checkpoint inputs differ from this run')
    state = entry['state']
    if state['next_epoch'] - state.get('start_epoch', 0) != len(state['rows']):
        raise ValueError('Checkpoint epoch count mismatch')
    # Validate the persisted predecessor, not only the newest file.
    if state['predecessor']:
        candidates = sorted(directory.glob('chunk-*.json'))
        previous = [p for p in candidates if p.name < name]
        if not previous:
            raise ValueError('Missing predecessor checkpoint')
        parent = json.loads(previous[-1].read_text())
        parent_body = {key: parent[key] for key in ('fingerprint', 'state')}
        actual_parent = hashlib.sha256(json.dumps(parent_body, sort_keys=True, allow_nan=False).encode()).hexdigest()
        if actual_parent != state['predecessor'] or actual_parent != parent['checksum']:
            raise ValueError('Predecessor checkpoint checksum mismatch')
    state['predecessor'] = checksum
    return state


def summarize(state, config):
    systems = {}
    for name in NAMES:
        rows = [row['systems'][name] for row in state['rows']]
        metrics = {key: float(np.mean([row[key] for row in rows]))
                   for key in ('demand_satisfaction', 'pair_accuracy', 'satellite_accuracy', 'outage_ratio', 'utility')}
        if name != 'oracle':
            latencies = [row['latency_ms'] for row in rows]
            metrics.update(latency_p50_ms=percentile(latencies, .5), latency_p95_ms=percentile(latencies, .95),
                           deadline_miss_ratio=sum(value > config.deadline_ms for value in latencies) / len(rows),
                           backhaul_mb=sum(row['backhaul_bytes'] for row in rows) / 1e6)
        systems[name] = metrics
    return systems


def worker_main():
    shard = None
    while True:
        try:
            command = receive(sys.stdin.buffer)
        except EOFError:
            return
        try:
            if command.get('action') == 'init':
                config = Config(**command['config'])
                shard = Shard(config, command['seed'], load_orbital_snapshot(Path(command['snapshot'])),
                              load(command['grid']), command['rank'], command['workers'], command['query_workers'])
                response = {'ready': True, 'source': {name: digest(ROOT / name) for name in command['expected']['source']}, 'grid': digest(command['grid']),
                            'catalog': digest(command['snapshot']),
                            'dependencies': {'numpy': np.__version__, 'scipy': scipy.__version__,
                                         'h3': h3.__version__, 'sgp4': sgp4.__version__}}
            else:
                response = shard.handle(command)
            send(sys.stdout.buffer, response)
        except Exception as error:
            send(sys.stdout.buffer, {'error': f'{type(error).__name__}: {error}'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--grid', type=Path)
    parser.add_argument('--snapshot', type=Path, default=ROOT / 'data/orbital_snapshot_full.json')
    parser.add_argument('--provider', default='starlink', choices=('starlink', 'kuiper'))
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--epochs', type=int, default=1200)
    parser.add_argument('--start-epoch', type=int, default=0,
                        help='Calibration window start; nonzero windows reset periodic history and are not complete trajectories')
    parser.add_argument('--chunk-epochs', type=int, default=120)
    parser.add_argument('--checkpoint-epochs', type=int, default=1,
                        help='Durable epoch interval; logical 10-minute chunks remain 120 epochs')
    parser.add_argument('--stop-after', type=int, help='Stop safely after this many additional epochs for calibration')
    parser.add_argument('--wall-seconds', type=int, default=43200,
                        help='Stop at a completed-epoch boundary after this wall budget (0 disables)')
    parser.add_argument('--query-workers', type=int, default=1)
    parser.add_argument('--regional-controllers', type=int, default=Config.regional_controllers)
    parser.add_argument('--local-workers', type=int, default=1)
    parser.add_argument('--hosts', nargs='+', help='SSH aliases; use the installed ~/.ssh/config identities')
    parser.add_argument('--tls-config', type=Path, help='Mutually authenticated TLS workers for an allocation-local coordinator')
    parser.add_argument('--slurm', action='store_true', help='Use srun on --hosts inside an existing SLURM allocation')
    parser.add_argument('--remote-root', default='/users/ketanbj/bratos-option2')
    parser.add_argument('--remote-python', default='/users/ketanbj/bratos-option2/.venv/bin/python')
    parser.add_argument('--directory', type=Path, default=ROOT / 'harness/state/option2')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--calibration', action='store_true', help='Explicitly allow an unverified domain for performance testing')
    args = parser.parse_args()
    if args.worker:
        worker_main()
        return
    if not args.grid:
        parser.error('--grid is required')
    if args.slurm and (not args.hosts or not os.environ.get('SLURM_JOB_ID')):
        parser.error('--slurm requires --hosts and an active SLURM allocation')
    if min(args.epochs, args.chunk_epochs, args.checkpoint_epochs, args.query_workers, args.local_workers) < 1 or (args.stop_after is not None and args.stop_after < 1):
        parser.error('Epoch, chunk, and worker counts must be positive')
    if not 0 <= args.start_epoch < args.epochs or (args.start_epoch and not args.calibration):
        parser.error('Nonzero --start-epoch requires calibration and must precede --epochs')
    if args.wall_seconds < 0:
        parser.error('--wall-seconds must be nonnegative')
    if not 1 <= args.regional_controllers <= Config.ground_stations:
        parser.error('--regional-controllers must be between 1 and 12')
    grid = load(args.grid)
    metadata = grid['metadata']
    if not args.calibration and (metadata.get('production_ready') is not True or metadata['resolution'] != 5
                                or args.epochs != 1200 or metadata.get('domain') not in ('commercial-union', 'global-study')):
        raise ValueError('Option 2 production requires a verified commercial-union or explicit global-study res5 grid and all 1,200 epochs; use --calibration only for validation')
    if metadata.get('domain') == 'global-study' and len(grid['ids']) != h3.get_num_cells(metadata['resolution']):
        raise ValueError('A global study requires the complete Earth grid')
    snapshot = load_orbital_snapshot(args.snapshot)
    config = Config(provider=args.provider, satellites=resolve_satellite_count('all', args.provider, snapshot),
                    service_cells=len(grid['ids']), h3_resolution=metadata['resolution'], epochs=args.epochs,
                    regional_controllers=args.regional_controllers)
    expected, inputs = fingerprint(config, args.seed, args.grid, args.snapshot, args.start_epoch, args.calibration)
    args.directory.mkdir(parents=True, exist_ok=True)
    if args.resume:
        state = resume(args.directory, expected)
    else:
        if (args.directory / 'latest.json').exists():
            raise ValueError('Run directory has checkpoints; use --resume or a new directory')
        state = initial_state(args.start_epoch)
    tls_config = json.loads(args.tls_config.read_text()) if args.tls_config else None
    if tls_config and (args.hosts or args.slurm):
        parser.error('TLS worker configuration cannot be combined with SSH/SLURM hosts')
    manifest = {'fingerprint': expected, 'inputs': inputs, 'grid_metadata': metadata,
                'calibration': args.calibration, 'workers': len(tls_config['endpoints']) if tls_config else args.hosts or args.local_workers,
                'model': 'architecture-level synthetic demand/RF/control model; four cell slots per satellite',
                'phase_scheme': 'SplitMix64(master-cell ordinal XOR seed); differs from legacy sample RNG',
                'simulated_seconds': config.epochs * config.epoch_seconds,
                'chunk_epochs': args.chunk_epochs, 'checkpoint_epochs': args.checkpoint_epochs,
                'state': 'running'}
    workers = []
    scenario = GridScenario(config, args.seed, snapshot)
    manifest['regional_controller_model'] = regional_topology(scenario)
    atomic_json(args.directory / 'run.json', manifest)
    wall_start = time.monotonic()
    count = len(tls_config['endpoints']) if tls_config else len(args.hosts) if args.hosts else args.local_workers
    try:
        for rank in range(count):
            root = args.remote_root if args.hosts and not args.slurm else str(ROOT)
            init = {'config': asdict(config), 'seed': args.seed, 'rank': rank, 'workers': count,
                    'query_workers': args.query_workers, 'expected': inputs,
                    'grid': str(Path(root) / 'data/grid.npz') if args.hosts and not args.slurm else str(args.grid.resolve()),
                    'snapshot': str(Path(root) / 'data/orbital_snapshot_full.json') if args.hosts and not args.slurm else str(args.snapshot.resolve())}
            if tls_config:
                from harness.tls_transport import TLSRemote
                workers.append(TLSRemote(tls_config['endpoints'][rank], tls_config, init))
            else:
                workers.append(Remote(args.hosts[rank] if args.hosts else 'local', root,
                                      args.remote_python if args.hosts and not args.slurm else sys.executable, init, slurm=args.slurm))
            # Verify all computational inputs before the first epoch.
            response = workers[-1].call({'action': 'memory'})
            print(f'Worker {rank} ready: {response}', flush=True)
        # Worker init includes hashes; the Remote wrapper verifies them below.
        end = min(config.epochs, state['next_epoch'] + args.stop_after) if args.stop_after else config.epochs
        while state['next_epoch'] < end:
            if args.wall_seconds and time.monotonic() - wall_start >= args.wall_seconds:
                print('Wall budget reached; preserving completed-epoch checkpoint', flush=True)
                if state['rows'] and not (args.directory / f"chunk-{state['next_epoch']:06d}.json").exists():
                    save_checkpoint(args.directory, state, expected)
                break
            row = step(workers, scenario, state, progress=True)
            print(json.dumps({key: row[key] for key in ('current_epoch', 'visible_cells', 'candidate_edges', 'wall_seconds')}), flush=True)
            if state['next_epoch'] % args.checkpoint_epochs == 0 or state['next_epoch'] % args.chunk_epochs == 0 or state['next_epoch'] == end:
                save_checkpoint(args.directory, state, expected)
        manifest.update(state='completed' if state['next_epoch'] == config.epochs and not state['start_epoch'] else 'checkpointed',
                        completed_epochs=len(state['rows']), next_epoch=state['next_epoch'],
                        memory_by_worker=all_call(workers, {'action': 'memory'}))
        if state['rows']:
            atomic_json(args.directory / 'results.json', {'config': asdict(config), 'seed': args.seed,
                        'completed_epochs': len(state['rows']), 'start_epoch': state['start_epoch'],
                        'next_epoch': state['next_epoch'], 'calibration': args.calibration,
                        'systems': summarize(state, config), 'grid': metadata,
                        'wall_seconds': sum(row['wall_seconds'] for row in state['rows'])})
    except BaseException as error:
        # Only completed epochs enter the checkpoint. Mid-epoch failures replay
        # that epoch with the preceding periodic plan, preserving semantics.
        if state['rows'] and not (args.directory / f"chunk-{state['next_epoch']:06d}.json").exists():
            save_checkpoint(args.directory, state, expected)
        manifest.update(state='interrupted', error=f'{type(error).__name__}: {error}', completed_epochs=len(state['rows']))
        raise
    finally:
        atomic_json(args.directory / 'run.json', manifest)
        for worker in workers:
            worker.close()


if __name__ == '__main__':
    main()
