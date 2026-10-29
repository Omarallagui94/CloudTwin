"""Neo4j persistence store for CloudTwin Current Twin snapshots.

Stores infrastructure resources and directed relationships as a replaceable,
queryable graph snapshot in Neo4j with full idempotence, dependency query
capabilities, and stale resource lifecycle management.

IMPORTANT: dependency traversal is a storage inspection helper, not Hamdi's
canonical affected-resource/dependency analysis. The real Neo4j backend still
stores resource and relationship identity globally; relationship properties and
``last_seen_snapshot`` therefore describe the latest import. Snapshot membership
is retained through ``CONTAINS_RESOURCE``, but real-backend traversal is not yet
historically snapshot-isolated. Callers must not present it as blast radius or a
snapshot-accurate impact result. The in-memory backend does filter traversal by
snapshot membership for deterministic local tests.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

logger = logging.getLogger("cloudtwin.storage")

ALLOWED_RELATIONSHIPS = frozenset({
    "CONTAINS",
    "LOCATED_IN",
    "USES",
    "MEMBER_OF",
    "RUNS_ON",
    "SELECTS",
    "EXPOSES",
    "ROUTES_TO",
})


# ---------------------------------------------------------------------------
# In-Memory Backend (for offline testing & local fixture workflows)
# ---------------------------------------------------------------------------

class InMemoryGraphBackend:
    """In-memory graph store matching Neo4j graph semantics for local fixture testing."""

    def __init__(self) -> None:
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.resources: dict[str, dict[str, Any]] = {}
        # snapshot_id -> set of resource_ids
        self.snapshot_resources: dict[str, set[str]] = defaultdict(set)
        # (source_id, target_id, rel_type) -> rel dict
        self.relationships: dict[tuple[str, str, str], dict[str, Any]] = {}
        # Adjacency indexes for fast traversal
        self.out_edges: dict[str, list[tuple[str, str, dict[str, Any]]]] = defaultdict(list)
        self.in_edges: dict[str, list[tuple[str, str, dict[str, Any]]]] = defaultdict(list)

    def init_schema(self) -> None:
        """Schema constraints are inherently enforced by dictionary keys."""
        pass

    def upsert_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        snapshot_id = snapshot["id"]
        existing = self.snapshots.get(snapshot_id, {})
        merged = {**existing, **snapshot}
        self.snapshots[snapshot_id] = merged
        return merged

    def upsert_resources(
        self,
        resources: list[dict[str, Any]],
        snapshot_id: str,
    ) -> int:
        for res in resources:
            r_id = res["id"]
            existing = self.resources.get(r_id, {})
            merged = {
                **existing,
                **res,
                "last_seen_snapshot": snapshot_id,
                "is_stale": False,
            }
            self.resources[r_id] = merged
            self.snapshot_resources[snapshot_id].add(r_id)
        return len(resources)

    def upsert_relationships(
        self,
        relationships: list[dict[str, Any]],
        snapshot_id: str,
    ) -> int:
        for rel in relationships:
            source = rel["source"]
            target = rel["target"]
            rel_type = rel["type"]
            key = (source, target, rel_type)

            if key not in self.relationships:
                rel_data = {
                    "source": source,
                    "target": target,
                    "type": rel_type,
                    "last_seen_snapshot": snapshot_id,
                }
                self.relationships[key] = rel_data
                self.out_edges[source].append((rel_type, target, rel_data))
                self.in_edges[target].append((rel_type, source, rel_data))
            else:
                self.relationships[key]["last_seen_snapshot"] = snapshot_id

        return len(relationships)

    def mark_stale_resources(self, current_snapshot_id: str) -> list[str]:
        current_ids = self.snapshot_resources.get(current_snapshot_id, set())
        stale_ids = []
        for r_id, res in self.resources.items():
            if r_id not in current_ids:
                res["is_stale"] = True
                stale_ids.append(r_id)
        return stale_ids

    def remove_stale_resources(self, current_snapshot_id: str) -> int:
        current_ids = self.snapshot_resources.get(current_snapshot_id, set())
        to_delete = [r_id for r_id in self.resources if r_id not in current_ids]

        for r_id in to_delete:
            del self.resources[r_id]
            # Remove from all snapshot memberships
            for s_id in self.snapshot_resources:
                self.snapshot_resources[s_id].discard(r_id)

        # Remove relationships attached to deleted resources
        dead_keys = [
            key for key in self.relationships
            if key[0] in to_delete or key[1] in to_delete
        ]
        for key in dead_keys:
            del self.relationships[key]

        # Rebuild adjacency
        self.out_edges.clear()
        self.in_edges.clear()
        for (source, target, rel_type), data in self.relationships.items():
            self.out_edges[source].append((rel_type, target, data))
            self.in_edges[target].append((rel_type, source, data))

        return len(to_delete)

    def get_stale_resources(self, current_snapshot_id: str) -> list[dict[str, Any]]:
        current_ids = self.snapshot_resources.get(current_snapshot_id, set())
        return [
            dict(res) for r_id, res in self.resources.items()
            if r_id not in current_ids
        ]

    def get_resource(self, resource_id: str) -> dict[str, Any] | None:
        res = self.resources.get(resource_id)
        return dict(res) if res else None

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        snap = self.snapshots.get(snapshot_id)
        return dict(snap) if snap else None

    def list_snapshots(self) -> list[dict[str, Any]]:
        return [dict(s) for s in self.snapshots.values()]

    def get_snapshot_summary(self, snapshot_id: str) -> dict[str, Any] | None:
        snap = self.snapshots.get(snapshot_id)
        if not snap:
            return None
        res_ids = self.snapshot_resources.get(snapshot_id, set())
        aws_count = sum(1 for r_id in res_ids if self.resources.get(r_id, {}).get("provider") == "aws")
        k8s_count = sum(1 for r_id in res_ids if self.resources.get(r_id, {}).get("provider") == "kubernetes")

        return {
            "snapshot_id": snapshot_id,
            "resource_count": len(res_ids),
            "relationship_count": snap.get("relationship_count", 0),
            "aws_resources": aws_count,
            "kubernetes_resources": k8s_count,
            "provider": snap.get("provider"),
            "region": snap.get("region"),
            "cluster": snap.get("cluster"),
            "generated_at": snap.get("generated_at"),
            "imported_at": snap.get("imported_at"),
        }

    def get_direct_dependencies(
        self,
        resource_id: str,
        direction: str = "outgoing",
        snapshot_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if resource_id not in self.resources:
            return []

        allowed_ids = (
            self.snapshot_resources.get(snapshot_id)
            if snapshot_id is not None
            else None
        )

        results: list[dict[str, Any]] = []

        if direction in ("outgoing", "both"):
            for rel_type, target_id, _ in self.out_edges.get(resource_id, []):
                if allowed_ids is not None and target_id not in allowed_ids:
                    continue
                target = self.resources.get(target_id)
                if target:
                    results.append({
                        "resource_id": target["id"],
                        "name": target.get("name"),
                        "type": target.get("type"),
                        "provider": target.get("provider"),
                        "relationship_type": rel_type,
                        "direction": "outgoing",
                    })

        if direction in ("incoming", "both"):
            for rel_type, source_id, _ in self.in_edges.get(resource_id, []):
                if allowed_ids is not None and source_id not in allowed_ids:
                    continue
                source = self.resources.get(source_id)
                if source:
                    results.append({
                        "resource_id": source["id"],
                        "name": source.get("name"),
                        "type": source.get("type"),
                        "provider": source.get("provider"),
                        "relationship_type": rel_type,
                        "direction": "incoming",
                    })

        return results

    def get_transitive_dependencies(
        self,
        resource_id: str,
        direction: str = "outgoing",
        max_depth: int = 10,
        snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        if resource_id not in self.resources:
            return {"root": resource_id, "direction": direction, "dependencies": [], "edges": []}

        allowed_ids = (
            self.snapshot_resources.get(snapshot_id)
            if snapshot_id is not None
            else None
        )

        visited: dict[str, int] = {resource_id: 0}
        path_map: dict[str, list[str]] = {resource_id: []}
        direction_map: dict[str, str] = {}
        edges_seen: set[tuple[str, str, str]] = set()

        # Queue contains (curr_id, current_depth, current_direction)
        queue: deque[tuple[str, int, str]] = deque()
        if direction in ("outgoing", "both"):
            queue.append((resource_id, 0, "outgoing"))
        if direction == "incoming":
            queue.append((resource_id, 0, "incoming"))

        while queue:
            curr_id, curr_depth, curr_dir = queue.popleft()
            if curr_depth >= max_depth:
                continue

            # Outgoing neighbors
            if curr_dir in ("outgoing", "both") or direction == "both":
                for rel_type, next_id, _ in self.out_edges.get(curr_id, []):
                    if allowed_ids is not None and next_id not in allowed_ids:
                        continue
                    edges_seen.add((curr_id, next_id, rel_type))
                    if next_id not in visited:
                        visited[next_id] = curr_depth + 1
                        path_map[next_id] = path_map[curr_id] + [rel_type]
                        direction_map[next_id] = "outgoing"
                        queue.append((next_id, curr_depth + 1, "outgoing"))

            # Incoming neighbors
            if curr_dir in ("incoming", "both") or direction == "both":
                for rel_type, prev_id, _ in self.in_edges.get(curr_id, []):
                    if allowed_ids is not None and prev_id not in allowed_ids:
                        continue
                    edges_seen.add((prev_id, curr_id, rel_type))
                    if prev_id not in visited:
                        visited[prev_id] = curr_depth + 1
                        path_map[prev_id] = path_map[curr_id] + [rel_type]
                        direction_map[prev_id] = "incoming"
                        queue.append((prev_id, curr_depth + 1, "incoming"))

        dependencies = []
        for dep_id, depth in sorted(visited.items(), key=lambda item: (item[1], item[0])):
            if dep_id == resource_id:
                continue
            res = self.resources.get(dep_id)
            if not res:
                continue
            dependencies.append({
                "resource_id": dep_id,
                "name": res.get("name"),
                "type": res.get("type"),
                "provider": res.get("provider"),
                "depth": depth,
                "direction": direction_map.get(dep_id, direction),
                "path": path_map.get(dep_id, []),
            })

        edges = [
            {"source": s, "target": t, "type": rel_t}
            for (s, t, rel_t) in edges_seen
        ]

        return {
            "root": resource_id,
            "direction": direction,
            "max_depth": max_depth,
            "dependencies": dependencies,
            "edges": edges,
        }

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Real Neo4j Backend (Bolt driver)
# ---------------------------------------------------------------------------

class RealNeo4jBackend:
    """Production Neo4j backend using the official neo4j Python driver."""

    def __init__(self, driver: Any, database: str = "neo4j") -> None:
        self.driver = driver
        self.database = database

    def init_schema(self) -> None:
        """Create uniqueness constraints and performance indexes."""
        queries = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (r:Resource) REQUIRE r.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (s:Snapshot) REQUIRE s.id IS UNIQUE",
            "CREATE INDEX IF NOT EXISTS FOR (r:Resource) ON (r.type)",
            "CREATE INDEX IF NOT EXISTS FOR (r:Resource) ON (r.provider)",
        ]
        with self.driver.session(database=self.database) as session:
            for q in queries:
                session.run(q)

    def upsert_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        query = """
        MERGE (s:Snapshot {id: $snapshot_id})
        SET s.provider = $provider,
            s.region = $region,
            s.cluster = $cluster,
            s.schema_version = $schema_version,
            s.generated_at = $generated_at,
            s.imported_at = $imported_at,
            s.resource_count = $resource_count,
            s.relationship_count = $relationship_count,
            s.aws_resources = $aws_resources,
            s.kubernetes_resources = $kubernetes_resources,
            s.status = 'ACTIVE'
        RETURN s
        """
        params = {
            "snapshot_id": snapshot["id"],
            "provider": snapshot.get("provider"),
            "region": snapshot.get("region"),
            "cluster": snapshot.get("cluster"),
            "schema_version": snapshot.get("schema_version"),
            "generated_at": snapshot.get("generated_at"),
            "imported_at": snapshot.get("imported_at"),
            "resource_count": snapshot.get("resource_count", 0),
            "relationship_count": snapshot.get("relationship_count", 0),
            "aws_resources": snapshot.get("aws_resources", 0),
            "kubernetes_resources": snapshot.get("kubernetes_resources", 0),
        }
        with self.driver.session(database=self.database) as session:
            session.run(query, **params)
        return snapshot

    def upsert_resources(
        self,
        resources: list[dict[str, Any]],
        snapshot_id: str,
        batch_size: int = 500,
    ) -> int:
        query = """
        UNWIND $batch AS res
        MERGE (r:Resource {id: res.id})
        SET r.name = res.name,
            r.type = res.type,
            r.provider = res.provider,
            r.properties_json = res.properties_json,
            r.last_seen_snapshot = $snapshot_id,
            r.is_stale = false
        WITH r
        MATCH (s:Snapshot {id: $snapshot_id})
        MERGE (s)-[:CONTAINS_RESOURCE]->(r)
        """
        with self.driver.session(database=self.database) as session:
            for i in range(0, len(resources), batch_size):
                batch = [
                    {
                        "id": r["id"],
                        "name": r.get("name", ""),
                        "type": r.get("type", ""),
                        "provider": r.get("provider", ""),
                        "properties_json": json.dumps(r.get("properties", {})),
                    }
                    for r in resources[i:i + batch_size]
                ]
                session.run(query, batch=batch, snapshot_id=snapshot_id)
        return len(resources)

    def upsert_relationships(
        self,
        relationships: list[dict[str, Any]],
        snapshot_id: str,
    ) -> int:
        by_type: dict[str, list[dict[str, str]]] = defaultdict(list)
        for rel in relationships:
            rel_type = rel["type"]
            if rel_type not in ALLOWED_RELATIONSHIPS:
                raise ValueError(f"Disallowed relationship type: {rel_type}")
            by_type[rel_type].append({"source": rel["source"], "target": rel["target"]})

        with self.driver.session(database=self.database) as session:
            for rel_type, rel_batch in by_type.items():
                query = f"""
                UNWIND $batch AS rel
                MATCH (a:Resource {{id: rel.source}})
                MATCH (b:Resource {{id: rel.target}})
                MERGE (a)-[r:{rel_type}]->(b)
                SET r.last_seen_snapshot = $snapshot_id
                """
                session.run(query, batch=rel_batch, snapshot_id=snapshot_id)

        return len(relationships)

    def mark_stale_resources(self, current_snapshot_id: str) -> list[str]:
        query = """
        MATCH (r:Resource)
        WHERE NOT (r)<-[:CONTAINS_RESOURCE]-(:Snapshot {id: $snapshot_id})
        SET r.is_stale = true
        RETURN r.id AS id
        """
        with self.driver.session(database=self.database) as session:
            result = session.run(query, snapshot_id=current_snapshot_id)
            return [record["id"] for record in result]

    def remove_stale_resources(self, current_snapshot_id: str) -> int:
        query = """
        MATCH (r:Resource)
        WHERE NOT (r)<-[:CONTAINS_RESOURCE]-(:Snapshot {id: $snapshot_id})
        DETACH DELETE r
        """
        with self.driver.session(database=self.database) as session:
            summary = session.run(query, snapshot_id=current_snapshot_id).consume()
            return summary.counters.nodes_deleted

    def get_stale_resources(self, current_snapshot_id: str) -> list[dict[str, Any]]:
        query = """
        MATCH (r:Resource)
        WHERE NOT (r)<-[:CONTAINS_RESOURCE]-(:Snapshot {id: $snapshot_id})
        RETURN r.id AS id, r.name AS name, r.type AS type, r.provider AS provider,
               r.last_seen_snapshot AS last_seen_snapshot, r.is_stale AS is_stale
        """
        with self.driver.session(database=self.database) as session:
            result = session.run(query, snapshot_id=current_snapshot_id)
            return [dict(record) for record in result]

    def get_resource(self, resource_id: str) -> dict[str, Any] | None:
        query = """
        MATCH (r:Resource {id: $resource_id})
        RETURN r.id AS id, r.name AS name, r.type AS type, r.provider AS provider,
               r.properties_json AS properties_json, r.last_seen_snapshot AS last_seen_snapshot,
               r.is_stale AS is_stale
        """
        with self.driver.session(database=self.database) as session:
            record = session.run(query, resource_id=resource_id).single()
            if not record:
                return None
            data = dict(record)
            if data.get("properties_json"):
                data["properties"] = json.loads(data["properties_json"])
            return data

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        query = "MATCH (s:Snapshot {id: $snapshot_id}) RETURN s"
        with self.driver.session(database=self.database) as session:
            record = session.run(query, snapshot_id=snapshot_id).single()
            if not record:
                return None
            return dict(record["s"])

    def list_snapshots(self) -> list[dict[str, Any]]:
        query = "MATCH (s:Snapshot) RETURN s ORDER BY s.imported_at DESC"
        with self.driver.session(database=self.database) as session:
            result = session.run(query)
            return [dict(rec["s"]) for rec in result]

    def get_snapshot_summary(self, snapshot_id: str) -> dict[str, Any] | None:
        query = """
        MATCH (s:Snapshot {id: $snapshot_id})
        OPTIONAL MATCH (s)-[:CONTAINS_RESOURCE]->(r:Resource)
        RETURN s.id AS snapshot_id,
               s.provider AS provider,
               s.region AS region,
               s.cluster AS cluster,
               s.generated_at AS generated_at,
               s.imported_at AS imported_at,
               s.relationship_count AS relationship_count,
               count(r) AS resource_count,
               count(CASE WHEN r.provider = 'aws' THEN 1 END) AS aws_resources,
               count(CASE WHEN r.provider = 'kubernetes' THEN 1 END) AS kubernetes_resources
        """
        with self.driver.session(database=self.database) as session:
            record = session.run(query, snapshot_id=snapshot_id).single()
            if not record or record["snapshot_id"] is None:
                return None
            return dict(record)

    def get_direct_dependencies(
        self,
        resource_id: str,
        direction: str = "outgoing",
        snapshot_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Inspect neighbours; not canonical affected-resource analysis.

        ``snapshot_id`` is not yet enforced by the real backend. Snapshot-
        isolated relationship storage requires a later data-model migration.
        """
        results: list[dict[str, Any]] = []

        with self.driver.session(database=self.database) as session:
            if direction in ("outgoing", "both"):
                q_out = """
                MATCH (s:Resource {id: $resource_id})-[r]->(t:Resource)
                RETURN type(r) AS relationship_type, t.id AS resource_id,
                       t.name AS name, t.type AS type, t.provider AS provider,
                       'outgoing' AS direction
                """
                for rec in session.run(q_out, resource_id=resource_id):
                    results.append(dict(rec))

            if direction in ("incoming", "both"):
                q_in = """
                MATCH (s:Resource)-[r]->(t:Resource {id: $resource_id})
                RETURN type(r) AS relationship_type, s.id AS resource_id,
                       s.name AS name, s.type AS type, s.provider AS provider,
                       'incoming' AS direction
                """
                for rec in session.run(q_in, resource_id=resource_id):
                    results.append(dict(rec))

        return results

    def get_transitive_dependencies(
        self,
        resource_id: str,
        direction: str = "outgoing",
        max_depth: int = 10,
        snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        """Inspect reachable nodes; not blast-radius/impact analysis.

        ``snapshot_id`` has the same real-backend limitation as direct queries.
        """
        results: list[dict[str, Any]] = []
        edges: list[dict[str, str]] = []
        seen_edges: set[tuple[str, str, str]] = set()

        depth_bound = max(1, min(max_depth, 25))

        with self.driver.session(database=self.database) as session:
            if direction in ("outgoing", "both"):
                q_out = f"""
                MATCH path = (s:Resource {{id: $resource_id}})-[*1..{depth_bound}]->(t:Resource)
                WHERE s <> t
                WITH t, min(length(path)) AS depth, collect(path) AS paths
                UNWIND paths AS p
                UNWIND relationships(p) AS rel
                RETURN t.id AS resource_id, t.name AS name, t.type AS type,
                       t.provider AS provider, depth, 'outgoing' AS direction,
                       startNode(rel).id AS edge_src, endNode(rel).id AS edge_dst, type(rel) AS edge_type
                ORDER BY depth, resource_id
                """
                for rec in session.run(q_out, resource_id=resource_id):
                    res_entry = {
                        "resource_id": rec["resource_id"],
                        "name": rec["name"],
                        "type": rec["type"],
                        "provider": rec["provider"],
                        "depth": rec["depth"],
                        "direction": rec["direction"],
                    }
                    if not any(r["resource_id"] == rec["resource_id"] for r in results):
                        results.append(res_entry)
                    edge_key = (rec["edge_src"], rec["edge_dst"], rec["edge_type"])
                    if edge_key not in seen_edges:
                        seen_edges.add(edge_key)
                        edges.append({
                            "source": rec["edge_src"],
                            "target": rec["edge_dst"],
                            "type": rec["edge_type"],
                        })

            if direction in ("incoming", "both"):
                q_in = f"""
                MATCH path = (s:Resource)-[*1..{depth_bound}]->(t:Resource {{id: $resource_id}})
                WHERE s <> t
                WITH s, min(length(path)) AS depth, collect(path) AS paths
                UNWIND paths AS p
                UNWIND relationships(p) AS rel
                RETURN s.id AS resource_id, s.name AS name, s.type AS type,
                       s.provider AS provider, depth, 'incoming' AS direction,
                       startNode(rel).id AS edge_src, endNode(rel).id AS edge_dst, type(rel) AS edge_type
                ORDER BY depth, resource_id
                """
                for rec in session.run(q_in, resource_id=resource_id):
                    res_entry = {
                        "resource_id": rec["resource_id"],
                        "name": rec["name"],
                        "type": rec["type"],
                        "provider": rec["provider"],
                        "depth": rec["depth"],
                        "direction": rec["direction"],
                    }
                    if not any(r["resource_id"] == rec["resource_id"] for r in results):
                        results.append(res_entry)
                    edge_key = (rec["edge_src"], rec["edge_dst"], rec["edge_type"])
                    if edge_key not in seen_edges:
                        seen_edges.add(edge_key)
                        edges.append({
                            "source": rec["edge_src"],
                            "target": rec["edge_dst"],
                            "type": rec["edge_type"],
                        })

        return {
            "root": resource_id,
            "direction": direction,
            "max_depth": max_depth,
            "dependencies": results,
            "edges": edges,
        }

    def close(self) -> None:
        if self.driver:
            self.driver.close()


# ---------------------------------------------------------------------------
# High-Level Neo4jTwinStore
# ---------------------------------------------------------------------------

class Neo4jTwinStore:
    """Unified Neo4j persistence store for Current Twin snapshots.

    Features:
    - Idempotent resource and relationship upserts (re-importing creates no duplicates).
    - Snapshot metadata tracking and lineage association.
    - Stale resource retention (historical snapshot association) or removal (pruning).
    - Direct and transitive dependency querying with configurable depth.
    - Explicit in-memory fixture backend; connection failures are never hidden.
    """

    def __init__(
        self,
        uri: str | None = None,
        auth: tuple[str, str] | None = None,
        database: str = "neo4j",
        driver: Any | None = None,
        use_in_memory: bool | None = None,
    ) -> None:
        self.database = database
        self._backend: InMemoryGraphBackend | RealNeo4jBackend

        if use_in_memory is True:
            logger.info("Initializing Neo4jTwinStore with in-memory graph engine")
            self._backend = InMemoryGraphBackend()
            self._mode = "in_memory"
            return

        # Attempt to use real driver if provided or configured
        target_uri = uri or os.getenv("NEO4J_URI")
        target_user = auth[0] if auth else os.getenv("NEO4J_USER", "neo4j")
        target_pass = auth[1] if auth else os.getenv("NEO4J_PASSWORD")

        if driver is not None:
            self._backend = RealNeo4jBackend(driver, database=database)
            self._mode = "real"
            self.init_schema()
            return

        if target_uri:
            try:
                import neo4j
                auth_tuple = (target_user, target_pass) if target_pass else None
                drv = neo4j.GraphDatabase.driver(target_uri, auth=auth_tuple)
                drv.verify_connectivity()
                self._backend = RealNeo4jBackend(drv, database=database)
                self._mode = "real"
                self.init_schema()
                logger.info("Connected to Neo4j instance at %s", target_uri)
                return
            except Exception as exc:
                raise ConnectionError(f"Failed to connect to Neo4j at {target_uri}: {exc}") from exc

        if use_in_memory is False:
            raise ValueError("Neo4j URI or driver is required when use_in_memory=False")

        # Explicit/default local fixture mode when no Neo4j endpoint was supplied.
        self._backend = InMemoryGraphBackend()
        self._mode = "in_memory"
        logger.info("Neo4jTwinStore active in local in-memory fixture mode")

    @property
    def mode(self) -> str:
        """Operating mode: 'real' or 'in_memory'."""
        return self._mode

    def init_schema(self) -> None:
        """Initialize database schema constraints and indexes."""
        self._backend.init_schema()

    def import_snapshot(
        self,
        twin: dict[str, Any] | str | Path,
        snapshot_id: str | None = None,
        remove_stale: bool = False,
        validate: bool = False,
    ) -> dict[str, Any]:
        """Import or upsert a Validated Current Twin JSON into Neo4j.

        Args:
            twin: Current Twin dict, JSON string, or Path to JSON file.
            snapshot_id: Optional unique snapshot ID. Generated if omitted.
            remove_stale: When True, stale resources not present in this snapshot
                          are pruned. When False (default), stale resources are
                          associated with their historical snapshot.
            validate: When True, validate input with twin.schema.json.

        Returns:
            Dictionary containing import metrics and metadata.
        """
        snapshot_data = self._load_twin(twin)

        if validate:
            self._validate_twin(snapshot_data)

        metadata = snapshot_data.get("metadata", {})
        resources = snapshot_data.get("resources", [])
        relationships = snapshot_data.get("relationships", [])
        summary = snapshot_data.get("summary", {})

        cluster = metadata.get("cluster", "default")
        provider = metadata.get("provider", "AWS")
        region = metadata.get("region", "eu-west-3")
        schema_version = snapshot_data.get("schema_version", "1.0.0")
        generated_at = snapshot_data.get("generated_at") or datetime.now(timezone.utc).isoformat()
        imported_at = datetime.now(timezone.utc).isoformat()

        if not snapshot_id:
            # Deterministic/clean default snapshot ID
            snap_tag = generated_at.replace(":", "-").replace(".", "-")
            snapshot_id = f"snapshot-{cluster}-{snap_tag}"

        # 1. Upsert Snapshot node
        snapshot_node = {
            "id": snapshot_id,
            "provider": provider,
            "region": region,
            "cluster": cluster,
            "schema_version": schema_version,
            "generated_at": generated_at,
            "imported_at": imported_at,
            "resource_count": len(resources),
            "relationship_count": len(relationships),
            "aws_resources": summary.get("aws_resources", sum(1 for r in resources if r.get("provider") == "aws")),
            "kubernetes_resources": summary.get(
                "kubernetes_resources",
                sum(1 for r in resources if r.get("provider") == "kubernetes"),
            ),
        }
        self._backend.upsert_snapshot(snapshot_node)

        # 2. Upsert Resources
        res_count = self._backend.upsert_resources(resources, snapshot_id=snapshot_id)

        # 3. Upsert Relationships
        rel_count = self._backend.upsert_relationships(relationships, snapshot_id=snapshot_id)

        # 4. Handle Stale Resources (mark or delete)
        stale_ids = self._backend.mark_stale_resources(current_snapshot_id=snapshot_id)
        stale_removed = 0
        if remove_stale:
            stale_removed = self._backend.remove_stale_resources(current_snapshot_id=snapshot_id)

        return {
            "snapshot_id": snapshot_id,
            "status": "SUCCESS",
            "resources_imported": res_count,
            "relationships_imported": rel_count,
            "aws_resources": snapshot_node["aws_resources"],
            "kubernetes_resources": snapshot_node["kubernetes_resources"],
            "stale_resources_count": len(stale_ids),
            "stale_resources_removed": stale_removed,
            "metadata": metadata,
            "imported_at": imported_at,
        }

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        """Retrieve snapshot metadata by ID."""
        return self._backend.get_snapshot(snapshot_id)

    def list_snapshots(self) -> list[dict[str, Any]]:
        """List all stored snapshots."""
        return self._backend.list_snapshots()

    def get_snapshot_summary(self, snapshot_id: str) -> dict[str, Any] | None:
        """Retrieve aggregated counts and verification summary for a snapshot."""
        return self._backend.get_snapshot_summary(snapshot_id)

    def get_resource(self, resource_id: str) -> dict[str, Any] | None:
        """Retrieve a specific resource by globally unique ID."""
        return self._backend.get_resource(resource_id)

    def get_stale_resources(self, current_snapshot_id: str) -> list[dict[str, Any]]:
        """Retrieve resources that are not present in the given snapshot."""
        return self._backend.get_stale_resources(current_snapshot_id)

    def remove_stale_resources(self, current_snapshot_id: str) -> int:
        """Remove resources that do not belong to the given snapshot."""
        return self._backend.remove_stale_resources(current_snapshot_id)

    def get_direct_dependencies(
        self,
        resource_id: str,
        direction: str = "outgoing",
        snapshot_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve direct (1-hop) dependencies for a resource.

        Args:
            resource_id: ID of the resource to inspect.
            direction: 'outgoing' (targets), 'incoming' (sources), or 'both'.
            snapshot_id: Optional snapshot filter.
        """
        if direction not in ("outgoing", "incoming", "both"):
            raise ValueError(f"Invalid direction: {direction}. Must be 'outgoing', 'incoming', or 'both'.")
        return self._backend.get_direct_dependencies(
            resource_id=resource_id,
            direction=direction,
            snapshot_id=snapshot_id,
        )

    def get_transitive_dependencies(
        self,
        resource_id: str,
        direction: str = "outgoing",
        max_depth: int = 10,
        snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve transitive (multi-hop) dependency subgraph for a resource.

        Args:
            resource_id: Root resource ID.
            direction: 'outgoing', 'incoming', or 'both'.
            max_depth: Maximum path traversal depth.
            snapshot_id: Optional snapshot filter.
        """
        if direction not in ("outgoing", "incoming", "both"):
            raise ValueError(f"Invalid direction: {direction}. Must be 'outgoing', 'incoming', or 'both'.")
        return self._backend.get_transitive_dependencies(
            resource_id=resource_id,
            direction=direction,
            max_depth=max_depth,
            snapshot_id=snapshot_id,
        )

    def close(self) -> None:
        """Close database connections."""
        self._backend.close()

    def __enter__(self) -> Neo4jTwinStore:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _load_twin(twin: dict[str, Any] | str | Path) -> dict[str, Any]:
        if isinstance(twin, dict):
            return twin
        if isinstance(twin, Path):
            return json.loads(twin.read_text(encoding="utf-8"))
        if isinstance(twin, str):
            try:
                # check if file path
                p = Path(twin)
                if p.is_file():
                    return json.loads(p.read_text(encoding="utf-8"))
            except OSError:
                pass
            return json.loads(twin)
        raise TypeError(f"Unsupported twin input type: {type(twin)}")

    @staticmethod
    def _validate_twin(twin: dict[str, Any]) -> None:
        root_dir = Path(__file__).resolve().parents[1]
        schema_file = root_dir / "schema" / "v1" / "twin.schema.json"
        if not schema_file.is_file():
            logger.warning("twin.schema.json not found at %s; skipping validation", schema_file)
            return
        from jsonschema import Draft202012Validator
        schema = json.loads(schema_file.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        errors = list(validator.iter_errors(twin))
        if errors:
            first = errors[0]
            raise ValueError(f"Snapshot validation error at {list(first.path)}: {first.message}")


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------

def _run_cli() -> int:
    import argparse
    root_dir = Path(__file__).resolve().parents[1]
    default_fixture = root_dir / "fixtures" / "sample_current_state.json"

    parser = argparse.ArgumentParser(description="Persist CloudTwin Current Twin to Neo4j")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=default_fixture,
        help="Path to Current Twin snapshot JSON fixture",
    )
    parser.add_argument("--uri", type=str, default=None, help="Neo4j Bolt URI (e.g. bolt://localhost:7687)")
    parser.add_argument("--user", type=str, default="neo4j", help="Neo4j username")
    parser.add_argument("--password", type=str, default=None, help="Neo4j password")
    parser.add_argument("--in-memory", action="store_true", help="Force in-memory mode")
    parser.add_argument("--snapshot-id", type=str, default=None, help="Explicit snapshot ID")
    parser.add_argument("--remove-stale", action="store_true", help="Remove stale resources")
    parser.add_argument("--query-dep", type=str, default=None, help="Query dependencies for resource ID")
    parser.add_argument("--max-depth", type=int, default=5, help="Max depth for transitive query")

    args = parser.parse_args()

    auth = (args.user, args.password) if args.password else None
    store = Neo4jTwinStore(
        uri=args.uri,
        auth=auth,
        use_in_memory=True if args.in_memory else None,
    )

    print(f"[*] Neo4jTwinStore active in {store.mode} mode")
    print(f"[*] Importing fixture: {args.fixture}")
    result = store.import_snapshot(
        args.fixture,
        snapshot_id=args.snapshot_id,
        remove_stale=args.remove_stale,
        validate=True,
    )

    print(f"[OK] Import successful: {result['snapshot_id']}")
    print(f"     Resources imported: {result['resources_imported']} (AWS: {result['aws_resources']}, K8s: {result['kubernetes_resources']})")
    print(f"     Relationships imported: {result['relationships_imported']}")
    print(f"     Stale resources count: {result['stale_resources_count']}")

    summary = store.get_snapshot_summary(result["snapshot_id"])
    print(f"[OK] Verified snapshot summary: {summary}")

    if args.query_dep:
        target_id = args.query_dep
        direct = store.get_direct_dependencies(target_id, direction="both")
        print(f"\n[*] Direct dependencies for {target_id} ({len(direct)}):")
        for dep in direct:
            print(f"    - [{dep['direction'].upper()}] -{dep['relationship_type']}-> {dep['resource_id']} ({dep['type']})")

        trans = store.get_transitive_dependencies(target_id, direction="outgoing", max_depth=args.max_depth)
        print(f"\n[*] Transitive dependencies (outgoing, max_depth={args.max_depth}) ({len(trans['dependencies'])}):")
        for dep in trans["dependencies"]:
            print(f"    - depth {dep['depth']}: {dep['resource_id']} ({dep['type']}) path: {dep['path']}")

    store.close()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_run_cli())
