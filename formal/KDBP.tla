------------------------------ MODULE KDBP ------------------------------
EXTENDS Naturals, FiniteSets, Sequences, TLC

(***************************************************************************
K-DBP protocol model.

This model deliberately abstracts Raft/Kratos as a per-satellite linearizable
log with quorum certificates.  It models the protocol layer above that log:
beam admission, immutable epoch plans, fenced activation, make-before-break
handover, and staged replica-group reconfiguration.

The CHECK_* constants support mutation checking.  The production model sets
all of them to TRUE.  A bug configuration sets exactly one to FALSE and asks
TLC to find the corresponding safety violation.
***************************************************************************)

CONSTANTS
    SATELLITES,
    CELLS,
    GROUND_STATIONS,
    EPOCHS,
    TERMS,
    BEAM_CAPACITY,
    INITIAL_GROUP,
    NEXT_GROUP,
    CHECK_CAPACITY,
    CHECK_FENCING,
    CHECK_MAKE_BEFORE_BREAK,
    CHECK_CATCHUP,
    CHECK_JOINT_QUORUM,
    ALLOW_TERM_CHANGE

NoSatellite == "NoSatellite"

Phases == {
    "Stable",
    "Proposed",
    "Reserved",
    "Rejected",
    "TargetActive",
    "Released"
}

Quorums(gs) ==
    {q \in SUBSET gs : 2 * Cardinality(q) > Cardinality(gs)}

DefaultSatellite == CHOOSE s \in SATELLITES : TRUE

InitialTerm == CHOOSE t \in TERMS : \A u \in TERMS : t <= u

Newer(t1, e1, t2, e2) ==
    \/ t1 > t2
    \/ /\ t1 = t2
       /\ e1 > e2

VersionGE(t1, e1, t2, e2) ==
    \/ t1 > t2
    \/ /\ t1 = t2
       /\ e1 >= e2

ASSUME
    /\ SATELLITES # {}
    /\ CELLS # {}
    /\ GROUND_STATIONS # {}
    /\ EPOCHS \subseteq Nat \ {0}
    /\ EPOCHS # {}
    /\ TERMS \subseteq Nat \ {0}
    /\ TERMS # {}
    /\ BEAM_CAPACITY \in Nat \ {0}
    /\ INITIAL_GROUP \in SUBSET GROUND_STATIONS
    /\ NEXT_GROUP \in SUBSET GROUND_STATIONS
    /\ Cardinality(INITIAL_GROUP) >= 1
    /\ Cardinality(NEXT_GROUP) >= 1
    /\ CHECK_CAPACITY \in BOOLEAN
    /\ CHECK_FENCING \in BOOLEAN
    /\ CHECK_MAKE_BEFORE_BREAK \in BOOLEAN
    /\ CHECK_CATCHUP \in BOOLEAN
    /\ CHECK_JOINT_QUORUM \in BOOLEAN
    /\ ALLOW_TERM_CHANGE \in BOOLEAN

VARIABLES
    phase,
    target,
    targetEpoch,
    released,
    releaseWitness,
    serving,
    accepted,
    planCommitted,
    plan,
    planTerm,
    planConfig,
    planCert,
    planHistory,
    term,
    activeEpoch,
    activeTerm,
    activeCells,
    fenceEpoch,
    fenceTerm,
    members,
    reconfiguring,
    nextMembers,
    caughtUp,
    configVersion,
    lastOldMembers,
    lastNewMembers,
    lastOldCert,
    lastNewCert

vars == <<
    phase,
    target,
    targetEpoch,
    released,
    releaseWitness,
    serving,
    accepted,
    planCommitted,
    plan,
    planTerm,
    planConfig,
    planCert,
    planHistory,
    term,
    activeEpoch,
    activeTerm,
    activeCells,
    fenceEpoch,
    fenceTerm,
    members,
    reconfiguring,
    nextMembers,
    caughtUp,
    configVersion,
    lastOldMembers,
    lastNewMembers,
    lastOldCert,
    lastNewCert
>>

