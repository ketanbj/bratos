# Full-constellation simulation for one approximately 100-minute orbit

**Superseded for Option 2:** the active target is a complete resolution-5 census
of all **2,016,842 Earth cells**, explicitly labeled as a global simulation,
with globally shared capacity and
epoch checkpoints. The [new runner and measured CloudLab pilot](../harness/OPTION2.md)
replace the sampled 100,000-cell plan and scalar runtime estimates below. The
40-node Intel pool remains the proposed PACE target; its new transport still
needs cluster calibration. Retain the older measurements below as history.

Updated October 4, 2026. Target: every available satellite record for Starlink
and Kuiper over a common 100-minute window, using Phoenix's 32-core Intel
high-memory pool. All stages use the existing `pace/` harness.

## Experiment definition

| Parameter | Setting |
| --- | --- |
| Satellites | All unique records in each provider's frozen CelesTrak GP catalog |
| Starlink catalog | 11,122 records downloaded October 4, 2026 |
| Kuiper catalog | 391 records downloaded October 4, 2026 |
| Duration | 100 minutes: 1,200 epochs at 5 seconds |
| Geography | Proposed 100,000 H3 resolution-5 cells; calibrate at smaller counts first |
| Systems | Existing centralized, periodic centralized, distributed, and oracle models |
| Repetitions | Begin with one seed for validation; choose scientific repetitions from pilot variability |

The common 100-minute window covers approximately one orbit. Individual orbital
periods calculated from this catalog's mean motions range from 87.32–96.16
minutes for Starlink and 93.33–97.58 minutes for Kuiper. Satellites therefore do
not all return to their starting positions at exactly the same time.

The new `data/orbital_snapshot_full.json` stores every provider GP group record,
including those outside the shell classifier. Catalog coverage means every
available tracked record; operational status is not established by the shell
classifier. The earlier August snapshot contains only 256 sampled records per
provider and cannot satisfy this target.

Both complete catalogs passed SGP4 checks at the start and end of the 100-minute
window. Runtime preflight checks the window endpoints again. Epoch propagation
errors still fail the simulation rather than silently removing a satellite.
Freeze this full snapshot for the campaign. Refreshing data starts a new
experiment. The window starts at the frozen snapshot's reference time.

The cells are a sampled global set. Four beams per satellite imply theoretical
assignment ceilings of 44,488 for Starlink and 1,564 for Kuiper before visibility
and other constraints. Their different constellation sizes therefore represent
different capacities; paired algorithm comparisons within each provider are the
primary comparison. Demand, RF quality, control latency, and backhaul are modeled.

## Intel allocation

