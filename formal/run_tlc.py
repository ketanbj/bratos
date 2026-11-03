#!/usr/bin/env python3
"""Run K-DBP TLC models and preserve raw and structured evidence."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.request
import argparse
from dataclasses import dataclass, asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "KDBP.tla"
EVIDENCE = ROOT / "evidence"
TLC_URL = "https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar"
TLC_SHA256 = "936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88"
TLC_JAR = Path("/tmp/kdbp-tla/tla2tools.jar")


@dataclass(frozen=True)
class Model:
    name: str
    config: str
    expected: str
    violation: str | None = None


MODELS = [
    Model("safe", "KDBP_safe.cfg", "pass"),
    Model("liveness", "KDBP_liveness.cfg", "pass"),
    Model("capacity_bug", "KDBP_capacity_bug.cfg", "counterexample", "CapacitySafety"),
    Model("fencing_bug", "KDBP_fencing_bug.cfg", "counterexample", "FencedActivation"),
    Model("handover_bug", "KDBP_handover_bug.cfg", "counterexample", "MakeBeforeBreak"),
    Model("catchup_bug", "KDBP_catchup_bug.cfg", "counterexample", "MembersReady"),
    Model(
        "joint_quorum_bug",
        "KDBP_joint_quorum_bug.cfg",
        "counterexample",
        "ReconfigurationJointCertified",
    ),
]


def ensure_tlc() -> None:
    TLC_JAR.parent.mkdir(parents=True, exist_ok=True)
    if not TLC_JAR.exists():
        urllib.request.urlretrieve(TLC_URL, TLC_JAR)
    digest = hashlib.sha256(TLC_JAR.read_bytes()).hexdigest()
    if digest != TLC_SHA256:
        raise RuntimeError(f"unexpected TLC checksum: {digest}")


def extract(pattern: str, text: str) -> int | None:
    matches = re.findall(pattern, text)
    return int(matches[-1].replace(",", "")) if matches else None


def elapsed_from_log(text: str) -> float | None:
    matches = re.findall(r"Finished in (?:(\d+)min )?(\d+)s", text)
    if not matches:
        return None
    minutes, seconds = matches[-1]
    return float(60 * int(minutes or 0) + int(seconds))


def summarize(model: Model, output: str, exit_code: int, elapsed: float | None) -> dict:
    passed = "Model checking completed. No error has been found." in output
    violated = model.violation is not None and (
        f"Invariant {model.violation} is violated" in output
        or "Temporal properties were violated" in output
    )
    observed = "pass" if passed else "counterexample" if violated else "unexpected"
    config_path = ROOT / "models" / model.config
    return {
        **asdict(model),
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "observed": observed,
        "ok": observed == model.expected,
        "exit_code": exit_code,
        "elapsed_seconds": round(elapsed, 3) if elapsed is not None else None,
        "states_generated": extract(r"([0-9,]+) states generated", output),
        "distinct_states": extract(r"([0-9,]+) distinct states found", output),
        "state_depth": extract(r"The depth of the complete state graph search is ([0-9,]+)", output),
        "log": f"{model.name}.log",
        "log_sha256": hashlib.sha256(output.encode()).hexdigest(),
    }


def run(model: Model) -> dict:
    command = [
        "java",
        "-XX:+UseParallelGC",
        "-jar",
        str(TLC_JAR),
        "-cleanup",
        "-workers",
        "auto",
        "-config",
        str(ROOT / "models" / model.config),
        str(SPEC),
    ]
    started = time.monotonic()
    completed = subprocess.run(command, text=True, capture_output=True)
    elapsed = time.monotonic() - started
    output = completed.stdout + completed.stderr
    (EVIDENCE / f"{model.name}.log").write_text(output)
    return summarize(model, output, completed.returncode, elapsed)


def reuse(model: Model) -> dict:
    log_path = EVIDENCE / f"{model.name}.log"
    if not log_path.exists():
        raise FileNotFoundError(f"missing evidence log: {log_path}")
    output = log_path.read_text()
    exit_code = 0 if "Model checking completed. No error has been found." in output else 12
    return summarize(model, output, exit_code, elapsed_from_log(output))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only",
        help="comma-separated model names; defaults to the complete evidence suite",
    )
    parser.add_argument(
        "--reuse-logs",
        action="store_true",
        help="rebuild structured results from existing raw logs without running TLC",
    )
    args = parser.parse_args()
    ensure_tlc()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    selected = MODELS
    if args.only:
        names = set(args.only.split(","))
        selected = [model for model in MODELS if model.name in names]
        missing = names - {model.name for model in selected}
        if missing:
            parser.error(f"unknown models: {', '.join(sorted(missing))}")
    results = [reuse(model) if args.reuse_logs else run(model) for model in selected]
    payload = {
        "tool": "TLC 2.19 (TLA+ tools v1.7.4)",
        "jar_sha256": TLC_SHA256,
        "spec_sha256": hashlib.sha256(SPEC.read_bytes()).hexdigest(),
        "models": results,
        "all_expectations_met": all(result["ok"] for result in results),
    }
    (EVIDENCE / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))
    return 0 if payload["all_expectations_met"] else 1


if __name__ == "__main__":
    sys.exit(main())
