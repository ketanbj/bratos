# Shared settings for the 32-core Intel high-memory pool. Source from a profile.
intel_pace_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$intel_pace_dir/.env" ]]; then
  source "$intel_pace_dir/.env"
else
  source "$intel_pace_dir/.env.example"
fi
unset intel_pace_dir

SBATCH_ACCOUNT=gts-jyoung9-csse
SBATCH_PARTITION=cpu-large
SBATCH_QOS=inferno
SBATCH_CONSTRAINT='intel&core32'
SBATCH_GPUS_PER_NODE=
SBATCH_EXTRA_ARGS=
SIM_EXTRA_ARGS=
RESUME_JOB_ID=
H3_RESOLUTION=5
EPOCH_SECONDS=5
ORBITAL_SNAPSHOT=data/orbital_snapshot_full.json
SATELLITES=all
PROVIDERS=starlink,kuiper
SIM_WORKERS=0
