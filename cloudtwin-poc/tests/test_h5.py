"""Unit and integration tests for Task H5 - Implement Future Twin generation.

Validates:
1. Current Twin remains immutable.
2. Create/update/delete/replace operations are supported.
3. The replica scenario changes only the intended deployment property.
4. Invalid mutations fail clearly.
5. Repeated runs produce identical output (determinism).
6. Future Twin output validates against twin.schema.json.
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jsonschema import Draft202012Validator

from future_twin import (
    DuplicateResourceError,
    FutureTwin,
    FutureTwinGenerator,
    InvalidMutationError,
    TargetNotFoundError,
)
from ingestion import ChangeIngester, NormalizedChange, OperationType
CURRENT_TWIN_FIXTURE = ROOT / "fixtures" / "sample_current_state.json"
SCENARIO_FIXTURE = ROOT / "scenarios" / "scenario-01.json"
SCHEMA_PATH = ROOT / "schema" / "v1" / "twin.schema.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class H5FutureTwinGenerationTests(unittest.TestCase):
    """Core tests for Future Twin generation engine."""

    def setUp(self) -> None:
        self.current_twin = load_json(CURRENT_TWIN_FIXTURE)
        self.generator = FutureTwinGenerator()
        self.ingester = ChangeIngester(current_twin=self.current_twin)

    def test_current_twin_remains_immutable(self) -> None:
        twin_before = copy.deepcopy(self.current_twin)

        # Generate future twin with scenario
        future = self.generator.generate(
            current_twin=self.current_twin,
            changes=SCENARIO_FIXTURE,
        )

        # 1. Assert input dictionary was not mutated
        self.assertEqual(self.current_twin, twin_before)

        # 2. Assert mutating future twin does not affect current twin
        future.twin["resources"][0]["name"] = "mutated-name"
        self.assertEqual(self.current_twin, twin_before)

    def test_replica_scenario_changes_only_intended_property(self) -> None:
        # Ingest scenario-01 (Increase UI replicas from 1 to 3)
        changes = self.ingester.ingest(SCENARIO_FIXTURE)
        future = self.generator.generate(self.current_twin, changes)

        # 1. Verify intended property is updated
        ui_res = future.get_resource("k8s:deployment:default:ui")
        self.assertIsNotNone(ui_res)
        self.assertEqual(ui_res["properties"]["replicas"], 3)

        # 2. Verify all other properties on UI deployment are unchanged
        orig_ui = next(r for r in self.current_twin["resources"] if r["id"] == "k8s:deployment:default:ui")
        for k, v in orig_ui["properties"].items():
            if k != "replicas":
                self.assertEqual(ui_res["properties"][k], v)

        # 3. Verify all other 39 resources remain completely unchanged
        current_other_resources = {
            r["id"]: r for r in self.current_twin["resources"] if r["id"] != "k8s:deployment:default:ui"
        }
        future_other_resources = {
            r["id"]: r for r in future.twin["resources"] if r["id"] != "k8s:deployment:default:ui"
        }
        self.assertEqual(future_other_resources, current_other_resources)

        # 4. Verify relationships remain identical
        current_rels = sorted(self.current_twin["relationships"], key=lambda x: (x["source"], x["target"], x["type"]))
        future_rels = sorted(future.twin["relationships"], key=lambda x: (x["source"], x["target"], x["type"]))
        self.assertEqual(future_rels, current_rels)

        # 5. Verify counts remain 40 resources and 81 relationships
        self.assertEqual(future.twin["summary"]["aws_resources"], 15)
        self.assertEqual(future.twin["summary"]["kubernetes_resources"], 25)
        self.assertEqual(future.twin["summary"]["relationships"], 81)

        # 6. Verify provenance
        prov = future.get_provenance("k8s:deployment:default:ui")
        self.assertEqual(len(prov), 1)
        self.assertEqual(prov[0]["change_id"], "change-s01-001")
        self.assertEqual(prov[0]["operation"], "update")
        self.assertEqual(prov[0]["after"], 3)

    def test_create_operation_adds_resource_and_updates_summary(self) -> None:
        create_change = NormalizedChange(
            change_id="change-create-001",
            operation=OperationType.CREATE,
            resource_id="k8s:service:default:telemetry",
            resource_type="SERVICE",
            provider="kubernetes",
            property_path="properties",
            before=None,
            after={"type": "ClusterIP", "port": 9090},
            source={"kind": "test"},
            namespace="default",
        )

        future = self.generator.generate(self.current_twin, [create_change])

        # Verify resource exists in Future Twin
        new_res = future.get_resource("k8s:service:default:telemetry")
        self.assertIsNotNone(new_res)
        self.assertEqual(new_res["type"], "SERVICE")
        self.assertEqual(new_res["provider"], "kubernetes")
        self.assertEqual(new_res["properties"]["port"], 9090)

        # Verify summary reflects new Kubernetes resource
        self.assertEqual(future.twin["summary"]["kubernetes_resources"], 26)
        self.assertEqual(future.twin["summary"]["aws_resources"], 15)
        self.assertEqual(len(future.twin["resources"]), 41)

        # Verify provenance
        prov = future.get_provenance("k8s:service:default:telemetry")
        self.assertEqual(prov[0]["operation"], "create")

    def test_delete_operation_removes_resource_and_dangling_relationships(self) -> None:
        target_sg = "aws:security-group:sg-0aaa1111cccc0002b"
        delete_change = NormalizedChange(
            change_id="change-del-001",
            operation=OperationType.DELETE,
            resource_id=target_sg,
            resource_type="SECURITY_GROUP",
            provider="aws",
            property_path="properties",
            before={"name": "retail-store-node-sg"},
            after=None,
            source={"kind": "test"},
        )

        future = self.generator.generate(self.current_twin, [delete_change])

        # 1. Resource is gone
        self.assertIsNone(future.get_resource(target_sg))
        self.assertEqual(future.twin["summary"]["aws_resources"], 14)
        self.assertEqual(len(future.twin["resources"]), 39)

        # 2. No dangling relationships referencing target_sg
        for rel in future.twin["relationships"]:
            self.assertNotEqual(rel["source"], target_sg)
            self.assertNotEqual(rel["target"], target_sg)

        # 3. Provenance records deletion
        self.assertTrue(len(future.get_provenance(target_sg)) > 0)
        self.assertEqual(future.get_provenance(target_sg)[0]["operation"], "delete")

    def test_replace_operation_updates_properties_and_retains_provenance(self) -> None:
        target_ec2 = "aws:ec2:i-0aaa1111dddd0001a"
        replace_change = NormalizedChange(
            change_id="change-replace-001",
            operation=OperationType.REPLACE,
            resource_id=target_ec2,
            resource_type="EC2_INSTANCE",
            provider="aws",
            property_path="properties",
            before={"instance_type": "t3.medium"},
            after={"instance_type": "t3.small", "state": "running"},
            source={"kind": "test"},
        )

        future = self.generator.generate(self.current_twin, [replace_change])

        res = future.get_resource(target_ec2)
        self.assertIsNotNone(res)
        self.assertEqual(res["properties"]["instance_type"], "t3.small")

        prov = future.get_provenance(target_ec2)
        self.assertEqual(prov[0]["operation"], "replace")

    def test_invalid_mutations_fail_clearly(self) -> None:
        # 1. Update on non-existent resource
        bad_update = NormalizedChange(
            change_id="bad-01",
            operation=OperationType.UPDATE,
            resource_id="k8s:deployment:default:phantom",
            resource_type="DEPLOYMENT",
            provider="kubernetes",
            property_path="properties.replicas",
            before=1,
            after=5,
            source={"kind": "test"},
        )
        with self.assertRaises(TargetNotFoundError) as ctx:
            self.generator.generate(self.current_twin, [bad_update])
        self.assertIn("k8s:deployment:default:phantom", str(ctx.exception))

        # 2. Delete on non-existent resource
        bad_delete = NormalizedChange(
            change_id="bad-02",
            operation=OperationType.DELETE,
            resource_id="aws:vpc:vpc-phantom",
            resource_type="VPC",
            provider="aws",
            property_path="properties",
            before=None,
            after=None,
            source={"kind": "test"},
        )
        with self.assertRaises(TargetNotFoundError) as ctx:
            self.generator.generate(self.current_twin, [bad_delete])
        self.assertIn("aws:vpc:vpc-phantom", str(ctx.exception))

        # 3. Create on existing resource
        bad_create = NormalizedChange(
            change_id="bad-03",
            operation=OperationType.CREATE,
            resource_id="k8s:deployment:default:ui",
            resource_type="DEPLOYMENT",
            provider="kubernetes",
            property_path="properties",
            before=None,
            after={},
            source={"kind": "test"},
        )
        with self.assertRaises(DuplicateResourceError) as ctx:
            self.generator.generate(self.current_twin, [bad_create])
        self.assertIn("k8s:deployment:default:ui", str(ctx.exception))

    def test_repeated_runs_produce_identical_output(self) -> None:
        changes = self.ingester.ingest(SCENARIO_FIXTURE)

        run_1 = self.generator.generate(self.current_twin, changes, simulation_id="sim-fixed")
        run_2 = self.generator.generate(self.current_twin, changes, simulation_id="sim-fixed")

        # Serialized JSON representations must be byte-for-byte identical
        json_1 = json.dumps(run_1.to_dict(strict_schema=True), sort_keys=True)
        json_2 = json.dumps(run_2.to_dict(strict_schema=True), sort_keys=True)
        self.assertEqual(json_1, json_2)

        # Provenance must be identical
        self.assertEqual(run_1.provenance, run_2.provenance)

    def test_future_twin_validates_against_twin_schema(self) -> None:
        schema = load_json(SCHEMA_PATH)
        validator = Draft202012Validator(schema)

        future = self.generator.generate(
            current_twin=self.current_twin,
            changes=SCENARIO_FIXTURE,
        )

        future_dict = future.to_dict(strict_schema=True)
        errors = list(validator.iter_errors(future_dict))
        self.assertEqual(errors, [], f"Schema validation errors: {[e.message for e in errors]}")


if __name__ == "__main__":
    unittest.main()
