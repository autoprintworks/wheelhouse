#!/usr/bin/env python3
"""Exercise the AGOS FirstMate state adapter without network access."""

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

import agos_state  # noqa: E402


FIXTURE_PATH = ROOT / "tests" / "fixtures" / "agos_issue_states.json"


def fixture_issues() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def by_number() -> dict[int, dict]:
    return {issue["number"]: issue for issue in fixture_issues()}


class AgosStateTests(unittest.TestCase):
    def test_parses_firstmate_state_block(self) -> None:
        issue = by_number()[120]
        state = agos_state.parse_firstmate_state_block(issue["body"])

        self.assertIsNotNone(state)
        self.assertEqual(state["schema_version"], 1)
        self.assertEqual(state["repo"], "autoprintworks/firstmate-gui-agnostic")
        self.assertEqual(state["work_state"], "Ready to start")

    def test_unmanaged_ready_blocked_stowed_superseded_and_done_are_deterministic(
        self,
    ) -> None:
        adapted = {
            issue["number"]: agos_state.adapt_issue(issue)
            for issue in fixture_issues()
        }

        self.assertEqual(adapted[85]["status"], "unmanaged")
        self.assertFalse(adapted[85]["managed"])
        self.assertEqual(adapted[120]["status"], "ready")
        self.assertEqual(adapted[116]["status"], "blocked")
        self.assertEqual(adapted[117]["status"], "stowed")
        self.assertEqual(adapted[112]["status"], "superseded")
        self.assertEqual(adapted[113]["status"], "done")

    def test_project_projection_names_the_project_three_fields(self) -> None:
        projection = agos_state.adapt_issue(by_number()[120])
        fields = projection["project_fields"]

        self.assertEqual(fields["Status"], "Ready")
        self.assertEqual(fields["Pipeline stage"], "Kanban Board")
        self.assertEqual(fields["Priority"], "Do now")
        self.assertEqual(fields["Product outcome"], "Planning system")
        self.assertEqual(fields["Work state"], "Ready to start")
        self.assertEqual(fields["Dependency state"], "Unblocked")
        self.assertEqual(fields["Target model tier"], "Strong planning")
        self.assertEqual(fields["Review lane"], "Human decision")
        self.assertEqual(fields["Parallelization class"], "Serial gate")
        self.assertEqual(fields["Stow state"], "Active")

    def test_project_status_projection_uses_live_project_options(self) -> None:
        issues = by_number()

        self.assertEqual(
            agos_state.adapt_issue(issues[112])["project_fields"]["Status"],
            "Stowed",
        )
        self.assertEqual(
            agos_state.adapt_issue(issues[113])["project_fields"]["Status"],
            "Done",
        )
        self.assertEqual(
            agos_state.adapt_issue(issues[116])["project_fields"]["Status"],
            "Blocked",
        )
        fields = agos_state.project_fields_for_state(
            {
                "work_state": "Needs captain decision",
                "stow_state": "Active",
            }
        )
        self.assertEqual(fields["Work state"], "Needs founder decision")

    def test_label_projection_matches_firstmate_families(self) -> None:
        ready = agos_state.adapt_issue(by_number()[120])
        stowed = agos_state.adapt_issue(by_number()[117])

        self.assertIn("pipeline:kanban-board", ready["expected_labels"])
        self.assertIn("priority:do now", ready["expected_labels"])
        self.assertIn("outcome:planning system", ready["expected_labels"])
        self.assertIn("ready-for-agent", ready["expected_labels"])
        self.assertIn("stow:stowed", stowed["expected_labels"])
        self.assertIn("work state:stowed", stowed["expected_labels"])

    def test_project_field_diff_is_sorted_and_minimal(self) -> None:
        expected = agos_state.adapt_issue(by_number()[116])["project_fields"]
        current = {
            "Status": "Ready",
            "Pipeline stage": "Kanban Board",
            "Priority": "Do now",
        }

        diff = agos_state.project_field_diff(current, expected)
        changed = {item["field"]: item for item in diff}

        self.assertEqual(changed["Status"]["expected"], "Blocked")
        self.assertNotIn("Pipeline stage", changed)
        self.assertEqual([item["field"] for item in diff], sorted(changed))

    def test_renderer_replaces_exactly_one_state_block(self) -> None:
        issue = by_number()[120]
        state = agos_state.parse_firstmate_state_block(issue["body"])
        assert state is not None
        updated = dict(state, work_state="Blocked")

        body = agos_state.replace_firstmate_state_block(issue["body"], updated)

        self.assertEqual(body.count("firstmate-state"), 1)
        parsed = agos_state.parse_firstmate_state_block(body)
        self.assertEqual(parsed["work_state"], "Blocked")

    def test_cli_adapts_fixture_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "issues.json"
            path.write_text(json.dumps(fixture_issues()), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "scripts" / "agos_state.py"),
                    "adapt",
                    "--issue-file",
                    str(path),
                ],
                check=True,
                capture_output=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                text=True,
            )

        adapted = json.loads(result.stdout)
        statuses = {issue["number"]: issue["status"] for issue in adapted}
        self.assertEqual(statuses[85], "unmanaged")
        self.assertEqual(statuses[120], "ready")


if __name__ == "__main__":
    unittest.main()
