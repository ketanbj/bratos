# K-DBP: Kratos-backed distributed beam prediction

## Problem

Low Earth orbit satellites move across service areas and ground-station coverage.
Controllers must predict which satellite and beam should serve each geographic
cell, allocate limited beam capacity, and hand over service before the current
connection disappears. Sending every observation to a global controller adds
communication delay and backhaul traffic; distributing decisions introduces the
need to keep plans consistent as control ownership moves.

## Solution

We place beam prediction near the observations and use Kratos rolling
replication to coordinate each satellite's plan. Ground stations
predict future cell/beam scores, combine their forecasts within a small replica
group, and propose assignments to the satellite's leader. The leader admits
assignments within capacity and commits a versioned future plan through a
per-satellite Raft log. Incoming ground stations catch up before taking over;
activation fences reject stale plans, and handovers require target service before
source release.

We evaluate this design with a simulator, a formal protocol model, an interactive
replay, and experiment harnesses for PACE and CloudLab.

## Findings

In our seven-seed benchmark, K-DBP reduces modeled p95 control latency by
**42–45%** and modeled control backhaul by **about 60%** compared with a global
controller updating every five seconds. Demand-satisfaction gains are modest.

| Metric, mean across seven seeds | Starlink: global → K-DBP | Project Kuiper: global → K-DBP |
|---|---:|---:|
| Modeled p95 control latency | 374.53 → 217.73 ms | 400.36 → 218.43 ms |
| Modeled control backhaul per run | 360.67 → 140.91 MB | 447.78 → 179.04 MB |
| Exact satellite + beam agreement with oracle | 91.44% → 93.63% | 89.69% → 92.34% |
| Demand satisfaction | 57.74% → 57.98% | 69.11% → 69.39% |
| Modeled deadline misses at 250 ms | 100% → 0% | 100% → 0% |

