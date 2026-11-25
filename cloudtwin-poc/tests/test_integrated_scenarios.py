"""S01-S05 validation through the complete local CloudTwin pipeline."""

import copy
import json
from pathlib import Path

import pytest

from simulation.pipeline import simulate

ROOT = Path(__file__).parents[1]
EXPECTED = {
    "S01": (set(), 0),
    "S02": ({("RULE-RES-001", "high")}, 20),
    "S03": ({("RULE-NET-004", "high")}, 20),
    "S04": ({("RULE-NET-002", "high"), ("RULE-NET-003", "critical")}, 30),
    "S05": ({("RULE-RES-003", "medium")}, 11),
}


def folder(scenario_id: str) -> Path:
    return next((ROOT / "scenarios").glob(f"{scenario_id}-*"))


@pytest.mark.parametrize("scenario_id", sorted(EXPECTED))
def test_scenario_runs_end_to_end_without_mutating_current_twin(scenario_id: str):
    directory = folder(scenario_id)
    current = json.loads((directory / "current-twin.json").read_text(encoding="utf-8"))
    original = copy.deepcopy(current)
    out = simulate(current, directory / "scenario.json")
    assert current == original
    assert out["changes"]["changes"] and out["future_twin"] and out["diff"]["changes"]
    assert out["affected_resources"]
    failures = {(f["rule_id"], f["severity"]) for f in out["analysis"]["findings"] if f["status"] == "fail"}
    assert failures == EXPECTED[scenario_id][0]
    risk = out["analysis"]["risk"]
    assert risk["score"] == EXPECTED[scenario_id][1]
    assert {"score", "level", "findings_points", "blast_radius_bonus", "explanation"} <= risk.keys()


def test_s05_directional_paths_and_removed_relationships():
    directory = folder("S05")
    out = simulate(directory / "current-twin.json", directory / "scenario.json")
    assert len(out["removed_relationships"]) == 2
    affected = {item["target"]: item for item in out["affected_resources"]}
    orders = affected["k8s:deployment:default:orders"]
    assert orders["distance"] == 1 and orders["relationship_types"] == ["DEPENDS_ON"]
    assert affected["k8s:service:default:orders"]["distance"] == 2
    assert "k8s:statefulset:default:orders-postgresql" not in affected
