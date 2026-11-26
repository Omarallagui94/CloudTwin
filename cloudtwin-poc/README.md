# CloudTwin AI - Automatic Discovery and Current Twin

This proof of concept performs read-only discovery of the AWS account in `eu-west-3` and the Kubernetes cluster selected by the local kubeconfig. It normalizes the discovered resources and relationships into a Current Digital Twin, then renders that twin as an interactive HTML graph.

It does not run Terraform, apply Kubernetes manifests, or change cloud resources. The proposed scenario is data only and is not executed.

## Discovered resources

AWS discovery includes VPCs, subnets, security groups, EC2 instances, EKS clusters and managed node groups, Elastic Load Balancers (v2 and classic), and IAM roles.

Kubernetes discovery includes namespaces, nodes, deployments, pods, services, StatefulSets, service accounts, ingresses, and network policies. Kubernetes Secrets are not read.

Relationships are created only from discovered metadata, including VPC and subnet membership, EKS/node-group membership, node provider IDs, Kubernetes selectors, pod scheduling, service accounts, ingress backends, and load-balancer hostnames.

## Prerequisites

- Python 3.10 or newer
- AWS CLI authentication that can read the listed AWS services
- A kubeconfig whose current context points to the `retail-store` EKS cluster
- Network access to AWS APIs and the Kubernetes API server

Confirm access from Windows CMD:

```bat
aws sts get-caller-identity
kubectl config current-context
kubectl get nodes
```

AWS authentication uses Boto3's standard credential resolution chain. No access key or secret is stored by this project. Kubernetes authentication uses the existing kubeconfig, normally `%USERPROFILE%\.kube\config`, including its configured EKS authentication command.

## Install

Run these commands in Windows CMD:

```bat
cd path\to\CloudTwin\cloudtwin-poc
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

## Discover the Current Twin

```bat
python discovery\discover.py
```

The command continues when an individual AWS service or Kubernetes resource API cannot be read. It reports a warning and leaves that collection empty. Review warnings before using the output in a demonstration.

The normalized result is written to:

```text
outputs\current_state.json
```

Each run replaces that generated file with a fresh snapshot of the live environment.

## Build the interactive graph

After discovery, run:

```bat
python visualization\build_graph.py
```

Open the generated graph in a browser:

```bat
start outputs\current_twin.html
```

Hover over a node to inspect its discovered properties. Use the selection and filter menus to reduce visual noise in a large account.

## Run the Streamlit dashboard

The dashboard tests the configured AWS, EKS, and Kubernetes connections, runs the same read-only collectors, displays live metrics, and embeds the interactive graph:

```bat
streamlit run app.py
```

Use **Test Connection** in the sidebar, then **Discover Infrastructure** to regenerate both output files from the current environment.

## Proposed future change

`scenarios\scenario-01.json` describes a proposed increase of the `ui` Deployment from one to three replicas. It is not applied. A later Future Twin engine can consume this scenario, create a virtual future state, compare it with the Current Twin, and assess impact and risk.

## Project layout

```text
cloudtwin-poc\
|-- discovery\
|   |-- aws_collector.py
|   |-- kubernetes_collector.py
|   |-- normalizer.py
|   `-- discover.py
|-- visualization\
|   `-- build_graph.py
|-- scenarios\
|   `-- scenario-01.json
|-- outputs\
|   |-- current_state.json
|   `-- current_twin.html
|-- requirements.txt
`-- README.md
```

`current_state.json` and `current_twin.html` are generated artifacts. The JSON snapshot may contain account identifiers, ARNs, private IP addresses, and resource metadata; review it before sharing publicly.



## H1 — Versioned Common Schema

The common data contract is version **1.0.0**, defined in `schema/v1/`.

### Schema files
- `twin.schema.json` — current infrastructure snapshot.
- `scenario.schema.json` — infrastructure change scenario.
- `diff.schema.json` — resource changes between snapshots.
- `impact.schema.json` — direct, indirect, and risk impacts.

The Twin schema defines reusable `Resource` and `Relationship` structures under `$defs`.

### Resource IDs
- AWS resources use the `aws:` prefix.
- Kubernetes resources use the `k8s:` prefix.
- The Internet sentinel uses `aws:internet`.

