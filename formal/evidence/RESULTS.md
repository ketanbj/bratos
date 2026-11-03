# TLC evidence summary

Generated on 2026-08-06 with TLC 2.19 from TLA+ tools v1.7.4. The exact tool,
specification, configuration hashes, exit codes, and machine-readable results
are in [`results.json`](results.json); the full TLC output is preserved beside
it.

| Model | Expected result | Observed | Generated states | Distinct states | Depth |
|---|---|---:|---:|---:|---:|
| Safe protocol | Pass | Pass | 121,045,989 | 36,878,068 | 21 |
| Conditional liveness | Pass | Pass (4 temporal branches) | 877 | 617 | 10 |
| Capacity guard removed | `CapacitySafety` violation | Found | 93 | 74 | 6 |
| Activation fence removed | `FencedActivation` violation | Found | 3,363 | 1,512 | 9 |
| Make-before-break guard removed | `MakeBeforeBreak` violation | Found | 78 | 65 | 8 |
| Catch-up guard removed | `MembersReady` violation | Found | 126 | 111 | 8 |
| New-group quorum guard removed | `ReconfigurationJointCertified` violation | Found | 57 | 40 | 8 |

The safe model has two satellites, two cells, two epochs, two terms, four
ground stations, capacity one, term changes, and a rolling `3 -> 3` replica
group transition. TLC drained the state queue and reported no safety error.
Its log reports an actual-fingerprint collision miss estimate of `7.8e-5` for
this large run.

The smaller liveness model fixes one satellite, one cell, one epoch, and one
term. This encodes the necessary stable-leader assumption; permitting
unbounded term churn would make unconditional progress false.

## Counterexample meaning

- [`capacity_bug.log`](capacity_bug.log): two cells are reserved in the same
  capacity-one satellite epoch.
- [`fencing_bug.log`](fencing_bug.log): an older epoch is activated after a
  newer one.
- [`handover_bug.log`](handover_bug.log): the source releases after reservation
  but before target activation.
- [`catchup_bug.log`](catchup_bug.log): the next replica group is installed
  while an incoming voter is not caught up.
- [`joint_quorum_bug.log`](joint_quorum_bug.log): reconfiguration commits
  without a majority certificate from the new group.

These negative controls are evidence that the invariants are sensitive to the
specific guards, rather than merely holding because the state machine cannot
reach interesting behavior.
