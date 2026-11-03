# K-DBP TLA+ model

This directory contains an executable formal model of the safety-critical
control protocol in K-DBP. It is intentionally a protocol model, not a radio
or machine-learning model: prediction produces proposals; the model begins at
proposal admission and follows the decision through replicated plan commit,
fenced activation, handover, and replica-group reconfiguration.

## Formal boundary

`KDBP.tla` represents each satellite's Kratos/Raft group as a linearizable
commit point carrying a majority certificate. It does not re-prove Raft. The
model checks the K-DBP rules layered above that abstraction:

1. A cell proposes a `(satellite, epoch)` target.
2. The target admits the proposal only while epoch capacity remains.
3. A majority-certified, immutable plan snapshots the admitted set and the
   replica configuration that certified it.
4. The satellite activates only a committed plan with the current term and a
   version strictly newer than its activation fence.
5. Source release occurs only after a fresh observation that the target plan
   currently contains the cell.
6. A new replica group is installed only after all incoming members catch up
   and majorities of both the old and new groups certify the transition.

This is the smallest useful abstraction for the guarantees K-DBP actually
needs from Kratos. Network messages, individual Raft log indices, elections,
snapshot transfer, and retry mechanics are hidden behind the linearizable
commit action. Byzantine behavior is outside the failure model.

## Checked properties

The main finite instance checks these invariants in every reachable state:

| Property | Meaning |
|---|---|
| `CapacitySafety` | Accepted, committed, and active cells never exceed a satellite's epoch capacity. |
| `PlanBackedByReservations` | Every plan entry was admitted for that satellite and epoch. |
| `PlanQuorumCertified` | Every committed plan has a majority certificate in its snapshotted configuration. |
| `PlanAgreement` | A satellite/epoch has at most one committed plan value. |
| `ActivationIsCommitted` | Active state is exactly a committed plan, including its term. |
| `FencedActivation` | Activation never rolls back to an older `(term, epoch)`. |
| `MakeBeforeBreak` | At source release, a fresh witness confirms that the target currently serves the cell. |
| `MembersReady` | Every installed voting member has caught up. |
| `ReconfigurationJointCertified` | An installed membership change has old- and new-group majority certificates. |
| `TypeOK` | All state remains inside its declared domain. |

The liveness instance checks, under weak fairness, a stable term, a reachable
majority, and one target epoch:

- every proposal is accepted or rejected;
- every reservation eventually reaches target service;
- every current-term committed plan is eventually activated; and
- every started reconfiguration eventually completes.

These are conditional progress results. The protocol cannot guarantee commit
or handover progress after losing a majority, under permanent partition, or
under unbounded scheduler starvation.

## What TLC found during design

The first supposedly safe model failed `MakeBeforeBreak`. The original rule
allowed source release after the phase had once reached `TargetActive`. A
newer plan could subsequently replace the target's active cell set before the
release action, leaving no serving target at the actual break point.

The corrected rule requires both the earlier takeover phase and a fresh
`cell \in activeCells[target]` check in the atomic release action. A ghost
`releaseWitness` records that event-time fact so the invariant can state the
ordering guarantee. This translates to an implementation requirement: the
source-release command must be authorized against the target's current,
versioned activation, not against a stale acknowledgement.

## Reproduce

Requirements are Java 11 or newer and Python 3. The runner downloads the
official TLA+ tools v1.7.4 JAR and rejects it unless its SHA-256 is
`936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88`.

```bash
# Full suite; the safety model takes about seven minutes on the tested machine.
python3 formal/run_tlc.py

# A smaller selection.
python3 formal/run_tlc.py --only liveness,handover_bug

# Rebuild results.json from preserved logs without rerunning TLC.
python3 formal/run_tlc.py --reuse-logs
```

The expected-counterexample configurations are mutation tests: each disables
one protocol guard and checks that TLC reaches the forbidden state. TLC exits
nonzero for those runs, but the runner treats the expected named violation as
a successful test.

## Interpretation limits

Passing TLC is exhaustive for the configured finite instances, subject to
TLC's fingerprint collision probability; it is not a theorem for arbitrary
numbers of satellites, cells, epochs, or replicas. The model also does not
prove prediction accuracy, RF feasibility, interference safety, global
allocation optimality, wall-clock deadlines, or correctness of a concrete
Kratos/Raft implementation. Those require the benchmark, an RF-aware
simulator, implementation refinement tests, and eventually measured trials.

See [`evidence/RESULTS.md`](evidence/RESULTS.md) for the checked state counts
and counterexample summary.
