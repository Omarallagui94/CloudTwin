import json
from copy import deepcopy
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from ingestion import ChangeIngester
from reporting import build_simulation_report
from simulation import calculate_state_diff, simulate_future_twin
from simulation.pipeline import simulate


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 30, 8, 0, tzinfo=timezone.utc)


class _HTMLCheck(HTMLParser):
    pass


def _inputs(scenario_id, *, after=None):
    directory = next(ROOT.joinpath("scenarios").glob(f"{scenario_id}-*"))
    scenario = json.loads(directory.joinpath("scenario.json").read_text(encoding="utf-8"))
    if after is not None:
        scenario = {**scenario, "scenario_id": "CUSTOM", "name": "Custom UI scaling", "after": after}
    current = json.loads(directory.joinpath("current-twin.json").read_text(encoding="utf-8"))
    changeset = ChangeIngester(current).ingest_scenario(scenario, strict_conflict_check=True)
    future = simulate_future_twin(current, changeset)
    diff = calculate_state_diff(current, future).to_dict()
    return scenario, current, future, changeset.to_dict(), diff


def _report(scenario_id, *, after=None, current_override=None):
    scenario, current, future, changeset, diff = _inputs(scenario_id, after=after)
    if current_override:
        current = current_override
    report = build_simulation_report(
        scenario=scenario,
        changeset=changeset,
        current_twin=current,
        future_twin=future,
        state_diff=diff,
        analysis_result=None,
        datasource_mode="Local Fixture Mode",
        source=scenario_id if after is None else "Custom request",
        generated_at=NOW,
    )
    return report, (scenario, current, future, changeset, diff)


def test_s01_report_is_human_readable_and_simulation_only():
    report, _ = _report("S01")
    assert report.filename == "cloudtwin-simulation-S01-20261030-080000.html"
    for expected in ("UI Deployment", "UPDATE", "Replicas", ">1<", ">3<", "Modified properties", "SIMULATION ONLY"):
        assert expected in report.html
    assert "Blast Radius</td><td>NOT AVAILABLE" in report.html
    assert "Risk Score</td><td>NOT AVAILABLE" in report.html


def test_s05_report_contains_only_the_two_actual_removed_relationships():
    report, (_, _, _, _, diff) = _report("S05")
    assert "Orders PostgreSQL Service" in report.html
    assert ">PRESENT<" in report.html and ">REMOVED<" in report.html
    assert report.html.count("REMOVED RELATIONSHIP") == len(diff["removed_relationships"]) == 2
    assert "depends on" in report.html
    assert "exposes" in report.html


def test_custom_supported_change_reports_one_to_four():
    report, _ = _report("S01", after=4)
    assert "Custom UI scaling" in report.html
    assert ">1<" in report.html and ">4<" in report.html
    assert "cloudtwin-simulation-CUSTOM" in report.filename


def test_report_generation_does_not_mutate_inputs_and_produces_parseable_html():
    scenario, current, future, changeset, diff = _inputs("S05")
    originals = deepcopy((scenario, current, future, changeset, diff))
    report = build_simulation_report(
        scenario=scenario, changeset=changeset, current_twin=current, future_twin=future,
        state_diff=diff, analysis_result=None, datasource_mode="Local Fixture Mode",
        source="S05", generated_at=NOW,
    )
    assert (scenario, current, future, changeset, diff) == originals
    parser = _HTMLCheck()
    parser.feed(report.html)
    parser.close()
    assert report.html.endswith("</html>")


def test_export_redacts_sensitive_keys_account_ids_arns_and_access_keys():
    scenario, current, future, changeset, diff = _inputs("S01")
    current["resources"][0]["properties"].update({
        "password": "professor-secret",
        "account": "123456789012",
        "role": "arn:aws:iam::123456789012:role/demo",
        "access_key": "AKIAIOSFODNN7EXAMPLE",
    })
    report = build_simulation_report(
        scenario=scenario, changeset=changeset, current_twin=current, future_twin=future,
        state_diff=diff, analysis_result=None, datasource_mode="Local Fixture Mode",
        source="S01", generated_at=NOW,
    )
    for forbidden in ("professor-secret", "123456789012", "arn:aws", "AKIAIOSFODNN7EXAMPLE"):
        assert forbidden not in report.html
    assert "[REDACTED]" in report.html


def test_s05_complete_report_contains_impact_risk_remediation_and_explanation():
    directory = next(ROOT.joinpath("scenarios").glob("S05-*"))
    scenario = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
    current = json.loads((directory / "current-twin.json").read_text(encoding="utf-8"))
    output = simulate(current, directory / "scenario.json")
    state_diff = output["diff"] | {"removed_relationships": output["removed_relationships"],
                                   "added_relationships": []}
    report = build_simulation_report(
        scenario=scenario, changeset=output["changes"], current_twin=current,
        future_twin=output["future_twin"], state_diff=state_diff,
        analysis_result=output["analysis"],
        affected_resources={"affected": output["affected_resources"]},
        explanation={"source": "deterministic", "summary": output["analysis"]["risk"]["explanation"]},
        datasource_mode="Local Fixture Mode", source="S05", generated_at=NOW,
    )
    for expected in ("Affected Resources", "Orders", "RULE-RES-003", "11 / 100",
                     "Remediation", "Source: deterministic", "Risk Score</td><td>COMPLETE"):
        assert expected in report.html
