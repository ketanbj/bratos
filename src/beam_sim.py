#!/usr/bin/env python3
"""Deterministic architecture-level benchmark for Kratos beam prediction.

This is deliberately a control-plane model, not a link-level simulator. Public
GP mean elements drive SGP4 ground-track propagation; demand, RF quality,
latency, and backhaul remain modeled. The benchmark compares fresh and periodic
global controllers, regional controllers, and K-DBP's rolling per-satellite
replica groups.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import math
import os
import random
import statistics
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import h3
import sgp4
from sgp4.api import Satrec, jday
from sgp4.omm import initialize as initialize_omm


@dataclass(frozen=True)
class Config:
    epochs: int = 1200
    epoch_seconds: int = 5
    horizon_epochs: int = 2
    service_cells: int = 240
    h3_resolution: int = 5
    satellites: int = 96
    ground_stations: int = 12
    regional_controllers: int = 6
    beams_per_satellite: int = 4
    replica_group_size: int = 5
    min_elevation_degrees: float = 10.0
    central_cadence_epochs: int = 3
    proposal_rounds: int = 3
    deadline_ms: float = 250.0
    provider: str = "starlink"


TRACE_FRAMES = 100


@dataclass(frozen=True)
class Point:
    id: int
    x: float
    y: float


@dataclass(frozen=True)
class ServiceCell(Point):
    h3_index: str


@dataclass(frozen=True)
class OrbitRecord:
    name: str
    norad_catalog_id: int
    epoch: dt.datetime
    mean_motion_rev_day: float
    eccentricity: float
    inclination_deg: float
    raan_deg: float
    arg_pericenter_deg: float
    mean_anomaly_deg: float
    derived_altitude_km: float
    shell_id: str
    satrec: Satrec


@dataclass(frozen=True)
class Edge:
    cell: int
    sat: int
    beam: int
    score: float
    quality: float


@dataclass
class Plan:
    assignments: dict[int, Edge]


def distance(a: Point, b: Point) -> float:
    """Great-circle distance normalized to half the Earth's circumference."""
    lon_a, lon_b = 2 * math.pi * a.x - math.pi, 2 * math.pi * b.x - math.pi
    lat_a, lat_b = math.pi / 2 - math.pi * a.y, math.pi / 2 - math.pi * b.y
    cosine = math.sin(lat_a) * math.sin(lat_b) + math.cos(lat_a) * math.cos(lat_b) * math.cos(lon_a - lon_b)
    return math.acos(max(-1.0, min(1.0, cosine))) / math.pi


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - pos) + ordered[hi] * (pos - lo)


