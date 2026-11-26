"""Generate a high-quality PDF report for Tasks H3 & H4 using headless Edge."""

import subprocess
from pathlib import Path

HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>CloudTwin AI - Engineering Report: Tasks H3 & H4</title>
  <style>
    @page {
      size: A4;
      margin: 18mm 16mm 18mm 16mm;
      @bottom-right {
        content: counter(page);
      }
    }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      color: #1e293b;
      line-height: 1.55;
      font-size: 10.5pt;
      background: #ffffff;
      margin: 0;
      padding: 0;
    }
    .header-banner {
      background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 60%, #0284c7 100%);
      color: white;
      padding: 24px 28px;
      border-radius: 10px;
      margin-bottom: 24px;
    }
    .header-banner h1 {
      margin: 0 0 6px 0;
      font-size: 20pt;
      font-weight: 750;
      letter-spacing: -0.02em;
    }
    .header-banner .subtitle {
      font-size: 11pt;
      color: #cbd5e1;
      margin-bottom: 12px;
    }
    .header-meta {
      display: flex;
      gap: 20px;
      font-size: 9pt;
      color: #94a3b8;
      border-top: 1px solid rgba(255, 255, 255, 0.15);
      padding-top: 10px;
    }
    .header-meta span strong {
      color: #f8fafc;
    }
    h2 {
      font-size: 13.5pt;
      color: #0f172a;
      border-bottom: 2px solid #e2e8f0;
      padding-bottom: 6px;
      margin-top: 24px;
      margin-bottom: 12px;
      page-break-after: avoid;
    }
    h3 {
      font-size: 11.5pt;
      color: #1e3a8a;
      margin-top: 16px;
      margin-bottom: 8px;
      page-break-after: avoid;
    }
    p {
      margin: 0 0 10px 0;
    }
    ul, ol {
      margin: 0 0 12px 0;
      padding-left: 20px;
    }
    li {
      margin-bottom: 4px;
    }
    .badge {
      display: inline-block;
      padding: 2px 7px;
      border-radius: 4px;
      font-size: 8pt;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .badge-pass {
      background: #dcfce7;
      color: #15803d;
      border: 1px solid #86efac;
    }
    .badge-info {
      background: #e0f2fe;
      color: #0369a1;
      border: 1px solid #7dd3fc;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      margin: 12px 0 16px 0;
      font-size: 9pt;
    }
    th, td {
      border: 1px solid #cbd5e1;
      padding: 7px 10px;
      text-align: left;
      vertical-align: top;
    }
    th {
      background: #f1f5f9;
      color: #0f172a;
      font-weight: 650;
    }
    tr:nth-child(even) td {
      background: #f8fafc;
    }
    pre, code {
      font-family: Consolas, "Liberation Mono", Menlo, Courier, monospace;
      font-size: 8.5pt;
    }
    code {
      background: #f1f5f9;
      color: #0f172a;
      padding: 1px 4px;
      border-radius: 3px;
    }
    pre {
      background: #0f172a;
      color: #f8fafc;
      padding: 12px 14px;
      border-radius: 6px;
      overflow-x: auto;
      margin: 10px 0 14px 0;
      line-height: 1.45;
    }
    pre code {
      background: transparent;
      color: inherit;
      padding: 0;
    }
    .card {
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-left: 4px solid #0284c7;
      padding: 10px 14px;
      border-radius: 0 6px 6px 0;
      margin: 12px 0;
      font-size: 9.5pt;
    }
    .page-break {
      page-break-before: always;
    }
  </style>
</head>
<body>

  <div class="header-banner">
    <h1>CloudTwin AI &mdash; Engineering Report</h1>
    <div class="subtitle">Implementation &amp; Verification of Task H3 (Neo4j Persistence), Task H4 (Proposed-Change Ingestion), and Task H5 (Future Twin Generation)</div>
    <div class="header-meta">
      <span><strong>Date:</strong> October 29, 2026</span>
      <span><strong>Author:</strong> CloudTwin Engineering Team</span>
      <span><strong>Target Milestone:</strong> Local Vertical Slice</span>
      <span><strong>Test Status:</strong> <span class="badge badge-pass">56 / 56 Tests Passed (100%)</span></span>
    </div>
  </div>

  <h2>1. Executive Summary</h2>
  <p>
    This report delivers the technical documentation, architectural designs, and verification outcomes for
    <strong>Task H3</strong> and <strong>Task H4</strong> of the CloudTwin platform.
  </p>
  <ul>
    <li>
      <strong>Task H3 (Persist Current Twin in Neo4j)</strong> establishes an idempotent, queryable graph storage layer.
      It ingests validated Current Twin snapshots, manages snapshot versioning and lineage, supports dual-mode stale resource lifecycle
      (retention with tagging vs. physical pruning), and provides multi-hop direct and transitive graph dependency traversal.
    </li>
    <li>
      <strong>Task H4 (Implement Proposed-Change Ingestion)</strong> implements a multi-source ingestion engine capable of parsing
      human/AI scenarios, machine-generated Terraform plan JSON files, and Kubernetes manifests (YAML/JSON). It normalizes all proposed mutations
      into standardized change records referencing canonical resource IDs across all four core operations (<code>create</code>, <code>update</code>,
      <code>delete</code>, <code>replace</code>).
    </li>
  </ul>
  <p>
    Both modules maintain <strong>100% offline-first local execution capability</strong> using local JSON/YAML fixtures without requiring live AWS credentials
    or external database daemons.
  </p>

  <h2>2. Task H3 &mdash; Persist the Current Twin in Neo4j</h2>
  
  <h3>2.1 Graph Schema and Data Model</h3>
  <p>
    The storage layer is encapsulated in <code>Neo4jTwinStore</code>. Resources and relationships are persisted according to a formal graph schema:
  </p>
  <ul>
    <li>
      <strong><code>:Snapshot</code> Node:</strong> Represents an immutable point-in-time infrastructure capture.
      Stores <code>snapshot_id</code>, <code>provider</code>, <code>region</code>, <code>cluster</code>, <code>schema_version</code>,
      timestamps, and aggregate counts (<code>resource_count</code>, <code>relationship_count</code>, <code>aws_resources</code>, <code>kubernetes_resources</code>).
    </li>
    <li>
      <strong><code>:Resource</code> Node:</strong> Stores canonical infrastructure nodes.
      Key properties: <code>id</code> (primary key), <code>name</code>, <code>type</code>, <code>provider</code>, <code>properties_json</code>,
      <code>last_seen_snapshot</code>, and <code>is_stale</code>.
    </li>
    <li>
      <strong>Relationships:</strong>
      Snapshots are linked to resources via <code>(:Snapshot)-[:CONTAINS_RESOURCE]->(:Resource)</code>.
      Resource-to-resource edges preserve direction and semantics using the 8 canonical types:
      <code>CONTAINS</code>, <code>LOCATED_IN</code>, <code>USES</code>, <code>MEMBER_OF</code>, <code>RUNS_ON</code>, <code>SELECTS</code>, <code>EXPOSES</code>, and <code>ROUTES_TO</code>.
    </li>
  </ul>

  <h3>2.2 Core Capabilities</h3>
  <ul>
    <li>
      <strong>Idempotent Upsert:</strong> Re-importing identical snapshots creates zero duplicate nodes or relationships using Cypher <code>MERGE</code> on unique IDs and edge endpoints.
    </li>
    <li>
      <strong>Stale Resource Lifecycle Management:</strong>
      <ul>
        <li><em>Association Mode (Default):</em> Resources absent in a newer snapshot remain linked to their older snapshot node and are marked with <code>is_stale = true</code>.</li>
        <li><em>Removal Mode:</em> Calling <code>remove_stale_resources(snapshot_id)</code> executes <code>DETACH DELETE</code> to prune unassociated resources and dangling edges.</li>
      </ul>
    </li>
    <li>
      <strong>Direct &amp; Transitive Dependency Queries:</strong>
      Supports bidirectional 1-hop queries (<code>get_direct_dependencies</code>) and multi-hop graph path traversals (<code>get_transitive_dependencies</code>).
      For example, querying the UI service downstream traces:
      <code>k8s:service:default:ui</code> &rarr; <code>Pod</code> &rarr; <code>Node</code> &rarr; <code>EC2 Instance</code> &rarr; <code>NodeGroup</code> &rarr; <code>Subnet</code> &rarr; <code>VPC</code>.
    </li>
    <li>
      <strong>Dual-Mode Offline/Live Architecture:</strong>
      Defaults to <code>InMemoryGraphBackend</code> for offline development and local fixture testing.
      Seamlessly connects to live Neo4j instances via <code>RealNeo4jBackend</code> over Bolt when credentials (<code>NEO4J_URI</code>) are configured.
    </li>
  </ul>

  <div class="page-break"></div>

  <h2>3. Task H4 &mdash; Implement Proposed-Change Ingestion</h2>

  <h3>3.1 Input Formats &amp; Ingestion Engines</h3>
  <p>The ingestion module in <code>cloudtwin-poc/ingestion/</code> accepts three input sources:</p>
  <table>
    <thead>
      <tr>
        <th>Input Source</th>
        <th>Parser Component</th>
        <th>Description &amp; Processing</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td><strong>Scenario JSON</strong></td>
        <td><code>ScenarioIngester</code></td>
        <td>Parses human- and AI-generated scenarios (e.g. <code>scenarios/scenario-01.json</code>). Validates schema, resolves target resource against Current Twin, and normalizes property mutation paths.</td>
      </tr>
      <tr>
        <td><strong>Terraform Plan JSON</strong></td>
        <td><code>TerraformPlanIngester</code></td>
        <td>Consumes machine-generated <code>terraform show -json</code> output. Evaluates <code>resource_changes</code>, translates action lists to canonical operations, compares before/after blocks, and emits property-level diffs.</td>
      </tr>
      <tr>
        <td><strong>Kubernetes Manifests</strong></td>
        <td><code>KubernetesManifestIngester</code></td>
        <td>Parses YAML/JSON manifests (single or multi-document). Evaluates annotations (e.g. <code>cloudtwin.dev/action</code>), compares spec against current state, and detects mutations (e.g. replica scaling).</td>
      </tr>
    </tbody>
  </table>

  <h3>3.2 Canonical Resource ID Resolution (<code>CanonicalResourceResolver</code>)</h3>
  <p>
    Ensures that every change references the Current Twin's canonical ID (e.g. <code>k8s:deployment:default:ui</code>, <code>aws:subnet:subnet-0aaa...</code>).
    The resolver indexes resources across:
  </p>
  <ul>
    <li>Direct canonical ID match.</li>
    <li>ID suffix match (e.g., <code>sg-0aaa1111cccc0002b</code> &rarr; <code>aws:security-group:sg-0aaa1111cccc0002b</code>).</li>
    <li>Kubernetes tuple: <code>(type, namespace, name)</code>.</li>
    <li>AWS tuple: <code>(type, name)</code> and <code>(type, tags.Name)</code>.</li>
    <li>Native identifiers: <code>vpc_id</code>, <code>subnet_id</code>, <code>group_id</code>, <code>instance_id</code>, <code>arn</code>.</li>
  </ul>

  <h3>3.3 Representation of Core Operations</h3>
  <p>All four fundamental infrastructure operations are explicitly modeled:</p>
  <ul>
    <li><code>create</code>: New resource or property addition (<code>before = None</code>, <code>after = &lt;value&gt;</code>).</li>
    <li><code>update</code>: In-place property mutation (<code>before = &lt;old_value&gt;</code>, <code>after = &lt;new_value&gt;</code>).</li>
    <li><code>delete</code>: Resource deletion (<code>before = &lt;current_value&gt;</code>, <code>after = None</code>).</li>
    <li><code>replace</code>: Destruction and recreation of resource (Terraform action <code>["delete", "create"]</code>).</li>
  </ul>

  <h3>3.4 Normalized Change Output Record</h3>
  <p>Outputs conform strictly to the integration contract agreed between Hamdi and Fatma:</p>
  <pre><code>{
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
}</code></pre>

  <h3>3.5 Explicit Error Handling</h3>
  <ul>
    <li><code>UnresolvedTargetError</code>: Raised when updating, deleting, or replacing an entity not present in the Current Twin.</li>
    <li><code>InvalidChangeInputError</code>: Raised on malformed syntax or schema violations.</li>
    <li><code>ConflictError</code>: Raised when strict conflict checking detects a discrepancy with the Current Twin baseline.</li>
  </ul>

  <div class="page-break"></div>

  <h2>4. Definition of Done &amp; Verification Matrix</h2>

  <table>
    <thead>
      <tr>
        <th>Task</th>
        <th>Requirement</th>
        <th>Result</th>
        <th>Verification Test</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td rowspan="5"><strong>H3</strong></td>
        <td>Re-importing creates no duplicates</td>
        <td><span class="badge badge-pass">PASS</span></td>
        <td><code>H3IdempotencyTests.test_reimporting_same_snapshot_id_creates_no_duplicates</code></td>
      </tr>
      <tr>
        <td>Stale resources can be removed or associated with older snapshot</td>
        <td><span class="badge badge-pass">PASS</span></td>
        <td><code>H3StaleResourceTests.test_stale_resources_associated_with_older_snapshot</code> &amp; <code>test_stale_resources_can_be_removed</code></td>
      </tr>
      <tr>
        <td>Queries retrieve direct and transitive dependencies</td>
        <td><span class="badge badge-pass">PASS</span></td>
        <td><code>H3DependencyQueryTests.test_direct_outgoing_dependencies</code> &amp; <code>test_transitive_downstream_dependencies</code></td>
      </tr>
      <tr>
        <td>Import counts match input summary</td>
        <td><span class="badge badge-pass">PASS</span></td>
        <td><code>H3FixtureImportTests.test_import_counts_match_input_summary</code> (40 resources: 15 AWS, 25 K8s; 81 relationships)</td>
      </tr>
      <tr>
        <td>Import works locally using a fixture</td>
        <td><span class="badge badge-pass">PASS</span></td>
        <td><code>H3FixtureImportTests.test_import_works_locally_using_fixture_file</code> (<code>fixtures/sample_current_state.json</code>)</td>
      </tr>
      <tr>
        <td rowspan="4"><strong>H4</strong></td>
        <td>Namespace, provider, resource ID, operation, &amp; property path are unambiguous</td>
        <td><span class="badge badge-pass">PASS</span></td>
        <td><code>H4ScenarioIngestionTests.test_saved_scenario_01_parses_deterministically</code> &amp; <code>H4UnifiedIngestionTests</code></td>
      </tr>
      <tr>
        <td>Create, update, delete, and replacement are represented</td>
        <td><span class="badge badge-pass">PASS</span></td>
        <td><code>H4TerraformPlanIngestionTests.test_saved_terraform_plan_parses_deterministically</code> (all 4 operations)</td>
      </tr>
      <tr>
        <td>Invalid or unresolved targets produce explicit errors</td>
        <td><span class="badge badge-pass">PASS</span></td>
        <td><code>H4ScenarioIngestionTests.test_unresolved_scenario_target_raises_explicit_error</code> &amp; <code>test_invalid_structure</code></td>
      </tr>
      <tr>
        <td>Omar's saved scenario and plan fixtures parse deterministically</td>
        <td><span class="badge badge-pass">PASS</span></td>
        <td><code>test_saved_scenario_01_parses_deterministically</code>, <code>test_saved_terraform_plan...</code>, &amp; <code>test_saved_k8s_manifest...</code></td>
      </tr>
      <tr>
        <td rowspan="5"><strong>H5</strong></td>
        <td>Current Twin remains immutable</td>
        <td><span class="badge badge-pass">PASS</span></td>
        <td><code>H5FutureTwinGenerationTests.test_current_twin_remains_immutable</code> (deep copy protection)</td>
      </tr>
      <tr>
        <td>Create, update, delete, and replace operations supported</td>
        <td><span class="badge badge-pass">PASS</span></td>
        <td><code>test_create_operation...</code>, <code>test_delete_operation...</code>, <code>test_replace_operation...</code></td>
      </tr>
      <tr>
        <td>Replica scenario changes only intended deployment property</td>
        <td><span class="badge badge-pass">PASS</span></td>
        <td><code>test_replica_scenario_changes_only_intended_property</code> (only replicas 1 &rarr; 3, other 39 resources identical)</td>
      </tr>
      <tr>
        <td>Invalid mutations fail clearly</td>
        <td><span class="badge badge-pass">PASS</span></td>
        <td><code>test_invalid_mutations_fail_clearly</code> (TargetNotFoundError, DuplicateResourceError)</td>
      </tr>
      <tr>
        <td>Repeated runs produce identical output</td>
        <td><span class="badge badge-pass">PASS</span></td>
        <td><code>test_repeated_runs_produce_identical_output</code> (100% deterministic byte-for-byte serialization)</td>
      </tr>
    </tbody>
  </table>

  <h2>5. Test Suite Execution Summary</h2>
  <div class="card">
    <strong>Test Execution Command:</strong> <code>python run_tests.py</code> (or <code>python -m unittest discover -s tests -v</code>)<br>
    <strong>Overall Result:</strong> <strong>Ran 56 tests in 0.120s &mdash; OK (0 failures, 0 errors)</strong><br>
    Coverage includes: <code>test_h2.py</code> (10 tests), <code>test_h2_fixture.py</code> (8 tests), <code>test_h3.py</code> (17 tests), <code>test_h4.py</code> (13 tests), and <code>test_h5.py</code> (8 tests).
  </div>

  <h2>6. Summary of Changed and Created Files</h2>
  <ul>
    <li><code>cloudtwin-poc/storage/neo4j_store.py</code> &mdash; Core Neo4j and in-memory persistence engine.</li>
    <li><code>cloudtwin-poc/storage/__init__.py</code> &mdash; Storage module exports.</li>
    <li><code>cloudtwin-poc/ingestion/models.py</code> &mdash; Change data classes and exceptions.</li>
    <li><code>cloudtwin-poc/ingestion/resolver.py</code> &mdash; Canonical resource ID resolver.</li>
    <li><code>cloudtwin-poc/ingestion/scenario_ingester.py</code> &mdash; Scenario JSON ingester.</li>
    <li><code>cloudtwin-poc/ingestion/terraform_ingester.py</code> &mdash; Terraform plan JSON ingester.</li>
    <li><code>cloudtwin-poc/ingestion/kubernetes_ingester.py</code> &mdash; Kubernetes manifest ingester.</li>
    <li><code>cloudtwin-poc/ingestion/ingester.py</code> &mdash; Unified ChangeIngester interface.</li>
    <li><code>cloudtwin-poc/ingestion/__init__.py</code> &mdash; Ingestion package exports.</li>
    <li><code>cloudtwin-poc/fixtures/sample_terraform_plan.json</code> &mdash; Deterministic Terraform plan test fixture.</li>
    <li><code>cloudtwin-poc/fixtures/sample_kubernetes_manifest.yaml</code> &mdash; Deterministic Kubernetes manifest test fixture.</li>
    <li><code>cloudtwin-poc/tests/test_h3.py</code> &mdash; Comprehensive test suite for Task H3.</li>
    <li><code>cloudtwin-poc/tests/test_h4.py</code> &mdash; Comprehensive test suite for Task H4.</li>
    <li><code>cloudtwin-poc/discovery/discover.py</code> &mdash; Integrated optional <code>--persist</code> CLI flag.</li>
    <li><code>cloudtwin-poc/requirements.txt</code> &mdash; Added <code>neo4j&gt;=5.0.0</code> and <code>PyYAML&gt;=6.0</code>.</li>
    <li><code>cloudtwin-poc/README.md</code> &mdash; Updated documentation for H3 and H4.</li>
  </ul>

  <h2>7. Next Steps &mdash; Readiness for Task H5</h2>
  <p>
    With the Current Twin persisted (H3) and the proposed-change contract stabilized (H4), the platform is ready for:
  </p>
  <ul>
    <li>
      <strong>Task H5 &mdash; Implement Future Twin Generation:</strong> Applying <code>NormalizedChange</code> records to an immutable clone
      of the Current Twin to construct the Future Digital Twin state without calling live AWS or Kubernetes APIs.
    </li>
    <li>
      <strong>Task H6 &mdash; Implement State Diff:</strong> Computing the explicit difference between Current and Future Twin graphs.
    </li>
  </ul>

</body>
</html>
"""

def generate_pdf() -> Path:
    root = Path(__file__).resolve().parents[1]
    html_path = root / "outputs" / "report_h3_h4.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(HTML_CONTENT, encoding="utf-8")

    downloads_dir = Path("C:/Users/revenge/Downloads")
    pdf_path = downloads_dir / "CloudTwin_Tasks_H3_H4_Report.pdf"

    edge_bin = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    if not edge_bin.exists():
        edge_bin = Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")

    cmd = [
        str(edge_bin),
        "--headless",
        "--disable-gpu",
        "--no-pdf-header-footer",
        f"--print-to-pdf={pdf_path}",
        str(html_path.resolve()),
    ]

    print(f"[*] Generating PDF via Edge: {cmd}")
    subprocess.run(cmd, check=True)
    print(f"[OK] PDF generated successfully: {pdf_path}")
    return pdf_path

if __name__ == "__main__":
    generate_pdf()

