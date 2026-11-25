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
        # Le pont construit maintenant le Future Twin : les réplicas y ont leur nouvelle valeur.
        change = changeset.changes[0]
        future_ui = context.future_twin.get(change.resource_id)
        assert future_ui.properties["replicas"] == change.after
        assert context.current_twin.get(change.resource_id).properties["replicas"] == change.before
        assert context.affected_resources == []
    assert ("RULE-RES-001", "pass") in statuses["S01"]
    assert ("RULE-RES-001", "fail") in statuses["S02"]


@pytest.mark.parametrize("source", ["scenario", "terraform_plan"])
def test_s04_chain_produces_net003_critical(source):
    """Milestone 1 (sans Future Twin) : S04 d'Omar -> ingestion Hamdi -> NET-003."""
    scenario, twin = _scenario("S04")
    ingester = ChangeIngester(twin)
    if source == "scenario":
        changeset = ingester.ingest_scenario(scenario)
    else:
        plan = next(ROOT.joinpath("scenarios").glob("S04-*")) / "proposed-plan.json"
        changeset = ingester.ingest_terraform_plan(plan)

    result = run_analysis(context_from_changeset(changeset, twin))
    [finding] = [f for f in result.findings if f.rule_id == "RULE-NET-003"]
    assert finding.status.value == "fail"
    assert finding.severity.value == "critical"
    assert "k8s:statefulset:default:orders-postgresql" in finding.affected_resource_ids
    assert result.summary["fail_critical"] == 1


def test_terraform_plan_bom_and_coverage_metadata(tmp_path):
    plan = {
        "format_version": "1.2",
        "resource_changes": [
            {"mode": "managed", "type": "null_resource", "name": "wait",
             "address": "null_resource.wait", "change": {"actions": ["create"], "before": None, "after": {}}},
            {"mode": "managed", "type": "aws_vpc", "name": "main",
             "address": "aws_vpc.main", "change": {"actions": ["create"], "before": None, "after": {"cidr_block": "10.0.0.0/16"}}},
        ],
    }
    path = tmp_path / "plan.json"
    path.write_text("\ufeff" + json.dumps(plan), encoding="utf-8")
    changeset = ChangeIngester().ingest_terraform_plan(path)
    coverage = changeset.metadata["terraform_coverage"]
    assert coverage["status"] == "complete"
    assert coverage["total_resource_changes"] == 2
    assert coverage["normalized_supported"] == 1
    assert coverage["ignored_non_semantic"][0]["type"] == "null_resource"
    assert coverage["unsupported_material"] == []
