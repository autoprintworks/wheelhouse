# AGOS Board Bridge

Wheelhouse issue #3 starts with a dry-run bridge.
It compares AGOS issue state against an AGOS Board snapshot and writes a bounded JSON readback.

The bridge runs as a dry run by default and mutates nothing unless both `--apply` and `--confirm-project-mutation` are passed.
With both flags present it performs live ProjectV2 writes, issuing a real `updateProjectV2ItemFieldValue` mutation for each planned action.
Apply still refuses to run while Project validation is blocked, and preflight limits it to `set-project-field` actions on single-select fields, so it never adds a Project item.

## Inputs

The bridge reads two inputs:

- GitHub issues that may contain hidden `firstmate-state`.
- An AGOS Board snapshot with fields, views, and items.

The snapshot can be created later with:

```powershell
python scripts/agos_project_bridge.py snapshot --owner autoprintworks --project 3 --out data/project3-snapshot.json
```

If GitHub rejects ProjectV2 access, refresh auth with:

```powershell
gh auth refresh -s project
```

## Dry Run

Run a local fixture proof with:

```powershell
python scripts/agos_project_bridge.py plan --issues-file tests/fixtures/agos_issue_states.json --project-file tests/fixtures/project3_snapshot.json --readback-file data/agos-project3-readback.json
```

The readback reports:

- missing required Project fields.
- missing required Project views.
- unmanaged issues.
- invalid managed issues.
- missing Project items.
- Project field drift.

## Required Board Views

The bridge verifies these views:

- `Captain Decisions`
- `Ready For Agent`
- `Active Execution`
- `Review And Merge`
- `Blocked Work`
- `Stow And Future`

## Required Project Fields

The bridge verifies these fields:

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
