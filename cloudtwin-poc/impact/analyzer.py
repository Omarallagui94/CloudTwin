"""Structural affected-resource impact analyzer for CloudTwin (Task H7)."""

from __future__ import annotations

import json
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Sequence

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from diff.models import StateDiff
from impact.models import (
    AffectedResource,
    EdgeTraversal,
    ImpactAnalysisResult,
    ImpactType,
)

SEMANTIC_REASONS = {
    ("EXPOSES", "outgoing"): "exposes deployment/pod",
    ("EXPOSES", "incoming"): "service exposing target workload",
    ("SELECTS", "outgoing"): "selects and manages pods of",
    ("SELECTS", "incoming"): "controller/service selecting workload",
    ("ROUTES_TO", "outgoing"): "routes network traffic to",
    ("ROUTES_TO", "incoming"): "ingress/load balancer routing traffic to",
    ("RUNS_ON", "outgoing"): "runs workload pod on",
    ("RUNS_ON", "incoming"): "host node running workload",
    ("CONTAINS", "outgoing"): "contains child resource",
    ("CONTAINS", "incoming"): "enclosing parent container/namespace/VPC",
    ("LOCATED_IN", "outgoing"): "located inside network boundary",
    ("LOCATED_IN", "incoming"): "boundary locating resource",
    ("USES", "outgoing"): "uses/depends on configuration or identity",
    ("USES", "incoming"): "dependent resource using this component",
    ("MEMBER_OF", "outgoing"): "member of cluster or group",
    ("MEMBER_OF", "incoming"): "group holding member",
}


