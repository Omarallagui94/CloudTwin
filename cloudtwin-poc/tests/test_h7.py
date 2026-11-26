"""Unit and integration tests for Task H7 - Implement affected-resource analysis.

Validates all 5 Definition of Done criteria:
1. Paths are deterministic.
2. Direction and relationship semantics are respected.
3. Direct impact is distinguished from inferred/transitive impact.
4. Cycles do not cause repeated or infinite traversal.
5. Results explain the path connecting each change to each affected resource.
Plus schema compliance against schema/v1/impact.schema.json.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jsonschema import Draft202012Validator

from diff import compute_diff
from future_twin import FutureTwinGenerator
from impact import (
    AffectedResource,
    ImpactAnalysisResult,
    ImpactAnalyzer,
    ImpactType,
    analyze_impact,
)
from ingestion import ChangeIngester

CURRENT_TWIN_FIXTURE = ROOT / "fixtures" / "sample_current_state.json"
SCENARIO_FIXTURE = ROOT / "scenarios" / "scenario-01.json"
IMPACT_SCHEMA_PATH = ROOT / "schema" / "v1" / "impact.schema.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class H7AffectedResourceAnalysisTests(unittest.TestCase):
    """Core test suite for affected-resource impact analysis."""

    def setUp(self) -> None:
        self.current_twin = load_json(CURRENT_TWIN_FIXTURE)
        self.analyzer = ImpactAnalyzer()
        self.schema = load_json(IMPACT_SCHEMA_PATH)
        self.validator = Draft202012Validator(self.schema)

        # Baseline diff for scenario-01 (ui replica change)
        self.scenario_diff = {
            "schema_version": "1.0.0",
            "changes": [
                {
                    "resource_id": "k8s:deployment:default:ui",
                    "change_type": "MODIFIED",
                    "property": "properties.replicas",
                    "before": 1,
                    "after": 3,
                }
            ],
        }

    def test_paths_are_deterministic(self) -> None:
        """DoD 1: Repeated runs produce identical paths, order, and explanations."""
        result_1 = self.analyzer.analyze(
            diff=self.scenario_diff,
            relationships=self.current_twin,
            scenario_id="scenario-01",
        )
        result_2 = self.analyzer.analyze(
            diff=self.scenario_diff,
            relationships=self.current_twin,
            scenario_id="scenario-01",
        )

        dict_1 = result_1.to_dict()
        dict_2 = result_2.to_dict()

        self.assertEqual(dict_1, dict_2)
        self.assertEqual(len(result_1.impacts), len(result_2.impacts))

        for imp1, imp2 in zip(result_1.impacts, result_2.impacts):
            self.assertEqual(imp1.resource_id, imp2.resource_id)
            self.assertEqual(imp1.distance, imp2.distance)
            self.assertEqual(imp1.path, imp2.path)
            self.assertEqual(imp1.reason, imp2.reason)

    def test_direction_and_relationship_semantics_respected(self) -> None:
        """DoD 2: Direction (outgoing, incoming, both) and relationship semantics are respected."""
        # 1. Outgoing only from ui deployment: should reach Pod (SELECTS) and ServiceAccount (USES)
        res_out = self.analyzer.analyze(
            diff=self.scenario_diff,
            relationships=self.current_twin,
            direction="outgoing",
            max_depth=1,
        )
        out_ids = {i.resource_id for i in res_out.impacts if i.distance == 1}
        self.assertIn("k8s:pod:default:ui-6d4c8f7b9-abcde", out_ids)
        self.assertIn("k8s:service-account:default:ui", out_ids)
        # Service exposes deployment (incoming to deployment), so should NOT be in outgoing
        self.assertNotIn("k8s:service:default:ui", out_ids)

        # 2. Incoming only to ui deployment: should reach Service (EXPOSES) and Namespace (CONTAINS)
        res_in = self.analyzer.analyze(
            diff=self.scenario_diff,
            relationships=self.current_twin,
            direction="incoming",
            max_depth=1,
        )
        in_ids = {i.resource_id for i in res_in.impacts if i.distance == 1}
        self.assertIn("k8s:service:default:ui", in_ids)
        self.assertIn("k8s:namespace:default", in_ids)
        # Pod is selected by deployment (outgoing), so should NOT be in incoming
        self.assertNotIn("k8s:pod:default:ui-6d4c8f7b9-abcde", in_ids)

        # Check semantic reason on service:
        service_imp = res_in.get_affected("k8s:service:default:ui")
        self.assertIsNotNone(service_imp)
        self.assertIn("EXPOSES", service_imp.reason)
        self.assertIn("service exposing target workload", service_imp.reason)

    def test_direct_distinguished_from_inferred_transitive_impact(self) -> None:
        """DoD 3: Direct impact (dist <= 1) is clearly distinguished from indirect (dist >= 2)."""
        result = self.analyzer.analyze(
            diff=self.scenario_diff,
            relationships=self.current_twin,
            max_depth=4,
            direction="both",
        )

        for imp in result.impacts:
            if imp.distance <= 1:
                self.assertEqual(
                    imp.impact_type,
                    ImpactType.DIRECT,
                    f"Resource {imp.resource_id} at dist {imp.distance} should be DIRECT",
                )
            else:
                self.assertEqual(
                    imp.impact_type,
                    ImpactType.INDIRECT,
                    f"Resource {imp.resource_id} at dist {imp.distance} should be INDIRECT",
                )

        # Summary counts must match
        summary = result.summary
        self.assertGreater(summary["direct"], 0)
        self.assertGreater(summary["indirect"], 0)
        self.assertEqual(summary["total_affected"], summary["direct"] + summary["indirect"] + summary["risk"])

    def test_cycles_do_not_cause_repeated_or_infinite_traversal(self) -> None:
        """DoD 4: Cyclic graphs do not loop infinitely or generate duplicate entries."""
        # Create a graph with cycles: A -> B -> C -> A, and self-loop D -> D
        cyclic_rels = [
            {"source": "k8s:service:default:a", "target": "k8s:deployment:default:b", "type": "EXPOSES"},
            {"source": "k8s:deployment:default:b", "target": "k8s:pod:default:c", "type": "SELECTS"},
            {"source": "k8s:pod:default:c", "target": "k8s:service:default:a", "type": "USES"},
            {"source": "k8s:pod:default:c", "target": "k8s:pod:default:c", "type": "USES"},
        ]

        cyclic_diff = {
            "schema_version": "1.0.0",
            "changes": [
                {
                    "resource_id": "k8s:service:default:a",
                    "change_type": "MODIFIED",
                    "property": "properties.ports",
                }
            ],
        }

        # Analyze with very large depth limit
        result = self.analyzer.analyze(
            diff=cyclic_diff,
            relationships=cyclic_rels,
            max_depth=50,
            direction="both",
        )

        # Each node must be visited exactly once
        resource_ids = [imp.resource_id for imp in result.impacts]
        self.assertEqual(len(resource_ids), len(set(resource_ids)), "Cyclic graph produced duplicate visits")
        self.assertIn("k8s:service:default:a", resource_ids)
        self.assertIn("k8s:deployment:default:b", resource_ids)
        self.assertIn("k8s:pod:default:c", resource_ids)

        # Distances must be minimal:
        # a is root (0)
        # b is dist 1 (from a -> b)
        # c is dist 1 (from c -> a incoming)
        a_imp = result.get_affected("k8s:service:default:a")
        self.assertEqual(a_imp.distance, 0)
        b_imp = result.get_affected("k8s:deployment:default:b")
        self.assertEqual(b_imp.distance, 1)

    def test_results_explain_path_connecting_change_to_affected(self) -> None:
        """DoD 5: Every affected resource result explains the full path from the change."""
        result = self.analyzer.analyze(
            diff=self.scenario_diff,
            relationships=self.current_twin,
            max_depth=3,
        )

        root_id = "k8s:deployment:default:ui"

        for imp in result.impacts:
            # 1. Path must start with root change
            self.assertEqual(imp.path[0], root_id)
            # 2. Path must end with this resource
            self.assertEqual(imp.path[-1], imp.resource_id)
            # 3. Path length must equal distance + 1
            self.assertEqual(len(imp.path), imp.distance + 1)
            # 4. Reason must be non-empty and describe the relationship/path
            self.assertTrue(len(imp.reason) > 10)
            if imp.distance > 0:
                self.assertIn(root_id, imp.reason)

    def test_impact_validates_against_impact_schema(self) -> None:
        """Output strictly validates against schema/v1/impact.schema.json."""
        result = self.analyzer.analyze(
            diff=self.scenario_diff,
            relationships=self.current_twin,
            scenario_id="scenario-01",
        )

        impact_dict = result.to_dict()
        errors = list(self.validator.iter_errors(impact_dict))
        self.assertEqual(errors, [], f"Schema validation errors: {errors}")

    def test_end_to_end_from_scenario_diff(self) -> None:
        """Full end-to-end integration: Ingest -> Future Twin -> State Diff -> Impact Analysis."""
        # 1. Ingest scenario-01
        ingester = ChangeIngester(current_twin=self.current_twin)
        changes = ingester.ingest(SCENARIO_FIXTURE)

        # 2. Generate future twin
        generator = FutureTwinGenerator()
        future = generator.generate(self.current_twin, changes)

        # 3. Compute diff
        diff = compute_diff(self.current_twin, future)

        # 4. Analyze structural impact
        impact_result = analyze_impact(
            diff=diff,
            relationships=self.current_twin,
            scenario_id="scenario-01",
            max_depth=3,
        )

        # UI scale-out should transitively affect Load Balancer and Nodes
        affected_ids = {imp.resource_id for imp in impact_result.impacts}
        self.assertIn("k8s:deployment:default:ui", affected_ids)
        self.assertIn("k8s:service:default:ui", affected_ids)
        self.assertIn("k8s:pod:default:ui-6d4c8f7b9-abcde", affected_ids)
        # Load balancer routes to service (distance 2)
        lb_id = "aws:load-balancer:arn:aws:elasticloadbalancing:eu-west-3:000000000000:loadbalancer/net/retail-store-nlb/0000000000000001"
        self.assertIn(lb_id, affected_ids)
        lb_imp = impact_result.get_affected(lb_id)
        self.assertEqual(lb_imp.distance, 2)
        self.assertEqual(lb_imp.impact_type, ImpactType.INDIRECT)

        # Pod runs on node (distance 2)
        node_id = "k8s:node:ip-10-0-10-51.eu-west-3.compute.internal"
        self.assertIn(node_id, affected_ids)
        node_imp = impact_result.get_affected(node_id)
        self.assertEqual(node_imp.distance, 2)
        self.assertEqual(node_imp.impact_type, ImpactType.INDIRECT)

    def test_cli_execution_and_schema_validation(self) -> None:
        """CLI invocation executes cleanly and outputs valid schema."""
        diff_path = ROOT / "outputs" / "state_diff.json"
        out_impact_path = ROOT / "outputs" / "test_impact.json"

        cmd = [
            sys.executable,
            str(ROOT / "impact" / "analyzer.py"),
            "--diff",
            str(diff_path),
            "--relationships",
            str(CURRENT_TWIN_FIXTURE),
            "--scenario-id",
            "scenario-01",
            "--max-depth",
            "3",
            "--out",
            str(out_impact_path),
            "--validate",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"CLI execution failed: {result.stderr}")
        self.assertTrue(out_impact_path.exists())

        saved_data = json.loads(out_impact_path.read_text(encoding="utf-8"))
        errors = list(self.validator.iter_errors(saved_data))
        self.assertEqual(errors, [])

        if out_impact_path.exists():
            out_impact_path.unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)
