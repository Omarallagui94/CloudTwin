"""Tests de la vue « Simulate Change » du dashboard (Streamlit AppTest, sans AWS ni LLM)."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from simulation.pipeline import simulate
from visualization.simulation_view import ATTACK_EDGE, HIGHLIGHT, attack_edges, impact_graph_html

APP = Path(__file__).parents[1] / "app.py"
S04 = next(Path(__file__).parents[1].joinpath("scenarios").glob("S04-*"))


def app_on(scenario_id: str) -> AppTest:
    app = AppTest.from_file(APP, default_timeout=30).run()
    app.selectbox[0].set_value(scenario_id).run()
    assert not app.exception
    return app


def markdown_text(app: AppTest) -> str:
    return "\n".join(m.value for m in app.markdown)


def test_s04_shows_score_findings_diff_and_fixes():
    app = app_on("S04")
    text = markdown_text(app)
    assert "30/100 — Moyen" in text
    assert any("ACTIVE CRITICAL-SEVERITY FINDING — RULE-NET-003" in e.value for e in app.error)
    assert [t.label for t in app.tabs] == ["Risque", "Diff", "Impact", "Analyste IA", "Correctifs"]
    diff, fixes = app.dataframe[0].value, app.dataframe[-1].value
    assert list(diff["Propriété"]) == ["properties.ingress_rules"]   # format API AWS du scénario
    assert fixes["Score"].iloc[0] == "30 → 0" and fixes[""].iloc[0] == "✔"
    assert "Texte de secours" in "\n".join(c.value for c in app.caption)


def test_s04_can_be_simulated_from_the_terraform_plan():
    app = app_on("S04")
    assert app.radio[0].options == ["Scénario", "Plan Terraform (tfplan.json)"]
    app.radio[0].set_value("proposed-plan.json").run()
    assert not app.exception
    assert any("RULE-NET-003" in e.value for e in app.error)


def test_s03_manifest_is_not_evaluated_honestly():
    app = app_on("S03")
    app.radio[0].set_value("proposed-service.yaml").run()
    assert any("NOT EVALUATED — RULE-NET-004" in w.value for w in app.warning)
    assert "score minimum" in "\n".join(c.value for c in app.caption).lower()


def test_llm_is_off_by_default():
    app = AppTest.from_file(APP, default_timeout=30).run()
    assert app.toggle[0].value is False


def test_impact_graph_highlights_the_changed_resource():
    out = simulate(S04 / "current-twin.json", S04 / "proposed-plan.json")
    page = impact_graph_html(out)
    assert HIGHLIGHT["changed"] in page and "sg-fixture-orders-db" in page


def test_attack_path_edges_come_from_iam_003():
    findings = [{"rule_id": "RULE-IAM-003", "status": "fail",
                 "evidence": {"new_paths": [{"path": ["aws:internet", "svc", "pod", "role"]}]}}]
    assert attack_edges(findings) == {("aws:internet", "svc"), ("svc", "pod"), ("pod", "role")}
    assert ATTACK_EDGE == HIGHLIGHT["changed"]
