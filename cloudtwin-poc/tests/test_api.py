"""API HTTP (H8) : mêmes résultats que la ligne de commande, erreurs claires, schéma OpenAPI."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)
SCENARIOS = Path(__file__).parents[1] / "scenarios"


def test_health_and_rules_catalog():
    assert client.get("/health").json() == {"status": "ok", "rules": 11}
    rules = {r["rule_id"]: r for r in client.get("/rules").json()}
    assert rules["RULE-NET-003"]["severity"] == "critical"
    assert "RULE-K8S-001" in rules


def test_scenarios_are_listed_with_their_native_source():
    items = {s["id"]: s for s in client.get("/scenarios").json()}
    assert list(items) == ["S01", "S02", "S03", "S04", "S05"]
    assert items["S04"]["native_source"] == "proposed-plan.json"


@pytest.mark.parametrize("sid,score", [("S01", 0), ("S02", 20), ("S03", 20), ("S04", 30), ("S05", 11)])
def test_each_scenario_gives_the_same_score_as_the_cli(sid, score):
    for source in ("native", "scenario"):
        body = client.post(f"/simulations/scenario/{sid}", params={"source": source}).json()
        assert body["analysis"]["risk"]["score"] == score, source
        assert "_context" not in body and "_result" not in body


def test_s04_with_explanation_and_verified_fixes():
    body = client.post("/simulations/scenario/S04", params={"explain": True, "fixes": True}).json()
    assert body["ai_explanation"]["summary"].startswith("Ce changement obtient 30/100")
    assert body["fixes"]["score_before"] == 30 and body["fixes"]["all_fixes"]["score_after"] == 0


def test_simulation_with_a_twin_and_a_plan_sent_in_the_body():
    folder = SCENARIOS / "S04-open-security-group"
    payload = {"current_twin": json.loads((folder / "current-twin.json").read_text()),
               "change": json.loads((folder / "proposed-plan.json").read_text())}
    body = client.post("/simulations", json=payload).json()
    failed = [f["rule_id"] for f in body["analysis"]["findings"] if f["status"] == "fail"]
    assert "RULE-NET-003" in failed
    assert body["changes"]["source_kind"] == "terraform_plan"


def test_kubernetes_manifest_sent_as_yaml_text():
    folder = SCENARIOS / "S03-public-exposure"
    payload = {"current_twin": json.loads((folder / "current-twin.json").read_text()),
               "change": (folder / "proposed-service.yaml").read_text(), "kind": "kubernetes"}
    assert client.post("/simulations", json=payload).json()["analysis"]["risk"]["score"] == 20


def test_unreadable_input_is_a_400_with_the_reason():
    folder = SCENARIOS / "S04-open-security-group"
    payload = {"current_twin": json.loads((folder / "current-twin.json").read_text()),
               "change": {"not": "a plan"}, "kind": "terraform"}
    response = client.post("/simulations", json=payload)
    assert response.status_code == 400
    assert "resource_changes" in response.json()["detail"]


def test_unknown_scenario_is_a_404():
    assert client.post("/simulations/scenario/S99").status_code == 404


def test_openapi_documents_every_route():
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/health", "/rules", "/scenarios", "/simulations",
            "/simulations/scenario/{scenario_id}"} <= set(paths)