### Relationship direction
Each relationship is a directed edge: `source` → `target`. Supported types are `CONTAINS`, `LOCATED_IN`, `USES`, `MEMBER_OF`, `RUNS_ON`, `SELECTS`, `EXPOSES`, and `ROUTES_TO`.

### Validation
Run `python schema/v1/validate_snapshot.py` to validate the sample snapshot. Pass another snapshot path as an argument to validate that file.

The validator checks schema compliance, duplicate IDs, dangling relationship endpoints, allowed relationship types, and summary counts. Snapshot metadata and collector warnings are retained by the normalizer.

## H3 — Persist the Current Twin in Neo4j

Persist validated Current Twin snapshots into Neo4j as an idempotent, replaceable, queryable graph.

### Capabilities
- **Idempotent Upsert**: Re-importing identical snapshots creates no duplicates. Uses `MERGE` on unique resource IDs and relationship endpoints.
- **Snapshot Metadata**: Each import creates a `:Snapshot` node recording provider, region, cluster, timestamps, schema version, and resource counts.
- **Stale Resource Lifecycle**: Resources absent from newer snapshots can be retained and associated with historical snapshots (`is_stale=True`) or pruned on demand (`remove_stale=True`).
- **Direct & Transitive Dependency Queries**: Cypher-based graph traversals support downstream (outgoing), upstream (incoming), and bidirectional dependency analysis with depth limiting.
- **Local Fixture Support**: Runs offline out-of-the-box using the local JSON fixture with an embedded in-memory graph engine, or connects to a live Neo4j instance via Bolt.

### Usage

Persist a snapshot locally:

```bat
python storage\neo4j_store.py --fixture fixtures\sample_current_state.json --query-dep k8s:service:default:ui
```

Connect to a live Neo4j database:

```bat
python storage\neo4j_store.py --uri bolt://localhost:7687 --user neo4j --password secret --fixture fixtures\sample_current_state.json
```

Or configure environment variables:
- `NEO4J_URI`: Bolt connection URI (e.g. `bolt://localhost:7687` or `neo4j://localhost:7687`)
- `NEO4J_USER`: Database username (default: `neo4j`)
- `NEO4J_PASSWORD`: Database password

### Running Tests

```bat
python -m unittest discover -s tests -v
```

## H4 — Proposed-Change Ingestion

Ingest proposed infrastructure changes from simple scenarios, machine-generated Terraform plans, and Kubernetes manifests into normalized change records with canonical resource IDs.

### Supported Input Sources
- **Scenario JSON**: e.g. `scenarios/scenario-01.json` targeting Kubernetes or AWS resources.
- **Terraform Plan JSON**: Machine-generated `terraform show -json` output (e.g. `fixtures/sample_terraform_plan.json`).
- **Kubernetes Manifests**: Multi-document or single-document YAML/JSON (e.g. `fixtures/sample_kubernetes_manifest.yaml`).

### Normalized Change Record Format
Each ingested change conforms to the agreed integration contract:
```json
{
  "change_id": "change-s01-001",
  "operation": "update",
  "resource_id": "k8s:deployment:default:ui",
  "resource_type": "DEPLOYMENT",
  "provider": "kubernetes",
  "namespace": "default",
  "property_path": "properties.replicas",
  "before": 1,
  "after": 3,
  "source": {
    "kind": "scenario",
    "reference": "S01",
    "name": "Increase UI replicas"
  }
}
```

### Operations
All 4 primary infrastructure operations are represented:
- `create`: New resource or property addition (`before: None`, `after: <val>`).
- `update`: In-place mutation of existing property (`before: <val>`, `after: <val>`).
- `delete`: Resource deletion (`before: <val>`, `after: None`).
- `replace`: Destruction and recreation of resource (`actions: ["delete", "create"]`).

### Error Handling
- Unresolved target resources against Current Twin raise `UnresolvedTargetError`.
- Structural or format invalidity raises `InvalidChangeInputError`.
- Expected prior property conflicts raise `ConflictError`.

## H5 — Future Twin Generation

Apply normalized proposed changes to a copy of the Current Twin to produce a virtual Future Digital Twin without touching AWS or Kubernetes APIs.

