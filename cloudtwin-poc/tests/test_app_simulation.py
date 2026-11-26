"""Tests de la vue « Simulate Change » du dashboard (Streamlit AppTest, sans AWS ni LLM)."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from simulation.pipeline import simulate
from visualization.simulation_view import ATTACK_EDGE, HIGHLIGHT, attack_edges, impact_graph_html

APP = Path(__file__).parents[1] / "app.py"
S04 = next(Path(__file__).parents[1].joinpath("scenarios").glob("S04-*"))


def app_on_change_assistant() -> AppTest:
    app = AppTest.from_file(APP, default_timeout=30).run()
    app.radio[0].set_value("Change Assistant").run()
    assert not app.exception
    return app


def markdown_text(app: AppTest) -> str:
    return "\n".join(m.value for m in app.markdown)


def test_authoritative_frontend_is_the_five_page_dashboard():
    app = AppTest.from_file(APP, default_timeout=30).run()
    assert app.radio[0].options == [
        "Overview", "Infrastructure", "Current Twin", "Change Assistant", "Analysis"
    ]
    assert app.radio[0].value == "Overview"


def test_s04_fatma_rules_remain_available_in_integrated_pipeline():
    out = simulate(S04 / "current-twin.json", S04 / "proposed-plan.json")
    failures = {f["rule_id"]: f for f in out["analysis"]["findings"] if f["status"] == "fail"}
    assert failures["RULE-NET-003"]["severity"] == "critical"
    assert out["analysis"]["risk"]["score"] == 30


def test_change_assistant_exposes_all_scenarios_without_second_navigation():
    app = app_on_change_assistant()
    assert [option[:3] for option in app.selectbox[0].options] == ["S01", "S02", "S03", "S04", "S05"]
    assert app.chat_input[0].placeholder == "Describe a proposed infrastructure change"


def test_dashboard_does_not_require_ollama_to_load():
    app = AppTest.from_file(APP, default_timeout=30).run()
    assert not app.exception
    assert "CloudTwin AI" in markdown_text(app)


def test_impact_graph_highlights_the_changed_resource():
    out = simulate(S04 / "current-twin.json", S04 / "proposed-plan.json")
    page = impact_graph_html(out)
    assert HIGHLIGHT["changed"] in page and "sg-fixture-orders-db" in page


def test_attack_path_edges_come_from_iam_003():
    findings = [{"rule_id": "RULE-IAM-003", "status": "fail",
                 "evidence": {"new_paths": [{"path": ["aws:internet", "svc", "pod", "role"]}]}}]
    assert attack_edges(findings) == {("aws:internet", "svc"), ("svc", "pod"), ("pod", "role")}
    assert ATTACK_EDGE == HIGHLIGHT["changed"]
