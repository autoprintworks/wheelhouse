#!/usr/bin/env python3
"""Dry-run AGOS Project 3 bridge for Wheelhouse."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import agos_state


REQUIRED_PROJECT_FIELDS = (
    "Status",
    "Pipeline stage",
    "Priority",
    "Product outcome",
    "Work state",
    "Dependency state",
    "Target model tier",
    "Review lane",
    "Parallelization class",
    "Stow state",
)

REQUIRED_PROJECT_VIEWS = (
    "Pipeline Kanban",
    "Execution Now",
    "QA and Review",
    "Model Routing",
    "Stow and Parked",
    "AGOS Future",
)

PROJECT_SCOPE_REPAIR = "gh auth refresh -s project"

PROJECT_QUERY = """
query($owner: String!, $number: Int!) {
  repositoryOwner(login: $owner) {
    ... on User {
      projectV2(number: $number) {
        ...ProjectParts
      }
    }
    ... on Organization {
      projectV2(number: $number) {
        ...ProjectParts
      }
    }
  }
}

fragment ProjectParts on ProjectV2 {
  id
  title
  fields(first: 100) {
    nodes {
      __typename
      ... on ProjectV2Field {
        id
        name
        dataType
      }
      ... on ProjectV2SingleSelectField {
        id
        name
        dataType
        options {
          id
          name
        }
      }
      ... on ProjectV2IterationField {
        id
        name
        dataType
      }
    }
  }
  views(first: 20) {
    nodes {
      id
      name
    }
  }
  items(first: 100) {
    nodes {
      id
      content {
        __typename
        ... on Issue {
          id
          number
          title
          url
          repository {
            nameWithOwner
          }
        }
      }
      fieldValues(first: 100) {
        nodes {
          __typename
          ... on ProjectV2ItemFieldTextValue {
            text
            field {
              ... on ProjectV2FieldCommon {
                name
              }
            }
          }
          ... on ProjectV2ItemFieldSingleSelectValue {
            name
            field {
              ... on ProjectV2FieldCommon {
                name
              }
            }
          }
          ... on ProjectV2ItemFieldNumberValue {
            number
            field {
              ... on ProjectV2FieldCommon {
                name
              }
            }
          }
          ... on ProjectV2ItemFieldDateValue {
            date
            field {
              ... on ProjectV2FieldCommon {
                name
              }
            }
          }
          ... on ProjectV2ItemFieldIterationValue {
            title
            field {
              ... on ProjectV2FieldCommon {
                name
              }
            }
          }
        }
      }
    }
  }
}
"""

UPDATE_SINGLE_SELECT_FIELD_MUTATION = """
mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
  updateProjectV2ItemFieldValue(
    input: {
      projectId: $project
      itemId: $item
      fieldId: $field
      value: { singleSelectOptionId: $option }
    }
  ) {
    projectV2Item {
      id
    }
  }
}
"""


class ProjectBridgeError(RuntimeError):
    """Raised when Project bridge input or execution is unsafe."""


GhRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ProjectBridgeError("could not read %s: %s" % (path, error)) from error
    except json.JSONDecodeError as error:
        raise ProjectBridgeError("%s is not valid JSON: %s" % (path, error)) from error


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _as_list(payload: Any, key: str) -> list[Any]:
    if isinstance(payload, dict) and isinstance(payload.get(key), list):
        return payload[key]
    if isinstance(payload, list):
        return payload
    return []


def _field_names(snapshot: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for field in _as_list(snapshot.get("fields"), "fields"):
        if isinstance(field, str):
            names.add(field)
        elif isinstance(field, dict) and isinstance(field.get("name"), str):
            names.add(field["name"])
    return names


def _view_names(snapshot: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for view in _as_list(snapshot.get("views"), "views"):
        if isinstance(view, str):
            names.add(view)
        elif isinstance(view, dict) and isinstance(view.get("name"), str):
            names.add(view["name"])
    return names


def _item_issue_number(item: dict[str, Any]) -> int | None:
    for key in ("issue_number", "number"):
        if key not in item:
            continue
        try:
            return int(item[key])
        except (TypeError, ValueError):
            return None
    content = item.get("content")
    if isinstance(content, dict):
        try:
            return int(content.get("number"))
        except (TypeError, ValueError):
            return None
    return None


def _item_fields(item: dict[str, Any]) -> dict[str, Any]:
    fields = item.get("fields")
    return fields if isinstance(fields, dict) else {}


def _items_by_issue(snapshot: dict[str, Any]) -> dict[int, dict[str, Any]]:
    items: dict[int, dict[str, Any]] = {}
    for item in _as_list(snapshot.get("items"), "items"):
        if not isinstance(item, dict):
            continue
        number = _item_issue_number(item)
        if number is not None:
            items[number] = item
    return items


def _fields_by_name(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    fields: dict[str, dict[str, Any]] = {}
    for field in _as_list(snapshot.get("fields"), "fields"):
        if isinstance(field, dict) and isinstance(field.get("name"), str):
            fields[field["name"]] = field
    return fields


def _single_select_option_id(field: dict[str, Any], option_name: Any) -> str | None:
    if not isinstance(option_name, str):
        return None
    for option in field.get("options") or []:
        if not isinstance(option, dict):
            continue
        if option.get("name") == option_name and isinstance(option.get("id"), str):
            return option["id"]
    return None


def _diagnostic(
    code: str,
    message: str,
    *,
    severity: str = "error",
    hint: str | None = None,
) -> dict[str, str]:
    diagnostic = {
        "severity": severity,
        "code": code,
        "message": message,
    }
    if hint:
        diagnostic["hint"] = hint
    return diagnostic


def validate_project_snapshot(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    """Validate that a Project snapshot has the fields and views AGOS needs."""

    diagnostics: list[dict[str, str]] = []
    scope_status = str(snapshot.get("scope_status") or "ok")
    if scope_status != "ok":
        diagnostics.append(
            _diagnostic(
                "missing-projectv2-scope",
                "ProjectV2 data was not readable with the current GitHub token",
                hint=PROJECT_SCOPE_REPAIR,
            )
        )

    fields = _field_names(snapshot)
    views = _view_names(snapshot)
    for field in REQUIRED_PROJECT_FIELDS:
        if field not in fields:
            diagnostics.append(
                _diagnostic(
                    "missing-project-field",
                    "Project is missing required field: %s" % field,
                )
            )
    for view in REQUIRED_PROJECT_VIEWS:
        if view not in views:
            diagnostics.append(
                _diagnostic(
                    "missing-project-view",
                    "Project is missing required view: %s" % view,
                )
            )
    return diagnostics


def _issue_url(issue: dict[str, Any]) -> str | None:
    url = issue.get("url")
    return url if isinstance(url, str) and url else None


def _issue_content_id(issue: dict[str, Any]) -> str | None:
    for key in ("id", "node_id", "content_id"):
        value = issue.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _plan_set_fields(
    *,
    issue_number: int,
    item_id: str | None,
    current_fields: dict[str, Any],
    expected_fields: dict[str, Any],
    available_fields: set[str],
    after_add: bool = False,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    diff = agos_state.project_field_diff(current_fields, expected_fields)
    for change in diff:
        if change["field"] not in available_fields:
            continue
        action = dict(change)
        action["issue_number"] = issue_number
        action["item_id"] = item_id
        if after_add:
            action["after"] = "add-project-item"
        actions.append(action)
    return actions


def plan_project_sync(
    *,
    issues: list[dict[str, Any]],
    project_snapshot: dict[str, Any],
    repo: str | None = None,
) -> dict[str, Any]:
    """Plan a Project 3 sync without mutating GitHub."""

    diagnostics = validate_project_snapshot(project_snapshot)
    field_names = _field_names(project_snapshot)
    items_by_issue = _items_by_issue(project_snapshot)
    actions: list[dict[str, Any]] = []
    unmanaged: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    adapted_issues: list[dict[str, Any]] = []

    for issue in issues:
        adapted = agos_state.adapt_issue(issue)
        state = adapted.get("state")
        if repo and isinstance(state, dict) and state.get("repo") != repo:
            continue
        adapted_issues.append(adapted)

        if not adapted["managed"]:
            unmanaged.append(
                {
                    "issue_number": adapted["number"],
                    "title": adapted["title"],
                    "reason": "missing firstmate-state block",
                }
            )
            continue

        if adapted["status"] == "invalid":
            invalid.append(
                {
                    "issue_number": adapted["number"],
                    "title": adapted["title"],
                    "diagnostics": adapted["diagnostics"],
                }
            )
            continue

        number = adapted["number"]
        if number is None:
            invalid.append(
                {
                    "issue_number": None,
                    "title": adapted["title"],
                    "diagnostics": [
                        _diagnostic(
                            "missing-issue-number",
                            "cannot sync an issue without a number",
                        )
                    ],
                }
            )
            continue

        expected_fields = adapted["project_fields"]
        item = items_by_issue.get(number)
        if not item:
            actions.append(
                {
                    "action": "add-project-item",
                    "issue_number": number,
                    "content_id": _issue_content_id(issue),
                    "url": _issue_url(issue),
                    "reason": "managed AGOS issue is missing from Project 3",
                }
            )
            actions.extend(
                _plan_set_fields(
                    issue_number=number,
                    item_id=None,
                    current_fields={},
                    expected_fields=expected_fields,
                    available_fields=field_names,
                    after_add=True,
                )
            )
            continue

        item_id = item.get("id") if isinstance(item.get("id"), str) else None
        actions.extend(
            _plan_set_fields(
                issue_number=number,
                item_id=item_id,
                current_fields=_item_fields(item),
                expected_fields=expected_fields,
                available_fields=field_names,
            )
        )

    error_count = sum(1 for item in diagnostics if item.get("severity") == "error")
    return {
        "mode": "dry-run",
        "project_status": "blocked" if error_count else "ready",
        "project_number": project_snapshot.get("number"),
        "project_id": project_snapshot.get("id"),
        "project_title": project_snapshot.get("title"),
        "verified_fields": sorted(field_names.intersection(REQUIRED_PROJECT_FIELDS)),
        "verified_views": sorted(_view_names(project_snapshot).intersection(REQUIRED_PROJECT_VIEWS)),
        "diagnostics": diagnostics,
        "fetched_issues": len(issues),
        "managed_issues": len(adapted_issues) - len(unmanaged),
        "unmanaged_issues": unmanaged,
        "invalid_issues": invalid,
        "planned_actions": actions,
        "action_count": len(actions),
    }


def preflight_project_actions(
    *,
    project_snapshot: dict[str, Any],
    actions: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Validate every planned action before any live Project mutation."""

    diagnostics: list[dict[str, str]] = []
    if not project_snapshot.get("id"):
        diagnostics.append(
            _diagnostic("missing-project-id", "Project snapshot is missing id")
        )
    fields = _fields_by_name(project_snapshot)
    for action in actions:
        if action.get("action") != "set-project-field":
            diagnostics.append(
                _diagnostic(
                    "unsupported-action",
                    "apply only supports set-project-field actions",
                )
            )
            continue
        if not action.get("item_id"):
            diagnostics.append(
                _diagnostic(
                    "missing-item-id",
                    "cannot set a Project field before the item id is known",
                )
            )
        field_name = action.get("field")
        field = fields.get(field_name)
        if not field:
            diagnostics.append(
                _diagnostic(
                    "missing-project-field",
                    "Project field is missing from snapshot: %s" % field_name,
                )
            )
            continue
        if field.get("type") != "ProjectV2SingleSelectField":
            diagnostics.append(
                _diagnostic(
                    "unsupported-field-type",
                    "only ProjectV2 single-select fields are currently applyable",
                )
            )
            continue
        if not _single_select_option_id(field, action.get("expected")):
            diagnostics.append(
                _diagnostic(
                    "missing-project-option",
                    "Project field %s has no option %r"
                    % (field_name, action.get("expected")),
                )
            )
    return diagnostics


