# K-DBP: Kratos-backed distributed beam prediction

This repository turns a Kratos rolling-replication design into a concrete
LEO beam-prediction proposal, a reproducible
architecture-level benchmark, and an interactive Starlink/Project Kuiper demo.

## What is here

- [`docs/design.md`](docs/design.md) - algorithm, protocol, guarantees,
  benchmark definition, research context, and limitations.
- [`src/beam_sim.py`](src/beam_sim.py) - deterministic simulator for K-DBP and
  centralized baselines using public orbital elements and H3 service cells.
- [`data/orbital_snapshot.json`](data/orbital_snapshot.json) - checked-in,
  reproducible CelesTrak GP snapshot matched to FCC-authorized shells.
- [`scripts/update_orbital_data.py`](scripts/update_orbital_data.py) - refreshes
  the Starlink and Kuiper snapshot from CelesTrak.
- [`tests/test_beam_sim.py`](tests/test_beam_sim.py) - safety and
  reproducibility checks.
- [`benchmark/results.json`](benchmark/results.json) - seven-seed result set
  plus one replay trace.
- [`formal/KDBP.tla`](formal/KDBP.tla) - executable safety and liveness model.
- [`formal/evidence/RESULTS.md`](formal/evidence/RESULTS.md) - TLC state-space
  evidence and deliberately broken protocol variants.
- [`demo`](demo) - interactive replay and benchmark dashboard.

## Reproduce

The simulator now includes `regional_centralized`: configurable geographic
controllers allocate beams within shared per-satellite quotas. Use
`--regional-controllers 6` in either the scalar or Option 2 runner. The
[regional guide](harness/OPTION2.md#regional-centralized-allocation) describes
the policy and full-grid pilot. Existing benchmark artifacts retain their original
systems until regenerated.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r harness/requirements-option2.txt
.venv/bin/python -m unittest discover -s tests -v
cd demo
npm ci
npm run dev
```

Use Python 3.10–3.13 and Node.js 22.13 or newer. `requirements.txt` alone
installs the scalar simulator dependencies; `harness/requirements-option2.txt`
also installs dependencies for the complete-grid harness and its tests.
Run `npm test` inside `demo/` to build and check the rendered replay. Formal
validation is separate: `python3 formal/run_tlc.py` requires Java 11 or newer
and downloads the checksum-verified TLC tool; see [formal instructions](formal/README.md).

The checked-in results drive the demo immediately. Regenerating them runs the
configured simulation and may take substantially longer than the tests:

```bash
.venv/bin/python src/beam_sim.py
```

To refresh the public orbit snapshot before regenerating the benchmark:

```bash
python3 scripts/update_orbital_data.py
.venv/bin/python src/beam_sim.py
```

The checked-in run uses real satellite names and public GP mean elements from
CelesTrak, classified against the Starlink and Kuiper shell parameters in FCC
authorizations. The simulator propagates those OMM records with SGP4 and uses a
GMST-based TEME-to-Earth-fixed conversion for the ground track. Demand, channel
residuals, beam capacity, network latency, and backhaul remain modeled; these
are not RF measurements or results from a running Kratos implementation.

Service locations are a deterministic, equal-area global sample of 240 H3
resolution-5 cells. Every result records the H3 indexes and cell centers. H3
resolution 5 contains 2,016,842 cells globally, so the benchmark samples the
grid rather than evaluating every global cell against every satellite.
The sample size and resolution can be changed when regenerating results, for
example: `src/beam_sim.py --service-cells 480 --h3-resolution 5`.

Each seed evaluates 1,200 five-second epochs, or 100 simulated minutes. The
dashboard stores 101 evenly spaced replay frames across that window while the
benchmark metrics still include every epoch. Seed runs are parallelized by
default when regenerating the results.

## Run large simulations on PACE

The [`pace`](pace/README.md) harness adapts the supplied Ray harness's workflow
for native CPU-only SLURM execution. It distributes provider/seed pairs across
a configurable number of PACE nodes, uses CPU process pools within each node, saves completed seeds for
recovery, and merges results into the demo's benchmark schema.

```bash
make -C pace env
# Edit pace/.env for your PACE SSH connection, shared checkout, and modules.
make -C pace setup
make -C pace dry-run
make -C pace smoke
make -C pace status
make -C pace logs
make -C pace submit
make -C pace fetch
```

The default allocation uses account `gts-jyoung9-csse`, one CPU node with 14
cores and 32 GB of RAM, a one-hour time limit, job name `bratos`, and
`bratos-%j.log`. It requests no GPUs. Node and core counts can be increased
with `SBATCH_NODES` and `SBATCH_CPUS_PER_TASK` as the experiment set grows.
See the harness guide for sizing experiments and resuming completed seeds with
`RESUME_JOB_ID`.

For an existing CloudLab.us experiment, use the separate
[CloudLab backend](cloudlab/README.md). Both backends use the same
[shared runner](harness/README.md):

```bash
make -C cloudlab env
# Configure your experiment's SSH destinations in cloudlab/.env.
make -C cloudlab setup
make -C cloudlab smoke
make -C cloudlab wait
```

It uses independent node storage, detached SSH workers, local checkpoint
collection, and the same simulator/result format as PACE.

For the full-cell **Option 2** experiment, use the
[coupled census runner](harness/OPTION2.md). It partitions geometry across workers
with globally shared satellite capacity, freezes complete catalogs and grid
inputs, and checkpoints after each epoch. Its CloudLab adapter uses existing SSH
config identities; its PACE adapter uses `srun`. The complete Earth resolution-5
grid is the selected production domain: **all 2,016,842 cells**, explicitly
labeled as a global simulation. It makes no commercial-availability claim.
`cloudlab/cluster.py` runs a detached coordinator inside CloudLab. The commands
above retain the sampled runner.

## Repository contents and local files

`demo/` is part of this repository; no submodule checkout is required. Source,
tests, pinned dependencies, public orbital snapshots, public coverage references,
and compact benchmark/calibration artifacts are versioned together.

Virtual environments, `node_modules`, builds, `.env` files, private keys,
per-run TLS material, generated full-Earth grids, and cluster run/checkpoint
directories are ignored. The `.env.example` templates are included. Keep SSH
identities in your local `~/.ssh/config`. Follow the
[Option 2 guide](harness/OPTION2.md) to regenerate the large grid after cloning;
production outputs remain in local or cluster storage and are collected separately.
