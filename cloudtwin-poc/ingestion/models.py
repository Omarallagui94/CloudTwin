"""Data models and custom exceptions for proposed-change ingestion."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OperationType(str, Enum):
    """Normalized infrastructure operation types."""
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    REPLACE = "replace"


class IngestionError(Exception):
    """Base exception for change ingestion errors."""


class UnresolvedTargetError(IngestionError):
    """Raised when a proposed change references a resource that cannot be resolved."""


class InvalidChangeInputError(IngestionError):
    """Raised when input change payload fails validation or structure checks."""


class ConflictError(IngestionError):
    """Raised when expected prior state does not match current state."""


@dataclass(frozen=True)
class NormalizedChange:
    """Canonical representation of an individual proposed infrastructure change.

    Attributes:
        change_id: Unique identifier for this change.
        operation: Standardized operation (create, update, delete, replace).
        resource_id: Canonical CloudTwin resource ID (e.g. 'k8s:deployment:default:ui').
        resource_type: Upper-cased schema resource type (e.g. 'DEPLOYMENT', 'SUBNET').
        provider: 'kubernetes' or 'aws'.
        property_path: Specific dot-separated property path (e.g. 'properties.replicas').
        before: Previous/current property value, or None for create.
        after: Proposed new property value, or None for delete.
        source: Provenance metadata (kind, reference, file, etc.).
        namespace: Kubernetes namespace if applicable, otherwise None.
    """

    change_id: str
    operation: OperationType
    resource_id: str
    resource_type: str
    provider: str
    property_path: str | None
    before: Any
    after: Any
    source: dict[str, Any]
    namespace: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary following the integration contract."""
        data: dict[str, Any] = {
            "change_id": self.change_id,
            "operation": self.operation.value,
            "resource_id": self.resource_id,
            "resource_type": self.resource_type,
            "provider": self.provider,
            "property_path": self.property_path,
            "before": self.before,
            "after": self.after,
            "source": self.source,
        }
        if self.namespace is not None:
            data["namespace"] = self.namespace
        return data


@dataclass
class ChangeSet:
    """A collection of normalized changes produced from an ingested change source."""

    schema_version: str = "1.0.0"
    source_kind: str = "scenario"
    changes: list[NormalizedChange] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_kind": self.source_kind,
            "metadata": self.metadata,
            "changes": [c.to_dict() for c in self.changes],
        }

    def __len__(self) -> int:
        return len(self.changes)

    def __iter__(self):
        return iter(self.changes)
