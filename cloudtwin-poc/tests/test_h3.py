"""Unit and integration tests for Task H3 - Persist the Current Twin in Neo4j.

Validates:
1. Re-importing the same snapshot creates no duplicates (idempotency).
2. Stale resources can be removed or associated with an older snapshot.
3. Queries can retrieve a resource's direct and transitive dependencies.
4. Import counts match the input summary and fixture contents.
5. Import works locally using the sample_current_state.json fixture.
6. Cypher queries and parameters generated for real Neo4j driver sessions.
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call

from storage.neo4j_store import (
    InMemoryGraphBackend,
    Neo4jTwinStore,
    RealNeo4jBackend,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "sample_current_state.json"


def load_fixture() -> dict:
    if not FIXTURE.is_file():
        raise FileNotFoundError(f"Fixture not found at {FIXTURE}")
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class H3FixtureImportTests(unittest.TestCase):
    """Tests loading and importing the local Current Twin fixture."""

    def setUp(self) -> None:
        self.store = Neo4jTwinStore(use_in_memory=True)
        self.fixture_data = load_fixture()

    def tearDown(self) -> None:
        self.store.close()

    def test_import_works_locally_using_fixture_file(self) -> None:
        result = self.store.import_snapshot(FIXTURE, validate=True)

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["resources_imported"], 40)
        self.assertEqual(result["relationships_imported"], 81)
        self.assertEqual(result["aws_resources"], 15)
        self.assertEqual(result["kubernetes_resources"], 25)

        summary = self.store.get_snapshot_summary(result["snapshot_id"])
        self.assertIsNotNone(summary)
        self.assertEqual(summary["resource_count"], 40)
        self.assertEqual(summary["relationship_count"], 81)
        self.assertEqual(summary["aws_resources"], 15)
        self.assertEqual(summary["kubernetes_resources"], 25)

    def test_import_counts_match_input_summary(self) -> None:
        expected_summary = self.fixture_data["summary"]
        result = self.store.import_snapshot(self.fixture_data)

        self.assertEqual(result["resources_imported"], len(self.fixture_data["resources"]))
        self.assertEqual(result["relationships_imported"], len(self.fixture_data["relationships"]))
        self.assertEqual(result["aws_resources"], expected_summary["aws_resources"])
        self.assertEqual(result["kubernetes_resources"], expected_summary["kubernetes_resources"])


class H3IdempotencyTests(unittest.TestCase):
    """Tests ensuring re-importing the same snapshot creates no duplicates."""

    def setUp(self) -> None:
        self.store = Neo4jTwinStore(use_in_memory=True)
        self.fixture_data = load_fixture()

    def tearDown(self) -> None:
        self.store.close()

    def test_reimporting_same_snapshot_id_creates_no_duplicates(self) -> None:
        snap_id = "test-snap-01"

        first_run = self.store.import_snapshot(self.fixture_data, snapshot_id=snap_id)
        self.assertEqual(first_run["resources_imported"], 40)
        self.assertEqual(first_run["relationships_imported"], 81)

        # Re-import identical snapshot under the same ID
        second_run = self.store.import_snapshot(self.fixture_data, snapshot_id=snap_id)
        self.assertEqual(second_run["resources_imported"], 40)
        self.assertEqual(second_run["relationships_imported"], 81)

        summary = self.store.get_snapshot_summary(snap_id)
        self.assertEqual(summary["resource_count"], 40)
        self.assertEqual(summary["relationship_count"], 81)
        self.assertEqual(len(self.store._backend.resources), 40)
        self.assertEqual(len(self.store._backend.relationships), 81)

    def test_reimporting_same_resources_under_new_snapshot_shares_nodes(self) -> None:
        # First snapshot
        res_a = self.store.import_snapshot(self.fixture_data, snapshot_id="snap-A")
        self.assertEqual(len(self.store._backend.resources), 40)

        # Second snapshot with same resources
        res_b = self.store.import_snapshot(self.fixture_data, snapshot_id="snap-B")
        self.assertEqual(len(self.store._backend.resources), 40)
        self.assertEqual(len(self.store.list_snapshots()), 2)


class H3StaleResourceTests(unittest.TestCase):
    """Tests stale resource association with older snapshots and stale removal."""

    def setUp(self) -> None:
        self.store = Neo4jTwinStore(use_in_memory=True)
        self.full_twin = load_fixture()

        # Create a modified twin with 2 resources removed
        self.reduced_twin = copy.deepcopy(self.full_twin)
        removed_ids = {
            "k8s:pod:default:ui-6d4c8f7b9-abcde",
            "k8s:pod:default:catalog-5f8d9a6c7-fghij",
        }
        self.reduced_twin["resources"] = [
            r for r in self.reduced_twin["resources"] if r["id"] not in removed_ids
        ]
        self.reduced_twin["relationships"] = [
            rel for rel in self.reduced_twin["relationships"]
            if rel["source"] not in removed_ids and rel["target"] not in removed_ids
        ]
        self.reduced_twin["summary"]["kubernetes_resources"] -= 2
        self.reduced_twin["summary"]["relationships"] = len(self.reduced_twin["relationships"])

    def tearDown(self) -> None:
        self.store.close()

    def test_stale_resources_associated_with_older_snapshot(self) -> None:
        # 1. Import full snapshot 1 (40 resources)
        self.store.import_snapshot(self.full_twin, snapshot_id="snap-1")
        summary_1 = self.store.get_snapshot_summary("snap-1")
        self.assertEqual(summary_1["resource_count"], 40)

        # 2. Import reduced snapshot 2 (38 resources), keeping stale resources
        result_2 = self.store.import_snapshot(
            self.reduced_twin,
            snapshot_id="snap-2",
            remove_stale=False,
        )

        self.assertEqual(result_2["resources_imported"], 38)
        self.assertEqual(result_2["stale_resources_count"], 2)

        # Verify old snapshot still associates with all 40 resources
        summary_1_after = self.store.get_snapshot_summary("snap-1")
        self.assertEqual(summary_1_after["resource_count"], 40)

        # Verify new snapshot associates with only 38 resources
        summary_2 = self.store.get_snapshot_summary("snap-2")
        self.assertEqual(summary_2["resource_count"], 38)

        # Verify stale resources list
        stale = self.store.get_stale_resources("snap-2")
        stale_ids = {r["id"] for r in stale}
        self.assertEqual(
            stale_ids,
            {"k8s:pod:default:ui-6d4c8f7b9-abcde", "k8s:pod:default:catalog-5f8d9a6c7-fghij"},
        )
        for r in stale:
            self.assertTrue(r["is_stale"])

    def test_stale_resources_can_be_removed(self) -> None:
        # Import full snapshot 1
        self.store.import_snapshot(self.full_twin, snapshot_id="snap-1")
        self.assertEqual(len(self.store._backend.resources), 40)

        # Import reduced snapshot 2 with remove_stale=True
        result_2 = self.store.import_snapshot(
            self.reduced_twin,
            snapshot_id="snap-2",
            remove_stale=True,
        )

        self.assertEqual(result_2["stale_resources_removed"], 2)
        self.assertEqual(len(self.store._backend.resources), 38)

        # Verify stale resources are gone
        self.assertIsNone(self.store.get_resource("k8s:pod:default:ui-6d4c8f7b9-abcde"))
        self.assertIsNone(self.store.get_resource("k8s:pod:default:catalog-5f8d9a6c7-fghij"))

        # Verify remaining stale list is now empty
        self.assertEqual(self.store.get_stale_resources("snap-2"), [])


class H3DependencyQueryTests(unittest.TestCase):
    """Tests querying direct and transitive dependencies."""

    def setUp(self) -> None:
        self.store = Neo4jTwinStore(use_in_memory=True)
        self.fixture_data = load_fixture()
        self.store.import_snapshot(self.fixture_data, snapshot_id="snap-test")

    def tearDown(self) -> None:
        self.store.close()

    def test_direct_outgoing_dependencies(self) -> None:
        # k8s:service:default:ui connects out to pod (SELECTS) and deployment (EXPOSES)
        deps = self.store.get_direct_dependencies("k8s:service:default:ui", direction="outgoing")
        targets = {d["resource_id"]: d["relationship_type"] for d in deps}

        self.assertIn("k8s:pod:default:ui-6d4c8f7b9-abcde", targets)
        self.assertEqual(targets["k8s:pod:default:ui-6d4c8f7b9-abcde"], "SELECTS")

        self.assertIn("k8s:deployment:default:ui", targets)
        self.assertEqual(targets["k8s:deployment:default:ui"], "EXPOSES")

    def test_direct_incoming_dependencies(self) -> None:
        # NLB routes to k8s:service:default:ui; namespace default contains it
        deps = self.store.get_direct_dependencies("k8s:service:default:ui", direction="incoming")
        sources = {d["resource_id"]: d["relationship_type"] for d in deps}

        self.assertIn("k8s:namespace:default", sources)
        self.assertEqual(sources["k8s:namespace:default"], "CONTAINS")

        nlb_id = "aws:load-balancer:arn:aws:elasticloadbalancing:eu-west-3:000000000000:loadbalancer/net/retail-store-nlb/0000000000000001"
        self.assertIn(nlb_id, sources)
        self.assertEqual(sources[nlb_id], "ROUTES_TO")

    def test_direct_both_directions(self) -> None:
        deps = self.store.get_direct_dependencies("k8s:service:default:ui", direction="both")
        self.assertEqual(len(deps), 4)
        directions = {d["direction"] for d in deps}
        self.assertEqual(directions, {"outgoing", "incoming"})

    def test_transitive_downstream_dependencies(self) -> None:
        # Query transitive dependencies starting from ui service
        result = self.store.get_transitive_dependencies(
            "k8s:service:default:ui",
            direction="outgoing",
            max_depth=5,
        )

        dep_ids = {d["resource_id"] for d in result["dependencies"]}
        # Service -> Pod (depth 1)
        self.assertIn("k8s:pod:default:ui-6d4c8f7b9-abcde", dep_ids)
        # Service -> Pod -> Node (depth 2)
        self.assertIn("k8s:node:ip-10-0-10-51.eu-west-3.compute.internal", dep_ids)
        # Service -> Pod -> Node -> EC2 instance (depth 3)
        self.assertIn("aws:ec2:i-0aaa1111dddd0001a", dep_ids)
        # Service -> Pod -> Node -> EC2 -> NodeGroup (depth 4)
        self.assertIn("aws:eks-nodegroup:retail-store:retail-store-nodes", dep_ids)

        # Verify depths are ordered
        depth_map = {d["resource_id"]: d["depth"] for d in result["dependencies"]}
        self.assertEqual(depth_map["k8s:pod:default:ui-6d4c8f7b9-abcde"], 1)
        self.assertEqual(depth_map["k8s:node:ip-10-0-10-51.eu-west-3.compute.internal"], 2)
        self.assertEqual(depth_map["aws:ec2:i-0aaa1111dddd0001a"], 3)
        self.assertEqual(depth_map["aws:eks-nodegroup:retail-store:retail-store-nodes"], 4)

        # Edges should include paths
        self.assertTrue(len(result["edges"]) > 0)

    def test_transitive_upstream_dependencies(self) -> None:
        # Upstream (incoming) dependencies for the UI deployment:
        # NLB -[ROUTES_TO]-> UI Service -[EXPOSES]-> UI Deployment
        # Namespace -[CONTAINS]-> UI Service -[EXPOSES]-> UI Deployment
        deployment_id = "k8s:deployment:default:ui"
        result = self.store.get_transitive_dependencies(
            deployment_id,
            direction="incoming",
            max_depth=3,
        )

        incoming_ids = {d["resource_id"] for d in result["dependencies"]}
        # UI Service exposes UI deployment (depth 1)
        self.assertIn("k8s:service:default:ui", incoming_ids)
        # Namespace and NLB connect to UI Service (depth 2)
        self.assertIn("k8s:namespace:default", incoming_ids)
        nlb_id = (
            "aws:load-balancer:arn:aws:elasticloadbalancing:eu-west-3:"
            "000000000000:loadbalancer/net/retail-store-nlb/0000000000000001"
        )
        self.assertIn(nlb_id, incoming_ids)

        depth_map = {d["resource_id"]: d["depth"] for d in result["dependencies"]}
        self.assertEqual(depth_map["k8s:service:default:ui"], 1)
        self.assertEqual(depth_map["k8s:namespace:default"], 1)
        self.assertEqual(depth_map[nlb_id], 2)

    def test_transitive_downstream_from_vpc(self) -> None:
        # Downstream (outgoing) dependencies from VPC: VPC CONTAINS subnets & security groups
        vpc_id = "aws:vpc:vpc-0aaa1111bbbb2222c"
        result = self.store.get_transitive_dependencies(
            vpc_id,
            direction="outgoing",
            max_depth=3,
        )

        outgoing_ids = {d["resource_id"] for d in result["dependencies"]}
        self.assertIn("aws:subnet:subnet-0aaa1111aaaa0001a", outgoing_ids)
        self.assertIn("aws:subnet:subnet-0aaa1111aaaa0002b", outgoing_ids)
        self.assertIn("aws:security-group:sg-0aaa1111cccc0001a", outgoing_ids)

    def test_transitive_max_depth_limiting(self) -> None:
        res_depth_1 = self.store.get_transitive_dependencies(
            "k8s:service:default:ui",
            direction="outgoing",
            max_depth=1,
        )
        self.assertTrue(all(d["depth"] == 1 for d in res_depth_1["dependencies"]))
        self.assertEqual(len(res_depth_1["dependencies"]), 2)  # Pod and Deployment only


class H3RealNeo4jBackendMockTests(unittest.TestCase):
    """Tests RealNeo4jBackend Cypher query generation using mock driver/session."""

    def setUp(self) -> None:
        self.mock_driver = MagicMock()
        self.mock_session = MagicMock()
        self.mock_driver.session.return_value.__enter__.return_value = self.mock_session
        self.backend = RealNeo4jBackend(self.mock_driver, database="neo4j")

    def test_init_schema_creates_constraints_and_indexes(self) -> None:
        self.backend.init_schema()

        calls = [c[0][0] for c in self.mock_session.run.call_args_list]
        self.assertTrue(any("CONSTRAINT" in q and "Resource" in q for q in calls))
        self.assertTrue(any("CONSTRAINT" in q and "Snapshot" in q for q in calls))
        self.assertTrue(any("INDEX" in q and "r.type" in q for q in calls))

    def test_upsert_snapshot_executes_parameterized_merge(self) -> None:
        snap = {
            "id": "snap-123",
            "provider": "AWS",
            "region": "eu-west-3",
            "cluster": "retail-store",
            "schema_version": "1.0.0",
            "generated_at": "2026-10-29T18:00:00Z",
            "imported_at": "2026-10-29T18:05:00Z",
            "resource_count": 40,
            "relationship_count": 81,
            "aws_resources": 15,
            "kubernetes_resources": 25,
        }
        self.backend.upsert_snapshot(snap)

        self.mock_session.run.assert_called_once()
        query, kwargs = self.mock_session.run.call_args[0][0], self.mock_session.run.call_args[1]
        self.assertIn("MERGE (s:Snapshot {id: $snapshot_id})", query)
        self.assertEqual(kwargs["snapshot_id"], "snap-123")
        self.assertEqual(kwargs["aws_resources"], 15)

    def test_upsert_relationships_groups_by_type_and_merges(self) -> None:
        rels = [
            {"source": "a", "target": "b", "type": "SELECTS"},
            {"source": "b", "target": "c", "type": "RUNS_ON"},
            {"source": "a", "target": "d", "type": "SELECTS"},
        ]
        self.backend.upsert_relationships(rels, snapshot_id="snap-123")

        # Two distinct relationship types: SELECTS and RUNS_ON -> 2 session.run calls
        self.assertEqual(self.mock_session.run.call_count, 2)
        queries = [c[0][0] for c in self.mock_session.run.call_args_list]
        self.assertTrue(any("MERGE (a)-[r:SELECTS]->(b)" in q for q in queries))
        self.assertTrue(any("MERGE (a)-[r:RUNS_ON]->(b)" in q for q in queries))

    def test_remove_stale_resources_runs_detach_delete(self) -> None:
        mock_result = MagicMock()
        mock_summary = MagicMock()
        mock_summary.counters.nodes_deleted = 3
        mock_result.consume.return_value = mock_summary
        self.mock_session.run.return_value = mock_result

        deleted = self.backend.remove_stale_resources("snap-123")
        self.assertEqual(deleted, 3)

        query = self.mock_session.run.call_args[0][0]
        self.assertIn("DETACH DELETE r", query)


if __name__ == "__main__":
    unittest.main()
