from pathlib import Path

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).parents[1] / "app.py"


def _change_assistant():
    app = AppTest.from_file(APP_PATH, default_timeout=15).run()
    app.radio[0].set_value("Change Assistant").run()
    return app


def test_overview_is_professor_ready_and_defaults_to_safe_fixture_mode():
    app = AppTest.from_file(APP_PATH, default_timeout=15).run()
    assert not app.exception
    assert app.radio[0].value == "Overview"
    assert app.radio[1].value == "Local Fixture Mode"
    markdown = "\n".join(item.value for item in app.markdown)
    assert "CloudTwin AI" in markdown
    assert "Future Twin" in markdown
    assert "State Diff" in markdown


def test_change_assistant_loads_s01_to_s05_dynamically():
    app = _change_assistant()
    assert not app.exception
    assert [option[:3] for option in app.selectbox[0].options] == [
        "S01", "S02", "S03", "S04", "S05"
    ]
    assert app.chat_input[0].placeholder == "Describe a proposed infrastructure change"


def test_current_twin_defaults_to_application_view_and_exposes_all_views():
    app = AppTest.from_file(APP_PATH, default_timeout=15).run()
    app.radio[0].set_value("Current Twin").run()
    assert not app.exception
    graph_view = next(selectbox for selectbox in app.selectbox if selectbox.label == "Graph view")
    assert graph_view.value == "Application View"
    assert list(graph_view.options) == [
        "Application View", "Kubernetes View", "AWS Infrastructure View", "Full Infrastructure"
    ]
    assert any("2 visible nodes / 1 relationships" in caption.value for caption in app.caption)


def test_current_twin_view_switches_do_not_raise_graph_exceptions():
    app = AppTest.from_file(APP_PATH, default_timeout=15).run()
    app.radio[0].set_value("Current Twin").run()
    graph_view = next(selectbox for selectbox in app.selectbox if selectbox.label == "Graph view")
    graph_view.set_value("Kubernetes View").run()
    assert not app.exception
    graph_view = next(selectbox for selectbox in app.selectbox if selectbox.label == "Graph view")
    graph_view.set_value("Full Infrastructure").run()
    assert not app.exception


def test_s01_confirmed_analysis_is_pass_with_potential_severity_only():
    app = _change_assistant()
    app.button[0].click().run()  # Scale UI Up
    app.button[6].click().run()  # Confirm for Analysis
    page_markdown = "\n".join(item.value for item in app.markdown)
    assert any(item.value == "Simulation Result" for item in app.header)
    assert "State Diff" in page_markdown
    assert "CURRENT TWIN" in page_markdown
    assert "FUTURE TWIN — SIMULATED" in page_markdown
    assert ">1<" in page_markdown and ">3<" in page_markdown
    app.radio[0].set_value("Analysis").run()
    assert not app.exception
    assert any("Aucun risque actif" in item.value for item in app.success)
    markdown = "\n".join(item.value for item in app.markdown)
    assert 'class="finding"' not in markdown
    assert "Risque <span>faible</span>" in markdown          # bande de verdict
    assert any("vérifiée(s) sans risque" in item.label for item in app.expander)


def test_s02_confirmed_analysis_is_an_active_high_severity_finding():
    app = _change_assistant()
    app.button[1].click().run()  # Scale UI Down
    app.button[6].click().run()  # Confirm for Analysis
    page_markdown = "\n".join(item.value for item in app.markdown)
    assert ">3<" in page_markdown and ">1<" in page_markdown
    app.radio[0].set_value("Analysis").run()
    assert not app.exception
    markdown = "\n".join(item.value for item in app.markdown)
    assert 'class="finding"' in markdown and "RULE-RES-001" in markdown
    assert "Gravité élevée" in markdown


def test_custom_one_to_four_request_reaches_normalized_changeset_and_analysis():
    app = _change_assistant()
    app.chat_input[0].set_value("Scale the UI deployment from 1 to 4 replicas").run()
    assert any(button.label == "Confirm for Analysis" for button in app.button)
    confirm = next(button for button in app.button if button.label == "Confirm for Analysis")
    confirm.click().run()
    assert not app.exception
    assert any("Future Twin and State Diff generated" in item.value for item in app.success)
    app.radio[0].set_value("Analysis").run()
    assert any("Aucun risque actif" in item.value for item in app.success)
    markdown = "\n".join(item.value for item in app.markdown)
    assert ">1<" in markdown and ">4<" in markdown


def test_s05_simulation_removes_resource_and_two_relationships():
    app = _change_assistant()
    next(button for button in app.button if button.label == "Remove Critical Dependency").click().run()
    next(button for button in app.button if button.label == "Confirm for Analysis").click().run()
    assert not app.exception
    markdown = "\n".join(item.value for item in app.markdown)
    assert "Orders PostgreSQL Service" in markdown
    assert "PRESENT" in markdown and "REMOVED" in markdown
    assert markdown.count("Removed relationship") == 2
    assert "depends on" in markdown and "exposes" in markdown
    assert [tab.label for tab in app.tabs[-5:]] == [
        "Summary", "Diff View", "Current Twin", "Future Twin", "Technical Details"
    ]
    assert {item.label for item in app.expander} >= {
        "View normalized ChangeSet", "View raw State Diff",
        "View raw Current resource", "View raw Future resource",
    }


