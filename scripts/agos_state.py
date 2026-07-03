#!/usr/bin/env python3
"""AGOS issue-state adapter for Wheelhouse.

This module reads FirstMate `firstmate-state` blocks and projects them into a
small deterministic shape that Wheelhouse can use without asking an LLM.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any


SCHEMA_VERSION = 1

STATE_BLOCK_RE = re.compile(
    r"<!--\s*firstmate-state:\s*(\{.*?\})\s*-->",
    re.S,
)

PIPELINE_STAGES = {
    "Idea",
    "Research",
    "Prototype",
    "PRD",
    "Kanban Board",
    "Execution",
    "QA",
    "Stow",
    "Product Foundation",
    "AGOS Command Centre",
}

PRIORITIES = {
    "Do now",
    "Do next",
    "Park for later",
}

PRODUCT_OUTCOMES = {
    "Product foundation",
    "Windows runner",
    "Execution ledger",
    "Kanban sync",
    "No-mistakes QA",
    "No-mistakes quality gate",
    "Model routing",
    "Secondmate orchestration",
    "Parallel execution",
    "AGOS Command Centre",
    "Stow",
    "Agent workspace",
    "Work tracking",
    "Planning system",
    "Future scale-ups",
}

WORK_STATES = {
    "Needs clarity",
    "Ready to start",
    "In progress",
    "Blocked",
    "Needs captain decision",
    "Quality review",
    "Ready to merge",
    "Done",
    "Stow candidate",
    "Stowed",
    "Superseded",
    "Closed not planned",
}

DEPENDENCY_STATES = {
    "Unblocked",
    "Blocked",
    "Conditional",
    "Waiting on active work",
    "Needs captain decision",
}

TARGET_MODEL_TIERS = {
    "Strong planning",
    "Cheap bounded execution",
    "Strong review",
    "Deterministic tooling",
    "Mixed",
}

REVIEW_LANES = {
    "None",
    "Human decision",
    "Separate QA",
    "No-mistakes",
    "Ready for review",
}

PARALLELIZATION_CLASSES = {
    "Serial gate",
    "Can run in parallel",
    "Conflict risk",
    "Batch proof",
}

STOW_STATES = {
    "Active",
    "Stow candidate",
    "Stowed",
    "Superseded",
}

ENUM_FIELDS = {
    "pipeline_stage": PIPELINE_STAGES,
    "priority": PRIORITIES,
    "product_outcome": PRODUCT_OUTCOMES,
    "work_state": WORK_STATES,
    "dependency_state": DEPENDENCY_STATES,
    "target_model_tier": TARGET_MODEL_TIERS,
    "review_lane": REVIEW_LANES,
    "parallelization_class": PARALLELIZATION_CLASSES,
    "stow_state": STOW_STATES,
}

REQUIRED_FIELDS = {
    "schema_version",
    "repo",
    "issue_number",
    "pipeline_stage",
    "product_outcome",
    "work_state",
    "priority",
    "dependency_state",
    "target_model_tier",
    "review_lane",
    "parallelization_class",
    "stow_state",
}

OPTIONAL_LIST_FIELDS = {
    "source_issues",
    "replacement_issues",
}

WORK_STATE_TO_STATUS = {
    "Needs clarity": "needs-clarity",
    "Ready to start": "ready",
    "In progress": "in-progress",
    "Blocked": "blocked",
    "Needs captain decision": "needs-captain-decision",
    "Quality review": "review",
    "Ready to merge": "ready-to-merge",
    "Done": "done",
    "Stow candidate": "stow-candidate",
    "Stowed": "stowed",
    "Superseded": "superseded",
    "Closed not planned": "closed-not-planned",
}

STOW_STATE_TO_STATUS = {
    "Stow candidate": "stow-candidate",
    "Stowed": "stowed",
    "Superseded": "superseded",
}

STATUS_TO_PROJECT_STATUS = {
    "unmanaged": "Inbox",
    "invalid": "Inbox",
    "needs-clarity": "Inbox",
    "ready": "Ready",
    "in-progress": "In Progress",
    "blocked": "Blocked",
    "needs-captain-decision": "Blocked",
    "review": "Review",
    "ready-to-merge": "Review",
    "done": "Done",
    "stow-candidate": "Stowed",
    "stowed": "Stowed",
    "superseded": "Stowed",
    "closed-not-planned": "Done",
}

WORK_STATE_LABELS = {
    "Needs clarity": "needs-triage",
    "Ready to start": "ready-for-agent",
    "In progress": "work state:in progress",
    "Blocked": "work state:blocked",
    "Needs captain decision": "ready-for-human",
    "Quality review": "ready-for-review",
    "Ready to merge": "ready-to-merge",
    "Done": "work state:done",
    "Stow candidate": "work state:stow candidate",
    "Stowed": "work state:stowed",
    "Superseded": "work state:superseded",
    "Closed not planned": "work state:closed not planned",
}

PROJECT_FIELD_MAP = {
    "Pipeline stage": "pipeline_stage",
    "Priority": "priority",
    "Product outcome": "product_outcome",
    "Work state": "work_state",
    "Dependency state": "dependency_state",
    "Target model tier": "target_model_tier",
    "Review lane": "review_lane",
    "Parallelization class": "parallelization_class",
    "Stow state": "stow_state",
}

PROJECT_FIELD_VALUE_OVERRIDES = {
    ("Work state", "Needs captain decision"): "Needs founder decision",
}


class AgosStateError(RuntimeError):
    """Raised when an AGOS issue state block is present but invalid."""


def _label_value(text: str) -> str:
    return " ".join(text.casefold().split())


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")


def render_firstmate_state_block(state: dict[str, Any]) -> str:
    """Render the hidden FirstMate state block exactly once."""

    payload = json.dumps(state, indent=2, sort_keys=True)
    return "<!-- firstmate-state:\n%s\n-->" % payload


def replace_firstmate_state_block(body: str, state: dict[str, Any]) -> str:
    """Replace an existing FirstMate state block, or append one."""

    block = render_firstmate_state_block(state)
    if STATE_BLOCK_RE.search(body or ""):
        return STATE_BLOCK_RE.sub(block, body or "", count=1)
    stripped = (body or "").rstrip()
    if stripped:
        return "%s\n\n%s\n" % (stripped, block)
    return "%s\n" % block


def parse_firstmate_state_block(body: str) -> dict[str, Any] | None:
    """Parse a hidden FirstMate state block from an issue body."""

    match = STATE_BLOCK_RE.search(body or "")
    if not match:
        return None
    try:
        parsed = json.loads(match.group(1))
    except (TypeError, ValueError) as error:
        raise AgosStateError("firstmate-state JSON is invalid: %s" % error) from error
    if not isinstance(parsed, dict):
        raise AgosStateError("firstmate-state JSON must be an object")
    return parsed


def issue_number(issue: dict[str, Any]) -> int | None:
    """Read an issue number from common GitHub payload shapes."""

    raw = issue.get("number", issue.get("issue_number"))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def validate_state(
    state: dict[str, Any],
    *,
    expected_issue_number: int | None = None,
) -> list[dict[str, str]]:
    """Validate the FirstMate state block enough for safe projection."""

    diagnostics: list[dict[str, str]] = []

    for field in sorted(REQUIRED_FIELDS):
        if field not in state:
            diagnostics.append(
                {
                    "code": "missing-field",
                    "field": field,
                    "message": "required field is missing: %s" % field,
                }
            )

    if state.get("schema_version") != SCHEMA_VERSION:
        diagnostics.append(
            {
                "code": "invalid-schema-version",
                "field": "schema_version",
                "message": "schema_version must be %s" % SCHEMA_VERSION,
            }
        )

    if "repo" in state and not isinstance(state["repo"], str):
        diagnostics.append(
            {
                "code": "invalid-type",
                "field": "repo",
                "message": "repo must be an owner/name string",
            }
        )

    if "issue_number" in state:
        if not isinstance(state["issue_number"], int):
            diagnostics.append(
                {
                    "code": "invalid-type",
                    "field": "issue_number",
                    "message": "issue_number must be an integer",
                }
            )
        elif (
            expected_issue_number is not None
            and state["issue_number"] != expected_issue_number
        ):
            diagnostics.append(
                {
                    "code": "issue-number-mismatch",
                    "field": "issue_number",
                    "message": "issue_number does not match payload number",
                }
            )

    for field, allowed in ENUM_FIELDS.items():
        if field not in state:
            continue
        value = state[field]
        if not isinstance(value, str):
            diagnostics.append(
                {
                    "code": "invalid-type",
                    "field": field,
                    "message": "%s must be a string" % field,
                }
            )
        elif value not in allowed:
            diagnostics.append(
                {
                    "code": "invalid-enum-value",
                    "field": field,
                    "message": "%s has unsupported value %r" % (field, value),
                }
            )

    for field in OPTIONAL_LIST_FIELDS:
        if field in state and not isinstance(state[field], list):
            diagnostics.append(
                {
                    "code": "invalid-type",
                    "field": field,
                    "message": "%s must be a list when present" % field,
                }
            )

    return diagnostics


def canonical_status(state: dict[str, Any] | None) -> str:
    """Return the deterministic AGOS issue status."""

    if not state:
        return "unmanaged"

    stow_status = STOW_STATE_TO_STATUS.get(str(state.get("stow_state") or ""))
    if stow_status:
        return stow_status

    return WORK_STATE_TO_STATUS.get(str(state.get("work_state") or ""), "invalid")


def project_fields_for_state(
    state: dict[str, Any],
    status: str | None = None,
) -> dict[str, str]:
    """Project FirstMate state into Project/Kanban field names."""

    projected = {
        "Status": STATUS_TO_PROJECT_STATUS.get(
            status or canonical_status(state),
            "Needs repair",
        )
    }
    for project_field, state_field in PROJECT_FIELD_MAP.items():
        value = state.get(state_field)
        if isinstance(value, str):
            value = PROJECT_FIELD_VALUE_OVERRIDES.get((project_field, value), value)
            projected[project_field] = value
    return projected


def project_field_diff(
    current_fields: dict[str, Any],
    expected_fields: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return a deterministic list of Project field changes."""

    changes: list[dict[str, Any]] = []
    for field in sorted(expected_fields):
        current = current_fields.get(field)
        expected = expected_fields[field]
        if current != expected:
            changes.append(
                {
                    "field": field,
                    "current": current,
                    "expected": expected,
                    "action": "set-project-field",
                }
            )
    return changes


