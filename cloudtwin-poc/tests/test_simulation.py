import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import validate

from ingestion import ChangeIngester, ChangeSet, NormalizedChange, OperationType
from simulation import SimulationError, calculate_state_diff, simulate_future_twin


ROOT = Path(__file__).resolve().parents[1]


def _scenario(scenario_id):
    directory = next(ROOT.joinpath("scenarios").glob(f"{scenario_id}-*"))
    scenario = json.loads(directory.joinpath("scenario.json").read_text(encoding="utf-8"))
    twin = json.loads(directory.joinpath("current-twin.json").read_text(encoding="utf-8"))
    return scenario, twin


def _simulate(scenario_id):
    scenario, current = _scenario(scenario_id)
    changeset = ChangeIngester(current).ingest_scenario(scenario, strict_conflict_check=True)
    future = simulate_future_twin(current, changeset)
    return current, future, calculate_state_diff(current, future)


def _resource(twin, resource_id):
    return next(resource for resource in twin["resources"] if resource["id"] == resource_id)


def test_s01_future_twin_is_a_copy_and_scales_one_to_three():
    scenario, current = _scenario("S01")
    original = deepcopy(current)
    changeset = ChangeIngester(current).ingest_scenario(scenario, strict_conflict_check=True)
    future = simulate_future_twin(current, changeset)
    assert current == original
    assert future is not current
    assert _resource(current, scenario["target_resource"])["properties"]["replicas"] == 1
    assert _resource(future, scenario["target_resource"])["properties"]["replicas"] == 3


def test_s02_future_twin_scales_three_to_one():
    current, future, diff = _simulate("S02")
    target = "k8s:deployment:default:ui"
    assert _resource(current, target)["properties"]["replicas"] == 3
    assert _resource(future, target)["properties"]["replicas"] == 1
    assert diff.modified_resources == [{
        "resource_id": target,
        "change_type": "MODIFIED",
        "property": "properties.replicas",
        "before": 3,
        "after": 1,
    }]


def test_s05_deletes_only_target_and_its_relationships_from_future():
    current, future, diff = _simulate("S05")
    target = "k8s:service:default:orders-postgresql"
    assert any(resource["id"] == target for resource in current["resources"])
    assert all(resource["id"] != target for resource in future["resources"])
    assert len(current["resources"]) - len(future["resources"]) == 1
    assert all(
        relationship["source"] != target and relationship["target"] != target
        for relationship in future["relationships"]
    )
    assert diff.removed_resources[0]["resource_id"] == target
    assert len(diff.removed_relationships) == 2
    assert not diff.added_relationships


def test_diff_is_deterministic_and_validates_against_existing_resource_diff_schema():
    current, future, first = _simulate("S01")
    second = calculate_state_diff(current, future)
    assert first.to_dict() == second.to_dict()
    schema = json.loads(ROOT.joinpath("schema/v1/diff.schema.json").read_text(encoding="utf-8"))
    validate(first.canonical_dict(), schema)


def test_invalid_canonical_target_fails_without_mutating_current():
    _, current = _scenario("S01")
    original = deepcopy(current)
    change = NormalizedChange(
        change_id="invalid-target",
        operation=OperationType.UPDATE,
        resource_id="k8s:deployment:default:not-real",
        resource_type="DEPLOYMENT",
        provider="kubernetes",
        property_path="properties.replicas",
        before=1,
        after=3,
        source={"kind": "scenario"},
        namespace="default",
    )
    with pytest.raises(SimulationError, match="absent from Current Twin"):
        simulate_future_twin(current, ChangeSet(changes=[change]))
    assert current == original


def test_create_fails_explicitly_instead_of_fabricating_a_resource():
    _, current = _scenario("S01")
    change = NormalizedChange(
        change_id="unsupported-create",
        operation=OperationType.CREATE,
        resource_id="k8s:service:default:new-service",
        resource_type="SERVICE",
        provider="kubernetes",
        property_path=None,
        before=None,
        after={"service_type": "ClusterIP"},
        source={"kind": "scenario"},
        namespace="default",
    )
    with pytest.raises(SimulationError, match="not supported safely"):
        simulate_future_twin(current, ChangeSet(changes=[change]))
