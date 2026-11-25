"""Deterministic tests for structural affected-resource analysis."""

import copy
import json
from pathlib import Path

from simulation.affected_resources import analyze_affected_resources
from simulation.pipeline import simulate

POC = Path(__file__).resolve().parents[2]
DB = "k8s:service:default:orders-postgresql"
ORDERS = "k8s:deployment:default:orders"
STATEFULSET = "k8s:statefulset:default:orders-postgresql"
UI = "k8s:deployment:default:ui"


def scenario(identifier: str) -> Path:
    return next((POC / "scenarios").glob(f"{identifier}-*"))


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_s05_finds_orders_through_the_real_dependency_path():
    folder = scenario("S05")
    out = simulate(folder / "current-twin.json", folder / "scenario.json")
    orders = next(item for item in out["affected_resources"] if item["target"] == ORDERS)
    assert orders["impact"] == "direct"
    assert orders["distance"] == 1
    assert orders["path"] == [ORDERS, DB]
    assert orders["relationship_types"] == ["DEPENDS_ON"]
    assert orders["change_id"] == out["changes"]["changes"][0]["change_id"]
    assert STATEFULSET not in {item["target"] for item in out["affected_resources"]}


def test_cycles_terminate_and_duplicate_paths_choose_one_deterministically():
    twin = {
        "resources": [
            {"id": rid, "name": rid, "type": "SERVICE", "provider": "kubernetes"}
            for rid in ("a", "b", "c", "d")
        ],
        "relationships": [
            {"source": "b", "target": "a", "type": "DEPENDS_ON"},
            {"source": "c", "target": "a", "type": "DEPENDS_ON"},
            {"source": "d", "target": "b", "type": "DEPENDS_ON"},
            {"source": "d", "target": "c", "type": "DEPENDS_ON"},
            {"source": "a", "target": "d", "type": "DEPENDS_ON"},
        ],
    }
    future = copy.deepcopy(twin)
    future["resources"] = [item for item in future["resources"] if item["id"] != "a"]
    future["relationships"] = [rel for rel in future["relationships"] if "a" not in (rel["source"], rel["target"])]
    change = {"change_id": "delete-a", "operation": "delete", "resource_id": "a",
              "resource_type": "SERVICE", "provider": "kubernetes"}
    diff = {"changes": [{"resource_id": "a", "change_type": "REMOVED"}]}
    first = analyze_affected_resources(diff, twin, future, [change])
    second = analyze_affected_resources(diff, twin, future, [change])
    assert first == second
    assert [item.target for item in first.affected].count("d") == 1
    assert next(item for item in first.affected if item.target == "d").path == ["d", "b", "a"]


def test_direction_is_respected_and_unrelated_neighbors_are_excluded():
    folder = scenario("S05")
    out = simulate(folder / "current-twin.json", folder / "scenario.json")
    ids = {item["target"] for item in out["affected_resources"]}
    assert ORDERS in ids
    assert STATEFULSET not in ids  # DB Service depends on this workload, not the reverse.
    orders_service = next(item for item in out["affected_resources"]
                          if item["target"] == "k8s:service:default:orders")
    assert orders_service["impact"] == "indirect"
    assert orders_service["path"] == ["k8s:service:default:orders", ORDERS, DB]


def test_s01_and_s02_replica_updates_stay_local_and_twins_are_immutable():
    for identifier in ("S01", "S02"):
        folder = scenario(identifier)
        current = load(folder / "current-twin.json")
        original = copy.deepcopy(current)
        out = simulate(current, folder / "scenario.json")
        assert current == original
        assert {item["target"] for item in out["affected_resources"]} == {UI}
        assert out["affected_resources"][0]["distance"] == 0


def test_unknown_relationship_marks_the_result_incomplete():
    twin = {"resources": [{"id": "a", "name": "a", "type": "SERVICE", "provider": "kubernetes"}],
            "relationships": [{"source": "a", "target": "ghost", "type": "UNKNOWN_EDGE"}]}
    future = {"resources": [], "relationships": []}
    change = {"change_id": "delete-a", "operation": "delete", "resource_id": "a",
              "resource_type": "SERVICE", "provider": "kubernetes"}
    result = analyze_affected_resources(
        {"changes": [{"resource_id": "a", "change_type": "REMOVED"}]}, twin, future, [change]
    )
    assert not result.data_complete
    assert result.unknown_relationship_types == ["UNKNOWN_EDGE"]
    assert result.affected[0].confidence == "medium"