def apply_project_actions(
    *,
    project_snapshot: dict[str, Any],
    actions: list[dict[str, Any]],
    runner: GhRunner | None = None,
) -> list[dict[str, Any]]:
    """Apply preflighted ProjectV2 single-select field updates."""

    diagnostics = preflight_project_actions(
        project_snapshot=project_snapshot,
        actions=actions,
    )
    if diagnostics:
        raise ProjectBridgeError(
            "Project apply preflight failed: %s"
            % "; ".join(item["message"] for item in diagnostics)
        )

    active_runner = runner or _default_gh_runner
    project_id = str(project_snapshot["id"])
    fields = _fields_by_name(project_snapshot)
    applied: list[dict[str, Any]] = []
    for action in actions:
        field = fields[str(action["field"])]
        option_id = _single_select_option_id(field, action.get("expected"))
        completed = active_runner(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                "query=%s" % UPDATE_SINGLE_SELECT_FIELD_MUTATION,
                "-F",
                "project=%s" % project_id,
                "-F",
                "item=%s" % action["item_id"],
                "-F",
                "field=%s" % field["id"],
                "-F",
                "option=%s" % option_id,
            ]
        )
        if completed.returncode != 0:
            details = (completed.stderr or completed.stdout or "").strip()
            raise ProjectBridgeError(
                "Project field update failed for issue #%s field %s: %s"
                % (action.get("issue_number"), action.get("field"), details)
            )
        applied.append(
            {
                "action": "set-project-field",
                "issue_number": action.get("issue_number"),
                "item_id": action.get("item_id"),
                "field": action.get("field"),
                "expected": action.get("expected"),
            }
        )
    return applied


