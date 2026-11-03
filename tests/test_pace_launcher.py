import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class PaceLauncherTests(unittest.TestCase):
    def test_full_catalog_is_frozen_and_runs_provider_specific_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            config = self.config(temp)
            snapshot = json.loads((REPO_ROOT / "data/orbital_snapshot.json").read_text())
            for provider, count in (("starlink", 10), ("kuiper", 12)):
                catalog = snapshot["providers"][provider]
                catalog.update(selection="all", records_available=count)
                catalog["sampled_orbits"] = catalog["sampled_orbits"][:count]
            source = temp / "full.json"
            source.write_text(json.dumps(snapshot))
            with config.open("a") as stream:
                stream.write(f"SATELLITES=all\nORBITAL_SNAPSHOT={source}\nSERVICE_CELLS=240\n")
            captured = temp / "captured.json"
            env = {**os.environ, "ENV_FILE": str(config), "SBATCH_BIN": str(self.scheduler(temp)), "CAPTURE": str(captured)}
            subprocess.run(["bash", str(REPO_ROOT / "pace/submit.sh")], env=env, check=True, capture_output=True)
            runtime = json.loads(captured.read_text())
            root = Path(runtime["PROJECT_ROOT"])
            self.assertEqual(json.loads((root / "data/orbital_snapshot.json").read_text()), snapshot)
            source.write_text("{}")
            srun = temp / "srun"
            srun.write_text('#!/usr/bin/env bash\nwhile [[ "$1" == --* ]]; do shift; done\nexec "$@"\n')
            srun.chmod(0o755)
            run_env = {**os.environ, **runtime, "PATH": f"{temp}:{os.environ['PATH']}", "SLURM_JOB_ID": "4242", "SLURM_CPUS_PER_TASK": "2"}
            completed = subprocess.run(["bash", str(root / "pace/beam_sim.sbatch")], env=run_env, text=True, capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            result = json.loads((temp / "results/results-4242.json").read_text())
            self.assertEqual({key: value["config"]["satellites"] for key, value in result["profiles"].items()},
                             {"starlink": 10, "kuiper": 12})

    def test_sync_sha256_fallback_rejects_real_difference(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            local = temp / "local"
            remote = temp / "remote"
            local.mkdir()
            remote.mkdir()
            (local / "input").write_text("same bytes")
            (remote / "input").write_text("same bytes")
            program = subprocess.run([str(REPO_ROOT / ".venv/bin/python"), str(REPO_ROOT / "pace/verify_rsync.py"), str(local)],
                                     input="<fc...... input\n", text=True, capture_output=True, check=True).stdout
            verified = subprocess.run([str(REPO_ROOT / ".venv/bin/python"), "-", str(remote)], input=program,
                                      text=True, capture_output=True)
            self.assertEqual(verified.returncode, 0)
            (remote / "input").write_text("changed bytes")
            rejected = subprocess.run([str(REPO_ROOT / ".venv/bin/python"), "-", str(remote)], input=program,
                                      text=True, capture_output=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("SHA-256 mismatch", rejected.stderr)

    def test_three_node_checkpoints_match_serial_and_resume_missing_seed(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            shards = temp / "shards"
            output = temp / "merged.json"
            python = str(REPO_ROOT / ".venv/bin/python")
            script = str(REPO_ROOT / "harness/distributed.py")
            env = {**os.environ, "SEEDS": "7,19", "PROVIDERS": "starlink,kuiper",
                   "EPOCHS": "2", "EPOCH_SECONDS": "5", "SERVICE_CELLS": "12",
                   "H3_RESOLUTION": "5", "SATELLITES": "10", "SIM_WORKERS": "0",
                   "SLURM_NTASKS": "3", "SLURM_CPUS_PER_TASK": "2"}
            for rank in range(3):
                subprocess.run([python, script, "worker", str(shards)], env={**env, "SLURM_PROCID": str(rank)}, check=True, capture_output=True)
            subprocess.run([python, script, "merge", str(shards), str(output)], env=env, check=True, capture_output=True)
            serial = temp / "serial.json"
            subprocess.run([python, str(REPO_ROOT / "src/beam_sim.py"), "--out", str(serial), "--demo-out", "",
                            "--epochs", "2", "--service-cells", "12", "--satellites", "10", "--seeds", "7,19", "--workers", "1"],
                           check=True, capture_output=True)
            self.assertEqual(json.loads(output.read_text()), json.loads(serial.read_text()))
            previous = temp / "previous"
            shards.rename(previous)
            # Simulate a partial failed job: one provider/seed never finished.
            (previous / "kuiper-seed-1.json").unlink()
            subprocess.run([python, script, "prepare", str(shards), str(previous)], env=env, check=True, capture_output=True)
            incomplete = subprocess.run([python, script, "merge", str(shards), str(output)], env=env, capture_output=True)
            self.assertNotEqual(incomplete.returncode, 0)
            completed_paths = {path: path.stat().st_mtime_ns for path in shards.glob("*.json")}
            resumed = ""
            for rank in range(3):
                completed = subprocess.run([python, script, "worker", str(shards)], env={**env, "SLURM_PROCID": str(rank)}, text=True, check=True, capture_output=True)
                resumed += completed.stdout
            self.assertIn("Resumed starlink", resumed)
            self.assertIn("Checkpointed kuiper seed 19", resumed)
            self.assertTrue(all(path.stat().st_mtime_ns == timestamp for path, timestamp in completed_paths.items()))
            subprocess.run([python, script, "merge", str(shards), str(output)], env=env, check=True, capture_output=True)
            self.assertEqual(json.loads(output.read_text()), json.loads(serial.read_text()))
            mismatch = subprocess.run([python, script, "prepare", str(temp / "invalid"), str(previous)], env={**env, "EPOCHS": "3"}, text=True, capture_output=True)
            self.assertNotEqual(mismatch.returncode, 0)
            self.assertIn("inputs do not match", mismatch.stderr)

    def test_remote_submit_forwards_effective_config(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            config = self.config(temp)
            remote = temp / "remote"
            captured = temp / "remote-command"
            ssh = temp / "ssh"
            ssh.write_text('#!/usr/bin/env bash\nshift\nif [[ "$*" == "bash -s" ]]; then cat > "$REMOTE_CAPTURE"; else exec "$@"; fi\n')
            ssh.chmod(0o755)
            rsync = temp / "rsync"
            rsync.write_text("#!/usr/bin/env bash\nexit 0\n")
            rsync.chmod(0o755)
            with config.open("a") as stream:
                stream.write(f"PACE_LOGIN=phoenix\nPACE_REMOTE_DIR={remote}\nPACE_SSH={ssh}\nPACE_RSYNC={rsync}\n")
            subprocess.run(["make", "-C", str(REPO_ROOT / "pace"), "submit", f"ENV_FILE={config}", "EPOCHS=7"],
                           env={**os.environ, "REMOTE_CAPTURE": str(captured)}, check=True, capture_output=True)
            command = captured.read_text()
            self.assertIn("export EPOCHS=7", command)
            self.assertIn("export ENV_FILE=/dev/null REMOTE_BYPASS=1", command)
            self.assertIn("exec bash workflow.sh submit", command)

    def config(self, temp):
        path = temp / "pace.env"
        path.write_text(
            "PACE_LOGIN=\nSERVICE_CELLS=12\nH3_RESOLUTION=5\n"
            "EPOCHS=2\nEPOCH_SECONDS=5\nSATELLITES=10\n"
            "PROVIDERS=starlink,kuiper\nSEEDS=7,19\n"
            "SBATCH_CPUS_PER_TASK=2\nSIM_WORKERS=0\n"
            f"VENV_ACTIVATE={REPO_ROOT}/.venv/bin/activate\n"
            f"RESULTS_DIR={temp}/results\nLOG_DIR={temp}/logs\n"
            f"RUNS_DIR={temp}/runs\nJOB_FILE={temp}/jobid\n"
        )
        return path

    def scheduler(self, temp):
        script = temp / "sbatch"
        script.write_text(
            "#!/usr/bin/env bash\n"
            "python3 - <<'PY'\n"
            "import json, os\n"
            "from pathlib import Path\n"
            "Path(os.environ['CAPTURE']).write_text(json.dumps({"
            "k: os.environ[k] for k in ['ENV_FILE','PROJECT_ROOT','PACE_WORKFLOW_DIR']}))\n"
            "PY\n"
            "echo 'Submitted batch job 4242'\n"
        )
        script.chmod(0o755)
        return script

    def test_queued_job_uses_frozen_inputs_and_runs_real_simulator(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            config = self.config(temp)
            scheduler = self.scheduler(temp)
            captured = temp / "captured.json"
            env = {**os.environ, "ENV_FILE": str(config), "SBATCH_BIN": str(scheduler), "CAPTURE": str(captured)}
            subprocess.run(["bash", str(REPO_ROOT / "pace/submit.sh")], env=env, check=True, capture_output=True)
            runtime = json.loads(captured.read_text())
            root = Path(runtime["PROJECT_ROOT"])
            original_hash = hashlib.sha256((REPO_ROOT / "src/beam_sim.py").read_bytes()).hexdigest()
            self.assertEqual(hashlib.sha256((root / "src/beam_sim.py").read_bytes()).hexdigest(), original_hash)
            self.assertEqual((root / "data/orbital_snapshot.json").read_bytes(), (REPO_ROOT / "data/orbital_snapshot.json").read_bytes())
            # Editing the user configuration while the job waits must not affect it.
            config.write_text("EPOCHS=99\nPROVIDERS=kuiper\n")
            srun = temp / "srun"
            srun.write_text('#!/usr/bin/env bash\nwhile [[ "$1" == --* ]]; do shift; done\nexec "$@"\n')
            srun.chmod(0o755)
            run_env = {**os.environ, **runtime, "PATH": f"{temp}:{os.environ['PATH']}", "SLURM_JOB_ID": "4242", "SLURM_CPUS_PER_TASK": "2"}
            completed = subprocess.run(["bash", str(root / "pace/beam_sim.sbatch")], env=run_env, text=True, capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            data = json.loads((temp / "results/results-4242.json").read_text())
            self.assertEqual(set(data["profiles"]), {"starlink", "kuiper"})
            for profile in data["profiles"].values():
                self.assertEqual(profile["config"]["epochs"], 2)
                self.assertEqual(profile["seeds"], [7, 19])
            manifest = json.loads((temp / "results/run-4242.json").read_text())
            self.assertEqual(manifest["state"], "completed")
            self.assertEqual(manifest["exit_code"], 0)
            self.assertEqual(manifest["input_sha256"]["src/beam_sim.py"], original_hash)
            self.assertIn("Completed:", completed.stdout)

    def test_failed_job_records_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            config = self.config(temp)
            captured = temp / "captured.json"
            env = {**os.environ, "ENV_FILE": str(config), "SBATCH_BIN": str(self.scheduler(temp)), "CAPTURE": str(captured)}
            subprocess.run(["bash", str(REPO_ROOT / "pace/submit.sh")], env=env, check=True, capture_output=True)
            runtime = json.loads(captured.read_text())
            srun = temp / "srun"
            srun.write_text("#!/usr/bin/env bash\nexit 42\n")
            srun.chmod(0o755)
            env = {**os.environ, **runtime, "PATH": f"{temp}:{os.environ['PATH']}", "SLURM_JOB_ID": "4242", "SLURM_CPUS_PER_TASK": "2"}
            completed = subprocess.run(["bash", str(Path(runtime["PACE_WORKFLOW_DIR"]) / "beam_sim.sbatch")], env=env, capture_output=True)
            self.assertEqual(completed.returncode, 42, completed.stderr)
            manifest = json.loads((temp / "results/run-4242.json").read_text())
            self.assertEqual(manifest["state"], "failed")
            self.assertEqual(manifest["exit_code"], 42)

    def test_dry_run_honors_make_overrides_without_ssh_or_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            config = self.config(temp)
            with config.open("a") as stream:
                stream.write("PACE_LOGIN=phoenix\nPACE_REMOTE_DIR=/storage/example/bratos\nPACE_SSH=/not/ssh\n")
            completed = subprocess.run(
                ["make", "-C", str(REPO_ROOT / "pace"), "dry-run", f"ENV_FILE={config}", "EPOCHS=7", "SBATCH_BIN=/not/sbatch"],
                env=os.environ, text=True, capture_output=True, check=True,
            )
            self.assertIn("7 epochs", completed.stdout)
            self.assertIn("Preview only", completed.stdout)
            self.assertFalse((temp / "runs").exists())
            self.assertFalse((temp / "jobid").exists())

    def test_invalid_configuration_does_not_submit(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            config = self.config(temp)
            for overrides in ({"PROVIDERS": "unknown"}, {"SIM_WORKERS": "3"}, {"SERVICE_CELLS": "0"}, {"SBATCH_EXTRA_ARGS": "--nodes=4"}):
                with self.subTest(overrides=overrides):
                    env = {**os.environ, "ENV_FILE": str(config), "SBATCH_BIN": "/not/sbatch", **overrides}
                    completed = subprocess.run(["bash", str(REPO_ROOT / "pace/submit.sh")], env=env, text=True, capture_output=True)
                    self.assertEqual(completed.returncode, 2)
                    self.assertFalse((temp / "jobid").exists())

    def test_sync_mirrors_and_checksum_verifies_managed_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            fake_ssh = temp / "ssh"
            fake_rsync = temp / "rsync"
            captured_args = temp / "rsync-args"
            remote_dir = temp / "remote"
            fake_ssh.write_text("#!/usr/bin/env bash\nshift\nexec \"$@\"\n")
            fake_rsync.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' '--- invocation ---' \"$@\" >> \"$FAKE_RSYNC_ARGS\"\n"
            )
            fake_ssh.chmod(0o755)
            fake_rsync.chmod(0o755)
            env = {
                **os.environ,
                "PACE_LOGIN": "user@login.example.edu",
                "PACE_REMOTE_DIR": str(remote_dir),
                "PACE_SSH": str(fake_ssh),
                "PACE_RSYNC": str(fake_rsync),
                "FAKE_RSYNC_ARGS": str(captured_args),
            }

            completed = subprocess.run(
                ["bash", str(REPO_ROOT / "pace" / "sync.sh")],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )

            args = captured_args.read_text()
            self.assertEqual(args.count("--- invocation ---"), 2)
            self.assertIn("--delete\n", args)
            self.assertIn("--checksum\n", args)
            self.assertIn("--dry-run\n", args)
            self.assertIn("pace/results/\n", args)
            self.assertIn(".bratos-pace-sync-root\n", args)
            self.assertTrue((remote_dir / ".bratos-pace-sync-root").is_file())
            self.assertIn("byte-for-byte in sync", completed.stdout)

    def test_sync_refuses_nonempty_unmarked_remote_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            remote_dir = temp / "not-a-checkout"
            remote_dir.mkdir()
            (remote_dir / "important-data").write_text("keep me")
            fake_ssh = temp / "ssh"
            fake_rsync = temp / "rsync"
            fake_ssh.write_text("#!/usr/bin/env bash\nshift\nexec \"$@\"\n")
            fake_rsync.write_text("#!/usr/bin/env bash\nexit 99\n")
            fake_ssh.chmod(0o755)
            fake_rsync.chmod(0o755)
            env = {
                **os.environ,
                "PACE_LOGIN": "user@login.example.edu",
                "PACE_REMOTE_DIR": str(remote_dir),
                "PACE_SSH": str(fake_ssh),
                "PACE_RSYNC": str(fake_rsync),
            }

            completed = subprocess.run(
                ["bash", str(REPO_ROOT / "pace" / "sync.sh")],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("non-empty, unmarked directory", completed.stdout)
            self.assertEqual((remote_dir / "important-data").read_text(), "keep me")
            self.assertFalse((remote_dir / ".bratos-pace-sync-root").exists())

    def test_submit_tracks_job_and_passes_simulation_resources(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            fake_sbatch = temp / "sbatch"
            captured_args = temp / "sbatch-args"
            fake_sbatch.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$@\" > \"$FAKE_SBATCH_ARGS\"\n"
                "echo 'Submitted batch job 4242'\n"
            )
            fake_sbatch.chmod(0o755)
            config = temp / "pace.env"
            config.write_text(
                "SERVICE_CELLS=10000\n"
                "H3_RESOLUTION=5\n"
                "EPOCHS=100\n"
                "EPOCH_SECONDS=5\n"
                "SATELLITES=96\n"
                "PROVIDERS=starlink,kuiper\n"
                "SEEDS=7,19,31\n"
                "SBATCH_TIME=01:00:00\n"
                "SBATCH_ACCOUNT=gts-jyoung9-csse\n"
                "SBATCH_NODES=5\n"
                "SBATCH_GPUS_PER_NODE=\n"
                "SLURM_JOB_NAME=bratos\n"
                "LOG_PATTERN=bratos-%j.log\n"
                "SBATCH_CPUS_PER_TASK=2\n"
                "SBATCH_MEM=32G\n"
            )
            job_file = temp / "jobid"
            log_dir = temp / "logs"
            results_dir = temp / "results"
            env = {
                **os.environ,
                "ENV_FILE": str(config),
                "SBATCH_BIN": str(fake_sbatch),
                "FAKE_SBATCH_ARGS": str(captured_args),
                "JOB_FILE": str(job_file),
                "LOG_DIR": str(log_dir),
                "RESULTS_DIR": str(results_dir),
                "RUNS_DIR": str(temp / "runs"),
                "VENV_ACTIVATE": str(REPO_ROOT / ".venv/bin/activate"),
            }

            completed = subprocess.run(
                ["bash", str(REPO_ROOT / "pace" / "submit.sh")],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertEqual(job_file.read_text(), "4242\n")
            args = captured_args.read_text()
            self.assertIn("--nodes=5\n", args)
            self.assertIn("--ntasks=5\n", args)
            self.assertIn("--ntasks-per-node=1\n", args)
            self.assertNotIn("--gpus", args)
            self.assertNotIn("--gres", args)
            self.assertIn("--account=gts-jyoung9-csse\n", args)
            self.assertIn("--job-name=bratos\n", args)
            self.assertIn("bratos-%j.log", args)
            self.assertIn("--cpus-per-task=2\n", args)
            self.assertIn("--mem=32G\n", args)
            self.assertIn("--time=01:00:00\n", args)
            self.assertIn("Submitting 10000 cells, 100 epochs", completed.stdout)
            self.assertIn(str(results_dir / "results-4242.json"), completed.stdout)


if __name__ == "__main__":
    unittest.main()
