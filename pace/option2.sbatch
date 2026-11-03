#!/bin/bash
#SBATCH --account=gts-jyoung9-csse
#SBATCH --partition=cpu-large
#SBATCH --qos=inferno
#SBATCH --constraint=intel&core32
#SBATCH --nodes=40
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=640G
#SBATCH --time=01:00:00
#SBATCH --job-name=bratos-option2
#SBATCH --output=bratos-option2-%j.log

# Short calibration by default. Submit from a shared repository with an
# installed Option 2 venv. Override allocation/walltime only after calibration.
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?}"
option2_python="${OPTION2_PYTHON:-$PWD/.venv-option2/bin/python}"
option2_grid="${OPTION2_GRID:-$PWD/data/generated/global-option2-res5.npz}"
option2_root="$PWD/pace/runs/option2-${SLURM_JOB_ID}"
mkdir -p "$option2_root/inputs/harness" "$option2_root/inputs/src" "$option2_root/inputs/data"
if [[ -n "${OPTION2_RESUME_RUN:-}" ]]; then
  cp -a "$OPTION2_RESUME_RUN/inputs/." "$option2_root/inputs/"
  cp -a "$OPTION2_RESUME_RUN/simulation" "$option2_root/"
else
cp src/beam_sim.py "$option2_root/inputs/src/"
cp harness/coupled.py harness/grid.py harness/tls_transport.py harness/requirements-option2.txt "$option2_root/inputs/harness/"
cp requirements.txt "$option2_root/inputs/"
cp data/orbital_snapshot_full.json "$option2_root/inputs/data/"
cp "$option2_grid" "$option2_root/inputs/data/grid.npz"
fi
mapfile -t option2_nodes < <(scontrol show hostnames "$SLURM_JOB_NODELIST")
option2_hosts=()
for option2_node in "${option2_nodes[@]}"; do
  option2_hosts+=("$option2_node" "$option2_node")
done
option2_args=(--slurm --hosts "${option2_hosts[@]}" --query-workers 8
  --grid "$option2_root/inputs/data/grid.npz"
  --snapshot "$option2_root/inputs/data/orbital_snapshot_full.json"
  --directory "$option2_root/simulation" --provider "${OPTION2_PROVIDER:-starlink}"
  --seed "${OPTION2_SEED:-1}")
if [[ "${OPTION2_PRODUCTION:-0}" != 1 ]]; then
  option2_args+=(--calibration --stop-after "${OPTION2_CALIBRATION_EPOCHS:-2}")
fi
if [[ -n "${OPTION2_RESUME_RUN:-}" ]]; then option2_args+=(--resume); fi
if grep -q -- '--regional-controllers' "$option2_root/inputs/harness/coupled.py"; then
  option2_args+=(--regional-controllers "${OPTION2_REGIONAL_CONTROLLERS:-6}")
fi
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
"$option2_python" "$option2_root/inputs/harness/coupled.py" "${option2_args[@]}"