def _field_value_name(node: dict[str, Any]) -> tuple[str | None, Any]:
    field = node.get("field")
    field_name = field.get("name") if isinstance(field, dict) else None
    if not field_name:
        return None, None
    for key in ("name", "text", "number", "date", "title"):
        if key in node and node[key] is not None:
            return field_name, node[key]
    return field_name, None


def graphql_project_to_snapshot(
    payload: dict[str, Any],
    *,
    owner: str,
    project_number: int,
) -> dict[str, Any]:
    """Convert GitHub ProjectV2 GraphQL output into the bridge snapshot shape."""

    repository_owner = ((payload.get("data") or {}).get("repositoryOwner") or {})
    project = repository_owner.get("projectV2")
    if not isinstance(project, dict):
        raise ProjectBridgeError("ProjectV2 %s was not found for %s" % (project_number, owner))

    fields: list[dict[str, Any]] = []
    for node in (((project.get("fields") or {}).get("nodes")) or []):
        if not isinstance(node, dict) or not node.get("name"):
            continue
        field = {
            "id": node.get("id"),
            "name": node.get("name"),
            "dataType": node.get("dataType"),
            "type": node.get("__typename"),
        }
        if isinstance(node.get("options"), list):
            field["options"] = [
                {"id": option.get("id"), "name": option.get("name")}
                for option in node["options"]
                if isinstance(option, dict)
            ]
        fields.append(field)

    views = [
        {"id": node.get("id"), "name": node.get("name")}
        for node in (((project.get("views") or {}).get("nodes")) or [])
        if isinstance(node, dict) and node.get("name")
    ]

    items: list[dict[str, Any]] = []
    for node in (((project.get("items") or {}).get("nodes")) or []):
        if not isinstance(node, dict):
            continue
        content = node.get("content")
        if not isinstance(content, dict) or content.get("__typename") != "Issue":
            continue
        repository = content.get("repository")
        fields_by_name: dict[str, Any] = {}
        for value_node in (((node.get("fieldValues") or {}).get("nodes")) or []):
            if not isinstance(value_node, dict):
                continue
            field_name, value = _field_value_name(value_node)
            if field_name:
                fields_by_name[field_name] = value
        items.append(
            {
                "id": node.get("id"),
                "content_id": content.get("id"),
                "issue_number": content.get("number"),
                "title": content.get("title"),
                "url": content.get("url"),
                "repo": repository.get("nameWithOwner") if isinstance(repository, dict) else None,
                "fields": fields_by_name,
            }
        )

    return {
        "owner": owner,
        "number": project_number,
        "id": project.get("id"),
        "title": project.get("title"),
        "scope_status": "ok",
        "fields": fields,
        "views": views,
        "items": items,
    }