def expected_labels_for_state(state: dict[str, Any]) -> list[str]:
    """Project FirstMate state into canonical GitHub label names."""

    expected: set[str] = set()
    pipeline_stage = state.get("pipeline_stage")
    priority = state.get("priority")
    product_outcome = state.get("product_outcome")
    work_state = state.get("work_state")
    stow_state = state.get("stow_state")

    if isinstance(pipeline_stage, str) and pipeline_stage in PIPELINE_STAGES:
        expected.add("pipeline:%s" % _slug(pipeline_stage))
    if isinstance(priority, str) and priority in PRIORITIES:
        expected.add("priority:%s" % _label_value(priority))
    if isinstance(product_outcome, str) and product_outcome in PRODUCT_OUTCOMES:
        expected.add("outcome:%s" % _label_value(product_outcome))
    if isinstance(work_state, str) and work_state in WORK_STATE_LABELS:
        expected.add(WORK_STATE_LABELS[work_state])
    if (
        isinstance(stow_state, str)
        and stow_state != "Active"
        and stow_state in STOW_STATES
    ):
        expected.add("stow:%s" % _slug(stow_state))

    return sorted(expected)


def adapt_issue(issue: dict[str, Any]) -> dict[str, Any]:
    """Adapt a GitHub issue payload into Wheelhouse-readable AGOS state."""

    number = issue_number(issue)
    title = str(issue.get("title") or "")
    body = str(issue.get("body") or "")
    diagnostics: list[dict[str, str]] = []

    try:
        state = parse_firstmate_state_block(body)
    except AgosStateError as error:
        return {
            "number": number,
            "title": title,
            "managed": True,
            "status": "invalid",
            "diagnostics": [
                {
                    "code": "invalid-state-block",
                    "message": str(error),
                }
            ],
            "state": None,
            "project_fields": {},
            "expected_labels": [],
        }

    if not state:
        return {
            "number": number,
            "title": title,
            "managed": False,
            "status": "unmanaged",
            "diagnostics": [
                {
                    "code": "missing-state",
                    "message": "issue body has no firstmate-state block",
                }
            ],
            "state": None,
            "project_fields": {},
            "expected_labels": [],
        }

    diagnostics.extend(validate_state(state, expected_issue_number=number))
    status = "invalid" if diagnostics else canonical_status(state)
    return {
        "number": number,
        "title": title,
        "managed": True,
        "status": status,
        "diagnostics": diagnostics,
        "state": state,
        "project_fields": project_fields_for_state(state, status=status),
        "expected_labels": [] if diagnostics else expected_labels_for_state(state),
    }


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _cmd_adapt(args: argparse.Namespace) -> int:
    payload = _load_json(args.issue_file)
    if isinstance(payload, list):
        adapted = [adapt_issue(issue) for issue in payload]
    elif isinstance(payload, dict):
        adapted = adapt_issue(payload)
    else:
        raise AgosStateError("issue file must contain an object or list of objects")
    print(json.dumps(adapted, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read AGOS firstmate-state issues.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    adapt = subparsers.add_parser("adapt", help="project one issue or a list of issues")
    adapt.add_argument("--issue-file", required=True)
    adapt.set_defaults(func=_cmd_adapt)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AgosStateError as error:
        print("agos-state-error: %s" % error, file=sys.stderr)
        raise SystemExit(2)
