# Run on CloudLab

For the full-cell **Option 2** experiment, use [the coupled census guide](../harness/OPTION2.md)
and `cloudlab/cluster.py`. Its detached CloudLab coordinator shares satellite
capacity across geographic workers and checkpoints each epoch. The selected
domain is all **2,016,842 Earth cells**, labeled as a global simulation. The
commands below retain the original sampled,
independent-seed workflow.

This adapter runs the simulator on SSH-accessible nodes in an existing
[CloudLab.us experiment](https://docs.cloudlab.us/getting-started.html). It lives
alongside the `pace/` backend and uses the shared seed worker, input validation,
checkpoint format, and result merge. It needs neither SLURM nor Ray nor a shared
filesystem. It does not create, reserve, extend, or delete CloudLab experiments.

## Provision an experiment

Upload [profile.py](profile.py) through CloudLab's **Experiments → Create
Experiment Profile** page, selecting your project and project-only visibility.
CloudLab validates the Python source and generates its resource request. Create
the profile, then instantiate it in the same project. The default is two CPU
hosts running Ubuntu 22.04, one job for each provider with one seed. Node count
and hardware type are configurable; blank hardware type lets the selected
cluster choose a compatible node. No GPUs or inter-node data network are needed.

The profile installs Python, venv, and rsync prerequisites from Ubuntu's package
repositories during startup. Wait for startup completion before using `setup`.
Simulation code and the frozen catalog are transferred by the harness, so no
public Git repository or stored repository credentials are required. The profile
provisions hosts; it does not automatically start a scientific run.

Choose an experiment lifetime that leaves time for setup, calibration, the
measured full run, and result retrieval. Use the portal's List View to obtain
the actual SSH addresses, then follow the configuration steps below.

## Configure and set up nodes

Use Ubuntu 22.04 or another Linux image with Python >=3.10, `python3-venv`, and
`rsync`. Nodes and your laptop need `rsync`; SSH key access must already work.
The CloudLab experiment List View supplies SSH login details. Put nonstandard
ports and key paths in your local `~/.ssh/config`, for example:

```sshconfig
Host bratos-node0
  HostName REPLACE_WITH_CLOUDLAB_HOST
  User REPLACE_WITH_CLOUDLAB_USERNAME
  Port 22
  IdentityFile ~/.ssh/REPLACE_WITH_YOUR_KEY
```

Normal host-key checking remains enabled. Connect manually once to verify each
node's identity before using the harness. Configure aliases for the other nodes.
On macOS, an encrypted key can be used directly with `UseKeychain yes`,
`AddKeysToAgent no`, and `IdentityAgent none` in these entries. Authenticate once
interactively to enter the passphrase; subsequent harness connections can use
Keychain without `ssh-add`. Remote commands run through Bash even when the
CloudLab login shell is tcsh.

```bash
make -C cloudlab env
# Edit cloudlab/.env:
# CLOUDLAB_HOSTS="bratos-node0 bratos-node1"
# CLOUDLAB_REMOTE_DIR=/users/YOUR_USERNAME/bratos-cloudlab
make -C cloudlab dry-run
make -C cloudlab setup
```

Choose a dedicated directory writable by your experiment user. Setup refuses
an existing nonempty directory without the harness marker. It creates per-node
virtual environments and installs the pinned requirements; it does not modify
system packages. If Ubuntu lacks prerequisites, install them on each node with
`sudo apt-get update && sudo apt-get install -y python3-venv rsync` first.

`dry-run` reads local configuration and data only; it makes no SSH connections,
transfers, directories, or launches. Command-line Make overrides win over `.env`.
`setup` is explicit and does not automatically run before every experiment.

## Validate, run, and collect

```bash
make -C cloudlab smoke
make -C cloudlab wait
make -C cloudlab status
make -C cloudlab logs
make -C cloudlab fetch
```

The smoke run uses both complete satellite catalogs, 240 cells, one epoch, one
seed, and at most the first two configured hosts. It preserves the configured
CPU and time cap while limiting workers to one per node.

The default scientific configuration is all catalog records, both providers,
1,000 cells, and 1,200 five-second epochs: 100 simulated minutes. Its one-hour
worker cap is a safety limit, not a runtime estimate. Calibrate before attempting
the full duration, and set `CLOUDLAB_TIME_LIMIT_SECONDS` to a measured runtime
that fits within the remaining CloudLab experiment lifetime. Zero disables the
worker time cap; experiment expiration still stops access to its nodes.

```bash
# A short full-catalog calibration on one selected host:
make -C cloudlab run CLOUDLAB_HOSTS=bratos-node0 EPOCHS=2
make -C cloudlab wait
# After calibration and performance improvements, use the configured target:
make -C cloudlab run
make -C cloudlab wait
```

Each launch copies immutable source, orbital data, and configuration into a
unique remote run directory on every node. Checksums, imports, catalog coverage,
propagation endpoints, and CPU availability are checked before starting workers.
All nodes must use identical Python, H3, and SGP4 versions. Configure
`CLOUDLAB_CPUS_PER_NODE` and optionally `SIM_WORKERS` after measuring memory.
Unused cores do not accelerate a sequential seed.

Runs detach from SSH, so disconnecting does not terminate them. `wait` repeatedly
fetches completed checkpoints and checks progress; stopping local `wait` leaves
workers running. `fetch` can also be called manually during a run. Failed or
unreachable nodes are reported, and partial cached checkpoints are retained.
Other workers continue until cancelled or their configured limit expires.

After every worker completes successfully, checkpoints are merged on the first
configured node using its verified environment, then the result is downloaded.
This avoids requiring your laptop to use the same Python version. Node-local
storage is independent; collection uses your laptop's SSH credentials and does
not require node-to-node SSH keys or NFS configuration.

## Outputs and recovery

Local output is under `cloudlab/state/RUN_ID/`:

- `inputs/`: frozen simulator, worker scripts, dependencies, and catalog.
- `run.json`: hosts, effective settings, source hashes, dependency versions.
- `node-N/`: fetched node logs, status, and checkpoints.
- `shards/`: combined durable checkpoints cached on your laptop.
- `results.json`: validated complete benchmark in the demo's existing schema.

The latest ID is recorded in `cloudlab/state/latest`. Select an older run
with `CLOUDLAB_RUN_ID=run-...` on status/logs/fetch/cancel/wait commands. Those
commands use the run's recorded hosts and paths, even if `.env` was later edited.

```bash
make -C cloudlab fetch CLOUDLAB_RUN_ID=run-OLD
make -C cloudlab run CLOUDLAB_RESUME_RUN_ID=run-OLD
make -C cloudlab wait
make -C cloudlab cancel
```

Resume uses previously fetched checkpoints and validates their fingerprints
before starting any worker. Change hosts/node count/CPU count if needed, while
keeping source, catalog, Python/dependency versions, and simulation settings
identical. Preserve old seed order if appending seeds. Unfinished seeds restart
from the beginning; there is no epoch checkpoint or automatic error retry.
Cancellation checks process identity and targets only the recorded run.

CloudLab experiment disks may be lost when the experiment ends. Keep `wait`
running or fetch periodically, and download results before expiration; completed
checkpoints that were never fetched cannot be recovered after node storage is
lost. The adapter does not extend the experiment automatically.

The simulator's full-constellation geometry bottleneck remains unchanged. See
the [100-minute campaign plan](../pace/LARGE_RUN_PLAN.md) before requesting a long run.