def classify_gh_project_error(text: str) -> dict[str, str] | None:
    """Detect the common missing ProjectV2-scope failure."""

    lowered = text.casefold()
    markers = (
        "projectv2",
        "project v2",
        "resource not accessible",
        "insufficient oauth scopes",
        "requires one of the following scopes",
        "could not resolve to a projectv2",
    )
    if any(marker in lowered for marker in markers):
        return _diagnostic(
            "missing-projectv2-scope",
            "GitHub token could not read ProjectV2 data",
            hint=PROJECT_SCOPE_REPAIR,
        )
    return None


def _default_gh_runner(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        text=True,
    )


def fetch_project_snapshot(
    *,
    owner: str,
    project_number: int,
    runner: GhRunner | None = None,
) -> dict[str, Any]:
    active_runner = runner or _default_gh_runner
    completed = active_runner(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            "query=%s" % PROJECT_QUERY,
            "-F",
            "owner=%s" % owner,
            "-F",
            "number=%s" % project_number,
        ]
    )
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "").strip()
        scope_diagnostic = classify_gh_project_error(details)
        if scope_diagnostic:
            raise ProjectBridgeError(
                "%s. Repair with `%s`."
                % (scope_diagnostic["message"], PROJECT_SCOPE_REPAIR)
            )
        raise ProjectBridgeError("gh project snapshot failed: %s" % details)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ProjectBridgeError("gh project snapshot was not JSON: %s" % error) from error
    return graphql_project_to_snapshot(
        payload,
        owner=owner,
        project_number=project_number,
    )


