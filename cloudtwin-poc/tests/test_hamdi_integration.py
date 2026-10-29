import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, validate

from analysis.engine import run as run_analysis
from ingestion import ChangeIngester, UnresolvedTargetError
from ingestion.resolver import normalize_resource_type
from integration.hamdi_fatma import context_from_changeset


ROOT = Path(__file__).resolve().parents[1]


def _scenario(scenario_id: str):
    directory = next(ROOT.joinpath("scenarios").glob(f"{scenario_id}-*"))
    scenario = json.loads(directory.joinpath("scenario.json").read_text(encoding="utf-8"))
    twin = json.loads(directory.joinpath("current-twin.json").read_text(encoding="utf-8"))
    return scenario, twin


@pytest.mark.parametrize("scenario_id", ["S01", "S02", "S03", "S04", "S05"])
def test_omar_scenarios_ingest_with_canonical_targets(scenario_id):
    scenario, twin = _scenario(scenario_id)
    changeset = ChangeIngester(twin).ingest_scenario(scenario)
    change = changeset.changes[0]
    assert change.resource_id == scenario["target_resource"]
    assert change.before == scenario["before"]
    assert change.after == scenario["after"]
    assert change.source["reference"] == scenario_id
    schema = json.loads(ROOT.joinpath("schema/v1/changeset.schema.json").read_text())
    validate(changeset.to_dict(), schema)


def test_unknown_canonical_target_fails_explicitly():
    scenario, twin = _scenario("S01")
    scenario["target_resource"] = "k8s:deployment:default:not-real"
    with pytest.raises(UnresolvedTargetError):
        ChangeIngester(twin).ingest_scenario(scenario)


def test_statefulset_has_one_canonical_slug():
    assert normalize_resource_type("StatefulSet") == (
        "STATEFUL_SET", "kubernetes", "statefulset"
    )


@pytest.mark.parametrize("schema_name", ["twin", "scenario", "diff", "impact", "changeset"])
def test_schema_is_valid_draft_2020_12(schema_name):
    schema = json.loads(ROOT.joinpath(f"schema/v1/{schema_name}.schema.json").read_text())
    Draft202012Validator.check_schema(schema)


def test_s01_s02_bridge_and_resilience_results():
    statuses = {}
    for scenario_id in ("S01", "S02"):
        scenario, twin = _scenario(scenario_id)
        changeset = ChangeIngester(twin).ingest_scenario(scenario)
        context = context_from_changeset(changeset, twin)
        result = run_analysis(context)
        statuses[scenario_id] = [(f.rule_id, f.status.value) for f in result.findings]
        assert context.future_twin is None
        assert context.affected_resources == []
    assert ("RULE-RES-001", "pass") in statuses["S01"]
    assert ("RULE-RES-001", "fail") in statuses["S02"]
