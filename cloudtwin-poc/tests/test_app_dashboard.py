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
    assert any("PASS" in item.value for item in app.success)
    assert not any("ACTIVE HIGH-SEVERITY" in item.value for item in app.error)
    assert any("Potential rule severity if violated: HIGH" in item.value for item in app.caption)


def test_s02_confirmed_analysis_is_an_active_high_severity_finding():
    app = _change_assistant()
    app.button[1].click().run()  # Scale UI Down
    app.button[6].click().run()  # Confirm for Analysis
    page_markdown = "\n".join(item.value for item in app.markdown)
    assert ">3<" in page_markdown and ">1<" in page_markdown
    app.radio[0].set_value("Analysis").run()
    assert not app.exception
    assert any("ACTIVE HIGH-SEVERITY FINDING" in item.value for item in app.error)


def test_custom_one_to_four_request_reaches_normalized_changeset_and_analysis():
    app = _change_assistant()
    app.chat_input[0].set_value("Scale the UI deployment from 1 to 4 replicas").run()
    assert any(button.label == "Confirm for Analysis" for button in app.button)
    confirm = next(button for button in app.button if button.label == "Confirm for Analysis")
    confirm.click().run()
    assert not app.exception
    assert any("Future Twin and State Diff generated" in item.value for item in app.success)
    app.radio[0].set_value("Analysis").run()
    assert any("PASS" in item.value for item in app.success)
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