Init ==
    /\ phase = [c \in CELLS |-> "Stable"]
    /\ target = [c \in CELLS |-> NoSatellite]
    /\ targetEpoch = [c \in CELLS |-> 0]
    /\ released = [c \in CELLS |-> FALSE]
    /\ releaseWitness = [c \in CELLS |-> FALSE]
    /\ serving = [c \in CELLS |-> DefaultSatellite]
    /\ accepted = [s \in SATELLITES |-> [e \in EPOCHS |-> {}]]
    /\ planCommitted = [s \in SATELLITES |-> [e \in EPOCHS |-> FALSE]]
    /\ plan = [s \in SATELLITES |-> [e \in EPOCHS |-> {}]]
    /\ planTerm = [s \in SATELLITES |-> [e \in EPOCHS |-> 0]]
    /\ planConfig = [s \in SATELLITES |-> [e \in EPOCHS |-> {}]]
    /\ planCert = [s \in SATELLITES |-> [e \in EPOCHS |-> {}]]
    /\ planHistory = [s \in SATELLITES |-> [e \in EPOCHS |-> {}]]
    /\ term = [s \in SATELLITES |-> InitialTerm]
    /\ activeEpoch = [s \in SATELLITES |-> 0]
    /\ activeTerm = [s \in SATELLITES |-> 0]
    /\ activeCells = [s \in SATELLITES |-> {}]
    /\ fenceEpoch = [s \in SATELLITES |-> 0]
    /\ fenceTerm = [s \in SATELLITES |-> 0]
    /\ members = [s \in SATELLITES |-> INITIAL_GROUP]
    /\ reconfiguring = [s \in SATELLITES |-> FALSE]
    /\ nextMembers = [s \in SATELLITES |-> {}]
    /\ caughtUp = [s \in SATELLITES |-> INITIAL_GROUP]
    /\ configVersion = [s \in SATELLITES |-> 0]
    /\ lastOldMembers = [s \in SATELLITES |-> {}]
    /\ lastNewMembers = [s \in SATELLITES |-> {}]
    /\ lastOldCert = [s \in SATELLITES |-> {}]
    /\ lastNewCert = [s \in SATELLITES |-> {}]

Propose(c, s, e) ==
    /\ phase[c] = "Stable"
    /\ phase' = [phase EXCEPT ![c] = "Proposed"]
    /\ target' = [target EXCEPT ![c] = s]
    /\ targetEpoch' = [targetEpoch EXCEPT ![c] = e]
    /\ UNCHANGED <<
          released, releaseWitness, serving, accepted, planCommitted, plan, planTerm,
          planConfig, planCert, planHistory, term, activeEpoch, activeTerm,
          activeCells, fenceEpoch, fenceTerm, members, reconfiguring,
          nextMembers, caughtUp, configVersion, lastOldMembers,
          lastNewMembers, lastOldCert, lastNewCert
       >>

Reserve(c) ==
    LET s == target[c]
        e == targetEpoch[c]
    IN
    /\ phase[c] = "Proposed"
    /\ s \in SATELLITES
    /\ e \in EPOCHS
    /\ ~planCommitted[s][e]
    /\ IF CHECK_CAPACITY
          THEN Cardinality(accepted[s][e]) < BEAM_CAPACITY
          ELSE TRUE
    /\ phase' = [phase EXCEPT ![c] = "Reserved"]
    /\ accepted' = [accepted EXCEPT ![s][e] = @ \cup {c}]
    /\ UNCHANGED <<
          target, targetEpoch, released, releaseWitness, serving, planCommitted, plan,
          planTerm, planConfig, planCert, planHistory, term, activeEpoch,
          activeTerm, activeCells, fenceEpoch, fenceTerm, members,
          reconfiguring, nextMembers, caughtUp, configVersion,
          lastOldMembers, lastNewMembers, lastOldCert, lastNewCert
       >>

