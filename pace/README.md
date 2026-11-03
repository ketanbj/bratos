# PACE simulator harness

For **Option 2**, use [the coupled census runner](../harness/OPTION2.md) and
`pace/option2.sbatch`. It shares capacity across spatial workers and checkpoints
each epoch. This new adapter has local transport tests; it still needs an actual
PACE calibration. The commands below retain the independent-seed sample runner.

This is the PACE/SLURM backend. The separate
[CloudLab backend](../cloudlab/README.md) supports existing SSH-accessible experiments.
Both use the shared runner in `harness/`.
It adapts the `.env`, remote
sync, `sbatch`, job tracking, status, logs, and cancellation workflow from the
supplied Ray harness to `src/beam_sim.py`.

One SLURM task runs on each allocated node. Provider/seed pairs are divided
between those tasks, and each task uses a Python process pool up to its allocated
CPU count. Results are merged in the original seed order into the same JSON
schema used by the demo. This workload does not require Ray.

For the 40-node, 32-core Intel high-memory pool, see the
[large simulation plan](LARGE_RUN_PLAN.md). Its calibration and full-node load
profiles use this same harness and select `cpu-large` with `intel&core32`:

```bash
make -C pace dry-run ENV_FILE=profiles/intel-calibration.env
make -C pace dry-run ENV_FILE=profiles/intel-node-load.env
```

These profiles now use every record in `data/orbital_snapshot_full.json`, with
provider-specific satellite counts. The scientific target is 1,200 five-second
epochs (100 minutes). Refresh a full catalog separately from the demo sample:

```bash
.venv/bin/python scripts/update_orbital_data.py --all-satellites --out data/orbital_snapshot_full.json
```

`SATELLITES=all` requires a complete catalog and rejects the old sampled
snapshot. Unmatched shells are retained in full-catalog mode. `ORBITAL_SNAPSHOT`
selects the data file, which is copied into each frozen run before submission.
The full production run remains gated on runtime optimization; the supplied
profiles are short calibration and throughput tests.

The default allocation is CPU-only:

```bash
SBATCH_ACCOUNT=gts-jyoung9-csse
SBATCH_NODES=1
SBATCH_CPUS_PER_TASK=14
SBATCH_GPUS_PER_NODE=
SBATCH_MEM=32G
SBATCH_TIME=01:00:00
SLURM_JOB_NAME=bratos
LOG_PATTERN=bratos-%j.log
```

The current seven seeds and two constellations provide 14 independent runs, so
one node with 14 CPU cores can run all of them concurrently, subject to memory
availability. Node and core counts are configurable; there is no fixed
three-node limit. Each individual seed run uses one process.

## Configure and install

```bash
make -C pace env
```

Edit `pace/.env`. Leave `PACE_LOGIN` blank when running directly on a PACE login
node. For laptop submission, set an SSH host or alias and a dedicated absolute
checkout path on PACE's shared filesystem:

```bash
PACE_LOGIN=phoenix
PACE_REMOTE_DIR=/storage/home/hcoda1/6/USERNAME/bratos
```

Set `SBATCH_PARTITION` and `SBATCH_QOS` if your allocation requires them. Set
`PACE_MODULES` to your site's Python module names. The template loads
`python/3.11.9`, verified on Phoenix; pinned H3 requires Python >=3.10. The same Python environment must be visible from every compute node.

```bash
make -C pace setup
make -C pace dry-run
make -C pace smoke
make -C pace status
make -C pace logs
```

`setup` syncs the checkout and installs the pinned requirements into `.venv` on
PACE. `dry-run` validates shell settings and prints the allocation without SSH
or submitting a job. Remote previews use the configured PACE paths. `smoke` submits a smaller one-node, one-CPU run with eight
epochs, 240 cells, one seed, and both providers. It requests no GPUs and keeps
the configured account. It tracks the smoke job using the same `.jobid` as a regular run.

The launcher checks imports, orbital-record counts, the H3 cell limit, and the
number of independent runs before submitting. SLURM resources are supplied as
`sbatch` command-line options; use the Makefile rather than submitting the
`.sbatch` file directly.

## Submit and monitor

```bash
make -C pace submit
make -C pace status
make -C pace logs TAIL_LINES=1000
make -C pace fetch
```

`up` aliases `submit`; `down` aliases `cancel`. `status` reports scheduler state,
exit code, elapsed time, and memory usage. `fetch` retrieves remote logs, results,
checkpoints, and frozen run inputs.

