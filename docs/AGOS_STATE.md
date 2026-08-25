# AGOS Issue State Contract

Scope note: this document describes the autoprintworks deployment it came from, not this fork.
No card path in this repository reads the `firstmate-state` block: `scripts/agos_state.py` is imported only by `scripts/agos_project_bridge.py` and `tests/test_agos_state.py`, and neither `scripts/reconcile.py` nor `scripts/render_card.py` consults it.
The Wheelhouse behavior described below is therefore not live here.

Wheelhouse reads AGOS work from the hidden `firstmate-state` block in a GitHub issue body.
The block is the canonical machine state for FirstMate execution, labels, and AGOS Board projection.

Wheelhouse keeps its own `wheelhouse-state` block for decision cards.
That card state should not compete with `firstmate-state`.
When the target issue is an AGOS issue, Wheelhouse reads `firstmate-state`, projects it into deterministic fields, and only then decides whether a card or Project update is needed.

## Canonical Status

The adapter returns one canonical status for each issue:

- `unmanaged` when no `firstmate-state` block exists.
- `ready` when `work_state` is `Ready to start`.
- `blocked` when `work_state` is `Blocked`.
- `stowed` when `stow_state` or `work_state` is `Stowed`.
- `superseded` when `stow_state` or `work_state` is `Superseded`.
- `done` when `work_state` is `Done`.

Other FirstMate states are still represented deterministically as `needs-clarity`, `in-progress`, `needs-captain-decision`, `review`, `ready-to-merge`, `stow-candidate`, or `closed-not-planned`.
Invalid state returns `invalid` and must not be applied to GitHub.

## Board Projection

The adapter projects these AGOS Board fields:

- `Status`
- `Pipeline stage`
- `Priority`
- `Product outcome`
- `Work state`
- `Dependency state`
- `Target model tier`
- `Review lane`
- `Parallelization class`
- `Stow state`

`Status` is derived from the canonical status.
Every other field is copied from the hidden `firstmate-state` block.

## Label Projection

The adapter mirrors the FirstMate label families:

- `pipeline:*`
- `priority:*`
- `outcome:*`
- work-state labels such as `ready-for-agent` and `work state:blocked`
- `stow:*` when the issue is not active

This lets Wheelhouse and FirstMate agree on labels without a second IssueOps engine.

## Fate Of `fm-issues`

`fm-issues` remains the temporary FirstMate-side bridge and readback command.
It should not become a competing long-term Project sync implementation.
After Wheelhouse issue #3 proves the AGOS Board bridge, FirstMate issue #116 should consume that bridge or be superseded by it.

## Fixture Coverage

The network-free adapter tests cover issue shapes based on FirstMate #85, #112, #116, #117, #120, and #113.
#85 is unmanaged.
#120 is ready.
#116 is blocked.
#117 is stowed.
#112 is superseded.
#113 is done.
