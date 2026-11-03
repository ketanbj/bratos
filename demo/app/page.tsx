"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import benchmark from "./benchmark-data.json";

type Mode = "distributed" | "centralized";
type Provider = "starlink" | "kuiper";
type Assignment = { cell: number; sat: number; beam: number };
type ServiceCell = { id: number; h3_index: string; x: number; y: number };
type Satellite = {
  id: number;
  x: number;
  y: number;
  group: number[];
};
type SatelliteMetadata = { id: number; name: string; catalog_id: number; shell_id: string; altitude_km: number; inclination_deg: number };
type TraceEpoch = {
  epoch: number;
  seconds: number;
  satellites: Satellite[];
  distributed: Assignment[];
  centralized: Assignment[];
  metrics: Record<Mode, Record<string, number>>;
};

function percent(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}

function signed(value: number, digits = 2) {
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function duration(seconds: number) {
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export default function Home() {
  const [provider, setProvider] = useState<Provider>("starlink");
  const [mode, setMode] = useState<Mode>("distributed");
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [selectedSat, setSelectedSat] = useState(0);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const profile = benchmark.profiles[provider];
  const trace = profile.trace as TraceEpoch[];
  const systems = profile.systems;
  const serviceCells = profile.service_cells as ServiceCell[];
  const frame = trace[index];
  const assignments = frame[mode];
  const selected = frame.satellites.find((sat) => sat.id === selectedSat) ?? frame.satellites[0];
  const selectedMetadata = (profile.satellite_catalog as SatelliteMetadata[]).find((sat) => sat.id === selected.id) ?? profile.satellite_catalog[0];
  const observedShells = profile.constellation.shells.filter((shell) => shell.observed_objects > 0);

  function mean(system: keyof typeof systems, metric: keyof (typeof systems)["centralized"]) {
    return systems[system][metric].mean;
  }

  function chooseProvider(next: Provider) {
    setProvider(next);
    setIndex(0);
    setSelectedSat(0);
    setPlaying(true);
  }

  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => setIndex((value) => (value + 1) % trace.length), 900);
    return () => window.clearInterval(timer);
  }, [playing, trace.length]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const holder = canvas.parentElement;
    if (!holder) return;

    const draw = () => {
      const rect = holder.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      const width = Math.max(320, rect.width);
      const height = width < 620 ? 390 : 455;
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      canvas.style.height = `${height}px`;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, width, height);

      const color = getComputedStyle(document.documentElement);
      const grid = color.getPropertyValue("--grid").trim();
      const ink = color.getPropertyValue("--ink").trim();
      const muted = color.getPropertyValue("--muted-ink").trim();
      const cyan = color.getPropertyValue("--cyan").trim();
      const amber = color.getPropertyValue("--amber").trim();
      const panel = color.getPropertyValue("--panel-2").trim();

      ctx.strokeStyle = grid;
      ctx.lineWidth = 1;
      for (let x = 0; x <= 10; x++) {
        ctx.beginPath();
        ctx.moveTo((x / 10) * width, 0);
        ctx.lineTo((x / 10) * width, height);
        ctx.stroke();
      }
      for (let y = 0; y <= 6; y++) {
        ctx.beginPath();
        ctx.moveTo(0, (y / 6) * height);
        ctx.lineTo(width, (y / 6) * height);
        ctx.stroke();
      }

      const assignmentByCell = new Map(assignments.map((item) => [item.cell, item]));
      const cellById = new Map(serviceCells.map((cell) => [cell.id, cell]));
      for (const cell of serviceCells) {
        const assignment = assignmentByCell.get(cell.id);
        ctx.beginPath();
        ctx.arc(cell.x * width, cell.y * height, assignment ? 3.2 : 2, 0, Math.PI * 2);
        ctx.fillStyle = assignment ? (mode === "distributed" ? cyan : amber) : muted;
        ctx.fill();
      }

      ctx.globalAlpha = 0.18;
      ctx.lineWidth = 1;
      for (const assignment of assignments) {
        const sat = frame.satellites.find((item) => item.id === assignment.sat);
        const cell = cellById.get(assignment.cell);
        if (!sat || !cell) continue;
        const cx = cell.x * width;
        const cy = cell.y * height;
        ctx.strokeStyle = mode === "distributed" ? cyan : amber;
        ctx.beginPath();
        ctx.moveTo(sat.x * width, sat.y * height);
        ctx.lineTo(cx, cy);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;

      const groundStations = Array.from({ length: profile.config.ground_stations }, (_, id) => ({
        id,
        x: (id * 0.381966 + 0.07) % 1,
        y: 0.16 + 0.68 * (((id * 5) % profile.config.ground_stations) + 0.5) / profile.config.ground_stations,
      }));
      ctx.setLineDash([4, 5]);
      ctx.strokeStyle = cyan;
      ctx.globalAlpha = 0.55;
      for (const gid of selected.group) {
        const gs = groundStations[gid];
        ctx.beginPath();
        ctx.moveTo(selected.x * width, selected.y * height);
        ctx.lineTo(gs.x * width, gs.y * height);
        ctx.stroke();
      }
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;

      for (const gs of groundStations) {
        const active = selected.group.includes(gs.id);
        ctx.fillStyle = active ? cyan : muted;
        ctx.fillRect(gs.x * width - 3, gs.y * height - 3, 6, 6);
      }

      for (const sat of frame.satellites) {
        const isSelected = sat.id === selected.id;
        ctx.beginPath();
        ctx.arc(sat.x * width, sat.y * height, isSelected ? 8 : 5, 0, Math.PI * 2);
        ctx.fillStyle = isSelected ? ink : panel;
        ctx.fill();
        ctx.strokeStyle = isSelected ? cyan : muted;
        ctx.lineWidth = isSelected ? 2 : 1;
        ctx.stroke();
        if (isSelected) {
          ctx.fillStyle = ink;
          ctx.font = "500 11px ui-monospace, monospace";
          const metadata = (profile.satellite_catalog as SatelliteMetadata[]).find((item) => item.id === sat.id);
          ctx.fillText(metadata?.name ?? `SAT-${sat.id}`, sat.x * width + 12, sat.y * height - 8);
        }
      }
    };

    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(holder);
    return () => observer.disconnect();
  }, [frame, assignments, mode, profile.config, profile.satellite_catalog, selected, serviceCells]);

  const selectedAssignments = useMemo(
    () => assignments.filter((assignment) => assignment.sat === selected.id),
    [assignments, selected.id],
  );

  function chooseSatellite(event: React.MouseEvent<HTMLCanvasElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = (event.clientX - rect.left) / rect.width;
    const y = (event.clientY - rect.top) / rect.height;
    const nearest = frame.satellites.reduce((best, sat) => {
      const distance = Math.hypot(sat.x - x, sat.y - y);
      return distance < best.distance ? { id: sat.id, distance } : best;
    }, { id: selectedSat, distance: Number.POSITIVE_INFINITY });
    if (nearest.distance < 0.08) setSelectedSat(nearest.id);
  }

  const latencyReduction = profile.comparisons.latency_reduction_pct;
  const backhaulReduction = profile.comparisons.backhaul_reduction_pct;
  const demandDelta = profile.comparisons.demand_satisfaction_delta_pp;

  return (
    <main>
      <header className="masthead">
        <div>
          <div className="eyebrow"><span className="live-dot" /> K-DBP research prototype</div>
          <h1>Consensus that moves<br />with the constellation.</h1>
          <p className="lede">Kratos-backed beam prediction turns nearby ground stations into a rolling control group for every satellite.</p>
        </div>
        <div className="header-note">
          <span>CelesTrak snapshot / {benchmark.snapshot_generated_at.slice(0, 10)}</span>
          <strong>{profile.config.epochs * profile.config.epoch_seconds / 60} MIN · {profile.config.satellites} SAT · {profile.config.service_cells} H3-R{profile.config.h3_resolution} CELLS</strong>
        </div>
      </header>

      <section className="constellation-switch" aria-label="Select orbital dataset">
        <div className="provider-tabs">
          <button className={provider === "starlink" ? "active" : ""} onClick={() => chooseProvider("starlink")} aria-pressed={provider === "starlink"}>Starlink</button>
          <button className={provider === "kuiper" ? "active" : ""} onClick={() => chooseProvider("kuiper")} aria-pressed={provider === "kuiper"}>Project Kuiper</button>
        </div>
        <div className="source-summary">
          <span>Public GP orbital elements · SGP4</span>
          <strong>{profile.constellation.records_available.toLocaleString()} tracked objects</strong>
          <small>{profile.constellation.records_matching_authorized_shells.toLocaleString()} matched to authorized shells</small>
          <small><a href={profile.constellation.orbital_source} target="_blank" rel="noreferrer">CelesTrak GP</a> · <a href={profile.constellation.authorization.urls[0]} target="_blank" rel="noreferrer">FCC authorization</a></small>
        </div>
        <div className="shell-list" aria-label="Observed authorized shells">
          {observedShells.map((shell) => (
            <span key={shell.id}><b>{shell.altitude_km} km</b> · {shell.inclination_deg}° <i>{shell.observed_objects.toLocaleString()}</i></span>
          ))}
        </div>
      </section>

      <section className="metric-strip" aria-label="Benchmark highlights">
        <article>
          <span>p95 control latency</span>
          <strong>−{latencyReduction.toFixed(1)}%</strong>
          <small>{mean("distributed", "latency_p95_ms").toFixed(0)} ms vs {mean("centralized", "latency_p95_ms").toFixed(0)} ms</small>
        </article>
        <article>
          <span>Control backhaul</span>
          <strong>−{backhaulReduction.toFixed(1)}%</strong>
          <small>{mean("distributed", "backhaul_mb").toFixed(2)} MB vs {mean("centralized", "backhaul_mb").toFixed(2)} MB</small>
        </article>
        <article>
          <span>Demand satisfaction</span>
          <strong>{signed(demandDelta)} pp</strong>
          <small>{percent(mean("distributed", "demand_satisfaction"))} vs {percent(mean("centralized", "demand_satisfaction"))}</small>
        </article>
      </section>

      <section className="workspace">
        <div className="map-panel">
          <div className="panel-head">
            <div>
              <span className="section-label">Live replay · t+10 s prediction</span>
              <h2>Rolling beam commitments</h2>
            </div>
            <div className="segmented" aria-label="Select control architecture">
              <button className={mode === "distributed" ? "active" : ""} onClick={() => setMode("distributed")} aria-pressed={mode === "distributed"}>K-DBP</button>
              <button className={mode === "centralized" ? "active" : ""} onClick={() => setMode("centralized")} aria-pressed={mode === "centralized"}>Central</button>
            </div>
          </div>
          <div className="canvas-wrap">
            <canvas ref={canvasRef} onClick={chooseSatellite} aria-label="Moving satellite, ground station, and service cell simulation" />
            <div className="map-legend"><span><i className="sat-mark" /> Satellite</span><span><i className="gs-mark" /> Ground station</span><span><i className="beam-mark" /> Active beam</span></div>
          </div>
          <div className="timeline">
            <button onClick={() => setPlaying((value) => !value)} aria-label={playing ? "Pause replay" : "Play replay"}>{playing ? "Ⅱ" : "▶"}</button>
            <input aria-label="Replay epoch" type="range" min="0" max={trace.length - 1} value={index} onChange={(event) => { setIndex(Number(event.target.value)); setPlaying(false); }} />
            <span>T+{duration(frame.seconds)}</span>
          </div>
        </div>

        <aside className="state-panel">
          <div className="section-label">Selected replica group</div>
          <div className="sat-title"><span>{selectedMetadata.name}</span><em>COMMITTED</em></div>
          <dl>
            <div><dt>NORAD catalog</dt><dd>{selectedMetadata.catalog_id}</dd></div>
            <div><dt>Observed orbit</dt><dd>{selectedMetadata.altitude_km.toFixed(0)} km / {selectedMetadata.inclination_deg.toFixed(1)}°</dd></div>
            <div><dt>Target epoch</dt><dd>{frame.epoch}</dd></div>
            <div><dt>Beam slots</dt><dd>{selectedAssignments.length} / {profile.config.beams_per_satellite}</dd></div>
            <div><dt>Replica quorum</dt><dd>3 / {selected.group.length}</dd></div>
            <div><dt>Plan latency</dt><dd>{frame.metrics[mode].latency_ms.toFixed(0)} ms</dd></div>
          </dl>
          <div className="group-list">
            {selected.group.map((gid, position) => <div key={gid}><span>GS-{String(gid).padStart(2, "0")}</span><i>{position === 0 ? "LEADER" : "VOTER"}</i></div>)}
          </div>
          <div className="commit-log">
            <span>RAFT LOG / EPOCH {frame.epoch}</span>
            <code>proposal → reserve → plan</code>
            <small>Term and codebook fenced</small>
          </div>
        </aside>
      </section>

      <section className="benchmark-section">
        <div className="section-intro">
          <div>
            <span className="section-label">{profile.seeds.length} seeds · {profile.seeds.length * profile.config.epochs} evaluated epochs</span>
            <h2>Near-centralized quality.<br />A local control path.</h2>
          </div>
          <p>K-DBP gives up global atomic optimization. In return, its current local telemetry reaches the satellite inside the modeled 250 ms plan deadline.</p>
        </div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Architecture</th><th>Demand served</th><th>Exact pair</th><th>p95 latency</th><th>Backhaul</th><th>250 ms misses</th></tr></thead>
            <tbody>
              <tr className="highlight"><td><strong>K-DBP 5 s</strong><small>Rolling five-GS groups</small></td><td>{percent(mean("distributed", "demand_satisfaction"))}</td><td>{percent(mean("distributed", "pair_accuracy"))}</td><td>{mean("distributed", "latency_p95_ms").toFixed(0)} ms</td><td>{mean("distributed", "backhaul_mb").toFixed(2)} MB</td><td>{percent(mean("distributed", "deadline_miss_ratio"))}</td></tr>
              <tr><td><strong>Central 5 s</strong><small>Global greedy assignment</small></td><td>{percent(mean("centralized", "demand_satisfaction"))}</td><td>{percent(mean("centralized", "pair_accuracy"))}</td><td>{mean("centralized", "latency_p95_ms").toFixed(0)} ms</td><td>{mean("centralized", "backhaul_mb").toFixed(2)} MB</td><td>{percent(mean("centralized", "deadline_miss_ratio"))}</td></tr>
              <tr><td><strong>Central 15 s</strong><small>Held periodic plan</small></td><td>{percent(mean("centralized_periodic", "demand_satisfaction"))}</td><td>{percent(mean("centralized_periodic", "pair_accuracy"))}</td><td>{mean("centralized_periodic", "latency_p95_ms").toFixed(0)} ms</td><td>{mean("centralized_periodic", "backhaul_mb").toFixed(2)} MB</td><td>{percent(mean("centralized_periodic", "deadline_miss_ratio"))}</td></tr>
              <tr><td><strong>Future-state oracle</strong><small>Non-deployable reference</small></td><td>{percent(mean("oracle", "demand_satisfaction"))}</td><td>100%</td><td>—</td><td>—</td><td>—</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <section className="protocol-section">
        <div className="section-label">One epoch, four ordered stages</div>
        <div className="protocol-grid">
          <article><b>01</b><h3>Forecast</h3><p>Members combine propagated geometry with fresh cell demand and channel residuals.</p></article>
          <article><b>02</b><h3>Propose</h3><p>Cells try ranked satellites; each leader validates beam and power capacity.</p></article>
          <article><b>03</b><h3>Commit</h3><p>Reservations and the future BeamPlan enter one linearizable satellite log.</p></article>
          <article><b>04</b><h3>Activate</h3><p>The satellite accepts only the newest fenced term, epoch, and codebook version.</p></article>
        </div>
      </section>

      <footer>
        <span>K-DBP / {profile.constellation.name} public orbit snapshot</span>
        <p>GP elements + SGP4 propagation; demand, RF, latency, and backhaul are modeled.</p>
      </footer>
    </main>
  );
}