class ImpactAnalyzer:
    """Analyzes structurally affected resources from a State Diff and relationship graph."""

    def __init__(
        self,
        default_max_depth: int = 5,
        include_root_change: bool = True,
    ) -> None:
        self.default_max_depth = default_max_depth
        self.include_root_change = include_root_change

    def analyze(
        self,
        diff: StateDiff | dict[str, Any] | str | Path,
        relationships: Sequence[dict[str, Any]] | dict[str, Any] | str | Path,
        scenario_id: str = "scenario-01",
        max_depth: int | None = None,
        direction: str = "both",
    ) -> ImpactAnalysisResult:
        depth_limit = max_depth if max_depth is not None else self.default_max_depth
        diff_changes = self._extract_diff_changes(diff)
        rel_list = self._extract_relationships(relationships)

        out_edges: dict[str, list[tuple[str, str]]] = defaultdict(list)
        in_edges: dict[str, list[tuple[str, str]]] = defaultdict(list)

        for rel in rel_list:
            s = rel.get("source")
            t = rel.get("target")
            r_type = rel.get("type", "USES")
            if s and t:
                out_edges[s].append((r_type, t))
                in_edges[t].append((r_type, s))

        for s in out_edges:
            out_edges[s].sort(key=lambda x: (x[1], x[0]))
        for t in in_edges:
            in_edges[t].sort(key=lambda x: (x[1], x[0]))

        affected_map: dict[str, AffectedResource] = {}
        sorted_changes = sorted(diff_changes, key=lambda c: c.get("resource_id", ""))

        for change in sorted_changes:
            root_id = change.get("resource_id")
            if not root_id:
                continue

            change_op = change.get("change_type", "MODIFIED")
            change_prop = change.get("property", "properties")

            if self.include_root_change:
                root_reason = f"Direct mutation target ({change_op} on {change_prop})"
                existing_root = affected_map.get(root_id)
                if not existing_root or existing_root.distance > 0:
                    affected_map[root_id] = AffectedResource(
                        resource_id=root_id,
                        impact_type=ImpactType.DIRECT,
                        reason=root_reason,
                        distance=0,
                        path=[root_id],
                        source_change=root_id,
                        edge_path=[],
                    )

            queue: deque[tuple[str, int, list[str], list[EdgeTraversal]]] = deque()
            visited_for_this_root: set[str] = {root_id}
            queue.append((root_id, 0, [root_id], []))

            while queue:
                curr_id, curr_dist, curr_path, curr_edges = queue.popleft()
                if curr_dist >= depth_limit:
                    continue

                next_dist = curr_dist + 1
                candidate_hops: list[tuple[str, str, str]] = []

                if direction in ("outgoing", "both"):
                    for rel_type, next_id in out_edges.get(curr_id, []):
                        candidate_hops.append((next_id, rel_type, "outgoing"))

                if direction in ("incoming", "both"):
                    for rel_type, prev_id in in_edges.get(curr_id, []):
                        candidate_hops.append((prev_id, rel_type, "incoming"))

                candidate_hops.sort(key=lambda h: (h[0], h[1], h[2]))

                for next_id, rel_type, dir_str in candidate_hops:
                    if next_id in visited_for_this_root:
                        continue
                    visited_for_this_root.add(next_id)

                    hop_traversal = EdgeTraversal(
                        source=curr_id if dir_str == "outgoing" else next_id,
                        target=next_id if dir_str == "outgoing" else curr_id,
                        rel_type=rel_type,
                        direction=dir_str,
                    )
                    new_path = curr_path + [next_id]
                    new_edges = curr_edges + [hop_traversal]
                    impact_type = ImpactType.DIRECT if next_dist == 1 else ImpactType.INDIRECT

                    reason_desc = self._format_reason(
                        root_id=root_id,
                        change_op=change_op,
                        curr_id=curr_id,
                        next_id=next_id,
                        rel_type=rel_type,
                        dir_str=dir_str,
                        distance=next_dist,
                        full_path=new_path,
                    )

                    candidate_affected = AffectedResource(
                        resource_id=next_id,
                        impact_type=impact_type,
                        reason=reason_desc,
                        distance=next_dist,
                        path=new_path,
                        source_change=root_id,
                        edge_path=new_edges,
                    )

                    if next_id not in affected_map or next_dist < affected_map[next_id].distance:
                        affected_map[next_id] = candidate_affected
                    elif next_dist == affected_map[next_id].distance:
                        if " -> ".join(new_path) < " -> ".join(affected_map[next_id].path):
                            affected_map[next_id] = candidate_affected

                    queue.append((next_id, next_dist, new_path, new_edges))

        sorted_impacts = sorted(
            affected_map.values(),
            key=lambda x: (x.distance, x.resource_id),
        )

        result = ImpactAnalysisResult(
            schema_version="1.0.0",
            scenario_id=scenario_id,
            impacts=sorted_impacts,
        )
        result.summary = result._compute_summary()
        return result

    @staticmethod
    def _format_reason(
        root_id: str,
        change_op: str,
        curr_id: str,
        next_id: str,
        rel_type: str,
        dir_str: str,
        distance: int,
        full_path: list[str],
    ) -> str:
        semantic = SEMANTIC_REASONS.get(
            (rel_type, dir_str),
            f"connected via {rel_type} ({dir_str})",
        )
        if distance == 1:
            if dir_str == "outgoing":
                return f"Direct impact from {root_id} ({change_op}): {root_id} --[{rel_type}]--> {next_id} ({semantic})"
            return f"Direct impact from {root_id} ({change_op}): {next_id} --[{rel_type}]--> {root_id} ({semantic})"
        path_str = " -> ".join(full_path)
        return (
            f"Transitive impact (distance {distance}) from {root_id} ({change_op}) "
            f"via path: {path_str} [last hop: {rel_type} {dir_str}]"
        )

    @staticmethod
    def _extract_diff_changes(diff_input: Any) -> list[dict[str, Any]]:
        if isinstance(diff_input, StateDiff):
            return [c.to_dict() for c in diff_input.changes]
        if isinstance(diff_input, dict):
            return diff_input.get("changes", [])
        if isinstance(diff_input, (str, Path)):
            p = Path(diff_input)
            if p.is_file():
                return json.loads(p.read_text(encoding="utf-8")).get("changes", [])
            return json.loads(str(diff_input)).get("changes", [])
        raise TypeError(f"Unsupported diff input type: {type(diff_input)}")

    @staticmethod
    def _extract_relationships(rel_input: Any) -> list[dict[str, Any]]:
        if isinstance(rel_input, list):
            return rel_input
        if hasattr(rel_input, "twin") and isinstance(rel_input.twin, dict):
            return rel_input.twin.get("relationships", [])
        if isinstance(rel_input, dict):
            if "relationships" in rel_input:
                return rel_input.get("relationships", [])
            if "twin" in rel_input and isinstance(rel_input["twin"], dict):
                return rel_input["twin"].get("relationships", [])
            return []
        if isinstance(rel_input, (str, Path)):
            p = Path(rel_input)
            if p.is_file():
                return ImpactAnalyzer._extract_relationships(json.loads(p.read_text(encoding="utf-8")))
            return ImpactAnalyzer._extract_relationships(json.loads(str(rel_input)))
        raise TypeError(f"Unsupported relationships input type: {type(rel_input)}")


def analyze_impact(
    diff: StateDiff | dict[str, Any] | str | Path,
    relationships: Sequence[dict[str, Any]] | dict[str, Any] | str | Path,
    scenario_id: str = "scenario-01",
    max_depth: int = 5,
    direction: str = "both",
) -> ImpactAnalysisResult:
    """Convenience function to compute structural impact."""
    return ImpactAnalyzer().analyze(
        diff=diff,
        relationships=relationships,
        scenario_id=scenario_id,
        max_depth=max_depth,
        direction=direction,
    )
