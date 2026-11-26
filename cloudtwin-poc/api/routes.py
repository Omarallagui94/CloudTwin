"""FastAPI route handlers for the CloudTwin backend API (Task H8)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, status

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from api.models import (
    DependencyNode,
    DiffChangeRecord,
    DiffRequest,
    DiffResponse,
    DiffSummary,
    GraphQueryRequest,
    GraphQueryResponse,
    ImpactRecord,
    ImpactRequest,
    ImpactResponse,
    ImpactSummary,
    SimulationRequest,
    SimulationResponse,
    SimulationSummary,
    SnapshotLoadRequest,
    SnapshotResponse,
    SnapshotSummary,
)
from api.store import APIStore, get_store
from diff.differ import StateDiffer
from future_twin.generator import FutureTwinGenerator
from future_twin.models import InvalidMutationError
from impact.analyzer import ImpactAnalyzer
from ingestion.ingester import ChangeIngester
from ingestion.models import IngestionError, UnresolvedTargetError
from storage.neo4j_store import InMemoryGraphBackend


def _make_app() -> FastAPI:
    app = FastAPI(
        title="CloudTwin API",
        version="1.0.0",
        description=(
            "Stable integration endpoints exposing Current Twin snapshots, "
            "Future Twin simulations, State Diffs, graph dependency queries, "
            "and affected-resource impact analysis."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
    )

    store = get_store()

    def _require_snapshot(snapshot_id: str, store: APIStore) -> dict[str, Any]:
        data = store.get_snapshot(snapshot_id)
        if data is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Snapshot '{snapshot_id}' not found. Load it first via POST /snapshots/load.",
            )
        return data

    def _require_simulation(simulation_id: str, store: APIStore) -> dict[str, Any]:
        data = store.get_simulation(simulation_id)
        if data is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Simulation '{simulation_id}' not found. Run it first via POST /simulations.",
            )
        return data

    def _resolve_path(raw: str) -> Path:
        p = Path(raw)
        if not p.is_absolute():
            p = ROOT_DIR / p
        if not p.exists():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"File not found: {raw}",
            )
        return p

    def _build_snapshot_summary(snapshot_id: str, data: dict[str, Any]) -> SnapshotSummary:
        resources = data.get("resources", [])
        rels = data.get("relationships", [])
        aws = sum(1 for r in resources if r.get("provider") in ("aws",))
        k8s = sum(1 for r in resources if r.get("provider") in ("kubernetes", "k8s"))
        meta = data.get("metadata", {})
        return SnapshotSummary(
            snapshot_id=snapshot_id,
            schema_version=data.get("schema_version", "1.0.0"),
            resource_count=len(resources),
            relationship_count=len(rels),
            aws_resources=aws,
            kubernetes_resources=k8s,
            generated_at=meta.get("generated_at"),
        )

    @app.get("/health", tags=["Health"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": "1.0.0"}

    @app.post(
        "/snapshots/load",
        response_model=SnapshotResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Snapshots"],
        summary="Load a Current Twin snapshot from a fixture file",
    )
    def load_snapshot(req: SnapshotLoadRequest) -> SnapshotResponse:
        path = _resolve_path(req.fixture_path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Cannot parse snapshot file: {exc}",
            )

        snapshot_id = req.snapshot_id or path.stem
        store.save_snapshot(snapshot_id, data)

        summary = _build_snapshot_summary(snapshot_id, data)
        return SnapshotResponse(
            snapshot_id=snapshot_id,
            summary=summary,
            resources=data.get("resources", []),
            relationships=data.get("relationships", []),
        )

    @app.get("/snapshots", response_model=list[str], tags=["Snapshots"])
    def list_snapshots() -> list[str]:
        return store.list_snapshots()

    @app.get("/snapshots/{snapshot_id}/summary", response_model=SnapshotSummary, tags=["Snapshots"])
    def get_snapshot_summary(snapshot_id: str) -> SnapshotSummary:
        data = _require_snapshot(snapshot_id, store)
        return _build_snapshot_summary(snapshot_id, data)

    @app.get("/snapshots/{snapshot_id}", response_model=SnapshotResponse, tags=["Snapshots"])
    def get_snapshot(snapshot_id: str) -> SnapshotResponse:
        data = _require_snapshot(snapshot_id, store)
        summary = _build_snapshot_summary(snapshot_id, data)
        return SnapshotResponse(
            snapshot_id=snapshot_id,
            summary=summary,
            resources=data.get("resources", []),
            relationships=data.get("relationships", []),
        )

    @app.post(
        "/simulations",
        response_model=SimulationResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Simulations"],
        summary="Run a Future Twin simulation",
    )
    def run_simulation(req: SimulationRequest) -> SimulationResponse:
        current_twin = _require_snapshot(req.snapshot_id, store)
        change_path = _resolve_path(req.change_source)

        try:
            ingester = ChangeIngester(current_twin=current_twin)
            changes = ingester.ingest(change_path)
        except (IngestionError, UnresolvedTargetError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "INGESTION_FAILED", "message": str(exc)},
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "INGESTION_ERROR", "message": str(exc)},
            )

        try:
            generator = FutureTwinGenerator()
            future = generator.generate(
                current_twin=current_twin,
                changes=changes,
                simulation_id=req.simulation_id,
            )
        except InvalidMutationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "MUTATION_FAILED", "message": str(exc)},
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "SIMULATION_ERROR", "message": str(exc)},
            )

        sim_record: dict[str, Any] = {
            "simulation_id": future.simulation_id,
            "snapshot_id": req.snapshot_id,
            "twin": future.twin,
            "provenance": future.provenance,
            "changes_applied": len(future.changes_applied),
            "created_at": future.created_at,
        }
        store.save_simulation(future.simulation_id, sim_record)

        resources = future.twin.get("resources", [])
        rels = future.twin.get("relationships", [])
        summary = SimulationSummary(
            simulation_id=future.simulation_id,
            snapshot_id=req.snapshot_id,
            changes_applied=len(future.changes_applied),
            resource_count=len(resources),
            relationship_count=len(rels),
            created_at=future.created_at,
        )
        return SimulationResponse(
            simulation_id=future.simulation_id,
            summary=summary,
            twin=future.twin,
            provenance=future.provenance,
        )

    @app.get("/simulations", response_model=list[str], tags=["Simulations"])
    def list_simulations() -> list[str]:
        return store.list_simulations()

    @app.get("/simulations/{simulation_id}", response_model=SimulationResponse, tags=["Simulations"])
    def get_simulation(simulation_id: str) -> SimulationResponse:
        rec = _require_simulation(simulation_id, store)
        resources = rec["twin"].get("resources", [])
        rels = rec["twin"].get("relationships", [])
        summary = SimulationSummary(
            simulation_id=rec["simulation_id"],
            snapshot_id=rec["snapshot_id"],
            changes_applied=rec["changes_applied"],
            resource_count=len(resources),
            relationship_count=len(rels),
            created_at=rec["created_at"],
        )
        return SimulationResponse(
            simulation_id=rec["simulation_id"],
            summary=summary,
            twin=rec["twin"],
            provenance=rec.get("provenance", {}),
        )

    @app.post("/diffs", response_model=DiffResponse, status_code=status.HTTP_200_OK, tags=["Diffs"])
    def compute_diff_endpoint(req: DiffRequest) -> DiffResponse:
        current_twin = _require_snapshot(req.snapshot_id, store)
        sim_rec = _require_simulation(req.simulation_id, store)
        future_payload: dict[str, Any] = {
            "twin": sim_rec["twin"],
            "provenance": sim_rec.get("provenance", {}),
        }

        differ = StateDiffer()
        state_diff = differ.diff(current_twin, future_payload)

        changes = [
            DiffChangeRecord(
                resource_id=c.resource_id,
                change_type=c.change_type.value,
                property=c.property,
                before=c.before,
                after=c.after,
            )
            for c in state_diff.changes
        ]
        summary = DiffSummary(
            total_changes=state_diff.summary.get("total_changes", 0),
            added=state_diff.summary.get("added", 0),
            removed=state_diff.summary.get("removed", 0),
            modified=state_diff.summary.get("modified", 0),
            replaced=state_diff.summary.get("replaced", 0),
        )
        return DiffResponse(
            schema_version="1.0.0",
            snapshot_id=req.snapshot_id,
            simulation_id=req.simulation_id,
            changes=changes,
            summary=summary,
        )

    @app.post("/graph/query", response_model=GraphQueryResponse, tags=["Graph"])
    def graph_query(req: GraphQueryRequest) -> GraphQueryResponse:
        data = _require_snapshot(req.snapshot_id, store)

        backend = InMemoryGraphBackend()
        backend.upsert_resources(data.get("resources", []), snapshot_id=req.snapshot_id)
        backend.upsert_relationships(data.get("relationships", []), snapshot_id=req.snapshot_id)

        if req.transitive:
            result = backend.get_transitive_dependencies(
                resource_id=req.resource_id,
                direction=req.direction,
                max_depth=req.max_depth,
                snapshot_id=req.snapshot_id,
            )
            deps_raw = result.get("dependencies", [])
            deps = [
                DependencyNode(
                    resource_id=d["resource_id"],
                    name=d.get("name"),
                    type=d.get("type"),
                    provider=d.get("provider"),
                    relationship_type=None,
                    direction=d.get("direction"),
                    depth=d.get("depth"),
                    path=d.get("path"),
                )
                for d in deps_raw
            ]
        else:
            deps_raw = backend.get_direct_dependencies(
                resource_id=req.resource_id,
                direction=req.direction,
                snapshot_id=req.snapshot_id,
            )
            deps = [
                DependencyNode(
                    resource_id=d["resource_id"],
                    name=d.get("name"),
                    type=d.get("type"),
                    provider=d.get("provider"),
                    relationship_type=d.get("relationship_type"),
                    direction=d.get("direction"),
                    depth=1,
                )
                for d in deps_raw
            ]

        return GraphQueryResponse(
            snapshot_id=req.snapshot_id,
            root=req.resource_id,
            direction=req.direction,
            transitive=req.transitive,
            dependencies=deps,
            total=len(deps),
        )

    @app.post("/impact", response_model=ImpactResponse, status_code=status.HTTP_200_OK, tags=["Impact"])
    def analyze_impact_endpoint(req: ImpactRequest) -> ImpactResponse:
        current_twin = _require_snapshot(req.snapshot_id, store)
        sim_rec = _require_simulation(req.simulation_id, store)

        future_payload: dict[str, Any] = {
            "twin": sim_rec["twin"],
            "provenance": sim_rec.get("provenance", {}),
        }

        differ = StateDiffer()
        state_diff = differ.diff(current_twin, future_payload)

        analyzer = ImpactAnalyzer()
        impact_result = analyzer.analyze(
            diff=state_diff,
            relationships=current_twin,
            scenario_id=req.scenario_id,
            max_depth=req.max_depth,
            direction=req.direction,
        )

        impacts = [
            ImpactRecord(
                resource_id=imp.resource_id,
                impact_type=imp.impact_type.value,
                reason=imp.reason,
                distance=imp.distance,
                path=imp.path,
                source_change=imp.source_change or None,
            )
            for imp in impact_result.impacts
        ]
        s = impact_result.summary
        summary = ImpactSummary(
            total_affected=s.get("total_affected", 0),
            direct=s.get("direct", 0),
            indirect=s.get("indirect", 0),
            risk=s.get("risk", 0),
        )
        return ImpactResponse(
            schema_version="1.0.0",
            scenario_id=req.scenario_id,
            snapshot_id=req.snapshot_id,
            simulation_id=req.simulation_id,
            summary=summary,
            impacts=impacts,
        )

    return app


app = _make_app()
