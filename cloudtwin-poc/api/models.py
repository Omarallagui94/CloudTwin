"""Pydantic request/response models for the CloudTwin backend API (Task H8)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None


class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    message: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[ErrorDetail] = None


class SnapshotSummary(BaseModel):
    snapshot_id: str
    schema_version: str
    resource_count: int
    relationship_count: int
    aws_resources: int = 0
    kubernetes_resources: int = 0
    generated_at: Optional[str] = None


class SnapshotLoadRequest(BaseModel):
    fixture_path: str = Field(
        description="Relative or absolute path to the snapshot JSON fixture file.",
        examples=["fixtures/sample_current_state.json"],
    )
    snapshot_id: Optional[str] = Field(
        default=None,
        description="Override snapshot ID (auto-derived from file if omitted).",
    )


class SnapshotResponse(BaseModel):
    snapshot_id: str
    summary: SnapshotSummary
    resources: List[Dict[str, Any]] = Field(default_factory=list)
    relationships: List[Dict[str, Any]] = Field(default_factory=list)


class SimulationRequest(BaseModel):
    snapshot_id: str = Field(description="ID of the already-loaded Current Twin snapshot.")
    change_source: str = Field(
        description="Relative path to scenario JSON, Terraform plan JSON, or Kubernetes manifest YAML.",
        examples=["scenarios/scenario-01.json"],
    )
    simulation_id: Optional[str] = Field(
        default=None,
        description="Explicit simulation identifier (auto-generated if omitted).",
    )


class SimulationSummary(BaseModel):
    simulation_id: str
    snapshot_id: str
    changes_applied: int
    resource_count: int
    relationship_count: int
    created_at: str


class SimulationResponse(BaseModel):
    simulation_id: str
    summary: SimulationSummary
    twin: Dict[str, Any] = Field(description="Future Twin snapshot conforming to twin.schema.json.")
    provenance: Dict[str, Any] = Field(default_factory=dict)


class DiffRequest(BaseModel):
    snapshot_id: str = Field(description="Current Twin snapshot ID.")
    simulation_id: str = Field(description="Future Twin simulation ID.")


class DiffChangeRecord(BaseModel):
    resource_id: str
    change_type: str
    property: Optional[str] = None
    before: Optional[Any] = None
    after: Optional[Any] = None


class DiffSummary(BaseModel):
    total_changes: int
    added: int
    removed: int
    modified: int
    replaced: int


class DiffResponse(BaseModel):
    schema_version: str = "1.0.0"
    snapshot_id: str
    simulation_id: str
    changes: List[DiffChangeRecord]
    summary: DiffSummary


class GraphQueryRequest(BaseModel):
    snapshot_id: str
    resource_id: str
    direction: str = Field(
        default="both",
        description="Traversal direction: outgoing | incoming | both",
    )
    max_depth: int = Field(default=5, ge=1, le=20)
    transitive: bool = Field(default=False, description="When True, return full transitive dependency set.")


class DependencyNode(BaseModel):
    resource_id: str
    name: Optional[str] = None
    type: Optional[str] = None
    provider: Optional[str] = None
    relationship_type: Optional[str] = None
    direction: Optional[str] = None
    depth: Optional[int] = None
    path: Optional[List[str]] = None


class GraphQueryResponse(BaseModel):
    snapshot_id: str
    root: str
    direction: str
    transitive: bool
    dependencies: List[DependencyNode]
    total: int


class ImpactRequest(BaseModel):
    snapshot_id: str = Field(description="Current Twin snapshot ID (provides relationships).")
    simulation_id: str = Field(description="Future Twin simulation ID.")
    scenario_id: str = Field(default="scenario-01")
    max_depth: int = Field(default=5, ge=1, le=20)
    direction: str = Field(default="both")


class ImpactRecord(BaseModel):
    resource_id: str
    impact_type: str
    reason: str
    distance: int
    path: List[str]
    source_change: Optional[str] = None


class ImpactSummary(BaseModel):
    total_affected: int
    direct: int
    indirect: int
    risk: int


class ImpactResponse(BaseModel):
    schema_version: str = "1.0.0"
    scenario_id: str
    snapshot_id: str
    simulation_id: str
    summary: ImpactSummary
    impacts: List[ImpactRecord]
