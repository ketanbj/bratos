# KRIOS Bratos experiment

Created October 5, 2026 using the checked-in [profile.py](profile.py).

- Private profile: [KRIOS/bratos-sim](https://www.cloudlab.us/show-profile.php?uuid=2533c468-83b5-46fd-ab23-436a9c3503fb)
- Experiment: [KRIOS/bratos-orbit](https://www.cloudlab.us/status.php?uuid=a1bf88b3-9c03-4e0e-a67a-4b7d99f23c77)
- Cluster: CloudLab Wisconsin, two `c220g1` CPU hosts, Ubuntu 22.04.
- Assigned hosts: `c220g1-030602.wisc.cloudlab.us` (`node0`) and
  `c220g1-030619.wisc.cloudlab.us` (`node1`); login user `ketanbj`.
- Initial lifetime: 16 hours. Approved one-day extension on October 6:
  current expiration **October 7, 2026 at 2:04 PM EDT**.
- Scientific target: full frozen Starlink and Kuiper catalogs, 1,200 epochs at
  five seconds each (100 simulated minutes), one seed per provider.

**Current plan (October 6):** all **2,016,842 Earth H3 resolution-5 cells**,
explicitly labeled a **global simulation**, with the complete frozen catalogs.
This supersedes the commercial-only footprint proposed in the history below.
Missing product/territorial boundaries do not block this global study. Use
`cloudlab/cluster.py` and [the current run guide](../harness/OPTION2.md).

**Production launched October 6 at approximately 10:10 AM EDT.** Both providers
run as independent seed-1 global trajectories, with 1,200 five-second epochs
and a 16-hour execution budget. Coordinators are detached on CloudLab node0;
each completed epoch is checkpointed. The two-node allocation expires October 7
at 2:04 PM EDT.

| Provider | Catalog satellites | Workers | Run directory under `cloudlab/state/` |
|---|---:|---:|---|
| Starlink | 11,122 | 16 (8 per node) | `option2-20261006T140950Z-e90ceb88` |
| Kuiper | 391 | 8 (4 per node) | `option2-20261006T141004Z-347d8fc9` |

Both use the complete 2,016,842-cell grid and frozen source/catalog inputs, with
calibration mode disabled. Combined concurrency is 12 worker processes per node
on 16 physical cores; the head also hosts both coordinators. Concurrent production
timing can differ from the standalone pilot. The ignored
`cloudlab/state/active-global.json` records the active paths and fingerprints.

```bash
.venv-option2/bin/python cloudlab/cluster.py fetch \
  --run cloudlab/state/option2-20261006T140950Z-e90ceb88
.venv-option2/bin/python cloudlab/cluster.py fetch \
  --run cloudlab/state/option2-20261006T141004Z-347d8fc9
```

Inspect the fetched checkpoint's `next_epoch` and final `completed_epochs`;
startup success alone does not establish a completed 100-minute trajectory.

## Hourly local collection

Installed October 6 at approximately 10:19 AM EDT: the user LaunchAgent
`us.bratos.option2.hourly` executes [monitor.py](monitor.py) every 3,600 seconds
and at login. Its first scheduled invocation authenticated successfully,
backed up both trajectories locally, and exited with status 0. The first
observation saw 15 completed Starlink epochs and 164 Kuiper epochs.

It backs up available simulation outputs and controller logs into each run's
existing `cloudlab/state/option2-.../` directory. When a provider finishes, it
checks remote/local SHA-256 hashes, all 1,200 epochs, the frozen fingerprint,
final result configuration, and the complete checkpoint chain, then writes
`collected.json` in that provider's run directory. A successful controller exit
with a partial orbit is flagged as incomplete. Failed runs and transfer errors
are recorded locally and retried on subsequent hourly checks; scientific runs
are not automatically restarted. Once both are verified as collected, subsequent
invocations exit without contacting CloudLab.

- Local status: `cloudlab/state/monitor-status.json` (symlink to the live monitor status).
- Runtime/config/logs: `~/Library/Application Support/BratosMonitor/`.
- Schedule: `~/Library/LaunchAgents/us.bratos.option2.hourly.plist`.
- Outputs: each run's `simulation/`, plus controller/supervisor/server logs.

The Mac must remain powered on and online for local transfers. Missed checks
catch up after wake/login; remote runs continue independently. Retrieve outputs
before the October 7, 2:04 PM EDT allocation expiration. The monitor does not
send email, project messages, or chat notifications; progress/errors are in its
local status and logs.

```bash
cat cloudlab/state/monitor-status.json
launchctl print gui/$(id -u)/us.bratos.option2.hourly
# Disable future checks if needed (does not stop the remote simulations):
launchctl bootout gui/$(id -u) "$HOME/Library/LaunchAgents/us.bratos.option2.hourly.plist"
```

The portal reports **ready**, with startup **Finished** on both nodes. The
existing registered key matches `~/.ssh/id_rsa_airbox.pub`. SSH recognizes it,
and direct authentication through macOS Keychain now works on both nodes. The local SSH aliases
use `IdentityFile ~/.ssh/id_rsa_airbox`, `UseKeychain yes`, `AddKeysToAgent no`,
and `IdentityAgent none`. Authenticate once interactively; SSH can then reuse
the passphrase through Keychain without loading an agent:

```bash
ssh bratos-cloudlab-node0
# Enter the key passphrase, then exit the remote shell.
make -C cloudlab setup
make -C cloudlab smoke
make -C cloudlab wait
```

The ignored `cloudlab/.env` is configured with aliases `bratos-cloudlab-node0`
and `bratos-cloudlab-node1`, using that existing key. These aliases are installed
in the local SSH config. The simulator is deployed on both nodes. The full-catalog smoke test and a
three-epoch calibration passed, with results fetched to `cloudlab/state/`.
At 240 cells, Starlink took 132.80 seconds for three epochs, projecting to
14.76 hours for 1,200 epochs; Kuiper took 5.03 seconds. This is a short-run
extrapolation and runtime can change over the orbit.

The 120-cell calibration passed: Starlink took 45.68 seconds for two epochs,
projecting to 7.61 hours for the full duration; Kuiper took 2.02 seconds,
projecting to about 20 minutes. Allow 7–9 hours overall; these estimates use a
short initial segment and are not guaranteed completion times.

## Cancelled full-orbit run

- ID: `run-20261006T024423Z-a4c18cd6`.
- Started: October 5 at 10:44 PM EDT.
- Cancelled at the user's request: October 5 at 10:49 PM EDT. Both nodes
  report **cancelled** and have no remaining simulator processes.
- Configuration: 120 cells, one seed per provider, all 11,122 Starlink and 391
  Kuiper records, 1,200 five-second epochs, one worker per node.
- Worker limit: 12 hours, ending before the CloudLab experiment expires.
- State and output directory: `cloudlab/state/run-20261006T024423Z-a4c18cd6/`.
- The local collector was stopped and its temporary idle-sleep inhibition ended.
  Logs and collector metadata remain in the run directory.

```bash
make -C cloudlab status
make -C cloudlab logs
make -C cloudlab fetch
```

All 34 local tests passed after fixing remote Bash execution for CloudLab
accounts whose login shell is tcsh. The smoke and calibration results are
retained locally. The cancelled run is not a completed benchmark. The CloudLab
experiment remains allocated. The old production run stays cancelled. The user
has now authorized preparing Option 2, including short full-grid calibrations.

## Historical proposed design — commercial Starlink regions (superseded)

The user selected **regions where Starlink service is commercially available**
as the main geographic domain, including **all commercial products: land,
maritime, and aviation**. Use the union of their verified availability masks
to select candidate cells, retaining product-specific eligibility and reporting
layers. Do not substitute a global sample or whole-country licensing list
for a verified availability mask.

The primary question is how the scheduling methods differ in served demand,
assignment quality, and modeled control overhead for a fixed constellation,
geography, workload, and time window. Comparisons are paired within each
provider. Starlink and Kuiper have different constellation sizes and should
not be interpreted as equivalent-capacity systems.

### Fixed scope and choices to settle

- Retain every unique record in the frozen October 4 catalog, with the same
  reference time and 100-minute window. Tracked catalog records are not proof
  that every satellite is operational.
- Build a dated commercial-availability geographic mask from Starlink's official
  availability source for the selected product. Satellite visibility alone does
  not establish commercial availability.
- Keep grid resolution separate from sample count. H3 resolution 5 contains
  2,016,842 global cells, with mean hexagon area about 253 square kilometers.
  These are modeling units, not a verified representation of Starlink's internal
  service-cell layout. Source: https://h3geo.org/docs/core-library/restable/
- Define geometric coverage separately from scheduled service. Report how many
  cells have a visible satellite at each epoch and over the window, then how much
  demand the scheduler serves under its resource constraints.
- Agree on demand density, per-satellite capacity, and beam/resource semantics
  before interpreting a full-grid result. The current model permits four cell
  assignments per satellite: at most 44,488 simultaneous Starlink assignments
  and 1,564 Kuiper assignments before visibility constraints. Increasing the
  grid with this capacity unchanged fundamentally changes offered load.

### Modeling checks before scaling

The simulator is an architecture-level model. It propagates public orbital data
with SGP4, but demand, RF quality, control latency, and backhaul are modeled.
The distributed algorithm is simulated; this harness does not deploy a real
Raft service. The current "oracle" is a truth-informed greedy scheduler, not a
proven optimal allocation. Describe and validate these assumptions explicitly.

Use a fixed master grid with stable cell IDs and nested subsets for convergence
tests. Preserve cell demand and noise across subsets, account for sampling
weights, and distinguish sampled estimates from a census of the entire grid.
The existing sampler changes locations when the count changes, so it needs
adjustment before using nested-subset comparisons.

### Commercial-region mask and cell count

Use https://www.starlink.com/map and Starlink's official product-specific
coverage/authorization guidance as the primary availability references. Preserve
retrieval time, product, status legend, source files, checksums, and geographic
classification rules. Exclude future service. Treat waitlist and authorization
statuses according to each product's official guidance; a blanket Residential
filter cannot define the union of all commercial products. Do not treat an
entire country as available solely because some service is offered there.
If machine-readable availability polygons
cannot be obtained and validated, document the gap before selecting an explicit
approximation. Do not manufacture an exact cell count from a screenshot.

Convert the verified mask into H3 resolution-5 cells and calculate its actual
cell count and covered area. Define border treatment in advance: fractional
intersection area is preferable for demand weighting; centroid inclusion may
be used as a clearly documented simplified mask. Check coastal regions,
enclaves, the date line, and small islands. Compare the rasterized mask with the
official map in several known available and unavailable locations.

Separate geometric visibility, product availability, and capacity-constrained
service in outputs. If Kuiper is retained, use the same Starlink-region grid
for a controlled algorithm comparison and label that geographic choice; it
does not imply Kuiper offers commercial service in those regions.

The domain includes international oceans where applicable Starlink plans are
available, so it may approach the full two-million-cell resolution-5 grid.
The exact union count is not established. Keep land, territorial water,
international water, and aviation product tags separate, including overlapping
eligibility. Aviation coverage may be projected onto this surface grid for
an architecture experiment; that does not simulate aircraft altitude, movement,
or actual flight demand. Those require a separately specified extension.

Define category-specific demand density and offered-load scenarios. A cell is
an area, not a customer or a beam; do not assign identical nonzero demand to
every ocean cell by default. Use disclosed synthetic demand initially, with
region/product weighting, or choose dated population, shipping, and aviation
datasets before making real-world traffic claims. Capacity sweeps must distinguish
algorithm behavior under load from claims about Starlink's physical capacity.

Product scope matters: Starlink's official support documentation distinguishes
inland, territorial-water, international-ocean, and aviation service products
and approval requirements. Sources checked October 5, 2026:

- https://starlink.com/sg/support/article/09b6cfbe-503a-bacb-6d2d-93222a828b97
- https://starlink.com/ro-md/support/article/50e933eb-54f5-1a77-cc85-c6c8325564cf
- https://starlink.com/id/support/article/4497c64e-d214-803a-bd8d-1f0c957b894d

No verified commercial-region polygon dataset has been acquired yet. The exact
target cell count and a production runtime estimate remain undetermined.

### Performance work required

The current implementation rebuilds candidates seven times per epoch and scans
all cell/satellite pairs. A full resolution-5 Starlink grid means approximately
22.4 billion pair checks per scan. Two nodes currently parallelize independent
provider/seed jobs; extra nodes do not shorten a single seed.

Prioritize reusing geometry and truth candidates, caching invariant quantities,
spatial queries for visible cells, and vectorized calculations. Validate against
the scalar implementation on identical inputs. Parallelize candidate generation
within a seed if needed, while preserving global capacity and assignment
constraints; independently scheduling geographic shards would change the model.
Add epoch checkpoints before long production jobs.

### Stages and launch gates

1. Freeze the scientific definition: geography/mask, grid resolution, workload,
   capacity semantics, metrics, and baseline interpretation.
2. Verify geometry, capacity constraints, and reference outputs on small fixed
   inputs. Establish an explicit correctness tolerance for optimized calculations.
3. Optimize and measure full-catalog runtime, peak memory, candidate count, and
   output size at progressively larger nested subsets. Use representative epochs
   across the 100-minute window, not only the initial segment.
4. Measure single-node and multi-node acceleration of one seed separately from
   throughput across seeds. Do not assume Ray or extra nodes provide acceleration
   without this measurement.
5. Run a production pilot only when measured runtime fits the allocation with
   checkpointing and collection margin. Set seed repetitions using pilot
   variability and the desired confidence interval, not an arbitrary fixed count.
6. Run the agreed campaign and report paired results, uncertainty, geographic
   coverage/convergence, runtime, and modeling limitations. Preserve the frozen
   inputs and complete results for replay.

The current 120-cell measurements demonstrate harness operation. They do not
establish global service coverage or justify a production geography. Resource
count and experiment lifetime will be sized after these gates, without silently
reducing the agreed scientific scope to fit an allocation.

## Geographic pieces and resumable time windows

The user proposed continent-based pieces instead of one monolithic simulation.
Use geographic region as an explicit experiment dimension, while preserving the
all-catalog-satellite input and full 100-minute duration for each regional case.

The proposed land regions are North America, South America, Europe, Africa,
Asia, Oceania, and Antarctica, intersected with the verified commercial-product
availability masks. Keep explicit ocean regions for maritime and over-ocean
aviation coverage: Atlantic, Pacific, Indian, Arctic, and Southern oceans.
Fix the geographic boundary source and handling of islands, inland waters,
continental boundaries, and coasts. Assign each selected H3 cell one geographic
owner so boundaries do not duplicate cells; preserve overlapping product tags.
Empty masked regions should be recorded and skipped explicitly.

### Meaning of independent regional runs

**Historical alternative, superseded by the selected Option 2:** regional architecture case studies. Run each region
with the same orbital reference time and 100-minute duration. Within a region,
the scheduling methods share identical cells, demand, capacity assumptions, and
seed, and can be compared fairly. Regions and independent seeds can execute in
parallel. Keep all catalog satellites, even if many are never visible locally.

Each isolated region receives its own satellite capacity budget. These results
do not equal a globally coupled simulation: a satellite visible in two regions
can have its capacity counted in both isolated experiments. Report regional
outcomes, and label any weighted summary as a summary of independent regional
case studies rather than one globally feasible schedule.

If the scientific question needs globally shared satellite capacity, use region
workers to generate candidates, with a common epoch and shared capacity admission
across regions. Preserve the global centralized ordering and distributed proposal
rounds. Compare this implementation with the unsplit reference on small inputs.
This is a separate parallel implementation, not a simple merge of independent
regional schedules.

### Time pieces

Proposed starting chunk size: 10 simulated minutes, 120 five-second epochs.
Ten sequential chunks cover the 100-minute run for one region and seed. Chunk
size is adjustable after runtime and checkpoint-overhead measurements; it is not
a claim that ten simulated minutes take ten wall-clock minutes.

Checkpoint after each chunk, preserving the periodic centralized plan, absolute
epoch/cadence, stable H3 IDs, deterministic demand/noise state, accumulated raw
metric series, configuration, frozen catalog/mask hashes, and predecessor state
hash. A resumed chunk must match an uninterrupted reference. Time chunks within
one trajectory run sequentially; regions and seeds provide independent parallel
jobs. Merge counts and demand totals with proper weighting; recompute percentiles
from raw samples instead of averaging regional or chunk percentiles.

The legacy harness parallelizes provider/seed jobs and checkpoints only after
a seed completes. Region masks, region job identifiers, absolute epoch offsets,
chunk checkpoints, and validated chunk aggregation must be implemented before
launching this campaign. First measure the largest masked region and memory use,
then choose nodes and workers. Do not assume equal work per continent or a
seven-fold speedup; region area, demand, visibility, and candidate counts differ.

Production simulations remain stopped. Short Option 2 calibrations are authorized and
run through the new coupled harness; see the preparation status below.


## Historical Option 2 preparation — October 5, 2026 EDT

The shared [Option 2 runner](../harness/OPTION2.md) is implemented. It builds a
complete resolution-5 census with stable master-grid identities, partitions
geographic tiles across CPU workers, and preserves global satellite capacity.
The new CloudLab adapter uses the existing SSH-config aliases, frozen inputs,
and persistent workers; the PACE adapter uses `srun` with an allocation-local
coordinator. The existing sampled-run commands are unchanged.

- Complete Earth grid generated: **2,016,842 cells**, 87 MiB, 122 geographic tiles.
- Official country/subnational status feed and seven polygon status overlays
  downloaded, decoded, and frozen in `data/coverage/raw/` with checksums and dates.
- Complete verified land/maritime/aviation union remains unavailable. The Earth
  grid is labeled **global calibration**, and production is rejected with it.
- Checkpoints persist each completed epoch, including the periodic plan, raw
  metric samples, absolute epoch, frozen-input fingerprint, and predecessor hash.
- All **45 tests passed**, including scalar parity, shared capacity, tie/refill
  ordering, implicit proposal rejection, recovery, and a local `srun` stand-in.
  The new PACE adapter has not been exercised on the real cluster.
- A real two-node, full-catalog smoke test passed, and complete-grid calibrations
  now evaluate roughly **304 million visible candidate edges** per epoch.
- Initial full-grid epoch: **132.96 seconds**, maximum **2.33 GiB RSS per worker**.
- Capacity-filtered pilot: **59.37 and 62.15 seconds** at epochs 0 and 1,
  using 16 processes (eight per host). The first epoch has the identical 44,488
  centralized assignments and matching metrics for all four systems.
- Initial-segment extrapolation: **20.25 hours** per 100-minute Starlink seed on
  these two hosts, excluding setup/checkpoints. More allocation time/resources
  would be required, and additional nodes must be measured rather than assuming
  proportional speedup. The late-orbit epoch 1199 passed in **65.48 seconds**,
  evaluating 304,271,829 visible edges. A real full-grid resume at epoch 2 passed
  in **61.55 seconds**, preserving the periodic plan and predecessor chain.

At that stage the CloudLab coordinator ran locally and required an awake, connected
laptop. Failed connections recover from the last persisted epoch. Production
should use an always-on coordinator or the batch-local PACE adapter.

## Current global preparation — October 6, 2026 EDT

- Full Earth study input: `data/generated/global-option2-res5.npz`, 2,016,842 cells,
  SHA-256 `c43107e3e4577ad6790f61af6d7e53c92baad448192118823cf9ea844776b51f`.
- Detached allocation-local coordinator and authenticated TLS workers implemented;
  provisioning/status/collection still use the existing SSH-config identities.
  User SSH keys stay local and no SSH agent is required.
- Full-grid Starlink pilot: **33.18 and 33.38 seconds** per epoch, projecting
  **11.09 hours** for 100 simulated minutes. Earlier allocation-local pilot:
  36.33/35.94 seconds; its real checkpoint resume at epoch 2 also passed.
- Assignments and all four models' metrics match the laptop-coordinator pilot.
  All **46 tests pass**, including TLS authentication and recovered trajectories.
- Extension approved: experiment expires **October 7 at 2:04 PM EDT**.
- Two nodes are the current allocation, not a harness limit. The next scaling
  comparison should measure four, then eight nodes against these identical inputs;
  global admission overhead prevents assuming linear speedup.
- Calibration checkpoints/results have been fetched and owned pilot services
  stopped. At preparation completion the full trajectories had not started;
  they were subsequently launched as recorded above.

See [measured results](../harness/calibration-results.json) and the guide for
production, checkpoint recovery, and PACE commands. The scientific model remains
synthetic, including ocean demand and four cell slots per satellite.

## Regional centralized implementation — October 6, 2026 EDT

The current source adds `regional_centralized` alongside the four existing systems.
Six fixed nearest-controller regions share satellite capacity through a
visibility-proportional quota broker; each region runs its own greedy allocation
with the same observations and score noise as fresh global control. There is no
within-epoch quota borrowing. These are simulated geographic controllers, not
independently deployed services or continent boundaries. See the
[regional policy](../harness/OPTION2.md#regional-centralized-allocation).

All 53 tests passed, including geographic ties, quota conservation, scalar/vector
parity, one-controller equivalence, authenticated transport, and checkpoint
recovery after worker-count changes. A three-epoch full-grid Kuiper pilot,
`option2-20261006T180526Z-f8ca6e11`, completed with all 2,016,842 cells and 391
satellites, eight workers across the existing two hosts. Outputs and checkpoints
were fetched locally and its owned worker services stopped.

All original systems' metrics matched the first three epochs of the completed
Kuiper production run exactly. Regional allocation filled all 1,564 cell slots;
mean utility was 1.42% below fresh global, modeled p95 control latency was 77.7%
lower, and raw-report backhaul was nearly unchanged. This is a short calibration,
not a full-orbit comparison or measured controller network. The
[validation report](../harness/regional-validation.json) records run IDs and metrics.

Existing Starlink/Kuiper production sources remain frozen at four systems, and the
hourly collector remains in place. No new 100-minute trajectory was launched as
part of implementing regional control.
