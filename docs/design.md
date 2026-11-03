# K-DBP: distributed beam prediction with Kratos

## Executive decision

Use Kratos as the **agreement and mobility substrate**, not as the predictor.
Run a small deterministic beam scorer at every ground station (GS) in a
satellite's rolling replica group, aggregate the member forecasts, and commit a
future `BeamPlan` through that satellite's Raft log. Allocate scarce beam slots
with a short deferred-acceptance protocol whose admission decision is made by
the target satellite's current Kratos leader.

This design is called **K-DBP** (Kratos-backed Distributed Beam Prediction).
It targets provider-side prediction of the `(satellite, beam-codebook index)`
that should serve each geographic service cell one or more epochs in the
future. It covers beam switching and inter-satellite handover planning. It does
not attempt distributed coherent beamforming.

## What the supplied paper actually provides

The supplied [Kratos draft](../papers/kratos.pdf) proposes:

1. One bounded, independent replica group per satellite rather than one global
   control store.
2. A sidecar that intercepts satellite/workload state and commits it through a
   per-satellite Raft log.
3. Proactive, orbit-predicted group reconfiguration: add and catch up incoming
   GSs before outgoing GSs leave.
4. Leader transfer to the member with the longest predicted remaining dwell.
5. Co-location of satellite and bound-workload state.
6. Leader-side capacity validation, based on Nomad's plan-queue pattern, to
   prevent concurrent schedulers from oversubscribing a satellite.
7. Multi-Raft heartbeat coalescing and log snapshots to bound overhead.

Its principal semantic claim is linearizability of each satellite's state
through membership changes. The evaluation section of the supplied PDF is
unfinished: it contains TODOs and placeholder subsections. Its headline
orchestration speedups therefore must not be used as measured beam-prediction
results.

Kratos solves agreement, state placement, and handover of control ownership.
It does **not** define a beam predictor, a radio model, cross-satellite atomic
transactions, or an accuracy guarantee. K-DBP fills the first gap while
carefully not claiming the other three.

## Why prediction is the right abstraction

