import json
from pathlib import Path

import pytest

from analysis.adapters import context_from_poc
from analysis.engine import run
from integration.scenario_compat import (
    CompatibilityError,
    discover_scenarios,
    to_fatma_poc_scenario,
)


ROOT = Path(__file__).parents[1]
SCENARIOS = ROOT / "scenarios"


def load_scenario(directory: str) -> dict:
    return json.loads((SCENARIOS / directory / "scenario.json").read_text(encoding="utf-8"))


def load_twin(directory: str) -> dict:
    return json.loads((SCENARIOS / directory / "current-twin.json").read_text(encoding="utf-8"))


def test_discovers_current_scenario_pack_in_id_order():
    scenarios = discover_scenarios(SCENARIOS)

    assert list(scenarios) == ["S01", "S02", "S03", "S04", "S05"]
    assert scenarios["S01"]["name"] == "Scale UI up"


@pytest.mark.parametrize(
    ("directory", "expected_before", "expected_after"),
    [
        ("S01-scale-ui-up", 1, 3),
        ("S02-scale-ui-down", 3, 1),
    ],
)
def test_maps_scale_scenarios_without_losing_identity_or_values(
    directory: str, expected_before: int, expected_after: int
):
    converted = to_fatma_poc_scenario(load_scenario(directory))

    assert converted["scenario_id"] == directory[:3]
    assert converted["target_resource"] == "k8s:deployment:default:ui"
    assert converted["resource"] == "ui"
    assert converted["namespace"] == "default"
    assert converted["change"] == {
        "operation": "update",
        "property": "replicas",
        "before": expected_before,
        "after": expected_after,
    }
    assert "affected_resources" not in converted
    assert "future_twin" not in converted
    assert "diff" not in converted


def test_s01_and_s02_run_through_fatmas_existing_adapter_and_engine():
    s01 = run(context_from_poc(
        load_twin("S01-scale-ui-up"),
        to_fatma_poc_scenario(load_scenario("S01-scale-ui-up")),
    ))
    s02 = run(context_from_poc(
        load_twin("S02-scale-ui-down"),
        to_fatma_poc_scenario(load_scenario("S02-scale-ui-down")),
    ))

    assert [(finding.rule_id, finding.status.value) for finding in s01.findings] == [
        ("RULE-RES-001", "pass")
    ]
    assert [(finding.rule_id, finding.status.value) for finding in s02.findings] == [
        ("RULE-RES-001", "fail")
    ]


def test_rejects_scenario_outside_fatmas_temporary_kubernetes_adapter_scope():
    with pytest.raises(CompatibilityError, match="Kubernetes resource IDs"):
        to_fatma_poc_scenario(load_scenario("S04-open-security-group"))


def test_rejects_inconsistent_resource_type_and_canonical_id():
    scenario = load_scenario("S01-scale-ui-up")
    scenario["resource_type"] = "SERVICE"

    with pytest.raises(CompatibilityError, match="does not match"):
        to_fatma_poc_scenario(scenario)
