"""Bout en bout avec les fichiers réels du PoC : current_state.json + scenario-01.json."""

import json
from pathlib import Path

import pytest

from analysis.adapters import ScenarioError, context_from_poc
from analysis.engine import run

POC = Path(__file__).parent / "fixtures" / "poc"


def load(name):
    return json.loads((POC / name).read_text(encoding="utf-8"))


def test_real_scenario_01_is_safe():
    # S01 du dépôt : ui passe de 1 à 3 réplicas -> aucun risque de résilience
    ctx = context_from_poc(load("current_state.json"), load("scenario-01.json"))
    assert ctx.changes[0].resource_id == "k8s:deployment:default:ui"
    result = run(ctx)
    assert result.summary["fail"] == 0
    assert [f.status.value for f in result.findings] == ["pass"]


def test_reverse_scenario_is_risky():
    scenario = load("scenario-01.json")
    scenario["scenario_id"] = "S02"
    scenario["change"] = {"property": "replicas", "before": 3, "after": 1}
    result = run(context_from_poc(load("current_state.json"), scenario))
    [f] = result.findings
    assert f.rule_id == "RULE-RES-001" and f.status.value == "fail"
    assert "k8s:service:default:ui" in f.affected_resource_ids


def test_unknown_resource_is_rejected():
    scenario = load("scenario-01.json")
    scenario["resource"] = "does-not-exist"
    with pytest.raises(ScenarioError):
        context_from_poc(load("current_state.json"), scenario)
