# Option 2: complete resolution-5 Earth grid and full catalogs

The selected experiment uses **all 2,016,842 Earth H3 resolution-5 cells** and
all **11,122 Starlink** or **391 Kuiper** records in the frozen October 4, 2026
catalog. Each provider/seed trajectory covers 1,200 five-second epochs: **100
simulated minutes**. This is labeled `global-study` and makes no claim that every
cell has commercial service. H3 cells are simulation units, not a published
Starlink internal cell layout. Product eligibility tags remain unset.

`coupled.py` evaluates centralized, periodic centralized, regional centralized,
distributed, and truth-informed greedy models. Workers own disjoint geographic tiles and share
a single satellite capacity budget through the coordinator. Geography is
parallelized within each epoch; time advances sequentially so periodic plans
retain their history. Regional summaries can be derived from a global run;
independent continent runs would change cross-region capacity competition.

## Regional centralized allocation

`regional_centralized` uses six fixed geographic controllers by default, configurable
with `--regional-controllers 1..12`. Controllers are evenly selected from the
12 synthetic ground stations; each cell and ground station belongs to its nearest
controller by spherical distance. These Voronoi regions cover land and ocean;
they are not continent boundaries or actual operator gateway locations. Region
ownership is independent of execution worker count and remains fixed on resume.

At each epoch a budget broker at ground station 0 allocates each satellite's four
cell slots across regions in proportion to visible candidate counts, using integer
largest remainders. Each region then runs its own exact score-sorted greedy allocator
within that quota. The broker exchanges counts and quotas, not candidate scores.
There is no within-epoch borrowing: a region can leave quota unused after cell
competition, reducing utilization. Quotas enforce the shared global satellite
limit even when a satellite is visible from several regions.

Regional and fresh global controllers use identical observation age, score noise,
and weights. One regional controller reproduces global allocation and modeled
control costs. Multi-region control latency is the slowest regional compute plus
local ground-station communication and broker round trip. Backhaul retains the
same 320-byte raw reports as global control, with regional plan dissemination and
count/quota exchange. These are synthetic formulas, not network measurements;
regional control does not assume compressed telemetry.

Both scalar and complete-grid runners include the new system in results. Epoch
checkpoints record regional cell counts, candidate counts, capacity shares,
assigned cells, and unused shares. The run manifest records controller topology
and quota policy. Physical workers still execute under one simulation coordinator;
this implements the allocation model, not independently deployed network controllers.

```bash
# Full-grid Kuiper calibration with six regional controllers.
.venv-option2/bin/python cloudlab/cluster.py start --provider kuiper \
  --regional-controllers 6 --calibration --stop-after 3
```

The three-epoch CloudLab pilot used all 2,016,842 cells and all 391 Kuiper satellites.
All existing systems' metrics matched the original Kuiper trajectory exactly for
those epochs. Regional allocation used all 1,564 slots; mean utility was 1.42% lower
than fresh global allocation and modeled p95 latency was 77.7% lower. Raw-report
backhaul was nearly unchanged. Total simulator execution averaged 5.43 seconds per
epoch, including about 2.07 seconds for the additional regional allocator.
These short-pilot results do not establish full-orbit performance.
[Validation report](regional-validation.json) contains metrics and frozen run IDs.
Pilot outputs were fetched locally and its worker services stopped.

Previously launched Starlink and Kuiper production runs retain their frozen
four-system implementation. A new trajectory is required for a full-orbit regional
comparison; no new production trajectory was started for this implementation.

## Inputs and installation

Use Python 3.10–3.13 for pinned scientific wheels. Local preparation uses 3.12;
CloudLab uses 3.10. Dependency versions must match across workers/coordinator.

```bash
python3.12 -m venv .venv-option2
.venv-option2/bin/python -m pip install -r harness/requirements-option2.txt
.venv-option2/bin/python -m harness.grid --resolution 5 --global-study \
  --out data/generated/global-option2-res5.npz
```

- `data/orbital_snapshot_full.json`: complete unique provider catalogs.
- `data/generated/global-option2-res5.npz`: 87 MiB census containing stable IDs,
  H3 indexes, centers, actual areas, and 122 base-cell ownership tiles.