def parse_timestamp(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


@lru_cache(maxsize=None)
def h3_service_cells(resolution: int, count: int) -> tuple[ServiceCell, ...]:
    """Return a deterministic, approximately equal-area global H3 sample."""
    if not 0 <= resolution <= 15:
        raise ValueError("H3 resolution must be between 0 and 15")
    if not 0 < count <= h3.get_num_cells(resolution):
        raise ValueError(f"service_cells must be between 1 and {h3.get_num_cells(resolution):,}")

    # Fibonacci-sphere points avoid the polar crowding of a latitude/longitude
    # grid. Each point is snapped to its containing H3 cell; increase the
    # candidate pool only if two points land in the same cell.
    candidate_count = max(256, count * 2)
    while True:
        indexes: list[str] = []
        seen: set[str] = set()
        golden_angle = math.pi * (3.0 - math.sqrt(5.0))
        for i in range(candidate_count):
            z = 1.0 - 2.0 * (i + 0.5) / candidate_count
            latitude = math.degrees(math.asin(z))
            longitude = math.degrees((i * golden_angle + math.pi) % (2 * math.pi) - math.pi)
            index = h3.latlng_to_cell(latitude, longitude, resolution)
            if index not in seen:
                seen.add(index)
                indexes.append(index)
        if len(indexes) >= count:
            indexes = [indexes[math.floor((i + 0.5) * len(indexes) / count)] for i in range(count)]
            break
        candidate_count *= 2

    cells = []
    for cell_id, index in enumerate(indexes):
        latitude, longitude = h3.cell_to_latlng(index)
        cells.append(
            ServiceCell(
                id=cell_id,
                x=(longitude + 180.0) / 360.0,
                y=(90.0 - latitude) / 180.0,
                h3_index=index,
            )
        )
    return tuple(cells)


def orbital_snapshot_path() -> Path:
    root = Path(__file__).resolve().parents[1]
    source = Path(os.environ.get("ORBITAL_SNAPSHOT", "data/orbital_snapshot.json"))
    return source if source.is_absolute() else root / source


def load_orbital_snapshot(path: Path | None = None) -> dict:
    source = path or orbital_snapshot_path()
    return json.loads(source.read_text())


def resolve_satellite_count(requested: str | int, provider: str, snapshot: dict) -> int:
    catalog = snapshot["providers"][provider]
    orbits = catalog["sampled_orbits"]
    if str(requested) == "all":
        if catalog.get("selection") != "all" or len(orbits) != catalog["records_available"]:
            raise ValueError(f"{provider}: SATELLITES=all requires a full catalog, not a sampled snapshot")
        if len({orbit['norad_catalog_id'] for orbit in orbits}) != len(orbits):
            raise ValueError(f"{provider}: full catalog contains duplicate satellite IDs")
        count = len(orbits)
    else:
        count = int(requested)
    if not 0 < count <= len(orbits):
        raise ValueError(f"{provider}: satellite count must be between 1 and {len(orbits)}")
    return count


def orbit_record(raw: dict) -> OrbitRecord:
    satrec = Satrec()
    initialize_omm(satrec, raw["omm"])
    return OrbitRecord(
        name=raw["name"],
        norad_catalog_id=int(raw["norad_catalog_id"]),
        epoch=parse_timestamp(raw["epoch"]),
        mean_motion_rev_day=float(raw["mean_motion_rev_day"]),
        eccentricity=float(raw["eccentricity"]),
        inclination_deg=float(raw["inclination_deg"]),
        raan_deg=float(raw["raan_deg"]),
        arg_pericenter_deg=float(raw["arg_pericenter_deg"]),
        mean_anomaly_deg=float(raw["mean_anomaly_deg"]),
        derived_altitude_km=float(raw["derived_altitude_km"]),
        shell_id=raw["shell_id"],
        satrec=satrec,
    )


def propagate_orbit(sid: int, orbit: OrbitRecord, timestamp: dt.datetime) -> Point:
    """Propagate an OMM record with SGP4 and return its Earth-fixed ground point."""
    seconds = timestamp.second + timestamp.microsecond / 1_000_000
    julian_date, fraction = jday(
        timestamp.year,
        timestamp.month,
        timestamp.day,
        timestamp.hour,
        timestamp.minute,
        seconds,
    )
    error, position_teme, _velocity_teme = orbit.satrec.sgp4(julian_date, fraction)
    if error:
        raise ValueError(f"SGP4 propagation failed for {orbit.name} with error code {error}")

    gmst = math.radians((280.46061837 + 360.98564736629 * (julian_date + fraction - 2451545.0)) % 360)
    x_teme, y_teme, z_teme = position_teme
    x_ecef = math.cos(gmst) * x_teme + math.sin(gmst) * y_teme
    y_ecef = -math.sin(gmst) * x_teme + math.cos(gmst) * y_teme
    longitude = math.atan2(y_ecef, x_ecef)

    # Convert WGS-84 ECEF coordinates to geodetic latitude. Polar motion is
    # intentionally omitted because this replay only needs cell-level tracks.
    semi_major_axis_km = 6378.137
    eccentricity_squared = 6.69437999014e-3
    horizontal = math.hypot(x_ecef, y_ecef)
    latitude = math.atan2(z_teme, horizontal * (1 - eccentricity_squared))
    for _ in range(5):
        prime_vertical = semi_major_axis_km / math.sqrt(
            1 - eccentricity_squared * math.sin(latitude) ** 2
        )
        latitude = math.atan2(z_teme + eccentricity_squared * prime_vertical * math.sin(latitude), horizontal)
    return Point(sid, (longitude + math.pi) / (2 * math.pi), (math.pi / 2 - latitude) / math.pi)


class Scenario:
    def __init__(self, config: Config, seed: int, snapshot: dict | None = None):
        self.c = config
        self.seed = seed
        self.rng = random.Random(seed)
        self.snapshot = snapshot or load_orbital_snapshot()
        if config.provider not in self.snapshot["providers"]:
            raise ValueError(f"unknown orbital provider: {config.provider}")
        self.provider = self.snapshot["providers"][config.provider]
        self.reference_time = parse_timestamp(self.snapshot["generated_at"])
        self.orbits = self._select_orbits()
        self.cells = self._make_cells()
        self.ground_stations = self._make_ground_stations()
        if not 1 <= config.regional_controllers <= config.ground_stations:
            raise ValueError("regional_controllers must be between 1 and ground_stations")
        self.region_controllers = [self.ground_stations[i * config.ground_stations // config.regional_controllers]
                                   for i in range(config.regional_controllers)]
        self.region_members = [[] for _ in self.region_controllers]
        for gs in self.ground_stations:
            self.region_members[region_for(gs, self.region_controllers)].append(gs)
        self.cell_regions = {cell.id: region_for(cell, self.region_controllers) for cell in self.cells}
        self.cell_phase = [self.rng.random() for _ in self.cells]

    def _select_orbits(self) -> list[OrbitRecord]:
        raw = self.provider["sampled_orbits"]
        if self.c.satellites > len(raw):
            raise ValueError(f"requested {self.c.satellites} satellites, snapshot contains {len(raw)}")
        if self.c.satellites == len(raw):
            # Full-catalog IDs remain stable across demand/noise seeds.
            return [orbit_record(record) for record in raw]
        offset = self.seed % len(raw)
        indices = [math.floor((i + 0.5) * len(raw) / self.c.satellites + offset) % len(raw) for i in range(self.c.satellites)]
        return [orbit_record(raw[index]) for index in indices]

    def _make_cells(self) -> list[Point]:
        return list(h3_service_cells(self.c.h3_resolution, self.c.service_cells))

    def _make_ground_stations(self) -> list[Point]:
        # A deterministic geodistributed mesh; offsets avoid exact cell overlap.
        return [
            Point(i, ((i * 0.381966 + 0.07) % 1.0), 0.16 + 0.68 * (((i * 5) % self.c.ground_stations) + 0.5) / self.c.ground_stations)
            for i in range(self.c.ground_stations)
        ]

    def satellites(self, epoch: int) -> list[Point]:
        timestamp = self.reference_time + dt.timedelta(seconds=epoch * self.c.epoch_seconds)
        return [propagate_orbit(sid, orbit, timestamp) for sid, orbit in enumerate(self.orbits)]

    def visibility_radius(self, sat: Point) -> float:
        earth_radius_km = 6378.137
        altitude_km = self.orbits[sat.id].derived_altitude_km
        elevation = math.radians(self.c.min_elevation_degrees)
        central_angle = math.acos(earth_radius_km / (earth_radius_km + altitude_km) * math.cos(elevation)) - elevation
        return central_angle / math.pi

    def demand(self, cell: int, epoch: int) -> float:
        base = 0.50 + 0.28 * math.sin(2 * math.pi * (epoch / 31.0 + self.cell_phase[cell]))
        burst_center = (cell * 7 + self.seed) % 43
        burst = 0.27 if min((epoch - burst_center) % 43, (burst_center - epoch) % 43) <= 2 else 0.0
        return max(0.10, min(1.0, base + burst))

    def channel_residual(self, cell: int, sat: int, epoch: int) -> float:
        return 0.09 * math.sin(0.71 * epoch + 1.37 * cell + 0.83 * sat + self.seed)

    def beam_id(self, cell: Point, sat: Point) -> int:
        dx = cell.x - sat.x
        if dx > 0.5:
            dx -= 1.0
        elif dx < -0.5:
            dx += 1.0
        dy = cell.y - sat.y
        if math.hypot(dx, dy) < 0.075:
            return 0
        angle = (math.atan2(dy, dx) + 2 * math.pi) % (2 * math.pi)
        return 1 + int((angle + math.pi / 6) // (math.pi / 3)) % 6

    def candidate_edges(self, epoch: int, observed_epoch: int | None = None, noise: float = 0.0) -> list[Edge]:
        sats = self.satellites(epoch)
        observation = epoch if observed_epoch is None else observed_epoch
        edges: list[Edge] = []
        for cell in self.cells:
            demand = self.demand(cell.id, observation)
            for sat in sats:
                d = distance(cell, sat)
                visibility_radius = self.visibility_radius(sat)
                if d > visibility_radius:
                    continue
                geometry = max(0.0, 1.0 - (d / visibility_radius) ** 1.55)
                residual = self.channel_residual(cell.id, sat.id, observation)
                quality = max(0.0, min(1.0, geometry + residual))
                jitter = noise * math.sin(3.11 * cell.id + 5.17 * sat.id + 0.91 * observation + self.seed)
                score = 0.58 * quality + 0.42 * demand + jitter
                edges.append(Edge(cell.id, sat.id, self.beam_id(cell, sat), score, quality))
        return edges

    def replica_group(self, sat: Point) -> list[int]:
        ranked = sorted(self.ground_stations, key=lambda gs: (distance(gs, sat), gs.id))
        return [gs.id for gs in ranked[: self.c.replica_group_size]]


def region_for(point: Point, controllers: list[Point]) -> int:
    """Static spherical Voronoi ownership, with deterministic boundary ties."""
    return min(range(len(controllers)), key=lambda r: (round(distance(point, controllers[r]), 12), r))


def regional_quotas(counts: list[list[int]], capacity: int) -> list[list[int]]:
    """Largest-remainder shares of one satellite budget, using only visibility.

    Rows are regions; columns are satellites. Unused shares are not borrowed
    during the epoch. No raw scores are needed by this modeled budget broker.
    """
    if not counts or capacity < 1 or any(len(row) != len(counts[0]) for row in counts):
        raise ValueError("Regional counts require equal rows and positive satellite capacity")
    if any(value < 0 or int(value) != value for row in counts for value in row):
        raise ValueError("Visible candidate counts must be nonnegative integers")
    quotas = [[0] * len(counts[0]) for _ in counts]
    for sid in range(len(counts[0])):
        total = sum(row[sid] for row in counts)
        if not total:
            continue
        slots = min(capacity, total)
        for rid, row in enumerate(counts):
            quotas[rid][sid] = slots * row[sid] // total
        remaining = slots - sum(row[sid] for row in quotas)
        ranking = sorted(range(len(counts)), key=lambda rid: (-(slots * counts[rid][sid] % total), rid))
        for rid in ranking[:remaining]:
            quotas[rid][sid] += 1
    return quotas


def regional_greedy(edges: Iterable[Edge], config: Config, cell_regions: dict[int, int]) -> Plan:
    """One score-sorted controller per region, constrained by shared quotas."""
    parts = [[] for _ in range(config.regional_controllers)]
    counts = [[0] * config.satellites for _ in parts]
    for candidate in edges:
        rid = cell_regions[candidate.cell]
        if not 0 <= rid < len(parts):
            raise ValueError("Cell has an invalid regional owner")
        parts[rid].append(candidate)
        counts[rid][candidate.sat] += 1
    quotas = regional_quotas(counts, config.beams_per_satellite)
    chosen = {}
    for rid, candidates in enumerate(parts):
        remaining = quotas[rid][:]
        for candidate in sorted(candidates, key=lambda e: (-e.score, e.cell, e.sat, e.beam)):
            if candidate.cell not in chosen and remaining[candidate.sat]:
                chosen[candidate.cell] = candidate
                remaining[candidate.sat] -= 1
    return Plan(chosen)


def global_greedy(edges: Iterable[Edge], config: Config) -> Plan:
    remaining = {sid: config.beams_per_satellite for sid in range(config.satellites)}
    chosen: dict[int, Edge] = {}
    for edge in sorted(edges, key=lambda e: (-e.score, e.cell, e.sat, e.beam)):
        if edge.cell in chosen or remaining[edge.sat] == 0:
            continue
        chosen[edge.cell] = edge
        remaining[edge.sat] -= 1
    return Plan(chosen)


def distributed_deferred_acceptance(edges: Iterable[Edge], config: Config) -> Plan:
    """Cell proposals plus linearizable per-satellite capacity admission."""
    by_cell: dict[int, list[Edge]] = {}
    for edge in edges:
        by_cell.setdefault(edge.cell, []).append(edge)
    for candidates in by_cell.values():
        candidates.sort(key=lambda e: (-e.score, e.sat, e.beam))

    accepted: dict[int, Edge] = {}
    next_choice = {cid: 0 for cid in by_cell}
    remaining = {sid: config.beams_per_satellite for sid in range(config.satellites)}
    active = set(by_cell)

    for _ in range(config.proposal_rounds):
        proposals: dict[int, list[Edge]] = {}
        for cid in sorted(active):
            choices = by_cell[cid]
            idx = next_choice[cid]
            if idx < len(choices):
                proposals.setdefault(choices[idx].sat, []).append(choices[idx])
                next_choice[cid] += 1
        next_active: set[int] = set()
        for sid, sat_proposals in sorted(proposals.items()):
            sat_proposals.sort(key=lambda e: (-e.score, e.cell, e.beam))
            slots = remaining[sid]
            for edge in sat_proposals[:slots]:
                accepted[edge.cell] = edge
                remaining[sid] -= 1
            for edge in sat_proposals[slots:]:
                if next_choice[edge.cell] < len(by_cell[edge.cell]):
                    next_active.add(edge.cell)
        active = next_active
        if not active:
            break
    return Plan(accepted)


def assert_plan_safe(plan: Plan, config: Config) -> None:
    counts: dict[int, int] = {}
    for cid, edge in plan.assignments.items():
        assert cid == edge.cell
        counts[edge.sat] = counts.get(edge.sat, 0) + 1
    assert all(v <= config.beams_per_satellite for v in counts.values())


def evaluate_plan(scenario: Scenario, plan: Plan, oracle: Plan, epoch: int) -> dict[str, float]:
    truth = {(e.cell, e.sat, e.beam): e for e in scenario.candidate_edges(epoch)}
    total_demand = sum(scenario.demand(c.id, epoch) for c in scenario.cells)
    served = 0.0
    exact = 0
    sat_exact = 0
    comparable = 0
    utility = 0.0
    for cid, edge in plan.assignments.items():
        actual = truth.get((cid, edge.sat, edge.beam))
        if actual is None:
            continue
        demand = scenario.demand(cid, epoch)
        rate = 1.28 * actual.quality
        served += min(demand, rate)
        utility += actual.score
    for cid, best in oracle.assignments.items():
        comparable += 1
        picked = plan.assignments.get(cid)
        if picked and picked.sat == best.sat:
            sat_exact += 1
            if picked.beam == best.beam:
                exact += 1
    return {
        "demand_satisfaction": served / total_demand if total_demand else 1.0,
        "pair_accuracy": exact / comparable if comparable else 1.0,
        "satellite_accuracy": sat_exact / comparable if comparable else 1.0,
        "outage_ratio": 1.0 - len(plan.assignments) / len(scenario.cells),
        "utility": utility,
    }


def network_latency_ms(scenario: Scenario, epoch: int, distributed: bool, edge_count: int) -> float:
    sats = scenario.satellites(epoch)
    if not distributed:
        controller = scenario.ground_stations[0]
        rtts = [18.0 + 360.0 * distance(controller, gs) for gs in scenario.ground_stations]
        compute = 0.018 * edge_count * math.log2(max(2, edge_count))
        return percentile(rtts, 0.90) + compute + 9.0

    consensus_rtts = []
    for sat in sats:
        group = scenario.replica_group(sat)
        members = [scenario.ground_stations[i] for i in group]
        pair_rtts = [12.0 + 150.0 * distance(a, b) for i, a in enumerate(members) for b in members[i + 1 :]]
        consensus_rtts.append(percentile(pair_rtts, 0.60))
    local_compute = 0.010 * edge_count * math.log2(max(2, edge_count)) / scenario.c.satellites
    return 2.0 * percentile(consensus_rtts, 0.90) + local_compute + 6.0


def backhaul_bytes(scenario: Scenario, epoch: int, edge_count: int, distributed: bool, assignments: int) -> int:
    if not distributed:
        # Raw per-edge measurement plus global plan dissemination.
        return edge_count * 320 + assignments * 96 * scenario.c.ground_stations
    # Aggregated score reports replicated inside bounded groups plus committed plans.
    replicas = max(0, scenario.c.replica_group_size - 1)
    return edge_count * 40 * replicas + assignments * 96 * replicas


def regional_control_cost(scenario: Scenario, edge_counts: list[int], assigned_counts: list[int]) -> tuple[float, int]:
    """Modeled critical-path regional control latency and total WAN bytes.

    Same raw report size and scoring coefficient as global centralized. Add
    counts/quota exchange with the budget broker at GS 0; plans stay regional.
    These costs are formulas, not measured network traffic or execution time.
    """
    if scenario.c.regional_controllers == 1:
        return (network_latency_ms(scenario, 0, False, sum(edge_counts)),
                backhaul_bytes(scenario, 0, sum(edge_counts), False, sum(assigned_counts)))
    broker = scenario.ground_stations[0]
    latencies = []
    dissemination = 0
    for rid, controller in enumerate(scenario.region_controllers):
        members = scenario.region_members[rid]
        rtts = [18.0 + 360.0 * distance(controller, gs) for gs in members]
        broker_rtt = 0. if controller.id == broker.id else 18.0 + 360.0 * distance(controller, broker)
        compute = .018 * edge_counts[rid] * math.log2(max(2, edge_counts[rid]))
        latencies.append(percentile(rtts, .90) + broker_rtt + compute + 9.)
        dissemination += assigned_counts[rid] * 96 * len(members)
    # 8-byte visible count and 4-byte returned quota per region/satellite.
    quota_exchange = sum(controller.id != broker.id for controller in scenario.region_controllers) * scenario.c.satellites * 12
    return max(latencies), sum(edge_counts) * 320 + dissemination + quota_exchange


def regional_topology(scenario: Scenario) -> dict:
    return {"partition": "nearest fixed regional controller by spherical distance; ties rounded to 12 decimals",
            "capacity_policy": "visibility-proportional largest-remainder satellite quotas; no within-epoch borrowing",
            "score_observation": "same observation age and noise as global centralized",
            "controllers": [{"region": rid, "ground_station": controller.id,
                             "x": controller.x, "y": controller.y,
                             "member_ground_stations": [gs.id for gs in scenario.region_members[rid]]}
                            for rid, controller in enumerate(scenario.region_controllers)]}


def serialize_trace_epoch(
    scenario: Scenario,
    epoch: int,
    distributed: Plan,
    centralized: Plan,
    dist_eval: dict[str, float],
    central_eval: dict[str, float],
    dist_latency: float,
    central_latency: float,
) -> dict:
    sats = scenario.satellites(epoch)
    return {
        "epoch": epoch,
        "seconds": epoch * scenario.c.epoch_seconds,
        "satellites": [
            {
                "id": s.id,
                "x": round(s.x, 4),
                "y": round(s.y, 4),
                "group": scenario.replica_group(s),
            }
            for s in sats
        ],
        "distributed": [
            {"cell": c, "sat": e.sat, "beam": e.beam} for c, e in sorted(distributed.assignments.items())
        ],
        "centralized": [
            {"cell": c, "sat": e.sat, "beam": e.beam} for c, e in sorted(centralized.assignments.items())
        ],
        "metrics": {
            "distributed": {**{k: round(v, 4) for k, v in dist_eval.items()}, "latency_ms": round(dist_latency, 2)},
            "centralized": {**{k: round(v, 4) for k, v in central_eval.items()}, "latency_ms": round(central_latency, 2)},
        },
    }


def run_seed(config: Config, seed: int, include_trace: bool = False) -> dict:
    scenario = Scenario(config, seed)
    metric_rows = {name: [] for name in ("centralized", "centralized_periodic", "regional_centralized", "distributed", "oracle")}
    latencies = {name: [] for name in metric_rows if name != "oracle"}
    backhaul = {name: 0 for name in latencies}
    deadline_misses = {name: 0 for name in latencies}
    trace = []
    trace_stride = max(1, config.epochs // TRACE_FRAMES)
    central_plan: Plan | None = None

    for current in range(config.epochs):
        target = current + config.horizon_epochs
        truth_edges = scenario.candidate_edges(target)
        oracle = global_greedy(truth_edges, config)

        observed = max(0, current - 1)
        central_edges = scenario.candidate_edges(target, observed_epoch=observed, noise=0.012)
        fresh_central_plan = global_greedy(central_edges, config)
        regional_plan = regional_greedy(central_edges, config, scenario.cell_regions)
        central_refresh = central_plan is None or current % config.central_cadence_epochs == 0
        if central_refresh:
            # Geometry is propagated to the target, but demand and residual telemetry
            # are from the previous 15-second collection boundary.
            central_plan = global_greedy(central_edges, config)

        # Each member forecasts locally. Median aggregation is represented by lower
        # observation noise than a single report, while telemetry remains current.
        dist_edges = scenario.candidate_edges(target, observed_epoch=current, noise=0.006)
        dist_plan = distributed_deferred_acceptance(dist_edges, config)
        assert_plan_safe(fresh_central_plan, config)
        assert_plan_safe(regional_plan, config)
        assert_plan_safe(central_plan, config)
        assert_plan_safe(dist_plan, config)

        fresh_central_eval = evaluate_plan(scenario, fresh_central_plan, oracle, target)
        periodic_central_eval = evaluate_plan(scenario, central_plan, oracle, target)
        dist_eval = evaluate_plan(scenario, dist_plan, oracle, target)
        oracle_eval = evaluate_plan(scenario, oracle, oracle, target)
        metric_rows["centralized"].append(fresh_central_eval)
        metric_rows["centralized_periodic"].append(periodic_central_eval)
        metric_rows["regional_centralized"].append(evaluate_plan(scenario, regional_plan, oracle, target))
        metric_rows["distributed"].append(dist_eval)
        metric_rows["oracle"].append(oracle_eval)

        central_latency = network_latency_ms(scenario, target, False, len(truth_edges))
        dist_latency = network_latency_ms(scenario, target, True, len(truth_edges))
        regional_edges = [0] * config.regional_controllers
        regional_assignments = [0] * config.regional_controllers
        for candidate in central_edges:
            regional_edges[scenario.cell_regions[candidate.cell]] += 1
        for cid in regional_plan.assignments:
            regional_assignments[scenario.cell_regions[cid]] += 1
        regional_latency, regional_bytes = regional_control_cost(scenario, regional_edges, regional_assignments)
        latencies["regional_centralized"].append(regional_latency)
        deadline_misses["regional_centralized"] += regional_latency > config.deadline_ms
        backhaul["regional_centralized"] += regional_bytes
        latencies["centralized"].append(central_latency)
        latencies["centralized_periodic"].append(central_latency)
        latencies["distributed"].append(dist_latency)
        deadline_misses["centralized"] += central_latency > config.deadline_ms
        deadline_misses["centralized_periodic"] += central_latency > config.deadline_ms
        deadline_misses["distributed"] += dist_latency > config.deadline_ms
        backhaul["centralized"] += backhaul_bytes(
            scenario, target, len(truth_edges), False, len(fresh_central_plan.assignments)
        )
        if central_refresh:
            backhaul["centralized_periodic"] += backhaul_bytes(
                scenario, target, len(truth_edges), False, len(central_plan.assignments)
            )
        backhaul["distributed"] += backhaul_bytes(scenario, target, len(truth_edges), True, len(dist_plan.assignments))

        if include_trace and (current % trace_stride == 0 or current == config.epochs - 1):
            trace.append(
                serialize_trace_epoch(
                    scenario,
                    target,
                    dist_plan,
                    fresh_central_plan,
                    dist_eval,
                    fresh_central_eval,
                    dist_latency,
                    central_latency,
                )
            )

    result = {
        "seed": seed,
        "systems": {},
        "trace": trace,
        "satellite_catalog": [
            {
                "id": sid,
                "name": orbit.name,
                "catalog_id": orbit.norad_catalog_id,
                "shell_id": orbit.shell_id,
                "altitude_km": round(orbit.derived_altitude_km, 1),
                "inclination_deg": round(orbit.inclination_deg, 1),
            }
            for sid, orbit in enumerate(scenario.orbits)
        ] if include_trace else [],
        "service_cells": [
            {
                "id": cell.id,
                "h3_index": cell.h3_index,
                "x": round(cell.x, 6),
                "y": round(cell.y, 6),
            }
            for cell in scenario.cells
        ] if include_trace else [],
    }
    for name, rows in metric_rows.items():
        summary = {
            metric: statistics.fmean(row[metric] for row in rows)
            for metric in ("demand_satisfaction", "pair_accuracy", "satellite_accuracy", "outage_ratio", "utility")
        }
        if name != "oracle":
            summary.update(
                {
                    "latency_p50_ms": percentile(latencies[name], 0.50),
                    "latency_p95_ms": percentile(latencies[name], 0.95),
                    "deadline_miss_ratio": deadline_misses[name] / config.epochs,
                    "backhaul_mb": backhaul[name] / 1_000_000,
                }
            )
        result["systems"][name] = summary
    return result


def aggregate(config: Config, seeds: list[int], workers: int = 1) -> dict:
    jobs = [(config, seed, i == 0) for i, seed in enumerate(seeds)]
    if workers == 1:
        runs = [run_seed(*job) for job in jobs]
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(run_seed, *job) for job in jobs]
            runs = [future.result() for future in futures]
    return aggregate_runs(config, seeds, runs)


def aggregate_runs(config: Config, seeds: list[int], runs: list[dict]) -> dict:
    """Summarize ordered seed runs from a local pool or multiple SLURM nodes."""
    if not runs or len(runs) != len(seeds):
        raise ValueError("one completed run is required for each seed")
    snapshot = load_orbital_snapshot()
    systems = {}
    for name in runs[0]["systems"]:
        metrics = runs[0]["systems"][name]
        systems[name] = {
            metric: {
                "mean": statistics.fmean(run["systems"][name][metric] for run in runs),
                "min": min(run["systems"][name][metric] for run in runs),
                "max": max(run["systems"][name][metric] for run in runs),
            }
            for metric in metrics
        }
    dist = systems["distributed"]
    central = systems["centralized"]
    comparisons = {
        "latency_reduction_pct": 100 * (1 - dist["latency_p95_ms"]["mean"] / central["latency_p95_ms"]["mean"]),
        "backhaul_reduction_pct": 100 * (1 - dist["backhaul_mb"]["mean"] / central["backhaul_mb"]["mean"]),
        "demand_satisfaction_delta_pp": 100 * (
            dist["demand_satisfaction"]["mean"] - central["demand_satisfaction"]["mean"]
        ),
        "pair_accuracy_delta_pp": 100 * (dist["pair_accuracy"]["mean"] - central["pair_accuracy"]["mean"]),
        "periodic_demand_satisfaction_delta_pp": 100
        * (dist["demand_satisfaction"]["mean"] - systems["centralized_periodic"]["demand_satisfaction"]["mean"]),
        "periodic_pair_accuracy_delta_pp": 100
        * (dist["pair_accuracy"]["mean"] - systems["centralized_periodic"]["pair_accuracy"]["mean"]),
    }
    if "regional_centralized" in systems:
        regional = systems["regional_centralized"]
        comparisons.update(
            regional_demand_satisfaction_delta_pp=100 * (regional["demand_satisfaction"]["mean"] - central["demand_satisfaction"]["mean"]),
            regional_utility_delta=regional["utility"]["mean"] - central["utility"]["mean"],
            regional_latency_reduction_pct=100 * (1 - regional["latency_p95_ms"]["mean"] / central["latency_p95_ms"]["mean"]),
            regional_backhaul_reduction_pct=100 * (1 - regional["backhaul_mb"]["mean"] / central["backhaul_mb"]["mean"]),
            distributed_vs_regional_demand_delta_pp=100 * (dist["demand_satisfaction"]["mean"] - regional["demand_satisfaction"]["mean"]))
    return {
        "schema_version": 2,
        "kind": "architecture-level benchmark with public GP orbital elements",
        "config": asdict(config),
        "constellation": {
            "provider": config.provider,
            "name": snapshot["providers"][config.provider]["name"],
            "records_available": snapshot["providers"][config.provider]["records_available"],
            "records_matching_authorized_shells": snapshot["providers"][config.provider]["records_matching_authorized_shells"],
            "sampled_satellites": config.satellites,
            "catalog_selection": snapshot["providers"][config.provider].get("selection", "shell-matched-sample"),
            "shells": snapshot["providers"][config.provider]["shells"],
            "orbital_source": snapshot["providers"][config.provider]["source"],
            "authorization": snapshot["providers"][config.provider]["authorization"],
            "snapshot_generated_at": snapshot["generated_at"],
            "element_epoch_range": snapshot["providers"][config.provider]["element_epoch_range"],
            "propagation": {
                "model": "SGP4",
                "library": f"python-sgp4 {sgp4.__version__}",
                "coordinates": "TEME to Earth-fixed using GMST; polar motion omitted",
            },
        },
        "seeds": seeds,
        "cell_system": {
            "kind": "H3 deterministic global sample",
            "resolution": config.h3_resolution,
            "sampled_cells": config.service_cells,
            "global_cells_at_resolution": h3.get_num_cells(config.h3_resolution),
            "average_cell_area_km2": h3.average_hexagon_area(config.h3_resolution, unit="km^2"),
            "average_edge_length_km": h3.average_hexagon_edge_length(config.h3_resolution, unit="km"),
        },
        "systems": systems,
        "regional_controller_model": regional_topology(Scenario(config, seeds[0], snapshot)),
        "comparisons": comparisons,
        "satellite_catalog": runs[0]["satellite_catalog"],
        "service_cells": runs[0]["service_cells"],
        "trace": runs[0]["trace"],
        "caveat": "Real public orbital elements propagated with SGP4; demand, RF quality, latency, and backhaul remain modeled.",
    }


def benchmark_result(profiles: dict) -> dict:
    snapshot = load_orbital_snapshot()
    return {
        "schema_version": 2,
        "kind": "Starlink and Project Kuiper shell-informed K-DBP benchmark",
        "snapshot_generated_at": snapshot["generated_at"],
        "propagator": f"python-sgp4 {sgp4.__version__}",
        "profiles": profiles,
        "caveat": "CelesTrak GP elements drive SGP4 geometry; polar motion and all demand/RF/control metrics remain modeled or omitted.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="benchmark/results.json")
    parser.add_argument(
        "--demo-out",
        default="demo/app/benchmark-data.json",
        help="optional second output path; pass an empty string to disable it",
    )
    parser.add_argument("--seeds", default="7,19,31,43,59,71,89")
    parser.add_argument(
        "--providers",
        default="all",
        help="comma-separated orbital providers, or 'all' (default)",
    )
    parser.add_argument("--epochs", type=int, default=Config.epochs)
    parser.add_argument("--epoch-seconds", type=int, default=Config.epoch_seconds)
    parser.add_argument("--service-cells", type=int, default=240)
    parser.add_argument("--h3-resolution", type=int, default=5)
    parser.add_argument("--regional-controllers", type=int, default=Config.regional_controllers)
    parser.add_argument("--satellites", default=str(Config.satellites), help="positive count or 'all' with a full catalog")
    parser.add_argument("--workers", type=int, default=0, help="parallel seed workers; 0 selects automatically")
    args = parser.parse_args()
    seeds = [int(value) for value in args.seeds.split(",") if value]
    snapshot = load_orbital_snapshot()
    providers = list(snapshot["providers"]) if args.providers == "all" else [
        value.strip() for value in args.providers.split(",") if value.strip()
    ]
    if not seeds:
        parser.error("--seeds must contain at least one integer")
    if not providers:
        parser.error("--providers must contain at least one provider")
    unknown_providers = sorted(set(providers) - set(snapshot["providers"]))
    if unknown_providers:
        parser.error(
            f"unknown provider(s): {', '.join(unknown_providers)}; "
            f"choose from {', '.join(snapshot['providers'])}"
        )
    if args.epochs <= 0 or args.epoch_seconds <= 0:
        parser.error("--epochs and --epoch-seconds must be positive")
    if not 1 <= args.regional_controllers <= Config.ground_stations:
        parser.error("--regional-controllers must be between 1 and 12")
    try:
        counts = {provider: resolve_satellite_count(args.satellites, provider, snapshot) for provider in providers}
    except (ValueError, TypeError) as error:
        parser.error(str(error))
    if args.workers < 0:
        parser.error("--workers cannot be negative")
    workers = min(args.workers, len(seeds)) if args.workers else min(len(seeds), 8)
    profiles = {
        provider: aggregate(
            Config(
                provider=provider,
                epochs=args.epochs,
                epoch_seconds=args.epoch_seconds,
                service_cells=args.service_cells,
                h3_resolution=args.h3_resolution,
                satellites=counts[provider],
                regional_controllers=args.regional_controllers,
            ),
            seeds,
            workers,
        )
        for provider in providers
    }
    result = benchmark_result(profiles)
    for target in (args.out, args.demo_out):
        if not target:
            continue
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                provider: {"systems": profile["systems"], "comparisons": profile["comparisons"]}
                for provider, profile in profiles.items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
