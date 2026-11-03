#!/usr/bin/env python3
"""Record the actual inputs and outcome beside each simulator result."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import sys

import h3
import sgp4


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def main():
    path = Path(sys.argv[1])
    if sys.argv[2] == "--finish":
        info = json.loads(path.read_text())
        code = int(sys.argv[3])
        info.update(finished_at=now(), exit_code=code, state="completed" if code == 0 else "failed")
    else:
        root = Path(sys.argv[2])
        inputs = ["src/beam_sim.py", "data/orbital_snapshot.json", "requirements.txt", "config.env", "pace/common.sh", "harness/config.sh", "harness/distributed.py", "harness/preflight.py", "pace/beam_sim.sbatch", "pace/run_info.py"]
        info = {
            "state": "running", "started_at": now(),
            "job_id": os.environ.get("SLURM_JOB_ID"), "hostname": socket.gethostname(),
            "allocated_cpus": os.environ.get("SLURM_CPUS_PER_TASK"),
            "command": sys.argv[4:], "result": sys.argv[3], "run_dir": str(root),
            "python": platform.python_version(), "h3": h3.__version__, "sgp4": sgp4.__version__,
            "input_sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in inputs if (root / name).is_file()},
        }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(info, indent=2) + "\n")
    temporary.replace(path)


if __name__ == "__main__":
    main()
