#!/usr/bin/env python3
"""Refresh the reproducible Starlink and Kuiper orbital-element snapshot.

The default output is a deterministic shell-matched sample. --all-satellites
retains every record in each provider's GP group, including unmatched shells.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import subprocess
from pathlib import Path


MU_KM3_S2 = 398600.4418
EARTH_RADIUS_KM = 6378.137
CELESTRAK_URL = "https://celestrak.org/NORAD/elements/supplemental/sup-gp.php?FILE={group}&FORMAT=json"
FULL_CATALOG_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=json"

PROVIDERS = {
    "starlink": {
        "name": "Starlink",
        "group": "starlink",
        "authorization": {
            "label": "FCC Gen1 modification + Gen2 authorization",
            "urls": [
                "https://docs.fcc.gov/public/attachments/FCC-21-48A1.pdf",
                "https://docs.fcc.gov/public/attachments/DA-26-36A1.pdf",
            ],
        },
        "shells": [
            {"id": "sl-g1-540", "generation": "Gen1", "altitude_km": 540, "inclination_deg": 53.2, "authorized_satellites": 1584, "planes": 72},
            {"id": "sl-g1-550", "generation": "Gen1", "altitude_km": 550, "inclination_deg": 53.0, "authorized_satellites": 1584, "planes": 72},
            {"id": "sl-g1-560", "generation": "Gen1", "altitude_km": 560, "inclination_deg": 97.6, "authorized_satellites": 520, "planes": 10},
            {"id": "sl-g1-570", "generation": "Gen1", "altitude_km": 570, "inclination_deg": 70.0, "authorized_satellites": 720, "planes": 36},
            {"id": "sl-g2-340", "generation": "Gen2", "altitude_km": 340, "inclination_deg": 53.0, "authorized_satellites": None, "planes": 72},
            {"id": "sl-g2-345", "generation": "Gen2", "altitude_km": 345, "inclination_deg": 48.0, "authorized_satellites": None, "planes": 72},
            {"id": "sl-g2-350", "generation": "Gen2", "altitude_km": 350, "inclination_deg": 38.0, "authorized_satellites": None, "planes": 72},
            {"id": "sl-g2-355", "generation": "Gen2", "altitude_km": 355, "inclination_deg": 43.0, "authorized_satellites": None, "planes": 72},
            {"id": "sl-g2-360", "generation": "Gen2", "altitude_km": 360, "inclination_deg": 96.9, "authorized_satellites": None, "planes": 72},
            {"id": "sl-g2-365", "generation": "Gen2", "altitude_km": 365, "inclination_deg": 32.0, "authorized_satellites": None, "planes": 72},
            {"id": "sl-g2-475", "generation": "Gen2", "altitude_km": 475, "inclination_deg": 32.0, "authorized_satellites": None, "planes": None},
            {"id": "sl-g2-480", "generation": "Gen2", "altitude_km": 480, "inclination_deg": 53.0, "authorized_satellites": None, "planes": None},
            {"id": "sl-g2-485", "generation": "Gen2", "altitude_km": 485, "inclination_deg": 43.0, "authorized_satellites": None, "planes": 56},
        ],
    },
    "kuiper": {
        "name": "Project Kuiper",
        "group": "kuiper",
        "authorization": {
            "label": "FCC Kuiper modified authorization",
            "urls": ["https://docs.fcc.gov/public/attachments/DA-24-224A1.pdf"],
        },
        "shells": [
            {"id": "kuiper-590-33", "generation": "Gen1", "altitude_km": 590, "inclination_deg": 33.0, "authorized_satellites": 782, "planes": 782},
            {"id": "kuiper-590-30", "generation": "Gen1", "altitude_km": 590, "inclination_deg": 30.0, "authorized_satellites": 2, "planes": 1},
            {"id": "kuiper-610", "generation": "Gen1", "altitude_km": 610, "inclination_deg": 42.0, "authorized_satellites": 1292, "planes": 1292},
            {"id": "kuiper-630", "generation": "Gen1", "altitude_km": 630, "inclination_deg": 51.9, "authorized_satellites": 1156, "planes": 289},
        ],
    },
}


def altitude_km(mean_motion_rev_day: float) -> float:
    radians_per_second = mean_motion_rev_day * 2 * math.pi / 86400
    semi_major_axis = (MU_KM3_S2 / radians_per_second**2) ** (1 / 3)
    return semi_major_axis - EARTH_RADIUS_KM


def nearest_shell(record: dict, shells: list[dict]) -> dict | None:
    altitude = altitude_km(float(record["MEAN_MOTION"]))
    inclination = float(record["INCLINATION"])
    ranked = sorted(
        shells,
        key=lambda shell: (
            abs(altitude - shell["altitude_km"]) / 20
            + abs(inclination - shell["inclination_deg"]) / 0.75,
            shell["id"],
        ),
    )
    shell = ranked[0]
    if abs(altitude - shell["altitude_km"]) > 25 or abs(inclination - shell["inclination_deg"]) > 1.0:
        return None
    return shell


def fetch(group: str, all_satellites: bool = False) -> list[dict]:
    template = FULL_CATALOG_URL if all_satellites else CELESTRAK_URL
    result = subprocess.run(
        ["curl", "--fail", "--location", "--silent", "--show-error", "--max-time", "60", template.format(group=group)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def evenly_sample(records: list[dict], limit: int) -> list[dict]:
    ordered = sorted(records, key=lambda item: (float(item["RA_OF_ASC_NODE"]), int(item["NORAD_CAT_ID"])))
    if len(ordered) <= limit:
        return ordered
    return [ordered[math.floor(i * len(ordered) / limit)] for i in range(limit)]


def normalize(record: dict, shell: dict | None) -> dict:
    return {
        "name": record["OBJECT_NAME"],
        "norad_catalog_id": int(record["NORAD_CAT_ID"]),
        "epoch": record["EPOCH"] + ("Z" if not record["EPOCH"].endswith("Z") else ""),
        "mean_motion_rev_day": float(record["MEAN_MOTION"]),
        "eccentricity": float(record["ECCENTRICITY"]),
        "inclination_deg": float(record["INCLINATION"]),
        "raan_deg": float(record["RA_OF_ASC_NODE"]),
        "arg_pericenter_deg": float(record["ARG_OF_PERICENTER"]),
        "mean_anomaly_deg": float(record["MEAN_ANOMALY"]),
        "derived_altitude_km": round(altitude_km(float(record["MEAN_MOTION"])), 2),
        "shell_id": shell["id"] if shell is not None else "unmatched",
        "omm": {
            field: record[field]
            for field in (
                "OBJECT_ID",
                "EPOCH",
                "MEAN_MOTION",
                "ECCENTRICITY",
                "INCLINATION",
                "RA_OF_ASC_NODE",
                "ARG_OF_PERICENTER",
                "MEAN_ANOMALY",
                "EPHEMERIS_TYPE",
                "CLASSIFICATION_TYPE",
                "NORAD_CAT_ID",
                "ELEMENT_SET_NO",
                "REV_AT_EPOCH",
                "BSTAR",
                "MEAN_MOTION_DOT",
                "MEAN_MOTION_DDOT",
            )
        },
    }


def build_provider(key: str, spec: dict, sample_limit: int, all_satellites: bool = False) -> dict:
    raw = fetch(spec["group"], all_satellites)
    if not raw:
        raise ValueError(f"empty orbital catalog for {key}")
    if len({int(record['NORAD_CAT_ID']) for record in raw}) != len(raw):
        raise ValueError(f"duplicate catalog IDs for {key}")
    matched: list[tuple[dict, dict]] = []
    counts = {shell["id"]: 0 for shell in spec["shells"]}
    for record in raw:
        shell = nearest_shell(record, spec["shells"])
        if shell is None:
            continue
        counts[shell["id"]] += 1
        matched.append((record, shell))

    selected_raw = sorted(raw, key=lambda record: int(record["NORAD_CAT_ID"])) if all_satellites else evenly_sample(
        [record for record, _ in matched], sample_limit
    )
    sampled = [normalize(record, nearest_shell(record, spec["shells"])) for record in selected_raw]

    epochs = sorted(record["EPOCH"] for record in raw)
    shells = [{**shell, "observed_objects": counts[shell["id"]]} for shell in spec["shells"]]
    return {
        "name": spec["name"],
        "source": (FULL_CATALOG_URL if all_satellites else CELESTRAK_URL).format(group=spec["group"]),
        "selection": "all" if all_satellites else "shell-matched-sample",
        "authorization": spec["authorization"],
        "records_available": len(raw),
        "records_matching_authorized_shells": len(matched),
        "element_epoch_range": {"first": epochs[0] + "Z", "last": epochs[-1] + "Z"},
        "shells": shells,
        "sampled_orbits": sampled,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data/orbital_snapshot.json")
    parser.add_argument("--sample-limit", type=int, default=256)
    parser.add_argument("--all-satellites", action="store_true", help="store every GP group record, without sampling or shell filtering")
    args = parser.parse_args()
    if args.sample_limit <= 0:
        parser.error("--sample-limit must be positive")

    snapshot = {
        "schema_version": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "method": "Every CelesTrak provider GP group record; shell classification is descriptive only" if args.all_satellites else
                  "CelesTrak GP elements matched to FCC-authorized shells; deterministic RAAN-stratified sample",
        "providers": {
            key: build_provider(key, spec, args.sample_limit, args.all_satellites)
            for key, spec in PROVIDERS.items()
        },
    }
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2) + "\n")
    print(json.dumps({key: {"available": value["records_available"], "sampled": len(value["sampled_orbits"])} for key, value in snapshot["providers"].items()}, indent=2))


if __name__ == "__main__":
    main()
