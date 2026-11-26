"""Resolve Gemini intent against Current Twin and feed the existing scenario contract."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

from change_assistant import ParsedChangeRequest, UnsupportedChangeRequest, parse_change_request
from gemini.client import GeminiClient, GeminiIntegrationError
from gemini.schemas import ParsedInfrastructureChange
from ingestion.resolver import normalize_resource_type

LOGGER = logging.getLogger(__name__)


class InterpretationError(ValueError):
    def __init__(self, message: str, stage: str = "CANONICALIZATION") -> None:
        super().__init__(message)
        self.safe_message, self.stage = message, stage


@dataclass(frozen=True)
class ChangeInterpretation:
    scenario: dict[str, Any]
    summary: str
    provider: str
    used_gemini: bool
    gemini_diagnostic: str | None = None


def sanitized_resources(twin: dict[str, Any], request: str = "") -> list[dict[str, Any]]:
    """Expose identifiers and safe matching fields only; never properties or secret values."""
    catalog = [{
        "id": r.get("id"), "provider": r.get("provider"), "type": r.get("type"),
        "name": r.get("name"), "namespace": (r.get("properties") or {}).get("namespace"),
    } for r in twin.get("resources", []) if r.get("type") != "SECRET"]
    words = {word.strip(".,:;!?()[]{}\"'").lower() for word in request.split()}
    named = [item for item in catalog if str(item.get("name", "")).lower() in words]
    if named:
        return named[:100]
    # A bounded catalogue lets Gemini identify ambiguity without receiving a full twin.
    relevant = [item for item in catalog if item.get("type") in {
        "DEPLOYMENT", "STATEFUL_SET", "SERVICE", "SECURITY_GROUP", "RDS_INSTANCE",
        "LOAD_BALANCER", "IAM_ROLE",
    }]
    return relevant[:100]


def interpret_change(text: str, twin: dict[str, Any], client: GeminiClient | None = None) -> ChangeInterpretation:
    client = client or GeminiClient()
    diagnostic = None
    if client.is_available():
        try:
            parsed = client.parse_change_request(text, sanitized_resources(twin, text))
            return _canonicalize(text, parsed, twin)
        except InterpretationError as exc:
            LOGGER.warning("Gemini interpretation failed stage=%s exception=%s message=%s model=%s elapsed_ms=0",
                           exc.stage, type(exc).__name__, exc.safe_message,
                           getattr(client, "model", "unknown"))
            raise
        except GeminiIntegrationError as exc:
            diagnostic = f"Gemini parsing unavailable during {exc.stage}: {exc.safe_message}"
            LOGGER.info("Deterministic fallback activated after %s", exc.stage)
        except Exception as exc:  # custom/test clients still fail closed with a useful stage
            diagnostic = f"Gemini parsing unavailable during API_REQUEST: {type(exc).__name__}"
            LOGGER.warning("Gemini parsing failed stage=API_REQUEST exception=%s message=%s model=%s elapsed_ms=0",
                           type(exc).__name__, str(exc)[:300], getattr(client, "model", "unknown"))
    else:
        diagnostic = "Gemini parsing unavailable during CLIENT_INITIALIZATION: GEMINI_API_KEY is not configured"
        LOGGER.info("Gemini unavailable stage=CLIENT_INITIALIZATION model=%s; deterministic fallback activated",
                    getattr(client, "model", "unknown"))
    try:
        fallback: ParsedChangeRequest = parse_change_request(text)
    except UnsupportedChangeRequest as exc:
        raise InterpretationError(
            f"{diagnostic or 'Gemini could not interpret this request'}. "
            "The deterministic parser did not match it. "
            "No infrastructure changes were performed."
        ) from exc
    return ChangeInterpretation(fallback.scenario, fallback.summary, "deterministic fallback", False,
                                diagnostic)


def _canonicalize(text: str, parsed: ParsedInfrastructureChange,
                  twin: dict[str, Any]) -> ChangeInterpretation:
    if parsed.ambiguous or parsed.operation == "UNKNOWN":
        raise InterpretationError(parsed.clarification_question or "The request is ambiguous. Please identify one resource and change.",
                                  stage="RESOURCE_RESOLUTION")
    resource = _resolve(parsed, twin)
    operation = "delete" if parsed.operation == "DELETE" else "update"
    props = resource.get("properties") or {}
    if operation == "delete":
        property_name, before, after = "resource", "present", "absent"
    else:
        if not parsed.requested_changes:
            raise InterpretationError("Gemini did not identify a proposed property change.",
                                      stage="CANONICALIZATION")
        desired = parsed.requested_changes
        names = {d.property.lower().replace("-", "_") for d in desired}
        if {"service_type", "scheme"}.issubset(names) or parsed.operation == "EXPOSE":
            property_name = "service_type_and_scheme"
            before = {"service_type": props.get("service_type"), "scheme": props.get("scheme", "internal")}
            values = {d.property.lower().replace("-", "_"): d.value for d in desired}
            after = {"service_type": values.get("service_type", "LoadBalancer"),
                     "scheme": values.get("scheme", "internet-facing")}
        elif len(desired) == 1:
            property_name = desired[0].property.removeprefix("properties.")
            before = props.get(property_name)
            after = desired[0].value
        else:
            raise InterpretationError("The request contains multiple unrelated property changes; please submit one change at a time.",
                                      stage="CANONICALIZATION")
    scenario = {
        "scenario_id": "GEMINI-" + hashlib.sha256(text.encode()).hexdigest()[:8].upper(),
        "name": parsed.summary,
        "description": text,
        "target_resource": resource["id"],
        "resource_type": resource["type"],
        "namespace": props.get("namespace", "default"),
        "operation": operation,
        "property": property_name,
        "before": before,
        "after": after,
    }
    return ChangeInterpretation(scenario, parsed.summary, "Gemini", True)


def _resolve(parsed: ParsedInfrastructureChange, twin: dict[str, Any]) -> dict[str, Any]:
    resources = twin.get("resources", [])
    expected_type = None
    if parsed.resource_type:
        try:
            expected_type = normalize_resource_type(parsed.resource_type)[0]
        except ValueError:
            expected_type = parsed.resource_type.upper()
    name = (parsed.resource_name or "").lower()
    matches = [r for r in resources
               if (not name or str(r.get("name", "")).lower() == name)
               and (not expected_type or r.get("type") == expected_type)
               and (parsed.provider == "unknown" or r.get("provider") == parsed.provider)
               and (not parsed.namespace or (r.get("properties") or {}).get("namespace", "default") == parsed.namespace)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        choices = ", ".join(r["id"] for r in matches[:8])
        raise InterpretationError(f"Multiple resources match the request: {choices}. Please be more specific.",
                                  stage="RESOURCE_RESOLUTION")
    raise InterpretationError("The requested target was not found in the active Current Twin.",
                              stage="RESOURCE_RESOLUTION")
