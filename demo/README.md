# K-DBP constellation replay

Interactive comparison of K-DBP and centralized beam planning over checked-in
Starlink and Project Kuiper orbital-element snapshots.

```bash
npm install
npm run dev
```

The app reads `app/benchmark-data.json`, generated from the repository root by
`python3 src/beam_sim.py`. Refresh `../data/orbital_snapshot.json` first with
`python3 scripts/update_orbital_data.py` when a newer CelesTrak snapshot is
needed.

The orbital names and GP mean elements are public data. The in-browser tracks
use the simulator's SGP4 propagation output; demand, RF quality, latency, and
backhaul are architecture-level model outputs.
