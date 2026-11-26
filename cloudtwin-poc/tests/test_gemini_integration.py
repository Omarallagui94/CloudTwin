from __future__ import annotations

import pytest

from gemini.adapter import InterpretationError, interpret_change, sanitized_resources
from gemini.client import GeminiClient, GeminiIntegrationError
from gemini.schemas import RequestedPropertyChange, GeminiAnalysisExplanation, ParsedInfrastructureChange
from ingestion import ChangeIngester


def twin(*resources):
    return {"resources": list(resources), "relationships": []}


UI = {"id": "k8s:deployment:default:ui", "name": "ui", "type": "DEPLOYMENT",
      "provider": "kubernetes", "properties": {"namespace": "default", "replicas": 3}}
SERVICE = {"id": "k8s:service:default:ui", "name": "ui", "type": "SERVICE",
           "provider": "kubernetes", "properties": {"namespace": "default", "service_type": "ClusterIP"}}


class StubClient:
    def __init__(self, parsed=None, error=None, available=True):
        self.parsed, self.error, self.available = parsed, error, available

    def is_available(self):
        return self.available

    def parse_change_request(self, request, resources):
        if self.error:
            raise self.error
        return self.parsed


def parsed(**updates):
    data = dict(operation="SCALE", provider="kubernetes", resource_type="deployment",
                resource_name="ui", namespace="default",
                requested_changes=[RequestedPropertyChange(property="replicas", value=1)],
                summary="Scale UI", ambiguous=False,
                clarification_question=None)
    data.update(updates)
    return ParsedInfrastructureChange(**data)


def test_valid_parse_uses_current_twin_value_and_existing_changeset():
    result = interpret_change("Reduce ui replicas to 1", twin(UI), StubClient(parsed()))
    assert result.scenario["before"] == 3
    change = ChangeIngester(twin(UI)).ingest_scenario(result.scenario).changes[0]
    assert change.resource_id == UI["id"] and change.before == 3 and change.after == 1


def test_exposure_builds_compound_canonical_change():
    proposal = parsed(operation="EXPOSE", resource_type="service",
                      requested_changes=[RequestedPropertyChange(property="service_type", value="LoadBalancer"),
                                         RequestedPropertyChange(property="scheme", value="internet-facing")])
    result = interpret_change("Expose ui", twin(SERVICE), StubClient(proposal))
    assert result.scenario["property"] == "service_type_and_scheme"
    assert result.scenario["before"]["service_type"] == "ClusterIP"


def test_ambiguous_target_never_guesses():
    duplicate = {**UI, "id": "k8s:deployment:other:ui", "properties": {"namespace": "other", "replicas": 2}}
    with pytest.raises(InterpretationError, match="Multiple resources"):
        interpret_change("scale ui", twin(UI, duplicate), StubClient(parsed(namespace=None)))


def test_model_declared_ambiguity_stops_simulation():
    proposal = parsed(operation="UNKNOWN", ambiguous=True, clarification_question="Which database?")
    with pytest.raises(InterpretationError, match="Which database"):
        interpret_change("Make database dangerous", twin(UI), StubClient(proposal))


def test_missing_key_uses_deterministic_fallback():
    result = interpret_change("Scale ui from 3 to 1", twin(UI), StubClient(available=False))
    assert not result.used_gemini and result.scenario["after"] == 1
    assert "CLIENT_INITIALIZATION" in result.gemini_diagnostic


@pytest.mark.parametrize("error", [TimeoutError(), RuntimeError("quota"), ValueError("malformed")])
def test_api_failures_are_safe_and_fallback(error):
    result = interpret_change("Scale ui from 3 to 1", twin(UI), StubClient(error=error))
    assert result.scenario["before"] == 3 and not result.used_gemini


def test_unsupported_fallback_returns_safe_error():
    with pytest.raises(InterpretationError, match="No infrastructure changes"):
        interpret_change("Delete everything and run terraform destroy", twin(UI), StubClient(error=TimeoutError()))


def test_sanitized_context_excludes_properties_and_secrets():
    secret = {"id": "secret", "name": "password", "type": "SECRET", "provider": "kubernetes",
              "properties": {"value": "never-send"}}
    context = sanitized_resources(twin(UI, secret))
    assert context == [{"id": UI["id"], "provider": "kubernetes", "type": "DEPLOYMENT",
                        "name": "ui", "namespace": "default"}]