Reject(c) ==
    LET s == target[c]
        e == targetEpoch[c]
    IN
    /\ phase[c] = "Proposed"
    /\ s \in SATELLITES
    /\ e \in EPOCHS
    /\ \/ planCommitted[s][e]
       \/ /\ CHECK_CAPACITY
          /\ Cardinality(accepted[s][e]) >= BEAM_CAPACITY
    /\ phase' = [phase EXCEPT ![c] = "Rejected"]
    /\ UNCHANGED <<
          target, targetEpoch, released, releaseWitness, serving, accepted, planCommitted,
          plan, planTerm, planConfig, planCert, planHistory, term,
          activeEpoch, activeTerm, activeCells, fenceEpoch, fenceTerm,
          members, reconfiguring, nextMembers, caughtUp, configVersion,
          lastOldMembers, lastNewMembers, lastOldCert, lastNewCert
       >>

CommitPlan(s, e, q) ==
    /\ ~planCommitted[s][e]
    /\ accepted[s][e] # {}
    /\ q \in Quorums(members[s])
    /\ planCommitted' = [planCommitted EXCEPT ![s][e] = TRUE]
    /\ plan' = [plan EXCEPT ![s][e] = accepted[s][e]]
    /\ planTerm' = [planTerm EXCEPT ![s][e] = term[s]]
    /\ planConfig' = [planConfig EXCEPT ![s][e] = members[s]]
    /\ planCert' = [planCert EXCEPT ![s][e] = q]
    /\ planHistory' = [planHistory EXCEPT
          ![s][e] = @ \cup {accepted[s][e]}]
    /\ UNCHANGED <<
          phase, target, targetEpoch, released, releaseWitness, serving, accepted, term,
          activeEpoch, activeTerm, activeCells, fenceEpoch, fenceTerm,
          members, reconfiguring, nextMembers, caughtUp, configVersion,
          lastOldMembers, lastNewMembers, lastOldCert, lastNewCert
       >>

AdvanceTerm(s, t) ==
    /\ ALLOW_TERM_CHANGE
    /\ t \in TERMS
    /\ t > term[s]
    /\ term' = [term EXCEPT ![s] = t]
    /\ UNCHANGED <<
          phase, target, targetEpoch, released, releaseWitness, serving, accepted,
          planCommitted, plan, planTerm, planConfig, planCert, planHistory,
          activeEpoch, activeTerm, activeCells, fenceEpoch, fenceTerm,
          members, reconfiguring, nextMembers, caughtUp, configVersion,
          lastOldMembers, lastNewMembers, lastOldCert, lastNewCert
       >>

Activate(s, e) ==
    LET pt == planTerm[s][e]
        isNew == Newer(pt, e, activeTerm[s], activeEpoch[s])
        advancesFence == Newer(pt, e, fenceTerm[s], fenceEpoch[s])
        newFenceTerm == IF advancesFence THEN pt ELSE fenceTerm[s]
        newFenceEpoch == IF advancesFence THEN e ELSE fenceEpoch[s]
    IN
    /\ planCommitted[s][e]
    /\ IF CHECK_FENCING
          THEN /\ pt = term[s]
               /\ isNew
          ELSE TRUE
    /\ activeEpoch' = [activeEpoch EXCEPT ![s] = e]
    /\ activeTerm' = [activeTerm EXCEPT ![s] = pt]
    /\ activeCells' = [activeCells EXCEPT ![s] = plan[s][e]]
    /\ fenceTerm' = [fenceTerm EXCEPT ![s] = newFenceTerm]
    /\ fenceEpoch' = [fenceEpoch EXCEPT ![s] = newFenceEpoch]
    /\ UNCHANGED <<
          phase, target, targetEpoch, released, releaseWitness, serving, accepted,
          planCommitted, plan, planTerm, planConfig, planCert, planHistory,
          term, members, reconfiguring, nextMembers, caughtUp,
          configVersion, lastOldMembers, lastNewMembers, lastOldCert,
          lastNewCert
       >>

TargetTakeover(c) ==
    LET s == target[c]
    IN
    /\ phase[c] = "Reserved"
    /\ s \in SATELLITES
    /\ c \in activeCells[s]
    /\ phase' = [phase EXCEPT ![c] = "TargetActive"]
    /\ serving' = [serving EXCEPT ![c] = s]
    /\ UNCHANGED <<
          target, targetEpoch, released, releaseWitness, accepted, planCommitted, plan,
          planTerm, planConfig, planCert, planHistory, term, activeEpoch,
          activeTerm, activeCells, fenceEpoch, fenceTerm, members,
          reconfiguring, nextMembers, caughtUp, configVersion,
          lastOldMembers, lastNewMembers, lastOldCert, lastNewCert
       >>

