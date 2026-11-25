"""Unit and integration tests for Task H4 - Proposed-change ingestion.

Validates:
1. Namespace, provider, resource ID, operation, and property path are unambiguous.
2. Create, update, delete, and replacement operations are represented.
3. Invalid or unresolved targets produce explicit errors.
4. Omar's saved scenario and plan fixtures parse deterministically.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from ingestion import (
    CanonicalResourceResolver,
    ChangeIngester,
    ConflictError,
    InvalidChangeInputError,
    NormalizedChange,
    OperationType,
    UnresolvedTargetError,
)

ROOT = Path(__file__).resolve().parents[1]
TWIN_FIXTURE = ROOT / "fixtures" / "sample_current_state.json"
SCENARIO_FIXTURE = ROOT / "scenarios" / "scenario-01.json"
TF_PLAN_FIXTURE = ROOT / "fixtures" / "sample_terraform_plan.json"
K8S_MANIFEST_FIXTURE = ROOT / "fixtures" / "sample_kubernetes_manifest.yaml"


class H4ScenarioIngestionTests(unittest.TestCase):
    """Tests for ingesting scenario JSON files."""

    def setUp(self) -> None:
        self.ingester = ChangeIngester(current_twin=TWIN_FIXTURE)

    def test_saved_scenario_01_parses_deterministically(self) -> None:
        change_set = self.ingester.ingest_scenario(SCENARIO_FIXTURE)

        self.assertEqual(len(change_set), 1)
        change = change_set.changes[0]

        # Verify unambiguous attributes
        self.assertEqual(change.change_id, "change-s01-001")
        self.assertEqual(change.operation, OperationType.UPDATE)
        self.assertEqual(change.resource_id, "k8s:deployment:default:ui")
        self.assertEqual(change.resource_type, "DEPLOYMENT")
        self.assertEqual(change.provider, "kubernetes")
        self.assertEqual(change.namespace, "default")
        self.assertEqual(change.property_path, "properties.replicas")
        self.assertEqual(change.before, 1)
        self.assertEqual(change.after, 3)

        # Verify provenance
        self.assertEqual(change.source["kind"], "scenario")
        self.assertEqual(change.source["reference"], "S01")
        self.assertEqual(change.source["name"], "Increase UI replicas")

        # Verify integration contract dictionary serialization
        change_dict = change.to_dict()
        self.assertEqual(change_dict["operation"], "update")
        self.assertEqual(change_dict["resource_id"], "k8s:deployment:default:ui")
        self.assertEqual(change_dict["property_path"], "properties.replicas")
        self.assertEqual(change_dict["before"], 1)
        self.assertEqual(change_dict["after"], 3)

    def test_unresolved_scenario_target_raises_explicit_error(self) -> None:
        bad_scenario = {
            "schema_version": "1.0.0",
            "scenario_id": "S99",
            "name": "Target non-existent service",
            "resource_type": "Deployment",
            "resource": "non-existent-deployment",
            "change": {
                "property": "replicas",
                "before": 1,
                "after": 5,
            },
        }

        with self.assertRaises(UnresolvedTargetError) as ctx:
            self.ingester.ingest_scenario(bad_scenario)

        self.assertIn("non-existent-deployment", str(ctx.exception))

    def test_invalid_scenario_structure_raises_explicit_error(self) -> None:
        missing_type = {
            "scenario_id": "S02",
            "resource": "ui",
            "change": {"property": "replicas", "after": 2},
        }
        with self.assertRaises(InvalidChangeInputError):
            self.ingester.ingest_scenario(missing_type)

    def test_strict_conflict_check_detects_mismatch(self) -> None:
        conflict_scenario = {
            "scenario_id": "S03",
            "resource_type": "Deployment",
            "resource": "ui",
            "change": {
                "property": "replicas",
                "before": 99,  # Current Twin actually has replicas: 1
                "after": 5,
            },
        }
        with self.assertRaises(ConflictError):
            self.ingester.ingest_scenario(conflict_scenario, strict_conflict_check=True)


class H4TerraformPlanIngestionTests(unittest.TestCase):
    """Tests for ingesting Terraform plan JSON fixtures."""

    def setUp(self) -> None:
        self.ingester = ChangeIngester(current_twin=TWIN_FIXTURE)

    def test_saved_terraform_plan_parses_deterministically(self) -> None:
        change_set = self.ingester.ingest_terraform_plan(TF_PLAN_FIXTURE)

        self.assertEqual(change_set.source_kind, "terraform_plan")
        self.assertEqual(len(change_set), 4)

        ops = [c.operation for c in change_set.changes]
        self.assertIn(OperationType.UPDATE, ops)
        self.assertIn(OperationType.CREATE, ops)
        self.assertIn(OperationType.DELETE, ops)
        self.assertIn(OperationType.REPLACE, ops)

        # 1. Verify Subnet update
        update_change = next(c for c in change_set.changes if c.operation == OperationType.UPDATE)
        self.assertEqual(update_change.resource_id, "aws:subnet:subnet-0aaa1111aaaa0001a")
        self.assertEqual(update_change.resource_type, "SUBNET")
        self.assertEqual(update_change.provider, "aws")
        self.assertEqual(update_change.property_path, "properties.cidr_block")
        self.assertEqual(update_change.before, "10.0.1.0/24")
        self.assertEqual(update_change.after, "10.0.10.0/24")

        # 2. Verify Security Group create
        create_change = next(c for c in change_set.changes if c.operation == OperationType.CREATE)
        self.assertEqual(create_change.resource_id, "aws:security-group:retail-store-allow-telemetry")
        self.assertEqual(create_change.resource_type, "SECURITY_GROUP")
        self.assertEqual(create_change.provider, "aws")
        self.assertIsNone(create_change.before)
        self.assertIsNotNone(create_change.after)

        # 3. Verify Security Group delete
        delete_change = next(c for c in change_set.changes if c.operation == OperationType.DELETE)
        self.assertEqual(delete_change.resource_id, "aws:security-group:sg-0aaa1111cccc0002b")
        self.assertEqual(delete_change.resource_type, "SECURITY_GROUP")
        self.assertEqual(delete_change.provider, "aws")
        self.assertIsNotNone(delete_change.before)
        self.assertIsNone(delete_change.after)

        # 4. Verify EC2 Instance replace
        replace_change = next(c for c in change_set.changes if c.operation == OperationType.REPLACE)
        self.assertEqual(replace_change.resource_id, "aws:ec2:i-0aaa1111dddd0001a")
        self.assertEqual(replace_change.resource_type, "EC2_INSTANCE")
        self.assertEqual(replace_change.provider, "aws")
        self.assertIsNotNone(replace_change.before)
        self.assertIsNotNone(replace_change.after)

    def test_unresolved_terraform_target_raises_explicit_error(self) -> None:
        unresolved_plan = {
            "format_version": "1.2",
            "resource_changes": [
                {
                    "address": "aws_subnet.phantom",
                    "mode": "managed",
                    "type": "aws_subnet",
                    "name": "phantom",
                    "change": {
                        "actions": ["update"],
                        "before": {
                            "id": "subnet-nonexistent-9999",
                        },
                        "after": {
                            "id": "subnet-nonexistent-9999",
                            "cidr_block": "10.99.0.0/16",
                        },
                    },
                }
            ],
        }
        with self.assertRaises(UnresolvedTargetError) as ctx:
            self.ingester.ingest_terraform_plan(unresolved_plan)

        self.assertIn("aws_subnet.phantom", str(ctx.exception))

    def test_invalid_terraform_plan_structure_raises_error(self) -> None:
        with self.assertRaises(InvalidChangeInputError):
            self.ingester.ingest_terraform_plan({"invalid": "structure"})


class H4KubernetesManifestIngestionTests(unittest.TestCase):
    """Tests for ingesting Kubernetes manifest fixtures."""

    def setUp(self) -> None:
        self.ingester = ChangeIngester(current_twin=TWIN_FIXTURE)

    def test_saved_kubernetes_manifest_parses_deterministically(self) -> None:
        change_set = self.ingester.ingest_kubernetes_manifest(K8S_MANIFEST_FIXTURE)

        self.assertEqual(change_set.source_kind, "kubernetes_manifest")
        self.assertEqual(len(change_set), 3)

        # 1. Update on ui deployment
        ui_change = next(c for c in change_set.changes if c.resource_id == "k8s:deployment:default:ui")
        self.assertEqual(ui_change.operation, OperationType.UPDATE)
        self.assertEqual(ui_change.property_path, "properties.replicas")
        self.assertEqual(ui_change.before, 1)
        self.assertEqual(ui_change.after, 3)
        self.assertEqual(ui_change.namespace, "default")
        self.assertEqual(ui_change.provider, "kubernetes")

        # 2. Create on telemetry service
        svc_change = next(c for c in change_set.changes if "telemetry" in c.resource_id)
        self.assertEqual(svc_change.operation, OperationType.CREATE)
        self.assertEqual(svc_change.resource_id, "k8s:service:default:telemetry")
        self.assertEqual(svc_change.resource_type, "SERVICE")
        self.assertEqual(svc_change.provider, "kubernetes")
        self.assertIsNone(svc_change.before)

        # 3. Delete on legacy network policy
        del_change = next(c for c in change_set.changes if c.operation == OperationType.DELETE)
        self.assertEqual(del_change.resource_id, "k8s:network-policy:default:legacy-policy")
        self.assertEqual(del_change.resource_type, "NETWORK_POLICY")
        self.assertIsNone(del_change.after)

    def test_invalid_kubernetes_manifest_raises_error(self) -> None:
        bad_manifest = "apiVersion: v1\n# missing kind and name"
        with self.assertRaises(InvalidChangeInputError):
            self.ingester.ingest_kubernetes_manifest(bad_manifest)


class H4UnifiedIngestionTests(unittest.TestCase):
    """Tests for unified auto-detect ingest interface."""

    def setUp(self) -> None:
        self.ingester = ChangeIngester(current_twin=TWIN_FIXTURE)

    def test_auto_detect_scenario(self) -> None:
        change_set = self.ingester.ingest(SCENARIO_FIXTURE)
        self.assertEqual(change_set.source_kind, "scenario")
        self.assertEqual(len(change_set), 1)

    def test_auto_detect_terraform_plan(self) -> None:
        change_set = self.ingester.ingest(TF_PLAN_FIXTURE)
        self.assertEqual(change_set.source_kind, "terraform_plan")
        self.assertEqual(len(change_set), 4)

    def test_auto_detect_kubernetes_manifest(self) -> None:
        change_set = self.ingester.ingest(K8S_MANIFEST_FIXTURE)
        self.assertEqual(change_set.source_kind, "kubernetes_manifest")
        self.assertEqual(len(change_set), 3)

    def test_all_four_operations_represented(self) -> None:
        for op in [OperationType.CREATE, OperationType.UPDATE, OperationType.DELETE, OperationType.REPLACE]:
            change = NormalizedChange(
                change_id=f"test-{op.value}",
                operation=op,
                resource_id="aws:vpc:vpc-0aaa1111bbbb2222c",
                resource_type="VPC",
                provider="aws",
                property_path="properties.cidr",
                before=None if op == OperationType.CREATE else "10.0.0.0/16",
                after=None if op == OperationType.DELETE else "10.0.0.0/16",
                source={"kind": "test"},
                namespace=None,
            )
            data = change.to_dict()
            self.assertEqual(data["operation"], op.value)
            self.assertIn("resource_id", data)
            self.assertIn("provider", data)
            self.assertIn("property_path", data)


if __name__ == "__main__":
    unittest.main()

