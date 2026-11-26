from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from scripts import ci_risk_gate


@pytest.mark.parametrize(("score", "expected"), [(79, 0), (80, 1), (81, 1), (100, 1)])
def test_real_gate_boundaries_and_report(monkeypatch, tmp_path, score, expected):
    twin = tmp_path / "current-twin.json"
    change = tmp_path / "proposed-plan.json"
    report = tmp_path / "risk-analysis.json"
    twin.write_text("{}", encoding="utf-8")
    change.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        ci_risk_gate,
        "simulate",
        lambda *_args, **_kwargs: {
            "analysis": {"risk": {"score": score}},
            "_context": object(),
            "_result": SimpleNamespace(risk={"score": score}),
        },
    )
    monkeypatch.setattr(sys, "argv", [
        "ci_risk_gate.py", "--twin", str(twin), "--change", str(change),
        "--kind", "terraform", "--report", str(report), "--fail-at", "80",
    ])

    assert ci_risk_gate.main() == expected
    assert json.loads(report.read_text(encoding="utf-8"))["analysis"]["risk"]["score"] == score


def test_real_gate_fails_closed_when_inputs_are_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", [
        "ci_risk_gate.py",
        "--twin", str(tmp_path / "missing-twin.json"),
        "--change", str(tmp_path / "missing-change.json"),
        "--report", str(tmp_path / "report.json"),
    ])

    with pytest.raises(SystemExit) as error:
        ci_risk_gate.main()
    assert error.value.code == 2
