"""Streamlit dashboard for the read-only CloudTwin proof of concept.

Supports two operating modes:

1. **Live AWS Mode** – uses boto3 credentials, tests AWS/EKS/Kubernetes
   connections and runs real infrastructure discovery.
2. **Local Fixture Mode** – loads a sanitized Current Twin fixture from
   ``fixtures/`` without calling any AWS or Kubernetes APIs.  This is the
   default when AWS credentials are unavailable, allowing local development
   of Future Twin, Diff, and graph integration without cloud access.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "outputs" / "current_state.json"
GRAPH_PATH = ROOT / "outputs" / "current_twin.html"
SCENARIO_PATH = ROOT / "scenarios" / "scenario-01.json"
FIXTURE_PATH = ROOT / "fixtures" / "sample_current_state.json"

# ---------------------------------------------------------------------------
# Mode helpers
# ---------------------------------------------------------------------------

_CREDENTIAL_HINTS = (
    "unable to locate credentials",
    "no credentials",
    "expired token",
    "invalidclienttokenid",
    "credential",
)


def _is_credential_error(exc: Exception) -> bool:
    """Return True when *exc* looks like a missing/invalid-credential error."""
    msg = str(exc).lower()
    return any(hint in msg for hint in _CREDENTIAL_HINTS)


def detect_aws_credentials() -> bool:
    """Return True when boto3 can resolve credentials without making an API call."""
    try:
        import boto3  # noqa: delay import so Local Fixture Mode never needs boto3 installed
        session = boto3.Session()
        creds = session.get_credentials()
        return creds is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Page configuration & styles
# ---------------------------------------------------------------------------

def configure_page() -> None:
    st.set_page_config(page_title="CloudTwin AI", page_icon="CT", layout="wide")
    st.markdown(
        """
        <style>
        .stApp { background: #f6f8fc; }
        .block-container { max-width: 1500px; padding-top: 2rem; }
        .hero {
            padding: 1.5rem 1.75rem; border-radius: 16px; color: white;
            background: linear-gradient(120deg, #0f172a 0%, #0f4c81 55%, #0369a1 100%);
            box-shadow: 0 10px 30px rgba(15, 23, 42, .14); margin-bottom: 1.5rem;
        }
        .hero h1 { margin: 0; font-size: 2.2rem; }
        .hero p { margin: .35rem 0 0; color: #dbeafe; font-size: 1.05rem; }
        .status-card {
            padding: .75rem 1rem; margin: .45rem 0; border: 1px solid #bbf7d0;
            border-radius: 10px; background: #f0fdf4; color: #166534; font-weight: 650;
        }
        .metric-card {
            padding: 1rem 1.1rem; min-height: 105px; border: 1px solid #dbe3ef;
            border-radius: 12px; background: white; box-shadow: 0 3px 12px rgba(15, 23, 42, .05);
        }
        .metric-label { color: #64748b; font-size: .86rem; }
        .metric-value { color: #0f172a; font-size: 1.75rem; font-weight: 750; margin-top: .25rem; }
        .proposal {
            padding: 1rem 1.25rem; border-left: 5px solid #f59e0b;
            border-radius: 10px; background: #fffbeb; color: #78350f;
        }
        .mode-badge {
            display: inline-block; padding: .25rem .7rem; border-radius: 6px;
            font-size: .82rem; font-weight: 700; margin-bottom: .5rem;
        }
        .mode-live { background: #dcfce7; color: #166534; border: 1px solid #86efac; }
        .mode-local { background: #fef3c7; color: #92400e; border: 1px solid #fcd34d; }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Live AWS Mode helpers (imports deferred so fixture mode works without deps)
# ---------------------------------------------------------------------------

def test_connections(region: str, cluster_name: str) -> dict[str, dict[str, Any]]:
    """Test read-only AWS, EKS, and Kubernetes access independently."""
    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError
    from kubernetes import client, config
    from kubernetes.client.exceptions import ApiException

    result: dict[str, dict[str, Any]] = {}
    sdk_config = Config(retries={"total_max_attempts": 2, "mode": "standard"}, connect_timeout=5, read_timeout=15)
    session = boto3.Session(region_name=region)

    try:
        identity = session.client("sts", config=sdk_config).get_caller_identity()
        result["aws"] = {"ok": True, "detail": f"Account {identity['Account']}"}
    except (ClientError, BotoCoreError) as exc:
        result["aws"] = {"ok": False, "detail": str(exc)}

    try:
        cluster = session.client("eks", config=sdk_config).describe_cluster(name=cluster_name)["cluster"]
        result["eks"] = {"ok": True, "detail": f"{cluster['name']} ({cluster['status']})"}
    except (ClientError, BotoCoreError) as exc:
        result["eks"] = {"ok": False, "detail": str(exc)}

    try:
        config.load_kube_config()
        version = client.VersionApi().get_code()
        result["kubernetes"] = {"ok": True, "detail": f"API {version.git_version}"}
    except (ApiException, OSError, ValueError, config.ConfigException) as exc:
        result["kubernetes"] = {"ok": False, "detail": str(exc)}
    return result


def run_discovery(region: str, cluster_name: str) -> tuple[dict[str, Any], list[str]]:
    """Run the existing collectors and write a fresh normalized Current Twin."""
    from discovery.aws_collector import AWSCollector
    from discovery.kubernetes_collector import KubernetesCollector
    from discovery.normalizer import normalize
    from visualization.build_graph import main as build_graph

    warnings: list[str] = []
    progress = st.status("Discovering infrastructure...", expanded=True)
    try:
        progress.write("Discovering AWS...")
        aws = AWSCollector(region).collect()
        warnings.extend(aws.get("warnings", []))

        progress.write("Discovering Kubernetes...")
        kubernetes = KubernetesCollector().collect()
        warnings.extend(kubernetes.get("warnings", []))

        progress.write("Building relationships...")
        twin = normalize(aws, kubernetes, region, cluster_name)
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(twin, indent=2), encoding="utf-8")

        progress.write("Generating Current Twin...")
        if build_graph() != 0:
            raise RuntimeError("The graph builder did not complete successfully.")
        progress.update(label="Current Twin generated", state="complete", expanded=False)
        return twin, warnings
    except Exception:
        progress.update(label="Discovery failed", state="error", expanded=True)
        raise


# ---------------------------------------------------------------------------
# Local Fixture Mode helpers
# ---------------------------------------------------------------------------

def load_fixture() -> dict[str, Any] | None:
    """Load the sample Current Twin fixture shipped with the repository.

    Also writes it to ``outputs/current_state.json`` so that the graph builder
    and any downstream Future Twin / Diff logic find the file in the usual
    location.
    """
    fixture = load_json(FIXTURE_PATH)
    if fixture is None:
        return None
    # Persist to the standard outputs path so build_graph and other tools work.
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(fixture, indent=2), encoding="utf-8")
    # Attempt to generate the interactive graph from the fixture.
    try:
        from visualization.build_graph import main as build_graph
        build_graph()
    except Exception:
        pass  # Graph generation is best-effort in fixture mode.
    return fixture


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def resource_count(twin: dict[str, Any], resource_type: str) -> int:
    return sum(1 for resource in twin["resources"] if resource["type"] == resource_type)


def metric_card(label: str, value: Any) -> None:
    st.markdown(
        f'<div class="metric-card"><div class="metric-label">{label}</div>'
        f'<div class="metric-value">{value}</div></div>',
        unsafe_allow_html=True,
    )


def render_connection_status(results: dict[str, dict[str, Any]]) -> None:
    labels = {"aws": "AWS", "eks": "EKS", "kubernetes": "Kubernetes"}
    for key, label in labels.items():
        item = results.get(key, {"ok": False, "detail": "Not tested"})
        if item["ok"]:
            st.markdown(
                f'<div class="status-card">&#10003; {label} Connected<br>'
                f'<small>{item["detail"]}</small></div>',
                unsafe_allow_html=True,
            )
        else:
            st.error(f"{label} connection failed: {item['detail']}")


def render_dashboard(twin: dict[str, Any]) -> None:
    summary = twin["summary"]
    total = summary["aws_resources"] + summary["kubernetes_resources"]
    top_metrics = [
        ("AWS Resources", summary["aws_resources"]),
        ("Kubernetes Resources", summary["kubernetes_resources"]),
        ("Relationships", summary["relationships"]),
        ("Total Resources", total),
    ]
    columns = st.columns(4)
    for column, (label, value) in zip(columns, top_metrics):
        with column:
            metric_card(label, value)

    st.write("")
    details = [
        ("EKS Cluster", twin["metadata"]["cluster"]),
        ("Worker Nodes", resource_count(twin, "NODE")),
        ("VPCs", resource_count(twin, "VPC")),
        ("Subnets", resource_count(twin, "SUBNET")),
        ("Deployments", resource_count(twin, "DEPLOYMENT")),
        ("Pods", resource_count(twin, "POD")),
        ("Services", resource_count(twin, "SERVICE")),
        ("Load Balancers", resource_count(twin, "LOAD_BALANCER")),
    ]
    detail_columns = st.columns(4)
    for index, (label, value) in enumerate(details):
        with detail_columns[index % 4]:
            metric_card(label, value)
            if index < 4:
                st.write("")


def render_graph() -> None:
    st.subheader("Current Digital Twin")
    if GRAPH_PATH.exists() and GRAPH_PATH.stat().st_size:
        components.html(GRAPH_PATH.read_text(encoding="utf-8"), height=920, scrolling=True)
    else:
        st.info("Run infrastructure discovery to generate the interactive Current Twin.")


def render_scenario(twin: dict[str, Any] | None) -> None:
    st.subheader("Proposed Change")
    scenario = load_json(SCENARIO_PATH)
    if not scenario:
        st.warning("The proposed scenario file is unavailable.")
        return
    current = scenario["change"]["before"]
    if twin:
        ui = next(
            (resource for resource in twin["resources"] if resource["type"] == "DEPLOYMENT"
             and resource["name"] == scenario["resource"]
             and resource["properties"].get("namespace") == "default"),
            None,
        )
        if ui:
            current = ui["properties"].get("replicas", current)
    proposed = scenario["change"]["after"]
    st.markdown(
        f'<div class="proposal"><b>{scenario["name"]}</b><br><br>'
        f'UI replicas &nbsp; Current: <b>{current}</b> &nbsp; Proposed: <b>{proposed}</b></div>',
        unsafe_allow_html=True,
    )
    st.write("")
    st.button("Simulate Change — Next Phase", disabled=True, use_container_width=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    configure_page()
    st.markdown('<div class="hero"><h1>CloudTwin AI</h1><p>Digital Twin for AWS &amp; Kubernetes</p></div>', unsafe_allow_html=True)

    # ------------------------------------------------------------------
    # Determine the operating mode
    # ------------------------------------------------------------------
    has_creds = detect_aws_credentials()

    # Allow the user to override via the sidebar (stored in session_state).
    if "mode" not in st.session_state:
        st.session_state.mode = "live" if has_creds else "local"

    # ------------------------------------------------------------------
    # Sidebar
    # ------------------------------------------------------------------
    with st.sidebar:
        st.header("Operating Mode")
        mode_options = ["Local Fixture Mode", "Live AWS Mode"]
        mode_index = 0 if st.session_state.mode == "local" else 1
        selected = st.radio(
            "Select mode",
            mode_options,
            index=mode_index,
            help="Local Fixture Mode works offline without AWS credentials. "
                 "Live AWS Mode requires configured boto3 credentials.",
        )
        st.session_state.mode = "local" if selected == "Local Fixture Mode" else "live"

        if st.session_state.mode == "local":
            st.markdown('<span class="mode-badge mode-local">📂 Local Fixture Mode</span>', unsafe_allow_html=True)
        else:
            st.markdown('<span class="mode-badge mode-live">☁️ Live AWS Mode</span>', unsafe_allow_html=True)

        st.divider()

        if st.session_state.mode == "live":
            # --- Live AWS sidebar controls ---
            st.header("Connect Infrastructure")
            st.text_input("Provider", value="AWS", disabled=True)
            region = st.text_input("Region", value="eu-west-3")
            cluster_name = st.text_input("EKS Cluster", value="retail-store")
            if st.button("Test Connection", use_container_width=True):
                with st.spinner("Testing read-only access..."):
                    try:
                        st.session_state.connection_results = test_connections(region, cluster_name)
                    except Exception as exc:
                        if _is_credential_error(exc):
                            st.warning(
                                "⚠️ AWS credentials not configured. "
                                "Switching to Local Fixture Mode."
                            )
                            st.session_state.mode = "local"
                            st.rerun()
                        else:
                            st.error(f"Connection test failed: {exc}")
            if "connection_results" in st.session_state:
                render_connection_status(st.session_state.connection_results)
            st.divider()
            discover_clicked = st.button("Discover Infrastructure", type="primary", use_container_width=True)
            st.caption("Uses configured AWS credentials and the active local kubeconfig. Read-only discovery only.")
        else:
            # --- Local Fixture sidebar controls ---
            st.header("Local Fixture Data")
            st.caption(
                "Loading a sanitized Current Twin fixture from "
                "`fixtures/sample_current_state.json`.  No AWS credentials "
                "required."
            )
            region = "eu-west-3"
            cluster_name = "retail-store"
            discover_clicked = False

    # ------------------------------------------------------------------
    # Show a persistent warning banner in Local Fixture Mode
    # ------------------------------------------------------------------
    if st.session_state.mode == "local":
        if not has_creds:
            st.warning(
                "⚠️ AWS credentials not configured. Running in **Local Fixture Mode**. "
                "Switch to *Live AWS Mode* after configuring credentials."
            )
        else:
            st.info(
                "📂 Running in **Local Fixture Mode**. "
                "Switch to *Live AWS Mode* in the sidebar to use real infrastructure."
            )

    # ------------------------------------------------------------------
    # Discovery / Fixture loading
    # ------------------------------------------------------------------
    if st.session_state.mode == "live" and discover_clicked:
        try:
            twin, warnings = run_discovery(region, cluster_name)
            st.session_state.twin = twin
            st.success("Infrastructure discovery completed successfully.")
            for warning in warnings:
                st.warning(warning)
        except Exception as exc:
            if _is_credential_error(exc):
                st.warning(
                    "⚠️ AWS credentials not configured. "
                    "Switching to Local Fixture Mode."
                )
                st.session_state.mode = "local"
                st.rerun()
            else:
                st.error(f"Discovery failed: {exc}")

    if st.session_state.mode == "local":
        # In fixture mode, always (re)load the fixture.
        twin = st.session_state.get("twin")
        if twin is None:
            twin = load_fixture()
            if twin:
                st.session_state.twin = twin
            else:
                st.error(
                    "Could not load the local fixture file. "
                    "Please ensure `fixtures/sample_current_state.json` exists."
                )
    else:
        twin = st.session_state.get("twin") or load_json(STATE_PATH)

    # ------------------------------------------------------------------
    # Render dashboard, graph, and scenario
    # ------------------------------------------------------------------
    if twin:
        render_dashboard(twin)
    else:
        st.info("Connect and discover the infrastructure to populate the dashboard.")
    st.divider()
    render_graph()
    st.divider()
    render_scenario(twin)


if __name__ == "__main__":
    main()
