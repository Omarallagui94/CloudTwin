"""Data models for CloudTwin State Diff."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DiffChangeType(str, Enum):
    """Operation types for State Diff records."""
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    MODIFIED = "MODIFIED"
    REPLACED = "REPLACED"


@dataclass(frozen=True)
class ResourceDiffRecord:
    """Represents a discrete property or resource mutation between Current and Future twins.

    Adheres strictly to schema/v1/diff.schema.json item definition.
    """

    resource_id: str
    change_type: DiffChangeType
    property: str | None = None
    before: Any = None
    after: Any = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary matching diff.schema.json."""
        data: dict[str, Any] = {
            "resource_id": self.resource_id,
            "change_type": self.change_type.value,
        }
        if self.property is not None:
            data["property"] = self.property
        if self.before is not None:
            data["before"] = self.before
        if self.after is not None:
            data["after"] = self.after
        return data


@dataclass
class StateDiff:
    """Collection of difference records representing the delta between Current and Future twins."""

    schema_version: str = "1.0.0"
    changes: list[ResourceDiffRecord] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary conforming to diff.schema.json."""
        return {
            "schema_version": self.schema_version,
            "changes": [c.to_dict() for c in self.changes],
            "summary": self.summary or self._compute_summary(),
        }

    def _compute_summary(self) -> dict[str, int]:
        counts = {
            "total_changes": len(self.changes),
            "added": sum(1 for c in self.changes if c.change_type == DiffChangeType.ADDED),
            "removed": sum(1 for c in self.changes if c.change_type == DiffChangeType.REMOVED),
            "modified": sum(1 for c in self.changes if c.change_type == DiffChangeType.MODIFIED),
            "replaced": sum(1 for c in self.changes if c.change_type == DiffChangeType.REPLACED),
        }
        return counts

    def __len__(self) -> int:
        return len(self.changes)

    def __iter__(self):
        return iter(self.changes)