### Key Guarantees
- **Immutability**: The Current Twin is strictly deep-copied and never mutated in place.
- **Full Operation Support**:
  - `create`: Inserts new resource and increments provider summary count.
  - `update`: Mutates designated property path (`properties.replicas`).
  - `delete`: Removes target resource and detaches/removes connecting relationships to eliminate dangling edges.
  - `replace`: Replaces properties while preserving lineage.
- **Replica Scenario Isolation**: Applying `scenario-01.json` modifies only `k8s:deployment:default:ui.properties.replicas` (from 1 to 3); all other 39 resources and 81 relationships remain identical.
- **Explicit Failure Modes**: Missing mutation targets raise `TargetNotFoundError`; duplicate creations raise `DuplicateResourceError`.
- **Determinism**: Outputs are deterministically ordered and repeatable byte-for-byte.
- **Contract & Schema Compliance**: Output validates against `schema/v1/twin.schema.json` and supplies change provenance linking each modified resource to its change records.

### Usage Example

```python
from future_twin import FutureTwinGenerator

generator = FutureTwinGenerator()
future = generator.generate(
    current_twin="fixtures/sample_current_state.json",
    changes="scenarios/scenario-01.json",
)

print("Future UI replicas:", future.get_resource("k8s:deployment:default:ui")["properties"]["replicas"])
print("Provenance:", future.get_provenance("k8s:deployment:default:ui"))
```

## H6 — State Diff

Explain exactly what changed between Current Twin and Future Twin snapshots. Produces machine-readable added, removed, modified, and replaced resources plus property-level changes.

### Key Capabilities
- **Canonical Resource IDs**: Every diff record identifies resources using agreed canonical IDs (`aws:*`, `k8s:*`).
- **Preserved Property Paths & Values**: Changes retain their dotted property paths (e.g. `properties.replicas`, `properties.tags.Environment`) alongside both `before` and `after` values.
- **Unchanged Resources Excluded**: Unmodified resources produce no diff noise. In `scenario-01`, only `k8s:deployment:default:ui` is emitted; all other 39 resources are omitted.
- **Contract & Schema Compliance**: Output strictly validates against `schema/v1/diff.schema.json` via JSON Schema Draft 2020-12.
- **All Four Operations Covered**: Emits `ADDED`, `REMOVED`, `MODIFIED`, and `REPLACED` records with aggregate summary counters.

### CLI Usage

```bat
python diff\differ.py --current fixtures\sample_current_state.json --future outputs\future_state.json --out outputs\state_diff.json --validate
```

### Python API Example

```python
from diff import compute_diff

state_diff = compute_diff(
    current_twin="fixtures/sample_current_state.json",
    future_twin="outputs/future_state.json",
)

print("Summary:", state_diff.summary)
for change in state_diff.changes:
    print(f"[{change.change_type.value}] {change.resource_id} -> {change.property}: {change.before} => {change.after}")
```

### Running All Tests (H2 - H6)

```bat
python run_tests.py
```

## H7 — Affected-Resource Analysis

Determine structurally affected resources from the State Diff and infrastructure relationship graph. Computes direct and transitive affected-resource paths with exact graph distances and relationship semantics.

### Key Capabilities
- **Deterministic Paths**: Traversal is completely deterministic. Repeated runs produce identical orderings, paths, and explanations.
- **Directional & Relationship Semantics**: Traverses outgoing, incoming, or bidirectional edges with context-aware semantic explanations (`EXPOSES`, `SELECTS`, `ROUTES_TO`, `RUNS_ON`, `CONTAINS`, `LOCATED_IN`, `USES`, `MEMBER_OF`).
- **Direct vs. Transitive Distinction**: Explicitly categorizes direct impact (`DIRECT`, distance $\le$ 1, including the mutation target and directly connected neighbors) from inferred/transitive impact (`INDIRECT`, distance $\ge$ 2).
- **Cycle Prevention**: Visited sets per change root prevent infinite loops or redundant traversal in cyclic graphs.
- **Path Reasoning**: Explains the exact path connecting each change to each affected resource.
- **Schema Validation**: Output strictly validates against `schema/v1/impact.schema.json`.

### CLI Usage