ReleaseSource(c) ==
    LET s == target[c]
        targetIsActive ==
            /\ s \in SATELLITES
            /\ c \in activeCells[s]
    IN
    /\ ~released[c]
    /\ IF CHECK_MAKE_BEFORE_BREAK
          THEN /\ phase[c] = "TargetActive"
               /\ targetIsActive
          ELSE phase[c] \in {"Reserved", "TargetActive"}
    /\ phase' = [phase EXCEPT ![c] = "Released"]
    /\ released' = [released EXCEPT ![c] = TRUE]
    /\ releaseWitness' = [releaseWitness EXCEPT ![c] = targetIsActive]
    /\ serving' = [serving EXCEPT
          ![c] = IF targetIsActive THEN s ELSE NoSatellite]
    /\ UNCHANGED <<
          target, targetEpoch, accepted, planCommitted, plan, planTerm,
          planConfig, planCert, planHistory, term, activeEpoch, activeTerm,
          activeCells, fenceEpoch, fenceTerm, members, reconfiguring,
          nextMembers, caughtUp, configVersion, lastOldMembers,
          lastNewMembers, lastOldCert, lastNewCert
       >>

StartReconfiguration(s) ==
    /\ configVersion[s] = 0
    /\ ~reconfiguring[s]
    /\ NEXT_GROUP # members[s]
    /\ reconfiguring' = [reconfiguring EXCEPT ![s] = TRUE]
    /\ nextMembers' = [nextMembers EXCEPT ![s] = NEXT_GROUP]
    /\ UNCHANGED <<
          phase, target, targetEpoch, released, releaseWitness, serving, accepted,
          planCommitted, plan, planTerm, planConfig, planCert, planHistory,
          term, activeEpoch, activeTerm, activeCells, fenceEpoch, fenceTerm,
          members, caughtUp, configVersion, lastOldMembers,
          lastNewMembers, lastOldCert, lastNewCert
       >>

CatchUp(s, g) ==
    /\ reconfiguring[s]
    /\ g \in nextMembers[s] \ caughtUp[s]
    /\ caughtUp' = [caughtUp EXCEPT ![s] = @ \cup {g}]
    /\ UNCHANGED <<
          phase, target, targetEpoch, released, releaseWitness, serving, accepted,
          planCommitted, plan, planTerm, planConfig, planCert, planHistory,
          term, activeEpoch, activeTerm, activeCells, fenceEpoch, fenceTerm,
          members, reconfiguring, nextMembers, configVersion,
          lastOldMembers, lastNewMembers, lastOldCert, lastNewCert
       >>

CommitReconfiguration(s, qo, qn) ==
    /\ reconfiguring[s]
    /\ qo \subseteq members[s]
    /\ qn \subseteq nextMembers[s]
    /\ qo \in Quorums(members[s])
    /\ IF CHECK_JOINT_QUORUM
          THEN qn \in Quorums(nextMembers[s])
          ELSE TRUE
    /\ IF CHECK_CATCHUP
          THEN nextMembers[s] \subseteq caughtUp[s]
          ELSE TRUE
    /\ lastOldMembers' = [lastOldMembers EXCEPT ![s] = members[s]]
    /\ lastNewMembers' = [lastNewMembers EXCEPT ![s] = nextMembers[s]]
    /\ lastOldCert' = [lastOldCert EXCEPT ![s] = qo]
    /\ lastNewCert' = [lastNewCert EXCEPT ![s] = qn]
    /\ members' = [members EXCEPT ![s] = nextMembers[s]]
    /\ reconfiguring' = [reconfiguring EXCEPT ![s] = FALSE]
    /\ nextMembers' = [nextMembers EXCEPT ![s] = {}]
    /\ configVersion' = [configVersion EXCEPT ![s] = 1]
    /\ UNCHANGED <<
          phase, target, targetEpoch, released, releaseWitness, serving, accepted,
          planCommitted, plan, planTerm, planConfig, planCert, planHistory,
          term, activeEpoch, activeTerm, activeCells, fenceEpoch, fenceTerm,
          caughtUp
       >>

