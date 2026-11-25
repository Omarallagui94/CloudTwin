from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.resolve_ci_risk_inputs import resolve


def twin(path: Path) -> Path:
    path.write_text(json.dumps({"resources": [], "relationships": []}), encoding="utf-8")
    return path


def test_code_only_change_does_not_require_production_inputs(tmp_path):
    result = resolve(["cloudtwin-poc/tests/test_ci_risk_gate.py"], tmp_path / "absent.json",
                     None, None, tmp_path / "generated.yaml")
    assert result["enabled"] == "false"
    assert result["required"] == "false"
    assert "code-only" in result["reason"]


def test_changed_kubernetes_manifest_is_the_proposed_change(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    manifest = Path("kubernetes/deployment.yaml")
    manifest.parent.mkdir()
    manifest.write_text("apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: ui\n", encoding="utf-8")
    generated = Path("artifacts/change.yaml")
    result = resolve([str(manifest)], twin(Path("baseline.json")), None, None, generated, True)
    assert result["enabled"] == "true"
    assert result["kind"] == "kubernetes"
    assert Path(result["change"]).read_text(encoding="utf-8") == manifest.read_text(encoding="utf-8")


def test_terraform_change_without_plan_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="no real Terraform plan JSON"):
        resolve(["terraform/eks/minimal/main.tf"], twin(tmp_path / "baseline.json"),
                None, None, tmp_path / "generated.yaml", True)


def test_terraform_change_accepts_machine_generated_plan(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"resource_changes": []}), encoding="utf-8")
    result = resolve(["terraform/eks/minimal/main.tf"], twin(tmp_path / "baseline.json"),
                     plan, "terraform", tmp_path / "generated.yaml", True)
    assert result["enabled"] == "true"
    assert result["required"] == "true"
    assert result["change"] == str(plan)


def test_unsanitized_current_twin_is_rejected(tmp_path):
    unsafe = twin(tmp_path / "unsafe.json")
    unsafe.write_text(json.dumps({"resources": [], "relationships": [],
                                  "account": "123456789012"}), encoding="utf-8")
    manifest = tmp_path / "kubernetes" / "service.yaml"
    manifest.parent.mkdir()
    manifest.write_text("apiVersion: v1\nkind: Service\nmetadata:\n  name: ui\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not sanitized"):
        resolve([str(manifest)], unsafe, manifest, "kubernetes", tmp_path / "generated.yaml", True)