def test_explanation_cannot_overwrite_score_or_severity():
    explanation = GeminiAnalysisExplanation(summary="Deterministic result", what_changes="UI scales",
        impact="One deployment", why_it_is_risky="Replica threshold", affected_components=["ui"],
        recommended_actions=["Keep two replicas"])
    assert "score" not in explanation.model_dump() and "severity" not in explanation.model_dump()


def test_client_missing_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert not GeminiClient().is_available()


@pytest.mark.parametrize("prompt", [
    "Expose the ui service publicly",
    "Make the ui accessible from the internet",
    "Put the ui behind a public load balancer",
    "Allow external users to access the ui",
])
def test_public_exposure_paraphrases_use_gemini(prompt):
    proposal = parsed(operation="EXPOSE", resource_type="service",
        requested_changes=[RequestedPropertyChange(property="service_type", value="LoadBalancer"),
                           RequestedPropertyChange(property="scheme", value="internet-facing")])
    result = interpret_change(prompt, twin(SERVICE), StubClient(proposal))
    assert result.used_gemini and result.provider == "Gemini"
    assert result.scenario["target_resource"] == SERVICE["id"]


@pytest.mark.parametrize(("prompt", "name", "replicas"), [
    ("Run only one replica of the ui deployment", "ui", 1),
    ("Scale ui to 4 replicas", "ui", 4),
    ("Increase catalog to 5 replicas", "catalog", 5),
])
def test_scaling_paraphrases_resolve_active_twin(prompt, name, replicas):
    catalog = {**UI, "id": "k8s:deployment:default:catalog", "name": "catalog",
               "properties": {"namespace": "default", "replicas": 2}}
    proposal = parsed(resource_name=name,
        requested_changes=[RequestedPropertyChange(property="replicas", value=replicas)])
    result = interpret_change(prompt, twin(UI, catalog), StubClient(proposal))
    target = UI if name == "ui" else catalog
    assert result.used_gemini and result.scenario["target_resource"] == target["id"]
    assert result.scenario["before"] == target["properties"]["replicas"]
    assert result.scenario["after"] == replicas


def test_delete_intent_uses_existing_resource_and_no_model_current_value():
    proposal = parsed(operation="DELETE", resource_name="ui", requested_changes=[])
    result = interpret_change("Delete the ui deployment", twin(UI), StubClient(proposal))
    assert result.scenario["operation"] == "delete"
    assert result.scenario["before"] == "present"


def test_namespace_resolution_is_exact_and_case_insensitive_name_is_safe():
    other = {**UI, "id": "k8s:deployment:other:UI", "name": "UI",
             "properties": {"namespace": "other", "replicas": 6}}
    result = interpret_change("scale UI in other", twin(UI, other),
                              StubClient(parsed(resource_name="UI", namespace="other")))
    assert result.scenario["target_resource"] == other["id"]


class FakeModels:
    def __init__(self, response=None, error=None):
        self.response, self.error = response, error
    def generate_content(self, **kwargs):
        if self.error:
            raise self.error
        return self.response


def test_official_client_accepts_sdk_parsed_structured_output():
    sdk = type("SDK", (), {"models": FakeModels(type("Response", (), {"parsed": parsed(), "text": None})())})()
    result = GeminiClient(api_key="test-only", sdk_client=sdk).parse_change_request("scale ui", [])
    assert result.operation == "SCALE"


def test_malformed_structured_output_reports_schema_stage():
    sdk = type("SDK", (), {"models": FakeModels(type("Response", (), {"parsed": {"bad": True}, "text": None})())})()
    with pytest.raises(GeminiIntegrationError) as caught:
        GeminiClient(api_key="test-only", sdk_client=sdk).parse_change_request("scale ui", [])
    assert caught.value.stage == "SCHEMA_VALIDATION"


def test_timeout_reports_api_request_stage():
    sdk = type("SDK", (), {"models": FakeModels(error=TimeoutError("timed out"))})()
    with pytest.raises(GeminiIntegrationError) as caught:
        GeminiClient(api_key="test-only", sdk_client=sdk).parse_change_request("scale ui", [])
    assert caught.value.stage == "API_REQUEST"
