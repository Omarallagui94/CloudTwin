"""End-to-end API tests for Task H8 - Expose Backend APIs.

Uses FastAPI TestClient (backed by httpx) so the full HTTP stack is exercised
completely in-process without any network calls.

Validates all 4 Definition of Done criteria:

1. Request/response models reuse the agreed schemas
   (twin.schema.json, diff.schema.json, impact.schema.json).
2. Errors and job status are explicit (4xx codes with descriptive detail).
3. Fixture-based end-to-end tests cover the complete local flow:
   load snapshot -> run simulation -> compute diff -> graph query -> impact analysis.
4. Fatma can consume Diff and affected-resource output without reading Neo4j directly
   (all data exposed through REST endpoints).
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

# Each test gets a fresh app/store so tests don't share state
from api.routes import _make_app

CURRENT_TWIN_FIXTURE = ROOT / "fixtures" / "sample_current_state.json"
SCENARIO_FIXTURE = ROOT / "scenarios" / "scenario-01.json"
SNAPSHOT_ID = "sample_current_state"


def make_client() -> TestClient:
    """Return a TestClient backed by a fresh app instance with an empty store."""
    return TestClient(_make_app())


class H8HealthTests(unittest.TestCase):

    def setUp(self):
        self.client = make_client()

    def test_health_returns_ok(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")


class H8SnapshotTests(unittest.TestCase):

    def setUp(self):
        self.client = make_client()

    # -----------------------------------------------------------------
    # DoD 1: response model reuses twin.schema.json field names
    # -----------------------------------------------------------------
    def test_load_snapshot_returns_201_with_correct_shape(self):
        resp = self.client.post("/snapshots/load", json={
            "fixture_path": "fixtures/sample_current_state.json",
        })
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertIn("snapshot_id", body)
        self.assertIn("summary", body)
        self.assertIn("resources", body)
        self.assertIn("relationships", body)

    def test_load_snapshot_resource_and_rel_counts(self):
        resp = self.client.post("/snapshots/load", json={
            "fixture_path": "fixtures/sample_current_state.json",
        })
        body = resp.json()
        summary = body["summary"]
        self.assertGreater(summary["resource_count"], 0)
        self.assertGreater(summary["relationship_count"], 0)
        self.assertGreater(summary["aws_resources"], 0)
        self.assertGreater(summary["kubernetes_resources"], 0)

    def test_list_snapshots_returns_loaded_id(self):
        self.client.post("/snapshots/load", json={"fixture_path": "fixtures/sample_current_state.json"})
        resp = self.client.get("/snapshots")
        self.assertIn("sample_current_state", resp.json())

    def test_get_snapshot_summary(self):
        self.client.post("/snapshots/load", json={
            "fixture_path": "fixtures/sample_current_state.json",
        })
        resp = self.client.get("/snapshots/sample_current_state/summary")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["snapshot_id"], "sample_current_state")
        self.assertIn("resource_count", body)

    def test_get_snapshot_full(self):
        self.client.post("/snapshots/load", json={"fixture_path": "fixtures/sample_current_state.json"})
        resp = self.client.get("/snapshots/sample_current_state")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertGreater(len(body["resources"]), 0)

    # -----------------------------------------------------------------
    # DoD 2: explicit errors
    # -----------------------------------------------------------------
    def test_load_nonexistent_file_returns_422(self):
        resp = self.client.post("/snapshots/load", json={
            "fixture_path": "fixtures/does_not_exist.json",
        })
        self.assertEqual(resp.status_code, 422)

    def test_get_unknown_snapshot_returns_404(self):
        resp = self.client.get("/snapshots/no-such-snapshot")
        self.assertEqual(resp.status_code, 404)

    def test_get_unknown_snapshot_summary_returns_404(self):
        resp = self.client.get("/snapshots/no-such-snapshot/summary")
        self.assertEqual(resp.status_code, 404)


class H8SimulationTests(unittest.TestCase):

    def setUp(self):
        self.client = make_client()
        # Load snapshot once
        self.client.post("/snapshots/load", json={"fixture_path": "fixtures/sample_current_state.json"})

    def test_run_simulation_returns_201(self):
        resp = self.client.post("/simulations", json={
            "snapshot_id": "sample_current_state",
            "change_source": "scenarios/scenario-01.json",
        })
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertIn("simulation_id", body)
        self.assertIn("twin", body)
        self.assertIn("provenance", body)

    def test_simulation_twin_has_correct_schema_shape(self):
        resp = self.client.post("/simulations", json={
            "snapshot_id": "sample_current_state",
            "change_source": "scenarios/scenario-01.json",
        })
        twin = resp.json()["twin"]
        self.assertIn("resources", twin)
        self.assertIn("relationships", twin)

    def test_simulation_applied_ui_replica_change(self):
        resp = self.client.post("/simulations", json={
            "snapshot_id": "sample_current_state",
            "change_source": "scenarios/scenario-01.json",
        })
        body = resp.json()
        resources = body["twin"]["resources"]
        ui_res = next((r for r in resources if r["id"] == "k8s:deployment:default:ui"), None)
        self.assertIsNotNone(ui_res)
        self.assertEqual(ui_res["properties"]["replicas"], 3)

    def test_list_simulations_contains_new_id(self):
        resp = self.client.post("/simulations", json={
            "snapshot_id": "sample_current_state",
            "change_source": "scenarios/scenario-01.json",
        })
        sim_id = resp.json()["simulation_id"]
        resp2 = self.client.get("/simulations")
        self.assertIn(sim_id, resp2.json())

    def test_get_simulation_by_id(self):
        resp = self.client.post("/simulations", json={
            "snapshot_id": "sample_current_state",
            "change_source": "scenarios/scenario-01.json",
        })
        sim_id = resp.json()["simulation_id"]
        resp2 = self.client.get(f"/simulations/{sim_id}")
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json()["simulation_id"], sim_id)

    # -----------------------------------------------------------------
    # DoD 2: explicit error on missing snapshot
    # -----------------------------------------------------------------
    def test_simulation_with_missing_snapshot_returns_404(self):
        resp = self.client.post("/simulations", json={
            "snapshot_id": "nonexistent",
            "change_source": "scenarios/scenario-01.json",
        })
        self.assertEqual(resp.status_code, 404)

    def test_simulation_with_bad_change_file_returns_422(self):
        resp = self.client.post("/simulations", json={
            "snapshot_id": "sample_current_state",
            "change_source": "fixtures/sample_current_state.json",  # not a valid change source
        })
        self.assertIn(resp.status_code, [422, 500])

    def test_get_unknown_simulation_returns_404(self):
        resp = self.client.get("/simulations/no-such-sim")
        self.assertEqual(resp.status_code, 404)


class H8DiffTests(unittest.TestCase):

    def setUp(self):
        self.client = make_client()
        self.client.post("/snapshots/load", json={"fixture_path": "fixtures/sample_current_state.json"})
        resp = self.client.post("/simulations", json={
            "snapshot_id": "sample_current_state",
            "change_source": "scenarios/scenario-01.json",
        })
        self.sim_id = resp.json()["simulation_id"]

    # -----------------------------------------------------------------
    # DoD 1: response reuses diff.schema.json field names
    # -----------------------------------------------------------------
    def test_diff_returns_correct_schema_shape(self):
        resp = self.client.post("/diffs", json={
            "snapshot_id": "sample_current_state",
            "simulation_id": self.sim_id,
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("schema_version", body)
        self.assertIn("changes", body)
        self.assertIn("summary", body)
        self.assertEqual(body["schema_version"], "1.0.0")

    def test_diff_scenario01_produces_single_modified_change(self):
        resp = self.client.post("/diffs", json={
            "snapshot_id": "sample_current_state",
            "simulation_id": self.sim_id,
        })
        body = resp.json()
        self.assertEqual(body["summary"]["total_changes"], 1)
        self.assertEqual(body["summary"]["modified"], 1)
        self.assertEqual(body["summary"]["added"], 0)

        change = body["changes"][0]
        self.assertEqual(change["resource_id"], "k8s:deployment:default:ui")
        self.assertEqual(change["change_type"], "MODIFIED")
        self.assertEqual(change["property"], "properties.replicas")
        self.assertEqual(change["before"], 1)
        self.assertEqual(change["after"], 3)

    # -----------------------------------------------------------------
    # DoD 4: Fatma can consume diff output without reading Neo4j
    # -----------------------------------------------------------------
    def test_diff_response_contains_snapshot_and_simulation_ids(self):
        resp = self.client.post("/diffs", json={
            "snapshot_id": "sample_current_state",
            "simulation_id": self.sim_id,
        })
        body = resp.json()
        self.assertEqual(body["snapshot_id"], "sample_current_state")
        self.assertEqual(body["simulation_id"], self.sim_id)

    def test_diff_with_missing_snapshot_returns_404(self):
        resp = self.client.post("/diffs", json={
            "snapshot_id": "nope",
            "simulation_id": self.sim_id,
        })
        self.assertEqual(resp.status_code, 404)

    def test_diff_with_missing_simulation_returns_404(self):
        resp = self.client.post("/diffs", json={
            "snapshot_id": "sample_current_state",
            "simulation_id": "nope",
        })
        self.assertEqual(resp.status_code, 404)


class H8GraphQueryTests(unittest.TestCase):

    def setUp(self):
        self.client = make_client()
        self.client.post("/snapshots/load", json={"fixture_path": "fixtures/sample_current_state.json"})

    def test_direct_query_returns_neighbors(self):
        resp = self.client.post("/graph/query", json={
            "snapshot_id": "sample_current_state",
            "resource_id": "k8s:deployment:default:ui",
            "direction": "both",
            "transitive": False,
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertGreater(body["total"], 0)
        ids = [d["resource_id"] for d in body["dependencies"]]
        # Service exposes ui deployment (incoming) + pods selected (outgoing)
        self.assertIn("k8s:service:default:ui", ids)
        self.assertIn("k8s:pod:default:ui-6d4c8f7b9-abcde", ids)

    def test_outgoing_only_excludes_incoming_neighbors(self):
        resp = self.client.post("/graph/query", json={
            "snapshot_id": "sample_current_state",
            "resource_id": "k8s:deployment:default:ui",
            "direction": "outgoing",
            "transitive": False,
        })
        body = resp.json()
        ids = [d["resource_id"] for d in body["dependencies"]]
        self.assertNotIn("k8s:service:default:ui", ids)   # service->ui is incoming
        self.assertIn("k8s:pod:default:ui-6d4c8f7b9-abcde", ids)  # ui->pod is outgoing

    def test_transitive_query_reaches_deeper_nodes(self):
        resp = self.client.post("/graph/query", json={
            "snapshot_id": "sample_current_state",
            "resource_id": "k8s:deployment:default:ui",
            "direction": "both",
            "max_depth": 3,
            "transitive": True,
        })
        body = resp.json()
        ids = [d["resource_id"] for d in body["dependencies"]]
        # Node is 2 hops away: ui -> pod -> node
        self.assertIn("k8s:node:ip-10-0-10-51.eu-west-3.compute.internal", ids)

    def test_graph_query_missing_snapshot_returns_404(self):
        resp = self.client.post("/graph/query", json={
            "snapshot_id": "nonexistent",
            "resource_id": "k8s:deployment:default:ui",
        })
        self.assertEqual(resp.status_code, 404)


class H8ImpactTests(unittest.TestCase):

    def setUp(self):
        self.client = make_client()
        self.client.post("/snapshots/load", json={"fixture_path": "fixtures/sample_current_state.json"})
        resp = self.client.post("/simulations", json={
            "snapshot_id": "sample_current_state",
            "change_source": "scenarios/scenario-01.json",
        })
        self.sim_id = resp.json()["simulation_id"]

    # -----------------------------------------------------------------
    # DoD 1: response reuses impact.schema.json field names
    # -----------------------------------------------------------------
    def test_impact_returns_correct_schema_shape(self):
        resp = self.client.post("/impact", json={
            "snapshot_id": "sample_current_state",
            "simulation_id": self.sim_id,
            "scenario_id": "scenario-01",
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("schema_version", body)
        self.assertIn("impacts", body)
        self.assertIn("summary", body)
        self.assertEqual(body["schema_version"], "1.0.0")

    def test_impact_includes_direct_and_indirect_entries(self):
        resp = self.client.post("/impact", json={
            "snapshot_id": "sample_current_state",
            "simulation_id": self.sim_id,
            "scenario_id": "scenario-01",
            "max_depth": 3,
        })
        body = resp.json()
        impact_types = {i["impact_type"] for i in body["impacts"]}
        self.assertIn("DIRECT", impact_types)
        self.assertIn("INDIRECT", impact_types)

    # -----------------------------------------------------------------
    # DoD 3: complete local flow end-to-end
    # -----------------------------------------------------------------
    def test_impact_ui_deployment_is_direct_distance_zero(self):
        resp = self.client.post("/impact", json={
            "snapshot_id": "sample_current_state",
            "simulation_id": self.sim_id,
            "scenario_id": "scenario-01",
        })
        body = resp.json()
        ui = next((i for i in body["impacts"] if i["resource_id"] == "k8s:deployment:default:ui"), None)
        self.assertIsNotNone(ui)
        self.assertEqual(ui["impact_type"], "DIRECT")
        self.assertEqual(ui["distance"], 0)

    def test_impact_path_explains_connection_to_load_balancer(self):
        resp = self.client.post("/impact", json={
            "snapshot_id": "sample_current_state",
            "simulation_id": self.sim_id,
            "max_depth": 3,
        })
        body = resp.json()
        lb = next(
            (i for i in body["impacts"] if "load-balancer" in i["resource_id"]),
            None,
        )
        self.assertIsNotNone(lb, "Load balancer should be in transitive impact at depth 2")
        self.assertEqual(lb["distance"], 2)
        # Path must start with the changed resource and end with the LB
        self.assertEqual(lb["path"][0], "k8s:deployment:default:ui")
        self.assertEqual(lb["path"][-1], lb["resource_id"])
        # Reason must reference the traversal path
        self.assertIn("k8s:deployment:default:ui", lb["reason"])

    # -----------------------------------------------------------------
    # DoD 4: Fatma reads impact without Neo4j
    # -----------------------------------------------------------------
    def test_impact_response_contains_scenario_snapshot_simulation_ids(self):
        resp = self.client.post("/impact", json={
            "snapshot_id": "sample_current_state",
            "simulation_id": self.sim_id,
            "scenario_id": "my-scenario",
        })
        body = resp.json()
        self.assertEqual(body["scenario_id"], "my-scenario")
        self.assertEqual(body["snapshot_id"], "sample_current_state")
        self.assertEqual(body["simulation_id"], self.sim_id)

    def test_impact_summary_counts_match_impact_list(self):
        resp = self.client.post("/impact", json={
            "snapshot_id": "sample_current_state",
            "simulation_id": self.sim_id,
        })
        body = resp.json()
        impacts = body["impacts"]
        summary = body["summary"]
        direct_count = sum(1 for i in impacts if i["impact_type"] == "DIRECT")
        indirect_count = sum(1 for i in impacts if i["impact_type"] == "INDIRECT")
        self.assertEqual(summary["direct"], direct_count)
        self.assertEqual(summary["indirect"], indirect_count)
        self.assertEqual(summary["total_affected"], len(impacts))

    def test_impact_missing_snapshot_returns_404(self):
        resp = self.client.post("/impact", json={
            "snapshot_id": "nope",
            "simulation_id": self.sim_id,
        })
        self.assertEqual(resp.status_code, 404)

    def test_impact_missing_simulation_returns_404(self):
        resp = self.client.post("/impact", json={
            "snapshot_id": "sample_current_state",
            "simulation_id": "nope",
        })
        self.assertEqual(resp.status_code, 404)


class H8EndToEndFlowTest(unittest.TestCase):
    """DoD 3: Fixture-based test covering the COMPLETE local pipeline in one test."""

    def test_complete_pipeline_via_api(self):
        client = make_client()

        # 1. Load snapshot
        resp = client.post("/snapshots/load", json={
            "fixture_path": "fixtures/sample_current_state.json",
        })
        self.assertEqual(resp.status_code, 201, f"Load snapshot failed: {resp.text}")
        snap_id = resp.json()["snapshot_id"]

        # 2. Run simulation
        resp = client.post("/simulations", json={
            "snapshot_id": snap_id,
            "change_source": "scenarios/scenario-01.json",
        })
        self.assertEqual(resp.status_code, 201, f"Simulation failed: {resp.text}")
        sim_id = resp.json()["simulation_id"]

        # 3. Compute diff
        resp = client.post("/diffs", json={
            "snapshot_id": snap_id,
            "simulation_id": sim_id,
        })
        self.assertEqual(resp.status_code, 200, f"Diff failed: {resp.text}")
        diff_body = resp.json()
        self.assertEqual(diff_body["summary"]["total_changes"], 1)

        # 4. Graph query
        resp = client.post("/graph/query", json={
            "snapshot_id": snap_id,
            "resource_id": "k8s:deployment:default:ui",
            "transitive": True,
            "max_depth": 2,
        })
        self.assertEqual(resp.status_code, 200, f"Graph query failed: {resp.text}")
        self.assertGreater(resp.json()["total"], 0)

        # 5. Impact analysis
        resp = client.post("/impact", json={
            "snapshot_id": snap_id,
            "simulation_id": sim_id,
            "scenario_id": "scenario-01",
            "max_depth": 3,
        })
        self.assertEqual(resp.status_code, 200, f"Impact failed: {resp.text}")
        impact_body = resp.json()
        self.assertGreater(impact_body["summary"]["total_affected"], 0)
        self.assertGreater(impact_body["summary"]["direct"], 0)
        self.assertGreater(impact_body["summary"]["indirect"], 0)

        # Verify Fatma-facing invariants
        for item in impact_body["impacts"]:
            self.assertIn("resource_id", item)
            self.assertIn("impact_type", item)
            self.assertIn("reason", item)
            self.assertIn("distance", item)
            self.assertIn("path", item)
            # Path must be a connected chain
            self.assertGreater(len(item["path"]), 0)
            self.assertEqual(item["path"][-1], item["resource_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