- `data/coverage/raw/`: optional official coverage status/overlay references,
  dates, HTTP metadata, and checksums; see [coverage notes](../data/coverage/README.md).

Public coverage inputs do not establish the complete land/maritime/aviation
commercial union. That missing mask **does not block the selected global study**.
The older `global-res5.npz` remains calibration-only for reproducible recovery
of prior pilots. `--global-study` explicitly enables the full-Earth production
scope; production also requires resolution 5 and 1,200 epochs.

Stable identity is the ordinal in the sorted full H3 grid. Masking and changing
worker count preserve identity and demand phase. Phases use SplitMix64(cell ID
XOR seed), differing from the legacy sample-dependent RNG. Scalar parity tests
use these identical phases. Never refresh source, catalogs, or grids within a
trajectory; changed inputs require a new run.

## CloudLab execution and recovery

`cloudlab/cluster.py` provisions through existing `~/.ssh/config` aliases with
direct identity/Keychain authentication. It freezes source, catalog, and grid
before transfer. Worker input hashes and dependency versions must match before
any epoch executes. User SSH keys stay on the laptop; no `ssh-add` is needed.

The coordinator runs detached on the first CloudLab node and survives laptop
disconnection. Worker control uses mutually authenticated TLS. Per-run certificates
last three days: each node receives only its server key, the head receives the
client key, and the signing key stays local. Service ports require authenticated
clients. SSH handles provisioning, monitoring, and collection. A shared filesystem
and Ray are unnecessary.

```bash
# Existing two-host allocation, eight processes per host, short full-grid pilot.
.venv-option2/bin/python cloudlab/cluster.py start --calibration --stop-after 2

# Read the printed run directory; retrieve status, results, and checkpoints.
.venv-option2/bin/python cloudlab/cluster.py fetch --run cloudlab/state/option2-RUN-ID

# Resume the original frozen trajectory for one additional epoch.
.venv-option2/bin/python cloudlab/cluster.py resume \
  --run cloudlab/state/option2-RUN-ID --stop-after 1

# Stop only process groups verified as belonging to this deployment.
.venv-option2/bin/python cloudlab/cluster.py stop --run cloudlab/state/option2-RUN-ID
```

Recovery reads captured scientific parameters automatically, refuses an active
coordinator, restarts workers, and replays an interrupted epoch from the last
completed checkpoint. It is explicit recovery, not automatic failover. Resume
preserves the original credentials: complete the run before they expire or
prepare a new deployment. Results/checkpoints are stored on the head until
`fetch` copies them locally; fetch them before CloudLab releases the nodes.

Use `--provider kuiper` for a separate Kuiper trajectory and `--seed N` for
repetitions. `--hosts ALIAS1 ALIAS2 ...` selects the allocated nodes.
`--workers-per-host` controls processes; `--query-workers` controls spatial-query
threads per process. Size concurrency from measured CPU/RSS rather than logical
CPU count alone. The 122 ownership tiles bound useful worker count today; finer
tiles are needed above 122 processes. There is no two-node harness limit.

After the portal lease covers the measured runtime plus margin, start the
selected global production trajectory without calibration or an epoch stop:

```bash
.venv-option2/bin/python cloudlab/cluster.py start --wall-seconds 57600
```

The 16-hour wall budget is an execution bound, not a lease request. It is checked
at completed-epoch boundaries and can overrun by one epoch. The default is 12
hours. A partial `results.json` reports `completed_epochs`; it is not a completed
100-minute trajectory. CloudLab approved the requested one-day extension; the
two-node experiment now expires **October 7 at 2:04 PM EDT**.

`cloudlab/option2.py` retains the SSH fallback with the coordinator on the laptop;
that mode requires an awake, connected laptop.

Logical reporting chunks are 120 epochs (10 simulated minutes). Durable
checkpoints are written after **every completed epoch**, retaining the periodic
plan, absolute epoch, raw metric/latency series, frozen-input fingerprint, and
predecessor checksum. Percentiles are recomputed from raw samples.

## PACE execution

`pace/option2.sbatch` uses the same runner with `srun` and shared frozen storage.
It requests the previously inspected 40-node Intel/core32 pool, account
`gts-jyoung9-csse`, CPU-only resources, and one hour for a **calibration** by default.
Two workers per node each receive eight query cores; array scoring and admission
are partly serial, so this does not guarantee full use of every allocated core.