def test_s01_report_action_generates_preview_and_local_html_download():
    app = _change_assistant()
    next(button for button in app.button if button.label == "Scale UI Up").click().run()
    next(button for button in app.button if button.label == "Confirm for Analysis").click().run()
    assert any(button.label == "View Diff Graph" for button in app.button)
    next(button for button in app.button if button.label == "Generate Simulation Report").click().run()
    assert not app.exception
    assert any("Simulation Report Preview" in item.value for item in app.markdown)
    downloads = app.get("download_button")
    assert len(downloads) == 1
    assert downloads[0].label == "Download Report"
    report = app.session_state["simulation_report"]
    assert report["filename"].startswith("cloudtwin-simulation-S01-")
    assert report["filename"].endswith(".html")
    assert "SIMULATION ONLY" in report["html"]


# --------------------------------------------- Analysis : IA, correctifs, chemin d'attaque

def _analysis_for(button_index: int) -> AppTest:
    app = _change_assistant()
    app.button[button_index].click().run()
    app.button[6].click().run()  # Confirm for Analysis
    app.radio[0].set_value("Analysis").run()
    return app


def test_security_scenarios_s03_and_s04_reach_the_analysis_page():
    """S03 et S04 modifient une propriété absente de leur fixture : la simulation doit aboutir."""
    for index, rule in ((2, "RULE-NET-004"), (3, "RULE-NET-003")):
        app = _analysis_for(index)
        assert not app.exception
        assert "confirmation_error" not in app.session_state
        cards = [m.value for m in app.markdown if 'class="finding"' in m.value]
        assert any(rule in card for card in cards)


def test_analysis_page_links_risk_impact_ai_and_verified_fixes():
    app = _analysis_for(3)  # S04 : PostgreSQL ouvert à Internet
    assert not app.exception
    labels = [tab.label for tab in app.tabs]
    assert labels[:5] == ["Risque", "Impact et chemin d'attaque", "Analyste IA", "Correctifs vérifiés", "Simulation"]
    verdict = next(m.value for m in app.markdown if 'class="verdict"' in m.value)
    assert '<span class="num">30</span>' in verdict and "Risque <span>moyen</span>" in verdict
    # Analyste IA (texte de secours, sans LLM) : il cite le score des règles, il ne le change pas.
    assert any(m.value.startswith("> Ce changement obtient 30/100") for m in app.markdown)
    # Correctifs rejoués dans le jumeau.
    assert app.dataframe
    assert any("30 → **0/100**" in m.value for m in app.markdown)
    pipeline = "\n".join(m.value for m in app.markdown)
    assert 'title="AI Explanation : terminée"' in pipeline
    assert 'title="Verified Fixes : terminée"' in pipeline


def test_llm_toggle_waits_for_click_and_falls_back_without_ollama():
    app = _analysis_for(3)
    app.toggle[0].set_value(True).run()
    app.number_input[0].set_value(5).run()
    ask = [b for b in app.button if b.label == "Demander à l'analyste IA"]
    assert ask, "avec Ollama activé, l'appel attend un clic"
    ask[0].click().run()
    assert not app.exception
    # Pas de serveur Ollama ici : le rapport sort quand même, avec le texte de secours.
    assert any(m.value.startswith("> Ce changement obtient 30/100") for m in app.markdown)
    assert any("30 → **0/100**" in m.value for m in app.markdown)


# ------------------------------------------------------------- Infrastructure

def test_infrastructure_shows_the_reference_lab_without_any_change():
    app = AppTest.from_file(APP_PATH, default_timeout=30).run()
    app.radio[0].set_value("Infrastructure").run()
    assert not app.exception
    assert any(m.label == "Resources" for m in app.metric) or any(
        "40</div>" in m.value for m in app.markdown)
    assert app.dataframe, "l'inventaire s'affiche sans changement proposé"
    assert any("Infrastructure de référence du labo" in c.value for c in app.caption)


def test_live_discovery_errors_are_explained_not_raised():
    from botocore.exceptions import EndpointConnectionError, NoCredentialsError
    from kubernetes.config import ConfigException

    import app as dashboard

    cause, action = dashboard.explain_discovery_error(NoCredentialsError())
    assert "identifiant" in cause and "aws configure" in action
    cause, action = dashboard.explain_discovery_error(ConfigException("Invalid kube-config file."))
    assert "kubeconfig" in cause and "update-kubeconfig" in action
    from botocore.exceptions import ClientError
    refused = ClientError({"Error": {"Code": "InvalidClientTokenId",
                                     "Message": "The security token included in the request is invalid"}},
                          "GetCallerIdentity")
    cause, action = dashboard.explain_discovery_error(refused)
    assert "refusés" in cause and "aws sts get-caller-identity" in action
    cause, _ = dashboard.explain_discovery_error(
        EndpointConnectionError(endpoint_url="https://sts.eu-west-3.amazonaws.com"))
    assert "ne répond pas" in cause
