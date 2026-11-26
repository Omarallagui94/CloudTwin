
"""Regression tests for the saved CloudTwin discovery-input fixture.

The discovery fixture was reconstructed from a normalized snapshot.
These tests validate normalization consistency, not live cloud discovery.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from discovery.normalizer import normalize

INPUT_FIXTURE = ROOT / "fixtures" / "sample_discovery_input.json"
BASELINE_SNAPSHOT = ROOT / "fixtures" / "sample_current_state_baseline.json"


def load_json(path: Path) -> dict:
    """Load a JSON file and return its top-level object."""
    if not path.is_file():
        raise FileNotFoundError(f"Required fixture not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}")

    return data


def relationship_graph(snapshot: dict) -> set[tuple[str, str, str]]:
    """Represent relationships as comparable source-target-type tuples."""
    return {
        (
            relationship["source"],
            relationship["target"],
            relationship["type"],
        )
        for relationship in snapshot.get("relationships", [])
    }


class H2SavedDiscoveryFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_json(INPUT_FIXTURE)
        cls.baseline = load_json(BASELINE_SNAPSHOT)

        metadata = cls.fixture.get("metadata", {})
        region = metadata.get("region")
        cluster_name = metadata.get("cluster")

        if not region:
            raise ValueError("Discovery fixture is missing metadata.region")

        if not cluster_name:
            raise ValueError("Discovery fixture is missing metadata.cluster")

        cls.regenerated = normalize(
            cls.fixture["aws"],
            cls.fixture["kubernetes"],
            region,
            cluster_name,
        )

    def test_fixture_is_labeled_as_reconstructed(self):
        self.assertEqual(
            self.fixture.get("fixture_type"),
            "reconstructed_from_normalized_snapshot",
        )

    def test_normalization_reproduces_baseline_resource_count(self):
        self.assertEqual(
            len(self.regenerated["resources"]),
            len(self.baseline["resources"]),
        )

    def test_normalization_reproduces_baseline_relationship_count(self):
        self.assertEqual(
            len(self.regenerated["relationships"]),
            len(self.baseline["relationships"]),
        )

    def test_normalization_reproduces_baseline_provider_counts(self):
        expected_summary = self.baseline["summary"]
        actual_summary = self.regenerated["summary"]

        self.assertEqual(
            actual_summary["aws_resources"],
            expected_summary["aws_resources"],
        )
        self.assertEqual(
            actual_summary["kubernetes_resources"],
            expected_summary["kubernetes_resources"],
        )
        self.assertEqual(
            actual_summary["relationships"],
            len(self.baseline["relationships"]),
        )

    def test_normalization_reproduces_resource_ids(self):
        expected_ids = {
            resource["id"]
            for resource in self.baseline["resources"]
        }
        actual_ids = {
            resource["id"]
            for resource in self.regenerated["resources"]
        }

        self.assertEqual(actual_ids, expected_ids)

    def test_normalization_reproduces_relationship_graph(self):
        expected_graph = relationship_graph(self.baseline)
        actual_graph = relationship_graph(self.regenerated)

        self.assertEqual(actual_graph, expected_graph)

    def test_regenerated_graph_has_no_dangling_relationships(self):
        resource_ids = {
            resource["id"]
            for resource in self.regenerated["resources"]
        }

        for relationship in self.regenerated["relationships"]:
            with self.subTest(relationship=relationship):
                self.assertIn(relationship["source"], resource_ids)
                self.assertIn(relationship["target"], resource_ids)

    def test_fixture_baseline_metadata_matches_snapshot(self):
        fixture_baseline = self.fixture["baseline"]
        snapshot_summary = self.baseline["summary"]

        self.assertEqual(
            fixture_baseline["resources"],
            len(self.baseline["resources"]),
        )
        self.assertEqual(
            fixture_baseline["relationships"],
            len(self.baseline["relationships"]),
        )
        self.assertEqual(
            fixture_baseline["aws_resources"],
            snapshot_summary["aws_resources"],
        )
        self.assertEqual(
            fixture_baseline["kubernetes_resources"],
            snapshot_summary["kubernetes_resources"],
        )


if __name__ == "__main__":
    unittest.main()
