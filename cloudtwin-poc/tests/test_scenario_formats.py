"""Chaque scénario donne le même score, qu'il soit décrit en scenario.json ou dans son vrai format
(manifest Kubernetes, intention de suppression, plan Terraform)."""

from pathlib import Path

import pytest

from ingestion import ChangeIngester
from simulation.pipeline import simulate

SCENARIOS = Path(__file__).parents[1] / "scenarios"
CASES = [
    ("S01-scale-ui-up", "proposed-deployment.yaml", 0, []),
    ("S02-scale-ui-down", "proposed-deployment.yaml", 20, ["RULE-RES-001"]),
    ("S03-public-exposure", "proposed-service.yaml", 20, ["RULE-NET-004"]),
    ("S04-open-security-group", "proposed-plan.json", 30, ["RULE-NET-003"]),
    ("S05-remove-critical-dependency", "proposed-delete.json", 11, ["RULE-RES-003"]),
]


@pytest.mark.parametrize("folder,native,score,failed", CASES)
def test_native_format_and_scenario_json_agree(folder, native, score, failed):
    twin = SCENARIOS / folder / "current-twin.json"
    for source, kind in ((SCENARIOS / folder / native, None), (SCENARIOS / folder / "scenario.json", "scenario")):
        out = simulate(twin, source, kind=kind)
        assert out["analysis"]["risk"]["score"] == score, source.name
        assert sorted(f["rule_id"] for f in out["analysis"]["findings"] if f["status"] == "fail" and
                      f["rule_id"] in failed) == failed, source.name


def test_s03_service_exposure_is_one_composite_change():
    twin = SCENARIOS / "S03-public-exposure" / "current-twin.json"
    import json
    changes = ChangeIngester(json.loads(twin.read_text())).ingest(
        SCENARIOS / "S03-public-exposure" / "proposed-service.yaml", kind="kubernetes").to_dict()["changes"]
    [change] = changes
    assert change["property_path"] == "properties.service_type_and_scheme"
    assert change["before"]["service_type"] == "ClusterIP"
    assert change["after"] == change["after"] | {"service_type": "LoadBalancer", "scheme": "internet-facing"}


def test_delete_intent_becomes_a_delete_of_its_target():
    import json
    twin = json.loads((SCENARIOS / "S05-remove-critical-dependency" / "current-twin.json").read_text())
    [change] = ChangeIngester(twin).ingest(
        SCENARIOS / "S05-remove-critical-dependency" / "proposed-delete.json", kind="kubernetes").to_dict()["changes"]
    assert (change["operation"], change["resource_id"]) == ("delete", "k8s:service:default:orders-postgresql")
