#!/usr/bin/env python3
"""Validate simulation inputs before starting cluster workers."""
import datetime as dt
import os
from pathlib import Path
import sys

import h3
import sgp4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.beam_sim import load_orbital_snapshot, resolve_satellite_count, orbit_record, propagate_orbit, parse_timestamp


def main():
    if sys.version_info < (3, 10):
        raise ValueError("Python >=3.10 is required by the pinned H3 dependency")
    maximum = h3.get_num_cells(int(os.environ["H3_RESOLUTION"]))
    if int(os.environ["SERVICE_CELLS"]) > maximum:
        raise ValueError(f"SERVICE_CELLS exceeds {maximum} cells at this H3 resolution")
    snapshot = load_orbital_snapshot()
    providers = list(snapshot["providers"]) if os.environ["PROVIDERS"] == "all" else os.environ["PROVIDERS"].split(",")
    for provider in providers:
        count = resolve_satellite_count(os.environ["SATELLITES"], provider, snapshot)
        print(f"{provider}: {count} satellites")
        if os.environ["SATELLITES"] == "all":
            start = parse_timestamp(snapshot["generated_at"])
            seconds = (int(os.environ["EPOCHS"]) + 2) * int(os.environ["EPOCH_SECONDS"])
            for sid, raw in enumerate(snapshot["providers"][provider]["sampled_orbits"]):
                orbit = orbit_record(raw)
                for timestamp in (start, start + dt.timedelta(seconds=seconds)):
                    propagate_orbit(sid, orbit, timestamp)
    jobs = len(providers) * len(os.environ["SEEDS"].split(","))
    if int(os.environ.get("BEAM_NODES", os.environ.get("SBATCH_NODES", "1"))) > jobs:
        raise ValueError(f"Node count exceeds the {jobs} independent provider/seed runs")
    print(f"Preflight passed: h3 {h3.__version__}, sgp4 {sgp4.__version__}; snapshot {snapshot['generated_at']}")


if __name__ == "__main__":
    main()