CommitPlanFor(s, e) ==
    \E q \in Quorums(members[s]) : CommitPlan(s, e, q)

DecideProposal(c) == Reserve(c) \/ Reject(c)

CatchUpFor(s) ==
    \E g \in GROUND_STATIONS : CatchUp(s, g)

CommitReconfigurationFor(s) ==
    \E qo, qn \in SUBSET GROUND_STATIONS :
        CommitReconfiguration(s, qo, qn)

Next ==
    \/ \E c \in CELLS, s \in SATELLITES, e \in EPOCHS : Propose(c, s, e)
    \/ \E c \in CELLS : DecideProposal(c)
    \/ \E s \in SATELLITES, e \in EPOCHS : CommitPlanFor(s, e)
    \/ \E s \in SATELLITES, t \in TERMS : AdvanceTerm(s, t)
    \/ \E s \in SATELLITES, e \in EPOCHS : Activate(s, e)
    \/ \E c \in CELLS : TargetTakeover(c)
    \/ \E c \in CELLS : ReleaseSource(c)
    \/ \E s \in SATELLITES : StartReconfiguration(s)
    \/ \E s \in SATELLITES : CatchUpFor(s)
    \/ \E s \in SATELLITES : CommitReconfigurationFor(s)

Fairness ==
    /\ \A c \in CELLS : WF_vars(DecideProposal(c))
    /\ \A s \in SATELLITES, e \in EPOCHS : WF_vars(CommitPlanFor(s, e))
    /\ \A s \in SATELLITES, e \in EPOCHS : WF_vars(Activate(s, e))
    /\ \A c \in CELLS : WF_vars(TargetTakeover(c))
    /\ \A c \in CELLS : WF_vars(ReleaseSource(c))
    /\ \A s \in SATELLITES : WF_vars(CatchUpFor(s))
    /\ \A s \in SATELLITES : WF_vars(CommitReconfigurationFor(s))

Spec == Init /\ [][Next]_vars /\ Fairness

(***************************************************************************
Safety invariants.
***************************************************************************)

TypeOK ==
    /\ phase \in [CELLS -> Phases]
    /\ target \in [CELLS -> SATELLITES \cup {NoSatellite}]
    /\ targetEpoch \in [CELLS -> EPOCHS \cup {0}]
    /\ released \in [CELLS -> BOOLEAN]
    /\ releaseWitness \in [CELLS -> BOOLEAN]
    /\ serving \in [CELLS -> SATELLITES \cup {NoSatellite}]
    /\ accepted \in [SATELLITES -> [EPOCHS -> SUBSET CELLS]]
    /\ planCommitted \in [SATELLITES -> [EPOCHS -> BOOLEAN]]
    /\ plan \in [SATELLITES -> [EPOCHS -> SUBSET CELLS]]
    /\ planTerm \in [SATELLITES -> [EPOCHS -> TERMS \cup {0}]]
    /\ planConfig \in [SATELLITES -> [EPOCHS -> SUBSET GROUND_STATIONS]]
    /\ planCert \in [SATELLITES -> [EPOCHS -> SUBSET GROUND_STATIONS]]
    /\ planHistory \in
          [SATELLITES -> [EPOCHS -> SUBSET (SUBSET CELLS)]]
    /\ term \in [SATELLITES -> TERMS]
    /\ activeEpoch \in [SATELLITES -> EPOCHS \cup {0}]
    /\ activeTerm \in [SATELLITES -> TERMS \cup {0}]
    /\ activeCells \in [SATELLITES -> SUBSET CELLS]
    /\ fenceEpoch \in [SATELLITES -> EPOCHS \cup {0}]
    /\ fenceTerm \in [SATELLITES -> TERMS \cup {0}]
    /\ members \in [SATELLITES -> SUBSET GROUND_STATIONS]
    /\ reconfiguring \in [SATELLITES -> BOOLEAN]
    /\ nextMembers \in [SATELLITES -> SUBSET GROUND_STATIONS]
    /\ caughtUp \in [SATELLITES -> SUBSET GROUND_STATIONS]
    /\ configVersion \in [SATELLITES -> 0..1]
    /\ lastOldMembers \in [SATELLITES -> SUBSET GROUND_STATIONS]
    /\ lastNewMembers \in [SATELLITES -> SUBSET GROUND_STATIONS]
    /\ lastOldCert \in [SATELLITES -> SUBSET GROUND_STATIONS]
    /\ lastNewCert \in [SATELLITES -> SUBSET GROUND_STATIONS]

