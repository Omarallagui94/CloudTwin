"""Data models for CloudTwin Affected-Resource Impact Analysis (Task H7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ImpactType(str, Enum):
    """Classification of impact on infrastructure resources."""
    DIRECT = "DIRECT"
    INDIRECT = "INDIRECT"
    RISK = "RISK"


@dataclass(frozen=True)
class EdgeTraversal:
    """Represents a single hop along a graph relationship edge during impact analysis."""
    source: str
    target: str
    rel_type: str
    direction: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "target": self.target,
            "rel_type": self.rel_type,
            "direction": self.direction,
        }


@dataclass
class AffectedResource:
    """Represents an affected resource with structural path, distance, and reasoning."""

    resource_id: str
    impact_type: ImpactType
    reason: str
    distance: int = 1
    path: list[str] = field(default_factory=list)
    source_change: str = ""
    edge_path: list[EdgeTraversal] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "resource_id": self.resource_id,
            "impact_type": self.impact_type.value,
            "reason": self.reason,
            "distance": self.distance,
            "path": list(self.path),
        }
        if self.source_change:
            data["source_change"] = self.source_change
        return data


@dataclass
class ImpactAnalysisResult:
    """Aggregated structural impact analysis for a scenario and state diff."""

    schema_version: str = "1.0.0"
    scenario_id: str = "scenario-01"
    impacts: list[AffectedResource] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scenario_id": self.scenario_id,
            "impacts": [imp.to_dict() for imp in self.impacts],
            "summary": self.summary or self._compute_summary(),
        }

    def _compute_summary(self) -> dict[str, int]:
        return {
            "total_affected": len(self.impacts),
            "direct": sum(1 for i in self.impacts if i.impact_type == ImpactType.DIRECT),
            "indirect": sum(1 for i in self.impacts if i.impact_type == ImpactType.INDIRECT),
            "risk": sum(1 for i in self.impacts if i.impact_type == ImpactType.RISK),
        }

    def get_affected(self, resource_id: str) -> AffectedResource | None:
        for imp in self.impacts:
            if imp.resource_id == resource_id:
                return imp
        return None

    def __len__(self) -> int:
        return len(self.impacts)

    def __iter__(self):
        return iter(self.impacts)