```bash
# Install the pinned Option 2 venv in the PACE shared checkout first.
sbatch pace/option2.sbatch
OPTION2_RESUME_RUN=/absolute/path/to/pace/runs/option2-JOBID sbatch pace/option2.sbatch
```

Recheck pool availability and scheduler limits before submitting. This new
adapter has local transport/recovery tests but has not run on real PACE.
Set `OPTION2_REGIONAL_CONTROLLERS=6` (or another count from 1 to 12) when submitting
to configure new regional runs. Frozen older runners retain their original CLI.
`make -C pace submit` and `make -C cloudlab run` retain the older sampled runner;
use Option 2 adapters for the coupled census.

## Measurements and node sizing

On two Wisconsin c220g1 nodes with 16 worker processes, the first full-grid
Starlink epoch evaluated **304,178,710 visible edges** in **132.96 seconds**,
with peak **2.33 GiB RSS per worker**. Capacity filtering reduced subsequent
measurements to **59.37–65.48 seconds** with the laptop coordinator. The first
44,488 centralized assignments and all models' metrics match the initial pilot.
Real full-grid SSH checkpoint recovery passed.

Moving the coordinator inside CloudLab measured **36.33 and 35.94 seconds** at
epochs 0 and 1: roughly **12.04 hours** for 1,200 epochs, excluding setup and
checkpoint overhead. With TCP_NODELAY enabled, a subsequent pilot measured
**33.18 and 33.38 seconds**, projecting **11.09 hours**. Its assignments and all
four models' metrics match the laptop pilot; allocation-local recovery also passed.
These are short calibrations, not a measured full trajectory.
A laptop-coordinator Kuiper epoch took **4.89 seconds**, projecting **1.63 hours**;
Kuiper has not yet been timed with the allocation-local coordinator.
[Machine-readable measurements](calibration-results.json) retain run IDs/hashes.

Two nodes were the initial smoke allocation, not a model restriction. Measure
**four nodes next, then eight** if useful speedup remains. More nodes can distribute
geometry and scoring, but global admission, candidate transfer, and tile imbalance
prevent assuming proportional speedup. Keep provider/seed/catalog/grid identical
in scaling pilots. An extra provider or seed can use another independent allocation.

## Model interpretation and validation

Exact spherical-cap queries and vectorized scores reuse geometry across models.
Greedy admission merges exact ordered streams with lazy refill; no candidate is
permanently pruned by an arbitrary top-K cutoff. Assigned cells/full satellites
can be discarded safely. Deferred acceptance preserves each cell's first three
choices and fixed proposal rounds. Selected pairs are evaluated against current
truth. No satellite is silently dropped on propagation failure.

Tests cover scalar candidate/score/assignment parity, contested global capacity,
ties/refills, stale plans, binary framing, mismatched inputs, TLS client
rejection, local/remote parity, and resumed trajectories. Regional tests cover
quota conservation, geographic boundary ties, unused quotas, scalar/vector parity,
single-controller equivalence, and recovery with changed worker counts. The suite
has 53 tests.

Demand remains synthetic and nonzero in every cell, including oceans. Four cell
slots per satellite remain a model assumption: Starlink admission is capped at
44,488 cells per epoch. High modeled outage on this grid is not a measurement of
Starlink customer service or physical capacity. RF/control/backhaul formulas are
architecture abstractions and are not calibrated predictions at this scale.
Aircraft altitude, motion, and actual traffic are not modeled.

## Optional commercial mask

A future commercial-only study can use a reviewed GeoJSON FeatureCollection with
only currently available polygons, `properties.products` (land/maritime/aviation)
and `properties.status: available`. Its `coverage_provenance` must include all
three `complete_products`, `observed_at`, source URLs, `review_notes`, and `verified`.
Setting verified asserts a real boundary/completeness review; the code cannot
certify it. Inclusion is by cell centroid, with product bits preserved once per cell.

```bash
.venv-option2/bin/python -m harness.grid --resolution 5 \
  --mask data/coverage/commercial-union.geojson --out data/generated/commercial-res5.npz
.venv-option2/bin/python cloudlab/cluster.py start --grid data/generated/commercial-res5.npz
```