CapacitySafety ==
    \A s \in SATELLITES, e \in EPOCHS :
        /\ Cardinality(accepted[s][e]) <= BEAM_CAPACITY
        /\ Cardinality(plan[s][e]) <= BEAM_CAPACITY
        /\ Cardinality(activeCells[s]) <= BEAM_CAPACITY

PlanBackedByReservations ==
    \A s \in SATELLITES, e \in EPOCHS :
        planCommitted[s][e] =>
            /\ plan[s][e] \subseteq accepted[s][e]
            /\ \A c \in plan[s][e] :
                  /\ target[c] = s
                  /\ targetEpoch[c] = e

PlanQuorumCertified ==
    \A s \in SATELLITES, e \in EPOCHS :
        planCommitted[s][e] =>
            /\ planConfig[s][e] # {}
            /\ planCert[s][e] \in Quorums(planConfig[s][e])

PlanAgreement ==
    \A s \in SATELLITES, e \in EPOCHS :
        Cardinality(planHistory[s][e]) <= 1

ActivationIsCommitted ==
    \A s \in SATELLITES :
        activeEpoch[s] # 0 =>
            LET e == activeEpoch[s]
            IN  /\ planCommitted[s][e]
                /\ activeTerm[s] = planTerm[s][e]
                /\ activeCells[s] = plan[s][e]

FencedActivation ==
    \A s \in SATELLITES :
        /\ activeTerm[s] = fenceTerm[s]
        /\ activeEpoch[s] = fenceEpoch[s]

MakeBeforeBreak ==
    \A c \in CELLS :
        released[c] =>
            /\ releaseWitness[c]
            /\ target[c] \in SATELLITES

MembersReady ==
    \A s \in SATELLITES : members[s] \subseteq caughtUp[s]

ReconfigurationJointCertified ==
    \A s \in SATELLITES :
        configVersion[s] = 1 =>
            /\ lastOldCert[s] \in Quorums(lastOldMembers[s])
            /\ lastNewCert[s] \in Quorums(lastNewMembers[s])

Safety ==
    /\ TypeOK
    /\ CapacitySafety
    /\ PlanBackedByReservations
    /\ PlanQuorumCertified
    /\ PlanAgreement
    /\ ActivationIsCommitted
    /\ FencedActivation
    /\ MakeBeforeBreak
    /\ MembersReady
    /\ ReconfigurationJointCertified

(***************************************************************************
Liveness properties.  The liveness model disables term changes and uses one
epoch.  The WF assumptions above then express stable leadership, a reachable
majority, and continuously scheduled protocol workers.
***************************************************************************)

ProposalEventuallyDecided ==
    \A c \in CELLS :
        (phase[c] = "Proposed") ~> (phase[c] # "Proposed")

ReservationEventuallyServed ==
    \A c \in CELLS :
        (phase[c] = "Reserved") ~>
            (phase[c] \in {"TargetActive", "Released"})

PlanEventuallyActivated ==
    \A s \in SATELLITES, e \in EPOCHS :
        (planCommitted[s][e] /\ planTerm[s][e] = term[s]) ~>
            /\ activeEpoch[s] # 0
            /\ VersionGE(activeTerm[s], activeEpoch[s], planTerm[s][e], e)

ReconfigurationEventuallyCompletes ==
    \A s \in SATELLITES :
        reconfiguring[s] ~> ~reconfiguring[s]

=============================================================================