3GPP's NTN study notes that a LEO satellite can remain visible for minutes but
a UE may remain in one spot beam only a few minutes; the resulting beam and
satellite handovers are time-critical. It also notes that terrestrial NR beam
management does not transfer unchanged because adjacent satellite beams can
use different frequencies or polarizations. See
[3GPP TR 38.811, section 7.3.2.1](https://hscc.csie.ncu.edu.tw/38811.pdf).

The orbital component is predictable. Recent work independently supports
planning handovers ahead of time: [PreHO](https://arxiv.org/abs/2603.07987)
uses orbital predictability for pre-planned LEO handovers, while experimental
measurements of six Starlink passes show codebook-like beam switching that
tracks received SNR
([Neinavaie and Kassas](https://people.engineering.osu.edu/sites/default/files/2022-10/Kassas_Unveiling_Beamforming_Strategies_of_Starlink_LEO_Satellites.pdf)).
The remaining unpredictable component is the short-term residual: traffic,
weather, blockage, interference, and measurement noise. That component is best
estimated near the users and fused across nearby GSs.

## State and timing model

Time is divided into epochs of length `T`. A plan created in epoch `e` targets
epoch `e + H`, where `H` is the lookahead. For satellite `s`, service cell `c`,
and codebook beam `b`, replica-group member `g` computes:

```text
q_g(s,c,b,e+H) = wq * predicted_link_quality
               + wd * predicted_demand
               - wh * handover_cost
               - wu * uncertainty
```

`predicted_link_quality` combines deterministic ephemeris/codebook gain with a
short-horizon residual filter. The prototype uses a robust median across GS
reports. A production implementation can replace the scalar filter with a GNN,
MLP, or Kalman model without changing the protocol. Federated GNN/MLP beam
selection is an active design direction, including
[Bartsioka et al.](https://arxiv.org/abs/2603.10983), but K-DBP deliberately
keeps model training off the commit path.

Each per-satellite Raft group stores:

```text
EphemerisWindow  {tle_version, propagated_positions, valid_until}
BeamCodebook     {version, beam_ids, frequency_mask, power_limits}
CellSummary      {cell_id, demand_ewma, channel_residual, uncertainty, cutoff}
BeamProposal     {epoch, model_version, scores_hash, candidates}
BeamReservation  {epoch, cell_id, beam_id, capacity, fencing_token, expiry}
BeamPlan         {epoch, term, codebook_version, assignments, fallback, hash}
```

All identifiers and tie-breaks are deterministic. Every plan has a target
epoch, Raft term, codebook version, and hash. The satellite-side agent rejects
old terms, old epochs, and mismatched codebooks.

## K-DBP protocol

### 1. Predict locally

Each GS in the rolling group propagates the satellite ephemeris to `e + H`,
scores visible cell/beam candidates, and submits a compact `BeamProposal`.
Raw UE measurements remain local; only cell aggregates and uncertainty cross
the GS boundary.

### 2. Aggregate at a quorum

The current leader waits until the proposal deadline or a Raft quorum is
available. It takes the median member score per candidate and applies a lower
confidence penalty. Missing reports increase uncertainty rather than blocking
the epoch.

### 3. Allocate with bounded proposals

Each cell's home GS proposes to its best predicted satellite. A satellite
leader validates proposals against beam count, power, and the offline
frequency/interference mask, then commits accepted `BeamReservation` entries.
Rejected cells try their next candidate for at most `R` rounds. This is a
bounded deferred-acceptance auction; it avoids a global transaction while
retaining satellite-local capacity safety.

### 4. Commit and pre-stage

The leader commits `BeamPlan(e+H)` through the per-satellite Raft log. Kratos
pushes state to an incoming GS and catches up its consensus log before it joins
as a voter. If the current leader will leave, leadership moves before the plan
becomes active. The satellite-side agent preloads the plan.

### 5. Activate with fencing

At the epoch boundary, the satellite agent activates only the highest valid
`(term, epoch)` plan. Inter-satellite handover is make-before-break: the target
reservation must commit before the source is told to drain. Expired
reservations release capacity automatically. If no valid future plan arrives,
the agent uses a codebook-safe ephemeris fallback or holds the previous plan for
one bounded grace epoch.

### Pseudocode

```text
for each epoch e at member g of group G_s:
    F_g <- score(ephemeris[e+H], local_telemetry[e], codebook)
    append Proposal(s, e+H, F_g, uncertainty_g)

at leader L_s before proposal_deadline(e+H):
    F_s <- robust_median(proposals from available members)
    publish ranked candidates with deterministic tie-breaks

for round = 1..R in parallel:
    each unassigned cell proposes to its next-ranked satellite
    each satellite leader:
        accepted <- highest-score proposals fitting remaining capacity/mask
        append Reservation(e+H, accepted, fencing_tokens)
    rejected cells advance to the next candidate

at plan_deadline(e+H):
    append BeamPlan(e+H, committed reservations, fallback)
    pre-stage plan on the satellite and any incoming GS
```

## Guarantees and their boundary

| Property | K-DBP claim | Conditions / source |
|---|---|---|
| Per-satellite agreement | Active GSs do not commit two different plans at the same log position. | Inherited from Raft/Kratos while a majority is reachable. Raft membership changes use overlapping majorities ([Ongaro and Ousterhout](https://www.usenix.org/conference/atc14/technical-sessions/presentation/ongaro)). |
| Linearizable plan history | Reservations, plan commits, and membership changes have one per-satellite order. | Inherited from the supplied Kratos design; every mutation must go through the sidecar/Raft path. |
| Capacity safety | A satellite never commits more than its configured beam/power capacity for an epoch. | Derived by leader validation plus deterministic follower validation. Holds despite concurrent GS proposals. |
| Reconfiguration continuity | An incoming GS can serve the plan without a cold state fetch. | Conditional on Kratos completing state push, voter add, snapshot, and catch-up before the old quorum disappears. |
| Crash tolerance | A group of size `r` tolerates `floor((r-1)/2)` crash failures. | Standard Raft assumption, subject to a connected majority and non-Byzantine members. |
| Bounded decision work | At most `R` proposal rounds; per-satellite ranking is `O(m_s log m_s)`. | `m_s` is the number of candidates in satellite `s`'s field of view. |
| No stale activation | A satellite rejects an older term/epoch/codebook plan. | Derived from satellite-side fencing and version checks. |
| Global optimality | **Not guaranteed.** | Bounded deferred acceptance can be suboptimal versus a global solver. |
| Inter-satellite interference safety | Guaranteed only for the pre-certified frequency/polarization mask. | Dynamic joint precoding would require an additional cross-group protocol and is outside Kratos's per-satellite linearizability. |
| Prediction accuracy | **Not guaranteed by consensus.** | Accuracy depends on ephemeris, telemetry, model calibration, and distribution shift. Consensus can make a bad forecast consistent. |
| Availability during partition | **Not unconditional.** | A minority partition cannot commit; use the bounded satellite fallback. |

This boundary is the main design point: do not claim that Raft makes the radio
decision correct, only that every controller acts on one versioned decision
and cannot oversubscribe satellite-local capacity.

## Formal model

The executable [`KDBP.tla`](../formal/KDBP.tla) model checks the protocol
between prediction and activation: capacity-checked reservations, immutable
quorum-certified plans, term/epoch activation fences, make-before-break
handover, and catch-up plus joint-quorum membership changes. The main TLC run
explored 36,878,068 distinct states without an invariant violation; a smaller
stable-term model passed four conditional liveness properties. Five mutation
models each remove one safety guard and produce the intended counterexample.

Formalization exposed a subtle handover race: a historical `TargetActive`
phase is insufficient because a later plan can evict the target cell before
the source releases. K-DBP must authorize release using a fresh check against
the target's currently active, versioned plan. Full assumptions, state counts,
raw logs, and reproduction instructions are in the
[`formal` evidence bundle](../formal/README.md).

## Centralized comparison

The current simulator contains five systems. Earlier checked-in benchmark results
and frozen production runs contain the original four:

1. **Centralized global (5 s):** all raw telemetry is sent to one controller;
   it runs a global score-sorted greedy assignment every epoch.
2. **Centralized periodic (15 s):** same algorithm and geometry propagation,
   but the assignment is held for three epochs. This reflects the coarse
   periodic architecture discussed in the Kratos paper.
3. **Regional centralized (5 s):** fixed nearest-controller geographic regions,
   independent regional greedy allocation, and visibility-proportional per-satellite
   quotas from a shared budget broker. It uses the same observations as fresh
   global control. Quotas are not borrowed within an epoch. See the
   [regional model and validation](../harness/OPTION2.md#regional-centralized-allocation).
4. **K-DBP (5 s):** current local summaries, five-member rolling groups,
   quorum aggregation, and three deferred-acceptance rounds.
5. **Oracle:** global greedy allocation on future true modeled state; an
   upper reference, not a deployable system.

The centralized greedy comparator is intentionally strong. Related beam
allocation work uses centralized service-cell planning and reports an online
`O(m^2)` Dynamic Service Cell Beam Allocation method
([Markovitz and Segal](https://www.sciencedirect.com/science/article/pii/S1389128622002687)).
Our global score sort is `O(E log E)` for `E` visible cell/satellite edges and
uses identical score weights to K-DBP, so the comparison isolates placement,
freshness, and allocation topology rather than model capacity.

## Benchmark result

The checked-in result uses 7 deterministic seeds, 1,200 five-second epochs
(100 simulated minutes per seed), a
96-satellite sample, 240 globally sampled H3 resolution-5 service cells, 12
GSs, four beams per satellite,
five-member replica groups, and a 10-second lookahead. There are two profiles:
Starlink and Project Kuiper. Both use real public GP mean elements sampled from
a frozen CelesTrak snapshot and matched to FCC-authorized altitude/inclination
shells. The checked-in OMM records are propagated with the Vallado SGP4
implementation in the pinned Python `sgp4` package.

### Starlink profile

| Metric (mean across seeds) | Central 5 s | Central 15 s | K-DBP 5 s | Oracle |
|---|---:|---:|---:|---:|
| Demand satisfaction | 57.74% | 57.47% | 57.98% | 58.28% |
| Exact satellite + beam accuracy | 91.44% | 89.67% | 93.63% | 100% |
| Outage cells | 28.71% | 28.71% | 28.71% | 28.71% |
| p95 control latency | 374.53 ms | 374.53 ms | 217.73 ms | - |
| Deadline misses at 250 ms | 100% | 100% | 0% | - |
| Control backhaul per run | 360.67 MB | 120.23 MB | 140.91 MB | - |

### Project Kuiper profile

| Metric (mean across seeds) | Central 5 s | Central 15 s | K-DBP 5 s | Oracle |
|---|---:|---:|---:|---:|
| Demand satisfaction | 69.11% | 68.76% | 69.39% | 69.74% |
| Exact satellite + beam accuracy | 89.69% | 87.90% | 92.34% | 100% |
| Outage cells | 18.89% | 18.89% | 18.89% | 18.89% |
| p95 control latency | 400.36 ms | 400.36 ms | 218.43 ms | - |
| Deadline misses at 250 ms | 100% | 100% | 0% | - |
| Control backhaul per run | 447.78 MB | 149.25 MB | 179.04 MB | - |

Demand satisfaction and outage are strongly shaped by the global H3 sample,
finite orbital inclination, and the 10-degree minimum elevation angle. Kuiper's
checked-in snapshot currently matches the authorized 630 km / 51.9° shell;
cells beyond that ground-track reach cannot be served. These absolute figures
are scenario outputs, not provider performance measurements.

The exact-pair accuracy advantage is not a theoretical result. It arises here
because K-DBP uses current local residual telemetry while the centralized
controller receives the prior epoch's telemetry. Change that delay assumption
and the result changes.

## What the simulation does and does not prove

The benchmark is an architecture-level experiment with real orbital inputs. It checks:

- capacity invariants and unique cell assignment;
- deterministic replay across seeds;
- mobility-driven group membership from public GP orbital elements;
- score freshness, cadence, control-path latency, and byte-accounting tradeoffs.

It does not use an antenna pattern, atmospheric attenuation, provider traffic,
a packet network, a running Raft implementation, or RF hardware. SGP4 produces
TEME positions; the ground-track conversion uses GMST and omits polar motion,
which is appropriate for these coarse service cells but not precision pointing.
Latency and bytes are modeled, not measured. The numbers should be used to
select the next experiment, not in a paper abstract.

## Production evaluation plan

1. Quantify SGP4 and Earth-orientation conversion error against provider
   ephemerides over longer replay windows; add full IERS Earth-orientation data
   if the experiment advances to link-level pointing.
2. Import a real beam codebook and antenna gain mask; model frequency reuse,
   polarization, EIRP, and inter-beam interference.
3. Replay anonymized per-cell demand and weather/blockage residuals. Split by
   time and geography to test distribution shift.
4. Run the same predictor in three control deployments: one global controller,
   static regional controllers, and real Kratos sidecars.
5. Emulate GS-to-GS paths with measured RTT/loss distributions; inject leader
   departure, joiner lag, majority loss, and stale ephemeris.
6. Report top-1/top-k beam accuracy, demand satisfaction, outage time, handover
   failure, ping-pong rate, plan age, deadline misses, CPU/memory, and WAN bytes.
7. Include ablations for group size, lookahead, proposal rounds, telemetry
   delay, model choice, and fallback policy.
8. Treat centralized global optimization as the quality ceiling and include a
   reactive strongest-SNR handover as the operational floor.

## Open design choices

- `T` and `H` should be chosen from measured plan-commit and satellite command
  latency, not RF slot duration.
- Group size trades failure tolerance and data diversity against quorum RTT.
- The confidence penalty should be calibrated to outage cost, especially at
  low elevation angles.
- Dynamic interference coordination is the largest protocol gap. If static
  masks leave too much capacity unused, add an overlap-zone coordinator with a
  separately replicated log; do not fake atomicity across independent
  satellite groups.
- A learned model should be shadow-deployed against the deterministic geometry
  baseline and gated by calibration/uncertainty before it can author plans.