Both systems update every five seconds. Each provider profile uses 96 sampled
satellites, 240 globally sampled H3 resolution-5 cells, 12 ground stations,
four beams per satellite, and a 10-second prediction lookahead. Each seed covers
1,200 epochs, or 100 simulated minutes. The oracle is a greedy allocator with
access to future true modeled state. We also evaluate a global controller updating
every 15 seconds and the oracle reference. See the
[benchmark data](benchmark/results.json) and
[full comparison](docs/design.md#benchmark-result).

We use public orbital inputs and model demand, RF quality, control latency, and
backhaul. The accuracy advantage depends on K-DBP receiving current local
telemetry while the global controller receives the previous epoch's telemetry.
We have evaluated the architecture in simulation; a running Kratos/Raft
deployment and provider performance measurements remain future work.

We also checked protocol safety and ran a separate full-grid pilot:

- **Formal protocol checks:** we used TLC to explore **36,878,068 distinct states**
  in the finite safety model without an invariant violation. A smaller model passed
  four conditional liveness properties. We removed safety guards in five variants;
  each produced the intended counterexample. The model assumes a linearizable,
  majority-certified commit abstraction. See the
  [formal evidence](formal/evidence/RESULTS.md) for its scope and assumptions.
- **Full-grid regional pilot:** we ran a three-epoch CloudLab calibration using
  **all 2,016,842 Earth cells and 391 Kuiper satellites**. Six regional controllers
  used all 1,564 capacity slots, with mean utility 1.42% lower and modeled p95
  latency 77.7% lower than fresh global control. This is a short calibration,
  separate from the seven-seed benchmark above. See the
  [regional validation](harness/OPTION2.md#regional-centralized-allocation).

## What this does and why

K-DBP predicts the `(satellite, beam-codebook index)` for a service cell one or
more epochs ahead. Predictable orbital motion supplies the geometry; local
telemetry supplies short-term demand and channel residuals. Small groups combine
those forecasts, while satellite-local admission prevents competing controllers
from allocating the same capacity twice.

Rolling replica groups keep the plan near the ground stations that can serve the
satellite next. Agreement, capacity checks, and versioned activation make control
ownership explicit through that transition. Prediction quality and protocol
safety can then be evaluated separately.

Use the tools here to:

- **Compare control architectures.** The deterministic
  [simulator](src/beam_sim.py) uses frozen public orbital elements, SGP4
  propagation, and H3 service cells to compare global, periodic global, regional,
  and K-DBP allocation against a truth-informed greedy oracle. We added the regional
  baseline after the seven-seed benchmark; rerun the simulator to include it.
- **Inspect decisions over time.** The [interactive demo](demo) replays Starlink
  and Project Kuiper scenarios from the benchmark data, including 101 evenly
  spaced frames per profile. Metrics cover every simulated epoch.
- **Check protocol rules.** The [TLA+ model](formal/README.md) checks reservations,
  committed plans, activation fences, handovers, and replica-group changes.
- **Scale reproducible experiments.** The [shared harness](harness/README.md)
  supports independent seed runs on PACE and CloudLab. The
  [Option 2 runner](harness/OPTION2.md) partitions a complete Earth grid across
  workers while sharing satellite capacity and checkpointing every epoch.

The [design document](docs/design.md) explains the algorithm, guarantees,
assumptions, and next experiments. Global allocation optimality, prediction
accuracy, and availability without a reachable majority remain outside the
protocol's guarantees. Full-Earth simulations make no commercial-availability
claim.

## Getting started

Clone the repository:

```bash
git clone https://github.com/ketanbj/bratos.git
cd bratos
```

### Explore the demo

Use Node.js 22.13 or newer. The demo includes benchmark data, so you can start it
without running Python or regenerating a benchmark.

```bash
cd demo
npm ci
npm run dev
```

Open the local URL printed by the development server. Run `npm test` from
`demo/` to build and check the rendered replay.

### Set up Python and run the checks

From the repository root, use Python 3.10–3.13; the commands below use 3.12.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r harness/requirements-option2.txt
.venv/bin/python -m unittest discover -s tests -v
```

The harness requirements include the scalar simulator and complete-grid test
dependencies. For the scalar simulator alone, install `requirements.txt` instead.

### Regenerate the benchmark

From the repository root:

```bash
.venv/bin/python src/beam_sim.py
```

This writes `benchmark/results.json` and `demo/app/benchmark-data.json` using
the frozen orbital snapshot and current simulator, including the regional
baseline. It may take substantially longer than the tests; seed runs are
parallelized by default. Use `--regional-controllers 6` to select six regional
controllers, or change the sampled domain with
`--service-cells 480 --h3-resolution 5`.

To run an experiment with refreshed public orbital inputs, update the snapshot
before regenerating results:

```bash
.venv/bin/python scripts/update_orbital_data.py
.venv/bin/python src/beam_sim.py
```

Refreshing inputs changes the experiment. We use `data/orbital_snapshot.json`
for the sampled benchmark and `data/orbital_snapshot_full.json` for the
complete-grid harness.

### Reproduce the formal checks

With Java 11 or newer installed:

```bash
python3 formal/run_tlc.py
```

The runner downloads checksum-verified TLC tools. See the
[formal guide](formal/README.md) for smaller model selections and assumptions.

### Run larger experiments

Choose the guide for your backend and experiment scope:

| Goal | Guide |
|---|---|
| Run independent provider/seed simulations on PACE/SLURM | [PACE setup and workflow](pace/README.md) |
| Run independent simulations on an existing CloudLab experiment | [CloudLab setup and workflow](cloudlab/README.md) |
| Evaluate all 2,016,842 Earth cells with shared satellite capacity | [Option 2 complete-grid runner](harness/OPTION2.md) |
| Understand shared workers, checkpoints, and result merging | [Shared harness](harness/README.md) |

For the sampled runners, begin with `make -C pace env` or
`make -C cloudlab env`, configure the generated `.env`, and follow the backend's
setup, dry-run, and smoke-test steps before submitting a larger experiment.
The complete-grid runner has separate preparation and launch commands in its
guide.

## Repository contents and local files

Source, tests, pinned dependencies, public orbital snapshots, public coverage
references, and compact benchmark/calibration artifacts are versioned together.
`demo/` is included directly; no submodule checkout is required.

Virtual environments, `node_modules`, builds, `.env` files, private keys,
per-run TLS material, generated full-Earth grids, and cluster run/checkpoint
directories are ignored. The `.env.example` templates are included. Keep SSH
identities in your local `~/.ssh/config`. Follow the
[Option 2 guide](harness/OPTION2.md) to regenerate the large grid after cloning;
production outputs remain in local or cluster storage and are collected separately.
