# Shared simulation runner

Platform-independent code lives here. Cluster-specific launchers live separately:

The complete-grid experiment uses [Option 2](OPTION2.md): `grid.py` builds a stable
H3 census and `coupled.py` shares satellite admission across spatial workers.
It supports local processes, CloudLab SSH provisioning with authenticated TLS
workers and a detached coordinator, and PACE `srun` steps, with
durable epoch checkpoints. `distributed.py` below is the earlier independent-seed
runner for sampled simulations.

The current runner compares fresh global, periodic global, regional centralized,
distributed, and truth-informed greedy allocation. `--regional-controllers`
configures fixed geographic regions, with shared satellite quotas rather than
independent capacity per worker. See [the regional policy](OPTION2.md#regional-centralized-allocation).

```text
harness/     input validation, seed distribution, checkpoints, result merge
pace/        Phoenix SLURM submission, remote sync, scheduler monitoring
cloudlab/    SSH launch, worker supervision, checkpoint collection
```

`distributed.py` splits provider/seed jobs among nodes and manages durable
checkpoints. It accepts `BEAM_RANK`, `BEAM_NODES`, and `BEAM_CPUS`; the PACE
backend can also supply the corresponding SLURM variables. It validates
checkpoint fingerprints before recovery or merging.

`preflight.py` checks catalog coverage, orbital propagation endpoints, H3 limits,
and the number of independent jobs. `config.sh` loads backend `.env` files while
preserving explicit environment and Make overrides.

Each backend freezes the shared code and selected simulator/catalog into a run
directory. Layout changes alter checkpoint fingerprints; older frozen runs
retain their original code. Do not mix checkpoints produced by different runner
versions.
