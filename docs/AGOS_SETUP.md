# AGOS Wheelhouse Setup

Scope note: this document describes the autoprintworks deployment it came from, not this fork.
The committed `wheelhouse.config.yml` in this repository is authoritative here.
The fleet contents and feature-flag assertions below do not describe this fork's configuration.

This fork is the GitHub IssueOps control layer for AGOS and FirstMate.
It is intentionally configured for a conservative first pilot.

## Current Fleet

The active fleet is one repo:

- `autoprintworks/firstmate-gui-agnostic`

No upstream placeholder repos should remain active in `wheelhouse.config.yml`.

## Safety Posture

Automatic PR triage is disabled.
Automatic issue triage is disabled.
Natural-language decisions are disabled.
Automatic fork-CI approval is disabled.
Issue scanning is disabled until the AGOS state adapter and pilot are ready.
Merge thank-you comments are disabled.

This means the fork can be validated without unexpectedly merging, approving, closing, or flooding issues.

## Required Secret For Live Wheelhouse

Do not add `FLEET_TOKEN` until the captain approves the first live pilot.

When approved, create a fine-grained GitHub token with access to:

- `autoprintworks/wheelhouse`
- `autoprintworks/firstmate-gui-agnostic`

Repository permissions required by Wheelhouse:

- Actions: read and write
- Contents: read and write
- Issues: read and write
- Pull requests: read and write

If the fork remains public, any Wheelhouse decision cards are public too.
Before enabling scheduled issue cards, confirm that the card bodies will not expose private operating details.

## Optional Secrets

`CLAUDE_CODE_OAUTH_TOKEN` is optional and should stay unset during the first deterministic pilot.
It enables auto triage, deep review, and natural-language decisions when the config allows those features.

`READONLY_TOKEN` is optional and should stay unset during the first deterministic pilot.
It only helps Claude-powered paths search configured repos.

## Local Validation

Install local validation dependencies with:

```powershell
python -m pip install -r requirements-dev.txt
```

Then run:

```powershell
python -m py_compile scripts/*.py tests/*.py
python tests/test_decision.py
python tests/test_qualify_refs.py
python tests/test_card_refresh.py
python tests/test_reconcile.py
python tests/test_merge_conflict.py
python tests/test_ci_autoapprove.py
python tests/test_author_filter.py
python tests/test_auto_triage.py
python tests/test_deep_review.py
python tests/test_nl_decisions_search.py
python tests/test_workflow_lint.py
```

If `actionlint` is available, also run:

```powershell
actionlint .github/workflows/*.yml
```

`actionlint` is optional for the first local Windows proof.
