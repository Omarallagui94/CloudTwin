"""Ingester for machine-generated Terraform plan JSON files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ingestion.models import (
    ChangeSet,
    InvalidChangeInputError,
    NormalizedChange,
    OperationType,
    UnresolvedTargetError,
)
from ingestion.resolver import CanonicalResourceResolver


class TerraformPlanIngester:
    """Ingests Terraform plan JSON documents into normalized change records."""

    def __init__(self, resolver: CanonicalResourceResolver | None = None) -> None:
        self.resolver = resolver or CanonicalResourceResolver()

    def ingest(self, plan: dict[str, Any] | str | Path) -> ChangeSet:
        """Parse and normalize a Terraform plan JSON payload.

        Args:
            plan: Dict, JSON string, or Path to terraform plan JSON file.

        Returns:
            ChangeSet containing normalized change records.
        """
        data = self._load_json(plan)
        resource_changes = data.get("resource_changes")
        if not isinstance(resource_changes, list):
            raise InvalidChangeInputError(
                "Invalid Terraform plan JSON: missing 'resource_changes' list."
            )

        changes: list[NormalizedChange] = []
        change_idx = 1

        for rc in resource_changes:
            # Skip non-managed items
            if rc.get("mode") != "managed":
                continue

            address = rc.get("address", "unknown")
            tf_type = rc.get("type")
            tf_name = rc.get("name")
            change_obj = rc.get("change") or {}
            actions = change_obj.get("actions", [])

            if not tf_type or not tf_name:
                raise InvalidChangeInputError(
                    f"Terraform resource change missing type or name at {address}"
                )

            # Determine operation type
            operation = self._map_actions_to_operation(actions)
            if operation is None:
                # no-op or read
                continue

            before_state = change_obj.get("before")
            after_state = change_obj.get("after")

            is_create = (operation == OperationType.CREATE)

            canonical_id, canonical_type, provider = self.resolver.resolve_terraform_target(
                tf_type=tf_type,
                tf_name=tf_name,
                before_state=before_state,
                after_state=after_state,
                is_create=is_create,
            )

            source_meta = {
                "kind": "terraform_plan",
                "reference": address,
                "type": tf_type,
                "name": tf_name,
                "actions": actions,
            }

            if operation == OperationType.CREATE:
                changes.append(
                    NormalizedChange(
                        change_id=f"change-tf-{change_idx:03d}",
                        operation=OperationType.CREATE,
                        resource_id=canonical_id,
                        resource_type=canonical_type,
                        provider=provider,
                        property_path="properties",
                        before=None,
                        after=after_state,
                        source=source_meta,
                        namespace=None,
                    )
                )
                change_idx += 1

            elif operation == OperationType.DELETE:
                changes.append(
                    NormalizedChange(
                        change_id=f"change-tf-{change_idx:03d}",
                        operation=OperationType.DELETE,
                        resource_id=canonical_id,
                        resource_type=canonical_type,
                        provider=provider,
                        property_path="properties",
                        before=before_state,
                        after=None,
                        source=source_meta,
                        namespace=None,
                    )
                )
                change_idx += 1

            elif operation == OperationType.REPLACE:
                # Replacement represents a delete-then-create of the resource
                changes.append(
                    NormalizedChange(
                        change_id=f"change-tf-{change_idx:03d}",
                        operation=OperationType.REPLACE,
                        resource_id=canonical_id,
                        resource_type=canonical_type,
                        provider=provider,
                        property_path="properties",
                        before=before_state,
                        after=after_state,
                        source=source_meta,
                        namespace=None,
                    )
                )
                change_idx += 1

            elif operation == OperationType.UPDATE:
                # Extract individual property differences
                diffs = self._extract_property_diffs(before_state or {}, after_state or {})
                if not diffs:
                    # Generic update if no specific field diff was identified
                    changes.append(
                        NormalizedChange(
                            change_id=f"change-tf-{change_idx:03d}",
                            operation=OperationType.UPDATE,
                            resource_id=canonical_id,
                            resource_type=canonical_type,
                            provider=provider,
                            property_path="properties",
                            before=before_state,
                            after=after_state,
                            source=source_meta,
                            namespace=None,
                        )
                    )
                    change_idx += 1
                else:
                    for prop_path, b_val, a_val in diffs:
                        changes.append(
                            NormalizedChange(
                                change_id=f"change-tf-{change_idx:03d}",
                                operation=OperationType.UPDATE,
                                resource_id=canonical_id,
                                resource_type=canonical_type,
                                provider=provider,
                                property_path=f"properties.{prop_path}",
                                before=b_val,
                                after=a_val,
                                source=source_meta,
                                namespace=None,
                            )
                        )
                        change_idx += 1

        return ChangeSet(
            schema_version="1.0.0",
            source_kind="terraform_plan",
            changes=changes,
            metadata={
                "format_version": data.get("format_version"),
                "terraform_version": data.get("terraform_version"),
                "total_changes": len(changes),
            },
        )

    @staticmethod
    def _map_actions_to_operation(actions: list[str]) -> OperationType | None:
        action_set = set(actions)
        if action_set in ({"no-op"}, {"read"}):
            return None
        if action_set == {"create"}:
            return OperationType.CREATE
        if action_set == {"update"}:
            return OperationType.UPDATE
        if action_set == {"delete"}:
            return OperationType.DELETE
        if "delete" in action_set and "create" in action_set:
            return OperationType.REPLACE
        raise InvalidChangeInputError(f"Unrecognized Terraform action set: {actions}")

    @staticmethod
    def _extract_property_diffs(
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> list[tuple[str, Any, Any]]:
        diffs = []
        all_keys = set(before.keys()).union(after.keys())
        # Ignore internal Terraform keys
        ignored_keys = {"id", "arn", "tags_all"}

        for k in sorted(all_keys):
            if k in ignored_keys:
                continue
            b_val = before.get(k)
            a_val = after.get(k)
            if b_val != a_val:
                diffs.append((k, b_val, a_val))
        return diffs

    @staticmethod
    def _load_json(plan: dict[str, Any] | str | Path) -> dict[str, Any]:
        if isinstance(plan, dict):
            return plan
        if isinstance(plan, Path):
            return json.loads(plan.read_text(encoding="utf-8"))
        if isinstance(plan, str):
            try:
                p = Path(plan)
                if p.is_file():
                    return json.loads(p.read_text(encoding="utf-8"))
            except OSError:
                pass
            return json.loads(plan)
        raise InvalidChangeInputError(f"Unsupported plan input type: {type(plan)}")
