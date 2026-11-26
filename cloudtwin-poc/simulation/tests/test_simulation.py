"""Tests du Future Twin, du Diff et de la chaîne complète du Milestone 1."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from simulation import build_future_twin, changed_resource_ids, diff_twins, removed_relationships
from simulation.pipeline import simulate

POC = Path(__file__).resolve().parents[2]
REAL = json.loads((POC / "analysis/tests/fixtures/poc/current_state.json").read_text(encoding="utf-8"))
TWIN_SCHEMA = Draft202012Validator(json.loads((POC / "schema/v1/twin.schema.json").read_text(encoding="utf-8")))
DIFF_SCHEMA = Draft202012Validator(json.loads((POC / "schema/v1/diff.schema.json").read_text(encoding="utf-8")))
UI = "k8s:deployment:default:ui"
SG = "aws:security-group:sg-0node"


def change(cid="change-001", op="update", rid=UI, rtype="DEPLOYMENT", provider="kubernetes",
           path="properties.replicas", before=None, after=None):
    return {"change_id": cid, "operation": op, "resource_id": rid, "resource_type": rtype,
            "provider": provider, "property_path": path, "before": before, "after": after}


def scenario(sid):
    return next((POC / "scenarios").glob(f"{sid}-*"))


def props(twin, rid):
    return next(r for r in twin["resources"] if r["id"] == rid)["properties"]


# ------------------------------------------------------------- Future Twin

def test_update_sets_the_property_and_never_touches_the_current_twin():
    original = copy.deepcopy(REAL)
    replicas = props(REAL, UI)["replicas"]
    future = build_future_twin(REAL, [change(before=replicas, after=1)])
    assert props(future.twin, UI)["replicas"] == 1
    assert future.changed == {UI: ["properties.replicas"]} and future.warnings == []
    assert REAL == original


def test_nested_path_is_created_if_needed():
    future = build_future_twin(REAL, [change(path="properties.strategy.max_surge", after="25%")])
    assert props(future.twin, UI)["strategy"]["max_surge"] == "25%"


def test_delete_removes_the_resource_and_its_relationships():
    future = build_future_twin(REAL, [change(op="delete", rid=SG, rtype="SECURITY_GROUP", provider="aws",
                                             path=None, before={}, after=None)])
    assert SG not in {r["id"] for r in future.twin["resources"]}
    assert all(SG not in (rel["source"], rel["target"]) for rel in future.twin["relationships"])
    assert future.deleted == [SG]


def test_create_adds_a_resource_in_the_same_format():
    new_id = "aws:security-group:sg-new"
    future = build_future_twin(REAL, [change(op="create", rid=new_id, rtype="SECURITY_GROUP", provider="aws",
                                             path="properties", after={"name": "new", "ingress": []})])
    created = next(r for r in future.twin["resources"] if r["id"] == new_id)
    assert set(created) == {"id", "name", "type", "provider", "properties"} and created["name"] == "new"


def test_replace_swaps_all_properties():
    future = build_future_twin(REAL, [change(op="replace", path="properties", after={"replicas": 2})])
    assert props(future.twin, UI) == {"replicas": 2}


def test_inconsistencies_become_warnings():
    future = build_future_twin(REAL, [
        change(cid="change-001", before=99, after=1),                       # l'avant ne correspond pas
        change(cid="change-002", rid="k8s:deployment:default:ghost"),       # ressource absente
    ])
    assert len(future.warnings) == 2 and "attendait 99" in future.warnings[0]


def test_a_valid_twin_stays_valid():
    valid = copy.deepcopy(REAL) | {"schema_version": "1.0.0"}
    assert not list(TWIN_SCHEMA.iter_errors(valid))
    future = build_future_twin(valid, [
        change(cid="change-001", after=1),
        change(cid="change-002", op="create", rid="aws:security-group:sg-new", rtype="SECURITY_GROUP",
               provider="aws", path="properties", after={"name": "new"}),
        change(cid="change-003", op="delete", rid=SG, rtype="SECURITY_GROUP", provider="aws", path=None),
    ])
    assert not list(TWIN_SCHEMA.iter_errors(future.twin))
    assert future.twin["summary"]["relationships"] == len(future.twin["relationships"])


# -------------------------------------------------------------------- Diff

def test_diff_matches_the_schema_and_lists_each_change():
    future = build_future_twin(REAL, [
        change(cid="change-001", after=5),
        change(cid="change-002", op="create", rid="aws:security-group:sg-new", rtype="SECURITY_GROUP",
               provider="aws", path="properties", after={"name": "new"}),
        change(cid="change-003", op="delete", rid=SG, rtype="SECURITY_GROUP", provider="aws", path=None),
    ])
    diff = diff_twins(REAL, future.twin)
    assert not list(DIFF_SCHEMA.iter_errors(diff))
    kinds = {(c["resource_id"], c["change_type"], c.get("property")) for c in diff["changes"]}
    assert kinds == {(UI, "MODIFIED", "properties.replicas"), ("aws:security-group:sg-new", "ADDED", None),
                     (SG, "REMOVED", None)}
    assert changed_resource_ids(diff) == sorted([UI, "aws:security-group:sg-new", SG])
    assert {"source": SG, "type": "CONTAINS", "target": SG} not in removed_relationships(REAL, future.twin)
    assert any(r["target"] == SG for r in removed_relationships(REAL, future.twin))


def test_same_twin_gives_an_empty_diff():
    assert diff_twins(REAL, copy.deepcopy(REAL))["changes"] == []
    # Une valeur identique à l'actuelle ne produit aucune ligne de Diff.
    current = props(REAL, UI)["replicas"]
    assert diff_twins(REAL, build_future_twin(REAL, [change(after=current)]).twin)["changes"] == []


def test_lists_are_compared_whole():
    before = [{"protocol": "tcp", "from_port": 443, "to_port": 443, "cidr_blocks": ["10.0.0.0/16"]}]
    after = before + [{"protocol": "tcp", "from_port": 22, "to_port": 22, "cidr_blocks": ["0.0.0.0/0"]}]
    twin = copy.deepcopy(REAL)
    props(twin, SG)["ingress"] = before
    future = build_future_twin(twin, [change(rid=SG, rtype="SECURITY_GROUP", provider="aws",
                                             path="properties.ingress", before=before, after=after)])
    [entry] = diff_twins(twin, future.twin)["changes"]
    assert entry["property"] == "properties.ingress" and entry["after"] == after


# ----------------------------------------------------- chaîne Milestone 1

def test_milestone_1_terraform_plan_end_to_end():
    d = scenario("S04")
    out = simulate(d / "current-twin.json", d / "proposed-plan.json")
    [entry] = out["diff"]["changes"]
    assert entry["change_type"] == "MODIFIED" and entry["property"] == "properties.ingress"
    assert any("0.0.0.0/0" in r.get("cidr_blocks", []) for r in entry["after"])
    fails = {f["rule_id"]: f["severity"] for f in out["analysis"]["findings"] if f["status"] == "fail"}
    assert fails["RULE-NET-003"] == "critical" and out["analysis"]["risk"]["score"] == 30
    # Le contexte transmis à l'analyse porte le Future Twin.
    assert out["_context"].future_twin is not None


def test_s03_composite_property_updates_the_real_fields():
    d = scenario("S03")
    out = simulate(d / "current-twin.json", d / "scenario.json")
    changes = {c["property"]: c["after"] for c in out["diff"]["changes"]}
    assert changes == {"properties.service_type": "LoadBalancer", "properties.scheme": "internet-facing"}


def test_s05_deletion_removes_the_database_and_its_links():
    d = scenario("S05")
    out = simulate(d / "current-twin.json", d / "scenario.json")
    assert [c["change_type"] for c in out["diff"]["changes"]] == ["REMOVED"]
    assert len(out["removed_relationships"]) == 2


def test_cli_writes_the_future_twin(tmp_path):
    d = scenario("S04")
    future_file = tmp_path / "future.json"
    run = subprocess.run([sys.executable, "-m", "simulation", "--twin", str(d / "current-twin.json"),
                          "--change", str(d / "proposed-plan.json"), "--future-out", str(future_file),
                          "--fixes"], cwd=POC, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert json.loads(future_file.read_text(encoding="utf-8")) == out["future_twin"]
    assert out["remediation"]["options"][0]["verified"]
    assert "1 propriété(s) modifiée(s)" in run.stderr