```bat
python impact\analyzer.py --diff outputs\state_diff.json --relationships fixtures\sample_current_state.json --scenario-id scenario-01 --max-depth 3 --out outputs\impact_analysis.json --validate
```

### Python API Example

```python
from impact import analyze_impact

impact_result = analyze_impact(
    diff="outputs/state_diff.json",
    relationships="fixtures/sample_current_state.json",
    scenario_id="scenario-01",
    max_depth=3,
)

print("Total affected resources:", impact_result.summary["total_affected"])
for affected in impact_result.impacts:
    print(f"[{affected.impact_type.value}] dist={affected.distance} {affected.resource_id}")
    print(f"   Reason: {affected.reason}")
```

### Running Full Pipeline Tests (H2 - H7)

```bat
python run_tests.py
```

## H8 — Backend API

Stable REST API exposing the complete CloudTwin pipeline so that consumers (e.g. Fatma's integration) can call simple HTTP endpoints without reading Neo4j, loading files, or importing Python modules directly.

**Technology:** FastAPI + Starlette (already bundled with Streamlit's dependencies). Tests use FastAPI's `TestClient` backed by `httpx`, running completely in-process.

### API Route Groups

| Group | Method + Path | Description |
|---|---|---|
| Health | `GET /health` | Liveness probe |
| Snapshots | `POST /snapshots/load` | Load a Current Twin fixture into the session store |
| Snapshots | `GET /snapshots` | List loaded snapshot IDs |
| Snapshots | `GET /snapshots/{id}` | Retrieve full snapshot (resources + relationships) |
| Snapshots | `GET /snapshots/{id}/summary` | Lightweight count summary only |
| Simulations | `POST /simulations` | Run a Future Twin simulation from a snapshot + change file |
| Simulations | `GET /simulations` | List simulation IDs |
| Simulations | `GET /simulations/{id}` | Retrieve Future Twin + provenance |
| Diffs | `POST /diffs` | Compute State Diff between snapshot and simulation |
| Graph | `POST /graph/query` | Direct or transitive dependency query |
| Impact | `POST /impact` | Structural affected-resource analysis |

### Starting the API Server

```bat
cd cloudtwin-poc
uvicorn api.routes:app --reload --port 8000
```

Then browse to `http://localhost:8000/docs` for the interactive Swagger UI.

### Quick API Example (curl)

```bat
:: 1. Load snapshot
curl -X POST http://localhost:8000/snapshots/load -H "Content-Type: application/json" ^
     -d "{\"fixture_path\": \"fixtures/sample_current_state.json\"}"

:: 2. Run simulation
curl -X POST http://localhost:8000/simulations -H "Content-Type: application/json" ^
     -d "{\"snapshot_id\": \"sample_current_state\", \"change_source\": \"scenarios/scenario-01.json\"}"

:: 3. Compute diff (use simulation_id from step 2)
curl -X POST http://localhost:8000/diffs -H "Content-Type: application/json" ^
     -d "{\"snapshot_id\": \"sample_current_state\", \"simulation_id\": \"<sim-id>\"}"

:: 4. Impact analysis
curl -X POST http://localhost:8000/impact -H "Content-Type: application/json" ^
     -d "{\"snapshot_id\": \"sample_current_state\", \"simulation_id\": \"<sim-id>\", \"max_depth\": 3}"
```

### Python TestClient Example (integration test style)

```python
from fastapi.testclient import TestClient
from api.routes import _make_app

client = TestClient(_make_app())

# 1-5. Full pipeline in-process
client.post("/snapshots/load", json={"fixture_path": "fixtures/sample_current_state.json"})
sim = client.post("/simulations", json={"snapshot_id": "sample_current_state", "change_source": "scenarios/scenario-01.json"}).json()
diff = client.post("/diffs", json={"snapshot_id": "sample_current_state", "simulation_id": sim["simulation_id"]}).json()
impact = client.post("/impact", json={"snapshot_id": "sample_current_state", "simulation_id": sim["simulation_id"]}).json()

print("Changes:", diff["summary"]["total_changes"])
print("Affected resources:", impact["summary"]["total_affected"])
```

### Running Full Pipeline Tests (H2 - H8)

```bat
python run_tests.py
```





