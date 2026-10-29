from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_streamlit_loads_the_directory_based_scenario_pack():
    app_path = Path(__file__).parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=15).run()

    assert not app.exception
    assert len(app.selectbox) == 1
    assert [option[:3] for option in app.selectbox[0].options] == [
        "S01",
        "S02",
        "S03",
        "S04",
        "S05",
    ]
    assert [message.value for message in app.success] == [
        "PASS — no active resilience finding"
    ]
    assert any(
        "Potential rule severity if it fails: HIGH" in caption.value
        for caption in app.caption
    )


def test_streamlit_shows_s02_as_an_active_high_severity_finding():
    app_path = Path(__file__).parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=15).run()

    app.selectbox[0].set_value("S02").run()

    assert any(
        "ACTIVE HIGH-SEVERITY FINDING" in message.value
        for message in app.error
    )
