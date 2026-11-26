"""Unit and integration tests for Task H6 - Implement State Diff.

Validates all 5 Definition of Done criteria:
1. Every diff references canonical resource IDs.
2. Before/after values and property paths are preserved.
3. Unchanged resources are excluded.
4. Output validates against the agreed contract (schema/v1/diff.schema.json).
5. Tests cover all four operation types (ADDED, REMOVED, MODIFIED, REPLACED).
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jsonschema import Draft202012Validator

from diff import DiffChangeType, ResourceDiffRecord, StateDiff, StateDiffer, compute_diff
from future_twin import FutureTwinGenerator
from ingestion import ChangeIngester

CURRENT_TWIN_FIXTURE = ROOT / "fixtures" / "sample_current_state.json"
SCENARIO_FIXTURE = ROOT / "scenarios" / "scenario-01.json"
SCHEMA_PATH = ROOT / "schema" / "v1" / "diff.schema.json"

CANONICAL_ID_REGEX = re.compile(r"^(aws:internet|(?:aws|k8s):[a-z][a-z0-9-]*:.+)$")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class H6StateDiffTests(unittest.TestCase):
    """Core test suite for State Diff computation and validation."""

    def setUp(self) -> None:
        self.current_twin = load_json(CURRENT_TWIN_FIXTURE)
        self.differ = StateDiffer()
        self.schema = load_json(SCHEMA_PATH)
        self.validator = Draft202012Validator(self.schema)

    def test_scenario_01_diff_produces_single_modified_record(self) -> None:
        """Scenario-01 increases UI replicas from 1 to 3; diff must show exactly that change."""
        # Generate Future Twin using H4 and H5
        ingester = ChangeIngester(current_twin=self.current_twin)
        changes = ingester.ingest(SCENARIO_FIXTURE)
        generator = FutureTwinGenerator()
        future = generator.generate(self.current_twin, changes)

        # Compute diff
        diff = self.differ.diff(self.current_twin, future)

        # DoD 3: Unchanged resources are excluded (only 1 changed out of 40)
        self.assertEqual(len(diff.changes), 1)
        self.assertEqual(diff.summary["total_changes"], 1)
        self.assertEqual(diff.summary["modified"], 1)
        self.assertEqual(diff.summary["added"], 0)
        self.assertEqual(diff.summary["removed"], 0)
        self.assertEqual(diff.summary["replaced"], 0)

        record = diff.changes[0]
        # DoD 1: Canonical resource ID
        self.assertEqual(record.resource_id, "k8s:deployment:default:ui")
        self.assertTrue(CANONICAL_ID_REGEX.match(record.resource_id))

        # DoD 2: Before/after values and property paths are preserved
        self.assertEqual(record.change_type, DiffChangeType.MODIFIED)
        self.assertEqual(record.property, "properties.replicas")
        self.assertEqual(record.before, 1)
        self.assertEqual(record.after, 3)

        # DoD 4: Validates against schema/v1/diff.schema.json
        errors = list(self.validator.iter_errors(diff.to_dict()))
        self.assertEqual(errors, [], f"Schema validation errors: {errors}")

    def test_unchanged_twins_produce_empty_diff(self) -> None:
        """DoD 3: Diffing identical twins produces zero diff records."""
        diff = self.differ.diff(self.current_twin, self.current_twin)
        self.assertEqual(len(diff.changes), 0)
        self.assertEqual(diff.summary["total_changes"], 0)
        self.assertEqual(diff.summary["added"], 0)
        self.assertEqual(diff.summary["removed"], 0)
        self.assertEqual(diff.summary["modified"], 0)
        self.assertEqual(diff.summary["replaced"], 0)

        # Validates against schema
        errors = list(self.validator.iter_errors(diff.to_dict()))
        self.assertEqual(errors, [])

    def test_all_four_operation_types(self) -> None:
        """DoD 5: Tests cover all four operation types: ADDED, REMOVED, MODIFIED, REPLACED."""
        current = copy.deepcopy(self.current_twin)
        future_twin_dict = copy.deepcopy(self.current_twin)

        # 1. ADDED: Add a new ConfigMap
        new_cm = {
            "id": "k8s:configmap:default:app-settings",
            "provider": "k8s",
            "type": "configmap",
            "namespace": "default",
            "name": "app-settings",
            "properties": {"LOG_LEVEL": "DEBUG"},
        }
        future_twin_dict["resources"].append(new_cm)

        # 2. REMOVED: Remove an existing deployment
        target_removed_id = "k8s:deployment:default:checkout"
        future_twin_dict["resources"] = [
            r for r in future_twin_dict["resources"] if r["id"] != target_removed_id
        ]

        # 3. MODIFIED: Modify an existing deployment replicas
        target_modified_id = "k8s:deployment:default:orders"
        for r in future_twin_dict["resources"]:
            if r["id"] == target_modified_id:
                r["properties"]["replicas"] = 5

        # 4. REPLACED: Replace an existing deployment with provenance marking
        target_replaced_id = "k8s:deployment:default:cart"
        for r in future_twin_dict["resources"]:
            if r["id"] == target_replaced_id:
                r["properties"] = {"replicas": 4, "image": "custom-cart:2.0.0"}

        provenance = {
            target_replaced_id: [
                {
                    "source": "terraform",
                    "operation": "replace",
                    "resource_id": target_replaced_id,
                }
            ]
        }
        future_payload = {
            "twin": future_twin_dict,
            "provenance": provenance,
        }

        diff = self.differ.diff(current, future_payload)

        # Check counts
        self.assertEqual(diff.summary["added"], 1)
        self.assertEqual(diff.summary["removed"], 1)
        self.assertEqual(diff.summary["modified"], 1)
        self.assertEqual(diff.summary["replaced"], 1)
        self.assertEqual(diff.summary["total_changes"], 4)

        # Check each change record specifically
        by_type = {c.change_type: c for c in diff.changes}

        # ADDED check
        added_rec = by_type[DiffChangeType.ADDED]
        self.assertEqual(added_rec.resource_id, new_cm["id"])
        self.assertIsNone(added_rec.before)
        self.assertEqual(added_rec.after, new_cm["properties"])

        # REMOVED check
        removed_rec = by_type[DiffChangeType.REMOVED]
        self.assertEqual(removed_rec.resource_id, target_removed_id)
        self.assertIsNotNone(removed_rec.before)
        self.assertIsNone(removed_rec.after)

        # MODIFIED check
        modified_rec = by_type[DiffChangeType.MODIFIED]
        self.assertEqual(modified_rec.resource_id, target_modified_id)
        self.assertEqual(modified_rec.property, "properties.replicas")
        self.assertEqual(modified_rec.before, 1)
        self.assertEqual(modified_rec.after, 5)

        # REPLACED check
        replaced_rec = by_type[DiffChangeType.REPLACED]
        self.assertEqual(replaced_rec.resource_id, target_replaced_id)
        self.assertIsNotNone(replaced_rec.before)
        self.assertEqual(replaced_rec.after, {"replicas": 4, "image": "custom-cart:2.0.0"})

        # DoD 4: Validates against schema
        errors = list(self.validator.iter_errors(diff.to_dict()))
        self.assertEqual(errors, [])

    def test_every_diff_references_canonical_id(self) -> None:
        """DoD 1: Every diff must strictly match canonical ID regex."""
        # Create multiple mixed changes
        current = copy.deepcopy(self.current_twin)
        future = copy.deepcopy(self.current_twin)

        # Mutate AWS and K8s resources that exist in fixture
        for r in future["resources"]:
            if r["id"] == "aws:subnet:subnet-0aaa1111aaaa0001a":
                r["properties"]["map_public_ip"] = False
            if r["id"] == "aws:vpc:vpc-0aaa1111bbbb2222c":
                r["properties"]["tags"]["Environment"] = "staging"
            if r["id"] == "k8s:deployment:default:ui":
                r["properties"]["replicas"] = 5

        diff = self.differ.diff(current, future)
        self.assertGreater(len(diff.changes), 0)

        for record in diff.changes:
            self.assertTrue(
                CANONICAL_ID_REGEX.match(record.resource_id),
                f"Resource ID '{record.resource_id}' is not a valid canonical ID",
            )

    def test_nested_property_paths_preserved(self) -> None:
        """DoD 2: Dotted property paths like properties.tags.Environment are preserved."""
        current = {
            "schema_version": "1.0.0",
            "snapshot_id": "snap-curr",
            "resources": [
                {
                    "id": "aws:instance:i-0123456789abcdef0",
                    "provider": "aws",
                    "type": "instance",
                    "properties": {
                        "instance_type": "t3.medium",
                        "tags": {
                            "Environment": "production",
                            "Team": "platform",
                        },
                    },
                }
            ],
            "relationships": [],
        }

        future = copy.deepcopy(current)
        future["resources"][0]["properties"]["tags"]["Environment"] = "staging"
        future["resources"][0]["properties"]["instance_type"] = "t3.large"

        diff = self.differ.diff(current, future)
        self.assertEqual(len(diff.changes), 2)

        paths = {c.property: (c.before, c.after) for c in diff.changes}
        self.assertIn("properties.tags.Environment", paths)
        self.assertEqual(paths["properties.tags.Environment"], ("production", "staging"))
        self.assertIn("properties.instance_type", paths)
        self.assertEqual(paths["properties.instance_type"], ("t3.medium", "t3.large"))

    def test_compute_diff_convenience_function(self) -> None:
        """compute_diff accepts file paths and dicts and returns StateDiff."""
        generated_future_path = ROOT / "outputs" / "future_state.json"
        if not generated_future_path.exists():
            # Generate it if not already present
            generator = FutureTwinGenerator()
            generator.generate(CURRENT_TWIN_FIXTURE, SCENARIO_FIXTURE, out_path=generated_future_path)

        diff = compute_diff(CURRENT_TWIN_FIXTURE, generated_future_path)
        self.assertIsInstance(diff, StateDiff)
        self.assertEqual(len(diff.changes), 1)
        self.assertEqual(diff.changes[0].resource_id, "k8s:deployment:default:ui")

    def test_cli_execution_and_schema_validation(self) -> None:
        """Test invoking differ.py via CLI produces valid output file."""
        generated_future_path = ROOT / "outputs" / "future_state.json"
        out_diff_path = ROOT / "outputs" / "test_state_diff.json"

        cmd = [
            sys.executable,
            str(ROOT / "diff" / "differ.py"),
            "--current",
            str(CURRENT_TWIN_FIXTURE),
            "--future",
            str(generated_future_path),
            "--out",
            str(out_diff_path),
            "--validate",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"CLI failed: {result.stderr}")
        self.assertTrue(out_diff_path.exists())

        saved_diff = json.loads(out_diff_path.read_text(encoding="utf-8"))
        errors = list(self.validator.iter_errors(saved_diff))
        self.assertEqual(errors, [])
        self.assertEqual(saved_diff["summary"]["total_changes"], 1)

        # Cleanup test output file
        if out_diff_path.exists():
            out_diff_path.unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)
