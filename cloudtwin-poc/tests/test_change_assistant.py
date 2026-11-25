import pytest

from change_assistant import UnsupportedChangeRequest, parse_change_request


def test_parses_scale_ui_from_one_to_four():
    parsed = parse_change_request("Scale the UI deployment from 1 to 4 replicas")
    scenario = parsed.scenario
    assert scenario["target_resource"] == "k8s:deployment:default:ui"
    assert scenario["operation"] == "update"
    assert scenario["property"] == "replicas"
    assert scenario["before"] == 1
    assert scenario["after"] == 4


def test_parses_reduce_ui_from_three_to_one():
    parsed = parse_change_request("Reduce UI replicas from 3 to 1")
    scenario = parsed.scenario
    assert scenario["target_resource"] == "k8s:deployment:default:ui"
    assert scenario["before"] == 3
    assert scenario["after"] == 1


@pytest.mark.parametrize("change_text", [
    "Make everything more resilient",
    "Scale some deployment",
    "Open PostgreSQL port 5432 to the internet",
    "",
])
def test_unsupported_language_fails_without_guessing(change_text):
    with pytest.raises(UnsupportedChangeRequest):
        parse_change_request(change_text)