Target the pool described in
[PACE's high-memory node announcement](https://pace.gatech.edu/2023/03/07/new-compute-nodes-on-the-phoenix-cluster/):
40 nodes, each with 32 CPU cores and 768 GB advertised RAM, using dual Xeon Gold
6226R processors. The live October 4 scheduler confirmed 40 matching nodes with
772,000 MB configured memory per node. The pool provides 1,280 CPU cores.

```bash
SBATCH_ACCOUNT=gts-jyoung9-csse
SBATCH_PARTITION=cpu-large
SBATCH_CONSTRAINT='intel&core32'
SBATCH_QOS=inferno
SBATCH_GPUS_PER_NODE=
```

The constraint excludes the older 24-core machines in the same partition.
Forty is the pool size, not an availability guarantee. Scheduler test-only
checks accepted 1-, 4-, and 40-node requests with 32 cores and 640G per node.
No compute jobs were submitted by those checks.

Calibration uses one CPU and 20G. Full-node tests use 32 CPUs and 640G per node.
The memory requests are provisional, not measured requirements. Neither profile
requests exclusive nodes. Reduce memory after measuring aggregate worker load.
The inspected `embers` QOS limit was eight hours; the account defaults to
`inferno`. The inspected partition maximum was 21 days, subject to other limits.
Recheck scheduler settings before production submission.

## Runtime estimate and feasibility

The prior local benchmark took 43.384 seconds for one Starlink seed with 10,000
cells, 256 satellites, and four epochs. The following estimates extrapolate
cells × satellites × epochs. They assume similar per-core speed and exclude
queue waiting, provider differences, concurrency slowdown, sorting growth, and
startup/merge overhead. They are not measured Phoenix runtimes.

| Work | Allocation | Local-baseline estimate |
| --- | --- | ---: |
| New calibration: 1,000 cells, 2 epochs, all satellites, one seed/provider | 1 CPU; providers sequential | 1.6 minutes |
| New node load: 10,000 cells, 2 epochs, all Starlink satellites, 32 seeds | 1 node, 32 workers | 16 minutes |
| Target-geography short test: 100,000 cells, 2 epochs, one seed/provider | 1 CPU; providers sequential | 54 minutes |
| 100-minute Starlink simulation: 10,000 cells, one seed | 1 worker | 6.5 days |
| 100-minute Starlink simulation: 100,000 cells, one seed | 1 worker | 65 days |
| 100-minute Kuiper simulation: 100,000 cells, one seed | 1 worker | 55 hours / 2.3 days |

The earlier five-day estimate described only 256 satellites over six simulated
hours. It is superseded by this full-catalog, 100-minute target. Shorter duration
does not offset the roughly 43-fold increase in Starlink satellite count.

The current scalar implementation is not ready for full-size Starlink production:
the rough 65-day estimate exceeds the inspected 21-day allocation maximum.
More nodes currently run more seeds concurrently; each seed's epochs are still
sequential. Allocating all 40 nodes cannot reduce one seed's elapsed time.

At this baseline, reaching 24 hours requires roughly 65x per-seed acceleration;
reaching 12 hours requires roughly 130x. These are required acceleration targets,
not demonstrated speedups. The two-hour load-test and 30-minute calibration
limits are buffers for their short tests, not production walltimes.

## Staged plan

### 1. Validate and calibrate the complete catalog

The full-catalog path and satellite mode are explicit:

```bash
ORBITAL_SNAPSHOT=data/orbital_snapshot_full.json
SATELLITES=all
EPOCH_SECONDS=5
# Production simulation duration, after performance gates pass:
EPOCHS=1200
```

`SATELLITES=all` resolves independently for each provider and rejects sampled,
incomplete, empty, or duplicate catalogs. Satellite IDs remain stable across
seeds when the whole catalog is used. Frozen jobs copy the selected snapshot
into their own inputs and fingerprint its contents.

```bash
make -C pace dry-run ENV_FILE=profiles/intel-calibration.env
# Submit the short calibration when ready:
make -C pace submit ENV_FILE=profiles/intel-calibration.env
make -C pace status ENV_FILE=profiles/intel-calibration.env
make -C pace fetch ENV_FILE=profiles/intel-calibration.env
```

This profile uses 1,000 cells, two epochs, all satellites, both providers, and
one seed on one CPU. Measure each provider separately using `PROVIDERS=starlink`
or `PROVIDERS=kuiper`. Increase cells to 10,000 with a one-hour limit, then
100,000 with a two-hour limit, retaining two epochs until measured runtime
supports a longer test. Preserve the full satellite count throughout.

Gate: correct output for both providers, verified coverage of catalog IDs,
measured seconds per epoch, and measured peak memory at the target cell count.
Record elapsed/CPU time, exit status, output size, and scheduler accounting.

### 2. Remove the geometry bottleneck

Existing profiling identified `candidate_edges` as the dominant cost. The
current algorithm rebuilds candidates seven times per epoch and scans every
cell/satellite pair. At 100,000 cells and 11,122 satellites, each such scan
contains about 1.1 billion pairs.

Priorities, not yet implemented in this change:

1. Reuse per-epoch truth candidates across the four plan evaluations.
2. Cache visibility radii and invariant trigonometric calculations.
3. Use a spherical spatial index to find visible cells for each satellite,
   avoiding most all-pairs distance calculations; vectorize remaining geometry.
4. If required, parallelize candidate generation within a seed. Preserve global
   assignment constraints when combining candidates; independently solving
   satellite subsets would change the algorithms being compared.
5. Add epoch checkpoints if a long seed cannot be safely restarted in full.

Verify assignments, safety invariants, and metrics against the existing code
before accepting any optimized implementation. Repeat calibration afterward.
Simple caching alone should not be assumed to meet the required acceleration.

Gate: measured full-catalog throughput supports 1,200 epochs within the selected
walltime and campaign budget. Retain all satellites and the 100-minute duration;
if the proposed geography is infeasible, explicitly revise the cell count.

### 3. Measure full-node and multi-node scaling

```bash
make -C pace dry-run ENV_FILE=profiles/intel-node-load.env
# After single-worker calibration:
make -C pace submit ENV_FILE=profiles/intel-node-load.env
```

This profile uses all Starlink satellites, 10,000 cells, two epochs, and 32 seeds
on 32 workers. Thirty-two seeds fill a node for a throughput test; they do not
establish statistical sample size. Starlink and Kuiper production jobs should
be submitted separately because their workload sizes differ substantially.

Repeat the load test at 100,000 cells and one epoch once memory estimates permit.
Then increase epochs to measure growth of stored per-epoch metrics. Inspect
aggregate node or cgroup memory; scheduler `MaxRSS` alone may not represent the
sum of every worker's peak. Add memory sampling if accounting is insufficient.

Gate: no OOM failures, aggregate peak below 80% of requested memory, and measured
throughput supporting the worker count. Otherwise cap `SIM_WORKERS`, adjust
memory, and retest. If within-seed parallelism is implemented, benchmark one
seed on 1, 2, and 4 nodes before requesting the full pool. The current harness
does not implement that mode.

### 4. Run one orbit, then choose scientific repetitions

After optimization, run one full-catalog, 1,200-epoch seed per provider. Freeze
code, catalog, and environment, then estimate variability using separately
labeled pilot seeds. Choose a fixed confirmation seed count from precision and
budget, rather than using 640 seeds simply to fill 40 nodes.

A proposed primary precision target is a 95% confidence interval half-width of
0.25 percentage points for seed-level distributed-minus-centralized demand
satisfaction. Agree meaningful precision before confirmation. Pilot variance
informs sample size; epochs within a seed are not independent replicates.
Keep pilot and confirmation seeds distinct and prespecify the final seed list.

## Resources and recovery

```text
per-seed time ≈ startup + 1,200 × measured seconds per epoch
workers/node ≤ min(32, floor((0.8 × requested RAM − shared overhead) / worker peak RAM))
job time ≈ number of worker waves × slow-seed time + imbalance and I/O overhead
allocated CPU-hours = nodes × allocated cores/node × elapsed wall-clock hours
```

Include a provisional 50% walltime margin until runtime variability is measured.
Forty nodes allocated for 12 hours have a 15,360 CPU-hour ceiling. This is
resource arithmetic, not a monetary price or account balance.

Retain job IDs, frozen configurations, data/code hashes, manifests, logs,
accounting, and checkpoints. The harness tracks only the latest `.jobid`; record
all IDs separately for simultaneous provider jobs. Completed seeds can be
recovered with `RESUME_JOB_ID` under identical simulation inputs. Reordering
seeds or changing duration, geography, catalog, or code invalidates checkpoints.
Unfinished seeds restart from the beginning; epoch checkpointing remains planned.

Profiles inherit connection settings from `pace/.env`, falling back to
`.env.example`; command-line overrides take precedence. Refresh a new complete
catalog using the documented [CelesTrak GP JSON query format](https://www.celestrak.org/NORAD/documentation/gp-data-formats.php):

```bash
.venv/bin/python scripts/update_orbital_data.py --all-satellites --out data/orbital_snapshot_full.json
```