The default experiment uses 10,000 H3 resolution-5 cells, 100 five-second epochs,
96 satellites, seven seeds, and both constellations. Override settings without
editing `.env`:

```bash
make -C pace submit SERVICE_CELLS=10000 EPOCHS=100 SEEDS=7,19
make -C pace submit SERVICE_CELLS=240 EPOCHS=1200
# Distribute the existing 14 runs across four CPU nodes.
make -C pace submit SBATCH_NODES=4 SBATCH_CPUS_PER_TASK=4
# More seeds create enough independent work to use 32 cores across four nodes.
make -C pace submit SBATCH_NODES=4 SBATCH_CPUS_PER_TASK=8 SEEDS=1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16
```

`SIM_WORKERS=0` uses the allocated CPUs on each node, capped by that node's work.
A positive value caps process workers per node and cannot exceed the CPU
allocation. `SBATCH_NODES` cannot exceed the number of provider/seed pairs,
which avoids allocating nodes with no work. Extra cores beyond the available
runs remain idle. A larger cell count increases work within each seed, but
does not create additional parallel jobs. Size memory for the number of seeds
running concurrently on each node.
Runtime grows roughly with cells × satellites × epochs × seeds × providers.
Measure a smaller run's elapsed time and `MaxRSS` before scaling up; a one-hour
allocation is not a guarantee that a chosen experiment fits.

The latest job ID is recorded in `pace/.jobid`. Default outputs are:

- `pace/logs/bratos-JOBID.log`: combined SLURM output and errors.
- `pace/results/results-JOBID.json`: complete merged benchmark.
- `pace/results/run-JOBID.json`: timestamps, exit code, command, dependency
  versions, and input hashes.
- `pace/results/shards-JOBID/`: individual completed provider/seed checkpoints.
- `pace/runs/run-TIMESTAMP-XXXXXX/`: copied simulator, orbital data, worker
  scripts, requirements, and effective configuration used for submission.

Queued and running jobs use the copied inputs. Subsequent source syncs and `.env`
edits do not change those inputs. The Python virtual environment is shared;
input fingerprints include the actual Python, H3, and SGP4 versions.

## Recover completed work

Each completed provider/seed run is atomically saved to shared storage. A failed
or interrupted seed has no completed checkpoint. A failed SLURM task stops the
job step; the harness records its failure and does not produce a partial merged
benchmark. Time limits or node loss can prevent final failure metadata from being
written, so scheduler accounting remains the authority on job state.

Resubmit with identical simulation settings, source, orbital data, and dependency
versions to reuse completed seeds from an earlier job:

```bash
make -C pace submit RESUME_JOB_ID=123456
```

The worker checks a fingerprint before using each saved result. A mismatch stops
the run. Missing checkpoints are computed, and merging succeeds only when every
expected provider/seed checkpoint is present. Recovery is at seed boundaries;
an interrupted seed starts from its beginning. Changing the node count or CPU
allocation is supported. Application exceptions propagate for diagnosis.

```bash
make -C pace cancel
```

Cancellation affects only the tracked job. Older jobs remain accessible through
normal `squeue`, `sacct`, and `scancel` commands.

## Remote synchronization

Before remote setup or submission, the launcher mirrors the checkout with
`rsync --delete` and verifies it with a checksum dry run. An independent SHA-256 check handles
checksum discrepancies reported by macOS openrsync, rejecting actual byte
differences. Source files removed
locally are removed remotely. Git metadata, `.venv`, job tracking, logs, results,
frozen run inputs, and generated Node dependencies/builds are protected.

The first sync requires an empty destination and creates a
`.bratos-pace-sync-root` marker. Subsequent syncs require that marker. Choose a
separate checkout from any existing verifier project. Custom configuration files
(`ENV_FILE=...`) and command-line overrides are forwarded to remote actions.

The harness follows the native [SLURM sbatch](https://slurm.schedmd.com/sbatch.html)
and [srun](https://slurm.schedmd.com/srun.html) model used in
[PACE's Phoenix SLURM guide](https://docs.pace.gatech.edu/phoenix_cluster/slurm_guide_phnx/).

## Local validation

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Tests exercise submission and remote forwarding with mocked scheduler commands,
run the real simulator from frozen inputs, check failure status propagation, and
compare a three-rank run and checkpoint recovery against serial output.