def ensure_apply_allowed(*, apply: bool, confirm_project_mutation: bool) -> None:
    if not apply:
        return
    if not confirm_project_mutation:
        raise ProjectBridgeError(
            "apply requires --confirm-project-mutation and captain approval"
        )


def _default_readback_file() -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    return Path("data") / ("agos-project3-readback-%s.json" % timestamp)


def _cmd_snapshot(args: argparse.Namespace) -> int:
    snapshot = fetch_project_snapshot(
        owner=args.owner,
        project_number=args.project,
    )
    diagnostics = validate_project_snapshot(snapshot)
    if args.out:
        _write_json(Path(args.out), snapshot)
    print("project_status: %s" % ("blocked" if diagnostics else "ready"))
    print("fields: %s" % len(_field_names(snapshot)))
    print("views: %s" % len(_view_names(snapshot)))
    print("items: %s" % len(_items_by_issue(snapshot)))
    for diagnostic in diagnostics:
        print("diagnostic: %(severity)s,%(code)s,%(message)s" % diagnostic)
    return 1 if any(item.get("severity") == "error" for item in diagnostics) else 0


def _cmd_plan(args: argparse.Namespace) -> int:
    ensure_apply_allowed(
        apply=args.apply,
        confirm_project_mutation=args.confirm_project_mutation,
    )
    issues_payload = _read_json(Path(args.issues_file))
    project_snapshot = _read_json(Path(args.project_file))
    issues = _as_list(issues_payload.get("issues") if isinstance(issues_payload, dict) else issues_payload, "issues")
    issues = [issue for issue in issues if isinstance(issue, dict)]
    readback = plan_project_sync(
        issues=issues,
        project_snapshot=project_snapshot,
        repo=args.repo,
    )
    if args.apply:
        if readback["project_status"] == "blocked":
            raise ProjectBridgeError("cannot apply while Project validation is blocked")
        applied = apply_project_actions(
            project_snapshot=project_snapshot,
            actions=readback["planned_actions"],
        )
        readback["mode"] = "apply"
        readback["applied_actions"] = applied
        readback["applied_action_count"] = len(applied)
    target = Path(args.readback_file) if args.readback_file else _default_readback_file()
    _write_json(target, readback)
    print("mode: %s" % readback["mode"])
    print("project_status: %s" % readback["project_status"])
    print("fetched_issues: %s" % readback["fetched_issues"])
    print("managed_issues: %s" % readback["managed_issues"])
    print("unmanaged_issues: %s" % len(readback["unmanaged_issues"]))
    print("invalid_issues: %s" % len(readback["invalid_issues"]))
    print("planned_actions: %s" % readback["action_count"])
    if args.apply:
        print("applied_actions: %s" % readback["applied_action_count"])
    print("readback_file: %s" % target)
    return 1 if readback["project_status"] == "blocked" else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run AGOS Project 3 sync.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot = subparsers.add_parser("snapshot", help="read ProjectV2 through gh")
    snapshot.add_argument("--owner", required=True)
    snapshot.add_argument("--project", required=True, type=int)
    snapshot.add_argument("--out")
    snapshot.set_defaults(func=_cmd_snapshot)

    plan = subparsers.add_parser("plan", help="plan Project 3 sync from snapshots")
    plan.add_argument("--issues-file", required=True)
    plan.add_argument("--project-file", required=True)
    plan.add_argument("--readback-file")
    plan.add_argument("--repo")
    plan.add_argument("--apply", action="store_true")
    plan.add_argument("--confirm-project-mutation", action="store_true")
    plan.set_defaults(func=_cmd_plan)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProjectBridgeError as error:
        print("agos-project-bridge-error: %s" % error, file=sys.stderr)
        raise SystemExit(2)
