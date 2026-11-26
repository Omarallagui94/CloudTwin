"""Ingester for CloudTwin Scenario JSON payloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ingestion.models import (
    ChangeSet,
    ConflictError,
    InvalidChangeInputError,
    NormalizedChange,
    OperationType,
    UnresolvedTargetError,
)
from ingestion.resolver import CanonicalResourceResolver


class ScenarioIngester:
    """Ingests scenario JSON documents (like scenario-01.json) into normalized change records."""

    def __init__(self, resolver: CanonicalResourceResolver | None = None) -> None:
        self.resolver = resolver or CanonicalResourceResolver()

    def ingest(
        self,
        scenario: dict[str, Any] | str | Path,
        strict_conflict_check: bool = False,
    ) -> ChangeSet:
        """Parse and normalize a scenario JSON payload.

        Args:
            scenario: Dict, JSON string, or Path to scenario JSON file.
            strict_conflict_check: When True, asserts that Current Twin's existing value
                                   matches scenario's 'before' value.

        Returns:
            ChangeSet containing the normalized change record(s).
        """
        data = self._load_json(scenario)
        self._validate_scenario_structure(data)

        scenario_id = data["scenario_id"]
        scenario_name = data.get("name", "Unnamed Scenario")
        raw_type = data["resource_type"]
        # Omar's maintained S01-S05 pack uses a canonical target_resource and
        # flat change fields. Hamdi's original nested shape remains accepted at
        # this ingestion boundary for backwards-compatible local fixtures.
        resource_name = data.get("target_resource", data.get("resource"))
        raw_namespace = data.get("namespace", "default")
        change_data = data.get("change") or {
            "property": data.get("property"),
            "before": data.get("before"),
            "after": data.get("after"),
        }
        raw_property = change_data["property"]
        before_val = change_data.get("before")
        after_val = change_data.get("after")

        # Determine operation
        op_str = data.get("operation")
        if op_str:
            try:
                operation = OperationType(op_str.lower())
            except ValueError:
                raise InvalidChangeInputError(f"Unsupported scenario operation: '{op_str}'")
        else:
            if before_val is None and after_val is not None:
                operation = OperationType.CREATE
            elif before_val is not None and after_val is None:
                operation = OperationType.DELETE
            else:
                operation = OperationType.UPDATE

        # Resolve target canonical ID
        canonical_id, canonical_type, provider, resolved_ns = self.resolver.resolve_scenario_target(
            resource_type_str=raw_type,
            resource_name=resource_name,
            namespace=raw_namespace,
        )

        # Property path normalization
        prop_path = (
            raw_property
            if raw_property.startswith("properties.")
            else f"properties.{raw_property}"
        )

        # Conflict check if requested
        if strict_conflict_check and operation == OperationType.UPDATE:
            existing = self.resolver.resources_by_id.get(canonical_id)
            if existing:
                prop_name = raw_property.split(".")[-1]
                current_val = existing.get("properties", {}).get(prop_name)
                if current_val is not None and current_val != before_val:
                    raise ConflictError(
                        f"Conflict for {canonical_id}.{prop_path}: expected '{before_val}', found '{current_val}'."
                    )

        change_id = f"change-{scenario_id.lower()}-001"
        source_meta = {
            "kind": "scenario",
            "reference": scenario_id,
            "name": scenario_name,
            "description": data.get("description"),
        }

        norm_change = NormalizedChange(
            change_id=change_id,
            operation=operation,
            resource_id=canonical_id,
            resource_type=canonical_type,
            provider=provider,
            property_path=prop_path,
            before=before_val,
            after=after_val,
            source=source_meta,
            namespace=resolved_ns,
        )

        return ChangeSet(
            schema_version="1.0.0",
            source_kind="scenario",
            changes=[norm_change],
            metadata={
                "scenario_id": scenario_id,
                "name": scenario_name,
            },
        )

    @staticmethod
    def _load_json(scenario: dict[str, Any] | str | Path) -> dict[str, Any]:
        if isinstance(scenario, dict):
            return scenario
        if isinstance(scenario, Path):
            return json.loads(scenario.read_text(encoding="utf-8"))
        if isinstance(scenario, str):
            try:
                p = Path(scenario)
                if p.is_file():
                    return json.loads(p.read_text(encoding="utf-8"))
            except OSError:
                pass
            return json.loads(scenario)
        raise InvalidChangeInputError(f"Unsupported scenario input type: {type(scenario)}")

    @staticmethod
    def _validate_scenario_structure(data: dict[str, Any]) -> None:
        required_fields = ["scenario_id", "resource_type"]
        missing = [f for f in required_fields if f not in data]
        if missing:
            raise InvalidChangeInputError(f"Scenario missing required field(s): {missing}")

        if not data.get("target_resource") and not data.get("resource"):
            raise InvalidChangeInputError("Scenario requires 'target_resource' or 'resource'")

        change = data.get("change") or {
            "property": data.get("property"),
            "before": data.get("before"),
            "after": data.get("after"),
        }
        if not isinstance(change, dict):
            raise InvalidChangeInputError("'change' field must be an object")

        if "property" not in change or not change["property"]:
            raise InvalidChangeInputError("'change.property' must be a non-empty string")
