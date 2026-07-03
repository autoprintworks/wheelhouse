#!/usr/bin/env python3
"""Exercise the AGOS Project 3 bridge without GitHub access."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import agos_project_bridge as bridge  # noqa: E402


ISSUES_FIXTURE = ROOT / "tests" / "fixtures" / "agos_issue_states.json"
PROJECT_FIXTURE = ROOT / "tests" / "fixtures" / "project3_snapshot.json"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class AgosProjectBridgeTests(unittest.TestCase):
    def test_snapshot_validation_requires_project_fields_and_views(self) -> None:
        snapshot = load_json(PROJECT_FIXTURE)
        diagnostics = bridge.validate_project_snapshot(snapshot)

        self.assertEqual(diagnostics, [])

        broken = dict(snapshot)
        broken["fields"] = ["Status"]
        broken["views"] = ["Pipeline Kanban"]
        codes = {
            diagnostic["code"]
            for diagnostic in bridge.validate_project_snapshot(broken)
        }

        self.assertIn("missing-project-field", codes)
        self.assertIn("missing-project-view", codes)

    def test_dry_run_plans_missing_items_and_field_drift(self) -> None:
        readback = bridge.plan_project_sync(
            issues=load_json(ISSUES_FIXTURE),
            project_snapshot=load_json(PROJECT_FIXTURE),
        )

        self.assertEqual(readback["project_status"], "ready")
        self.assertEqual(len(readback["unmanaged_issues"]), 1)
        self.assertEqual(readback["unmanaged_issues"][0]["issue_number"], 85)

        actions = readback["planned_actions"]
        add_numbers = {
            action["issue_number"]
            for action in actions
            if action["action"] == "add-project-item"
        }
        self.assertEqual(add_numbers, {116, 113})

        field_changes = [
            action
            for action in actions
            if action["action"] == "set-project-field"
            and action["issue_number"] == 112
        ]
        self.assertTrue(field_changes)
        self.assertNotIn(
            "Status",
            {action["field"] for action in field_changes},
        )
        self.assertIn(
            ("Stow state", "Superseded"),
            {(action["field"], action["expected"]) for action in field_changes},
        )

    def test_missing_scope_blocks_the_plan_before_apply(self) -> None:
        snapshot = load_json(PROJECT_FIXTURE)
        snapshot["scope_status"] = "missing-projectv2-scope"
        readback = bridge.plan_project_sync(
            issues=load_json(ISSUES_FIXTURE),
            project_snapshot=snapshot,
        )

        self.assertEqual(readback["project_status"], "blocked")
        self.assertEqual(
            readback["diagnostics"][0]["hint"],
            bridge.PROJECT_SCOPE_REPAIR,
        )

    def test_apply_mode_requires_explicit_confirmation(self) -> None:
        with self.assertRaises(bridge.ProjectBridgeError) as missing_confirm:
            bridge.ensure_apply_allowed(apply=True, confirm_project_mutation=False)
        self.assertIn("--confirm-project-mutation", str(missing_confirm.exception))

        bridge.ensure_apply_allowed(apply=True, confirm_project_mutation=True)

    def test_apply_preflight_rejects_missing_options_before_mutation(self) -> None:
        diagnostics = bridge.preflight_project_actions(
            project_snapshot={
                "id": "PVT",
                "fields": [
                    {
                        "id": "field_status",
                        "name": "Status",
                        "type": "ProjectV2SingleSelectField",
                        "options": [{"id": "ready", "name": "Ready"}],
                    }
                ],
            },
            actions=[
                {
                    "action": "set-project-field",
                    "issue_number": 120,
                    "item_id": "PVTI_120",
                    "field": "Status",
                    "expected": "Missing",
                }
            ],
        )

        self.assertEqual(diagnostics[0]["code"], "missing-project-option")

    def test_apply_project_actions_uses_single_select_option_id(self) -> None:
        calls = []

        def fake_runner(args):
            calls.append(args)
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout='{"data":{}}',
                stderr="",
            )

        applied = bridge.apply_project_actions(
            project_snapshot={
                "id": "PVT",
                "fields": [
                    {
                        "id": "field_status",
                        "name": "Status",
                        "type": "ProjectV2SingleSelectField",
                        "options": [{"id": "ready", "name": "Ready"}],
                    }
                ],
            },
            actions=[
                {
                    "action": "set-project-field",
                    "issue_number": 120,
                    "item_id": "PVTI_120",
                    "field": "Status",
                    "expected": "Ready",
                }
            ],
            runner=fake_runner,
        )

        self.assertEqual(applied[0]["field"], "Status")
        self.assertIn("project=PVT", calls[0])
        self.assertIn("item=PVTI_120", calls[0])
        self.assertIn("field=field_status", calls[0])
        self.assertIn("option=ready", calls[0])

    def test_apply_project_actions_fails_before_unsupported_live_work(self) -> None:
        with self.assertRaises(bridge.ProjectBridgeError) as blocked_live:
            bridge.apply_project_actions(
                project_snapshot={"id": "PVT", "fields": []},
                actions=[
                    {
                        "action": "add-project-item",
                        "issue_number": 120,
                    }
                ],
            )
        self.assertIn("preflight failed", str(blocked_live.exception))

    def test_graphql_project_snapshot_parser(self) -> None:
        payload = {
            "data": {
                "repositoryOwner": {
                    "projectV2": {
                        "id": "PVT_live",
                        "title": "FirstMate Execution MVP - Issue to Safe PR",
                        "fields": {
                            "nodes": [
                                {
                                    "__typename": "ProjectV2SingleSelectField",
                                    "id": "field_status",
                                    "name": "Status",
                                    "dataType": "SINGLE_SELECT",
                                    "options": [{"id": "ready", "name": "Ready"}],
                                }
                            ]
                        },
                        "views": {"nodes": [{"id": "view", "name": "Pipeline Kanban"}]},
                        "items": {
                            "nodes": [
                                {
                                    "id": "PVTI_live",
                                    "content": {
                                        "__typename": "Issue",
                                        "id": "I_120",
                                        "number": 120,
                                        "title": "Canonical plan",
                                        "url": "https://example.invalid/120",
                                        "repository": {
                                            "nameWithOwner": (
                                                "autoprintworks/"
                                                "firstmate-gui-agnostic"
                                            )
                                        },
                                    },
                                    "fieldValues": {
                                        "nodes": [
                                            {
                                                "__typename": (
                                                    "ProjectV2ItemField"
                                                    "SingleSelectValue"
                                                ),
                                                "name": "Ready",
                                                "field": {"name": "Status"},
                                            }
                                        ]
                                    },
                                }
                            ]
                        },
                    }
                }
            }
        }

        snapshot = bridge.graphql_project_to_snapshot(
            payload,
            owner="autoprintworks",
            project_number=3,
        )

        self.assertEqual(snapshot["id"], "PVT_live")
        self.assertEqual(snapshot["fields"][0]["options"][0]["name"], "Ready")
        self.assertEqual(snapshot["items"][0]["issue_number"], 120)
        self.assertEqual(snapshot["items"][0]["fields"]["Status"], "Ready")

    def test_cli_writes_bounded_readback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            readback = Path(temp_dir) / "readback.json"
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "scripts" / "agos_project_bridge.py"),
                    "plan",
                    "--issues-file",
                    str(ISSUES_FIXTURE),
                    "--project-file",
                    str(PROJECT_FIXTURE),
                    "--readback-file",
                    str(readback),
                ],
                check=True,
                capture_output=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                text=True,
            )

            self.assertIn("planned_actions:", result.stdout)
            payload = json.loads(readback.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "dry-run")
            self.assertLessEqual(len(payload["planned_actions"]), 100)


if __name__ == "__main__":
    unittest.main()
