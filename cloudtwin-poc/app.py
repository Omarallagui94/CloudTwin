"""Professor-demo-ready, read-only Streamlit dashboard for CloudTwin."""

from __future__ import annotations

import json
import html
from pathlib import Path
from typing import Any

import boto3
import streamlit as st
import streamlit.components.v1 as components
from botocore.config import Config
from kubernetes import client, config

from analysis.engine import run as run_analysis
from ai_analyst import explain as explain_analysis
from change_assistant import UnsupportedChangeRequest, parse_change_request
from discovery.aws_collector import AWSCollector
from discovery.kubernetes_collector import KubernetesCollector
from discovery.normalizer import normalize
from ingestion import ChangeIngester, IngestionError
from integration.hamdi_fatma import context_from_changeset
from integration.scenario_compat import CompatibilityError, discover_scenarios
from reporting import build_simulation_report
from simulation import (
    SimulationError, analyze_affected_resources, build_future_twin, calculate_state_diff,
    simulate_future_twin,
)
from ai_analyst.fixes import remediate_with_ai
from visualization.simulation_view import (
    ATTACK_EDGE, HIGHLIGHT, impact_graph_html, llm_client, render_ai, render_fixes,
    render_llm_settings,
)
from visualization import ui
from visualization.build_graph import (
    allowed_types, build_graph, legend_items, project_diff, project_twin, readable_label,
)

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "outputs" / "current_state.json"
REFERENCE_TWIN_PATH = ROOT / "fixtures" / "sample_current_state.json"
INFRA_GRAPH_PATH = ROOT / "outputs" / "infrastructure.html"
GRAPH_PATH = ROOT / "outputs" / "current_twin.html"
DIFF_GRAPH_PATH = ROOT / "outputs" / "diff_twin.html"
CURRENT_COMPARE_PATH = ROOT / "outputs" / "simulation_current.html"
FUTURE_COMPARE_PATH = ROOT / "outputs" / "simulation_future.html"
SCENARIOS_PATH = ROOT / "scenarios"

PAGES = ["Overview", "Infrastructure", "Current Twin", "Change Assistant", "Analysis"]
STATUS_ITEMS = [
    ("AWS/EKS Discovery", "READY", "ready"),
    ("Kubernetes Discovery", "READY", "ready"),
    ("Current Twin", "READY", "ready"),
    ("Change Ingestion", "READY", "ready"),
    ("Affected Resources", "READY", "ready"),
    ("Resilience Analysis", "READY", "ready"),
    ("Future Twin", "READY", "ready"),
    ("State Diff", "READY", "ready"),
    ("Security Analysis", "READY", "ready"),
    ("Blast Radius", "READY", "ready"),
    ("Risk Scoring", "READY", "ready"),
    ("AI Explanation", "READY", "ready"),
]
SUGGESTIONS = {
    "Scale UI Up": "S01",
    "Scale UI Down": "S02",
    "Expose Service Publicly": "S03",
    "Open Security Group": "S04",
    "Remove Critical Dependency": "S05",
}
GRAPH_VIEWS = ["Application View", "Kubernetes View", "AWS Infrastructure View", "Full Infrastructure"]


def configure_page() -> None:
    st.set_page_config(page_title="CloudTwin AI", page_icon="☁️", layout="wide")
    st.markdown(
        """
        <style>
        .hero { padding: 2rem; border-radius: 20px; color: #E9EDFF; background: linear-gradient(125deg,#161E42,#1C2550); border:1px solid #2A3566; }
        .hero h1 { margin: 0; font-size: 2.45rem; letter-spacing: -.03em; }
        .hero p { margin: .45rem 0 0; color: #5EE6D8; font-size: 1.12rem; }
        .status-row { background: #161E42; border: 1px solid #2A3566; border-radius: 12px; padding: .8rem 1rem; margin: .45rem 0; display: flex; justify-content: space-between; align-items: center; }
        .badge { border-radius: 999px; padding: .23rem .62rem; font-size: .72rem; font-weight: 700; }
        .ready { background:rgba(70,201,138,.15); color:#46C98A; } .partial { background:rgba(242,181,68,.15); color:#F2B544; } .future { background:#232C5C; color:#9AA3C7; }
        .flow { display:flex; gap:.45rem; align-items:center; flex-wrap:wrap; margin:1rem 0; }
        .flow-step { background:#161E42; border:1px solid #2A3566; border-radius:10px; padding:.65rem .8rem; font-weight:600; color:#E9EDFF; }
        .flow-arrow { color:#5EE6D8; font-weight:800; }
        .proposal { background:#161E42; border:1px solid #2A3566; border-left:5px solid #5EE6D8; border-radius:14px; padding:1.1rem 1.25rem; color:#C9CFEA; }
        .proposal h4 { color:#E9EDFF; }
        .pipeline-done { color:#46C98A; font-weight:700; } .pipeline-next { color:#9AA3C7; }
        .sim-card { background:#161E42; border:1px solid #2A3566; border-radius:14px; padding:1rem 1.1rem; min-height:105px; }
        .sim-card strong { color:#E9EDFF; font-size:1.05rem; }
        .sim-id { color:#9AA3C7; font-size:.72rem; margin-top:.35rem; overflow-wrap:anywhere; }
        .sim-value { font-size:1.65rem; font-weight:800; color:#E9EDFF; margin:.35rem 0; }
        .sim-future { border-top:4px solid #5EE6D8; } .sim-removed { border-top:4px solid #FF5470; opacity:.78; }
        .sim-removed strong { text-decoration:line-through; }
        .diff-stat { background:#161E42; border:1px solid #2A3566; border-radius:12px; padding:.75rem 1rem; }
        .diff-stat.active { border-color:#5EE6D8; box-shadow:0 0 0 1px #5EE6D8 inset; }
        .diff-number { font-size:1.55rem; font-weight:800; color:#9AA3C7; } .diff-stat.active .diff-number { color:#E9EDFF; }
        .relationship-card { background:#161E42; border:1px solid #2A3566; border-left:4px solid #FF5470; border-radius:12px; padding:.8rem 1rem; margin:.55rem 0; color:#C9CFEA; }
        </style>
        """ + ui.CSS,
        unsafe_allow_html=True,
    )


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def scenarios() -> dict[str, dict[str, Any]]:
    try:
        return discover_scenarios(SCENARIOS_PATH)
    except CompatibilityError:
        return {}


def fixture_for_scenario(scenario_id: str) -> dict[str, Any] | None:
    matches = list(SCENARIOS_PATH.glob(f"{scenario_id}-*/current-twin.json"))
    return load_json(matches[0]) if len(matches) == 1 else None


def matching_fixture(change: dict[str, Any]) -> dict[str, Any] | None:
    """Return an existing fixture whose target and before value actually match."""
    for path in sorted(SCENARIOS_PATH.glob("S*/current-twin.json")):
        twin = load_json(path)
        if not twin:
            continue
        resource = next((r for r in twin.get("resources", []) if r["id"] == change["target_resource"]), None)
        if not resource:
            continue
        property_name = change["property"].split(".")[-1]
        current = resource.get("properties", {}).get(property_name)
        if current == change["before"]:
            return twin
    return None


def current_twin_for(change: dict[str, Any], mode: str) -> dict[str, Any] | None:
    twin = st.session_state.get("twin")
    if mode == "Live AWS Mode":
        return twin
    return matching_fixture(change) or twin


def test_connections(region: str, cluster_name: str) -> dict[str, dict[str, Any]]:
    """Perform read-only connectivity checks without exposing identity data."""
    result: dict[str, dict[str, Any]] = {}
    sdk = Config(retries={"total_max_attempts": 2, "mode": "standard"}, connect_timeout=5, read_timeout=12)
    session = boto3.Session(region_name=region)
    try:
        session.client("sts", config=sdk).get_caller_identity()
        result["aws"] = {"ok": True, "detail": "Read-only session available"}
    except Exception as exc:  # toute erreur du SDK (identifiants, réseau, réponse illisible) : expliquée, jamais levée
        result["aws"] = {"ok": False, "detail": explain_discovery_error(exc)[0]}
    try:
        cluster = session.client("eks", config=sdk).describe_cluster(name=cluster_name)["cluster"]
        result["eks"] = {"ok": True, "detail": f"Cluster status: {cluster['status']}"}
    except Exception as exc:  # idem pour EKS
        result["eks"] = {"ok": False, "detail": explain_discovery_error(exc)[0]}
    try:
        config.load_kube_config()
        version = client.VersionApi().get_code()
        result["kubernetes"] = {"ok": True, "detail": f"API {version.git_version}"}
    except Exception as exc:  # idem pour Kubernetes (kubeconfig absent, API injoignable)
        result["kubernetes"] = {"ok": False, "detail": explain_discovery_error(exc)[0]}
    return result


def run_discovery(region: str, cluster_name: str) -> tuple[dict[str, Any], list[str]]:
    """Run the existing read-only collectors and normalize a Current Twin."""
    warnings: list[str] = []
    progress = st.status("Discovering infrastructure…", expanded=True)
    try:
        progress.write("Discovering AWS resources")
        aws = AWSCollector(region).collect()
        warnings.extend(aws.get("warnings", []))
        progress.write("Discovering Kubernetes resources")
        kubernetes = KubernetesCollector().collect()
        warnings.extend(kubernetes.get("warnings", []))
        progress.write("Normalizing resources and relationships")
        twin = normalize(aws, kubernetes, region, cluster_name)
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(twin, indent=2), encoding="utf-8")
        build_graph(twin, GRAPH_PATH)
        progress.update(label="Current Twin generated successfully", state="complete", expanded=False)
        return twin, warnings
    except Exception:
        progress.update(label="Discovery failed", state="error", expanded=True)
        raise


def metric_card(label: str, value: Any) -> None:
    st.markdown(f'<div class="card"><div class="eyebrow">{label}</div><div class="metric-value">{value}</div></div>', unsafe_allow_html=True)


def resource_count(twin: dict[str, Any], resource_type: str) -> int:
    return sum(1 for resource in twin.get("resources", []) if resource.get("type") == resource_type)


def render_metrics(twin: dict[str, Any]) -> None:
    resources = twin.get("resources", [])
    relationships = twin.get("relationships", [])
    aws_count = sum(r.get("provider") == "aws" for r in resources)
    k8s_count = sum(r.get("provider") == "kubernetes" for r in resources)
    metrics = [
        ("Resources", len(resources)), ("Relationships", len(relationships)),
        ("AWS", aws_count), ("Kubernetes", k8s_count),
        ("Worker nodes", resource_count(twin, "NODE")), ("Deployments", resource_count(twin, "DEPLOYMENT")),
        ("Services", resource_count(twin, "SERVICE")), ("StatefulSets", resource_count(twin, "STATEFUL_SET")),
    ]
    columns = st.columns(4)
    for index, (label, value) in enumerate(metrics):
        with columns[index % 4]:
            metric_card(label, value)
            if index < 4:
                st.write("")


def render_pipeline(
    parsed: bool = False,
    changeset_ready: bool = False,
    future_ready: bool = False,
    diff_ready: bool = False,
    analysis_ready: bool = False,
) -> None:
    stages = [
        ("Current Twin", bool(st.session_state.get("twin"))),
        ("Change Parsed", parsed),
        ("ChangeSet", changeset_ready),
        ("Future Twin", future_ready),
        ("State Diff", diff_ready),
    ]
    affected_ready = bool(st.session_state.get("affected_resources"))
    explanation = st.session_state.get("analysis_explanation") or {}
    ai_ready = analysis_ready and bool(explanation)
    fixes_ready = any(entry[1] for entry in (st.session_state.get("ai_cache") or {}).values())
    stages += [
        ("Affected Resources", affected_ready), ("Security Analysis", analysis_ready),
        ("Resilience Analysis", analysis_ready), ("Blast Radius", analysis_ready),
        ("Risk Score", analysis_ready), ("AI Explanation", ai_ready), ("Verified Fixes", fixes_ready),
    ]
    note = None
    if ai_ready:
        note = ("Explication rédigée par le LLM local." if explanation.get("source") == "llm"
                else "Explication : texte de secours, sans LLM. Active Ollama dans la barre latérale.")
    st.markdown("#### Pipeline CloudTwin")
    st.markdown(ui.pipeline_track(stages, note), unsafe_allow_html=True)


def scenario_rows() -> list[dict[str, Any]]:
    """Score simulé de chaque scénario du pack, pour le tableau de l'Overview."""
    rows = []
    for sid, scenario in scenarios().items():
        row: dict[str, Any] = {"id": sid, "name": scenario.get("name", sid)}
        twin = fixture_for_scenario(sid)
        try:
            changeset = ChangeIngester(twin).ingest_scenario(scenario)
            future = future_twin_for(twin, changeset)
            context = context_from_changeset(changeset, twin, future_twin=future)
            result = run_analysis(context).model_dump(mode="json")
            row["score"] = result["risk"]["score"]
            row["failed"] = [f["rule_id"] for f in result["findings"] if f["status"] == "fail"]
        except (IngestionError, SimulationError, ValueError, KeyError, TypeError) as exc:
            row["error"] = str(exc)
        rows.append(row)
    return rows


def render_overview(mode: str) -> None:
    st.markdown(ui.page_head(
        "CloudTwin AI",
        "Simulez un changement AWS ou Kubernetes sur un jumeau numérique, et mesurez son risque "
        "avant de le déployer. Rien n'est jamais modifié sur l'infrastructure réelle.",
    ), unsafe_allow_html=True)
    twin = st.session_state.get("twin") or {}
    resources, relationships = twin.get("resources", []), twin.get("relationships", [])
    result = st.session_state.get("analysis_result")
    columns = st.columns(3)
    values = [
        ("Source des données", "Fixtures locales" if mode == "Local Fixture Mode" else "AWS en lecture seule"),
        ("Current Twin", f"{len(resources)} ressource{'s' * (len(resources) > 1)}, {len(relationships)} relation{'s' * (len(relationships) > 1)}" if twin else "Non chargé"),
        ("Dernière simulation",
         f"{result['risk']['score']}/100, risque {result['risk']['label'].lower()}" if result else "Aucune"),
    ]
    for column, (label, value) in zip(columns, values):
        with column:
            metric_card(label, value)
    st.write("")
    render_pipeline(
        parsed=bool(st.session_state.get("pending_scenario")),
        changeset_ready=bool(st.session_state.get("confirmed_changeset")),
        future_ready=bool(st.session_state.get("future_twin")),
        diff_ready=bool(st.session_state.get("state_diff")),
        analysis_ready=bool(result),
    )
    st.markdown("#### Les cinq scénarios de démonstration")
    st.caption("Chaque changement est simulé sur sa fixture et analysé par les 11 règles. "
               "Ouvre Change Assistant pour en simuler un en détail.")
    st.markdown(ui.scenario_board(scenario_rows()), unsafe_allow_html=True)


def render_connection_status(results: dict[str, dict[str, Any]]) -> None:
    columns = st.columns(3)
    for column, key, label in zip(columns, ("aws", "eks", "kubernetes"), ("AWS", "EKS", "Kubernetes")):
        item = results.get(key, {"ok": False, "detail": "Not tested"})
        with column:
            metric_card(label, "Connected" if item["ok"] else "Disconnected")
            st.caption(item["detail"])


def explain_discovery_error(exc: BaseException) -> tuple[str, str]:
    """(cause lisible, action à faire) pour une découverte Live qui échoue. Jamais de secret affiché."""
    name, text = type(exc).__name__, str(exc)
    lowered = text.lower()
    if name in {"NoCredentialsError", "PartialCredentialsError"} or "unable to locate credentials" in lowered:
        return ("Aucun identifiant AWS n'est configuré sur cette machine.",
                "Configure le compte de lecture du collecteur : `aws configure` (ou la variable AWS_PROFILE).")
    if "invalidclienttokenid" in lowered or "unrecognizedclientexception" in lowered \
            or "signaturedoesnotmatch" in lowered:
        return ("Les identifiants AWS configurés sur cette machine sont refusés (clé supprimée, désactivée "
                "ou d'un autre compte).",
                "Demande de nouvelles clés au responsable du labo (Omar), puis `aws configure --profile cloudtwin` "
                "et `export AWS_PROFILE=cloudtwin`. Vérifie avec `aws sts get-caller-identity`.")
    if "expiredtoken" in lowered or ("expired" in lowered and "token" in lowered):
        return ("Les identifiants AWS ont expiré.", "Renouvelle la session : `aws sso login` ou de nouvelles clés.")
    if "accessdenied" in lowered or "not authorized" in lowered or "unauthorizedoperation" in lowered:
        return ("Le compte AWS n'a pas les droits de lecture nécessaires.",
                "Applique la politique du collecteur décrite dans docs/lab-security.md.")
    if "resourcenotfound" in lowered or "no cluster found" in lowered:
        return ("Le cluster EKS demandé n'existe pas dans cette région.",
                "Vérifie le nom et la région dans la barre latérale, ou recrée le labo (terraform apply).")
    if name == "ConfigException" or "kube-config" in lowered or "kubeconfig" in lowered or "invalid kube" in lowered:
        return ("Kubernetes n'est pas configuré : aucun kubeconfig utilisable.",
                "Relie kubectl au cluster : `aws eks update-kubeconfig --name <cluster> --region <région>`.")
    if name in {"EndpointConnectionError", "ConnectTimeoutError", "ConnectionError", "MaxRetryError"} \
            or name == "ResponseParserError" or "request blocked" in lowered \
            or "could not connect" in lowered or "timed out" in lowered or "name resolution" in lowered:
        return ("AWS ou le cluster ne répond pas (réseau, ou labo détruit).",
                "Vérifie la connexion, puis que le labo tourne. Le labo est détruit entre les sessions pour éviter les coûts.")
    return (f"{name} : {text[:300]}", "Clique sur « Test read-only connections » pour voir quel service bloque.")


def infrastructure_to_show(mode: str) -> tuple[dict[str, Any] | None, str]:
    """L'infrastructure à afficher, même si la découverte Live échoue : la plus récente disponible."""
    twin = st.session_state.get("twin")
    if mode == "Live AWS Mode":
        if twin and st.session_state.get("twin_source") == "Live read-only discovery":
            return twin, "Découverte Live de cette session"
        saved = load_json(STATE_PATH)
        if saved and saved.get("resources"):
            from datetime import datetime
            when = datetime.fromtimestamp(STATE_PATH.stat().st_mtime).strftime("%d/%m/%Y à %H:%M")
            return saved, f"Dernière découverte Live enregistrée ({when})"
        reference = load_json(REFERENCE_TWIN_PATH)
        return reference, "Infrastructure de référence du labo (fixture anonymisée, aucune découverte Live enregistrée)"
    return twin, st.session_state.get("twin_source", "Fixture locale")


def render_infrastructure_view(twin: dict[str, Any], source: str) -> None:
    """Inventaire et graphe de l'infrastructure, indépendants de tout changement proposé."""
    st.markdown("### Infrastructure actuelle")
    st.caption(f"Source : {source}. Affichage en lecture seule, aucun changement n'est simulé ici.")
    render_metrics(twin)
    resources = twin.get("resources", [])
    counts: dict[tuple[str, str], int] = {}
    for resource in resources:
        key = ("AWS" if resource.get("provider") == "aws" else "Kubernetes" if resource.get("provider") == "kubernetes"
               else str(resource.get("provider", "—")).title(), resource.get("type", "?").replace("_", " ").title())
        counts[key] = counts.get(key, 0) + 1
    left, right = st.columns([2, 3])
    with left:
        st.markdown("#### Inventaire")
        st.dataframe(
            [{"Fournisseur": p, "Type": t, "Nombre": n} for (p, t), n in sorted(counts.items())],
            hide_index=True, use_container_width=True, height=min(38 + 35 * len(counts), 520),
        )
    with right:
        st.markdown("#### Graphe")
        view = st.selectbox("Vue", GRAPH_VIEWS, index=GRAPH_VIEWS.index("Full Infrastructure"), key="infra-view")
        projection = project_twin(twin, view)
        if not projection["resources"]:
            st.info("Aucune ressource dans cette vue.")
            return
        build_graph(projection, INFRA_GRAPH_PATH, view=view)
        components.html(INFRA_GRAPH_PATH.read_text(encoding="utf-8"), height=560, scrolling=True)


def render_infrastructure(mode: str, region: str, cluster_name: str) -> None:
    st.markdown(ui.page_head(
        "Infrastructure",
        "L'infrastructure AWS et Kubernetes connue de CloudTwin : fixtures anonymisées, "
        "ou découverte Live en lecture seule.",
    ), unsafe_allow_html=True)
    if mode == "Local Fixture Mode":
        st.info("Mode fixtures locales : aucun appel AWS n'est fait.")
        available = scenarios()
        options = ["REF", *available]
        labels = {"REF": "Infrastructure de référence du labo (40 ressources)",
                  **{sid: f"{sid} — {available[sid]['name']}" for sid in available}}
        fixture_id = st.selectbox("Jumeau à afficher", options, format_func=labels.get, key="infra-fixture")
        twin = load_json(REFERENCE_TWIN_PATH) if fixture_id == "REF" else fixture_for_scenario(fixture_id)
        if not twin:
            st.error("Cette fixture est introuvable dans le dépôt.")
            return
        st.session_state.twin = twin
        st.session_state.twin_source = labels[fixture_id]
        render_infrastructure_view(twin, labels[fixture_id])
        return

    st.warning("Le mode Live interroge AWS et Kubernetes en lecture seule. Il n'applique jamais de changement.")
    columns = st.columns(2)
    with columns[0]:
        if st.button("Test read-only connections", use_container_width=True):
            with st.spinner("Vérification de l'accès à AWS, EKS et Kubernetes…"):
                st.session_state.connection_results = test_connections(region, cluster_name)
    with columns[1]:
        run = st.button("Run Discovery", type="primary", use_container_width=True)
    if "connection_results" in st.session_state:
        render_connection_status(st.session_state.connection_results)
    if run:
        try:
            twin, warnings = run_discovery(region, cluster_name)
            st.session_state.twin = twin
            st.session_state.twin_source = "Live read-only discovery"
            st.session_state.pop("discovery_error", None)
            st.success("Current Twin généré par la découverte Live.")
            for warning in warnings:
                st.warning(warning)
        except Exception as exc:  # collecteurs AWS/Kubernetes : toute erreur est expliquée, rien n'est modifié
            st.session_state.discovery_error = explain_discovery_error(exc)
    if st.session_state.get("discovery_error"):
        cause, action = st.session_state.discovery_error
        st.error(f"La découverte Live n'a pas abouti. Aucun changement n'a été fait.\n\n**Cause :** {cause}\n\n**À faire :** {action}")
    twin, source = infrastructure_to_show(mode)
    if twin:
        render_infrastructure_view(twin, source)
    else:
        st.info("Aucune infrastructure à afficher : lance une découverte Live.")


def render_current_twin(mode: str) -> None:
    st.title("Current Twin")
    st.write("Current Twin is the graph representation of the infrastructure CloudTwin discovered.")
    twin = st.session_state.get("twin")
    if not twin and mode == "Local Fixture Mode":
        twin = fixture_for_scenario("S01")
        st.session_state.twin = twin
        st.session_state.twin_source = "Local fixture S01"
    if not twin:
        st.info("Open Infrastructure and run discovery or select a local fixture first.")
        return
    future_twin = st.session_state.get("future_twin")
    snapshot = "Current Twin"
    if future_twin:
        snapshot = st.radio("Twin snapshot", ["Current Twin", "Future Twin"], horizontal=True)
        if snapshot == "Future Twin":
            twin = future_twin
            st.info("Future Twin is an in-memory simulation only. The discovered Current Twin is unchanged.")
    st.caption(f"Source: {st.session_state.get('twin_source', 'Current Twin output')} — {snapshot}")
    render_metrics(twin)
    st.write("")
    st.markdown("### Explore the graph")
    view = st.selectbox(
        "Graph view",
        GRAPH_VIEWS,
        index=0,
        help="Application View is a simplified projection. Full Infrastructure preserves the complete discovered graph.",
    )
    type_options = allowed_types(view, twin)
    discovered_providers = sorted({resource["provider"] for resource in twin.get("resources", [])})
    default_providers = {
        "Kubernetes View": [provider for provider in discovered_providers if provider == "kubernetes"],
        "AWS Infrastructure View": [provider for provider in discovered_providers if provider == "aws"],
    }.get(view, discovered_providers)
    filter_columns = st.columns(2)
    with filter_columns[0]:
        selected_providers = st.multiselect(
            "Provider",
            discovered_providers,
            default=default_providers,
            format_func=lambda provider: "AWS" if provider == "aws" else "Kubernetes",
            key=f"graph-providers-{view}",
        )
    with filter_columns[1]:
        selected_types = st.multiselect(
            "Resource type",
            type_options,
            default=type_options,
            format_func=lambda resource_type: resource_type.replace("_", " ").title(),
            key=f"graph-types-{view}",
        )
    projection = project_twin(
        twin,
        view,
        providers=set(selected_providers),
        resource_types=set(selected_types),
    )
    visible_nodes = len(projection["resources"])
    visible_relationships = len(projection["relationships"])
    total_nodes = len(twin.get("resources", []))
    total_relationships = len(twin.get("relationships", []))
    summary_columns = st.columns(4)
    for column, (label, value) in zip(summary_columns, [
        ("Visible nodes", visible_nodes),
        ("Visible relationships", visible_relationships),
        ("Total discovered nodes", total_nodes),
        ("Total relationships", total_relationships),
    ]):
        with column:
            metric_card(label, value)
    st.caption(
        f"{view} — {visible_nodes} visible nodes / {visible_relationships} relationships. "
        f"This is a projection of the {snapshot}; no relationships are inferred."
    )
    legend_html = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:.4rem;margin:0 .9rem .45rem 0;">'
        f'<img src="{icon}" alt="" style="width:1.6rem;height:1.6rem;">{label}</span>'
        for label, _color, icon in legend_items()
    )
    st.markdown(f'<div class="card" style="min-height:0;padding:.7rem 1rem;"><b>Legend</b><br>{legend_html}</div>', unsafe_allow_html=True)
    if not projection["resources"]:
        st.info("No resources match the selected view and filters.")
        return
    build_graph(projection, GRAPH_PATH, view=view)
    components.html(GRAPH_PATH.read_text(encoding="utf-8"), height=930, scrolling=True)
    st.caption("Select a node to highlight its direct relationships. Click empty space to restore the full projection.")


def twin_for_scenario(scenario_id: str, scenario: dict[str, Any], mode: str) -> tuple[dict[str, Any] | None, str | None]:
    """Jumeau sur lequel simuler un scénario de démonstration, et une note si on a dû changer.

    En Live, on simule sur le jumeau découvert si la ressource visée y existe. Sinon (S04 vise
    un Security Group synthétique, absent du vrai lab), on simule sur le jumeau de
    démonstration du scénario au lieu d'échouer, et on le dit.
    """
    if mode == "Local Fixture Mode":
        return fixture_for_scenario(scenario_id), None
    twin = current_twin_for(scenario, mode)
    target = scenario.get("target_resource")
    if twin and any(r.get("id") == target for r in twin.get("resources", [])):
        return twin, None
    fixture = fixture_for_scenario(scenario_id)
    if fixture is None:
        return twin, None
    name = str(target).rsplit(":", 1)[-1]
    return fixture, (f"La ressource {name} n'existe pas dans le lab Live : {scenario_id} est simulé "
                     "sur son jumeau de démonstration. Rien n'est lu ni modifié sur AWS pour ce scénario.")


def set_pending_change(scenario: dict[str, Any], twin: dict[str, Any], origin: str,
                       note: str | None = None) -> None:
    st.session_state.scenario_twin_note = note
    st.session_state.pending_scenario = scenario
    st.session_state.pending_twin = twin
    st.session_state.pending_origin = origin
    st.session_state.pop("confirmed_changeset", None)
    st.session_state.pop("analysis_result", None)
    st.session_state.pop("future_twin", None)
    st.session_state.pop("state_diff", None)
    st.session_state.pop("affected_resources", None)
    st.session_state.pop("simulation_report", None)
    st.session_state.pop("show_diff_graph", None)


def render_proposal(scenario: dict[str, Any]) -> None:
    labels = {"DEPLOYMENT": "Deployment", "SERVICE": "Service", "SECURITY_GROUP": "Security Group"}
    st.markdown("### Proposed change")
    st.markdown(
        f"""<div class="proposal"><div class="eyebrow">Simulation only — no infrastructure mutation</div>
        <h4>{scenario['name']}</h4><b>Resource:</b> {labels.get(scenario['resource_type'], scenario['resource_type'].title())}<br>
        <b>Resource ID:</b> <code>{scenario['target_resource']}</code><br><b>Operation:</b> {scenario.get('operation', 'update').upper()}<br>
        <b>Property:</b> {scenario['property']}<br><b>Current:</b> <code>{json.dumps(scenario.get('before'), sort_keys=True)}</code><br>
        <b>Proposed:</b> <code>{json.dumps(scenario.get('after'), sort_keys=True)}</code></div>""",
        unsafe_allow_html=True,
    )


def future_twin_for(twin: dict[str, Any], changeset: Any) -> dict[str, Any]:
    """Future Twin strict ; si la fixture n'a pas encore la propriété modifiée, interface canonique.

    Exemples : S03 (`service_type_and_scheme`, propriété composée) et S04 (`ingress_rules`,
    absente de la fixture qui ne garde que le nombre de règles). L'interface stricte refuse ;
    `build_future_twin` ajoute la propriété et le signale en avertissement. Une valeur `before`
    périmée reste une erreur : elle est déjà refusée par l'ingestion (strict_conflict_check).
    """
    st.session_state.pop("simulation_warnings", None)
    try:
        return simulate_future_twin(twin, changeset)
    except SimulationError as exc:
        if "does not exist" not in str(exc):
            raise
    built = build_future_twin(twin, changeset.changes)
    st.session_state.simulation_warnings = built.warnings
    return built.twin


def confirm_pending_change() -> None:
    scenario = st.session_state.pending_scenario
    twin = st.session_state.pending_twin
    try:
        changeset = ChangeIngester(twin).ingest_scenario(scenario, strict_conflict_check=True)
        future_twin = future_twin_for(twin, changeset)
        state_diff = calculate_state_diff(twin, future_twin)
        diff_data = state_diff.to_dict()
        structural = analyze_affected_resources(diff_data, twin, future_twin, changeset)
        context = context_from_changeset(
            changeset, twin, future_twin=future_twin, state_diff=diff_data
        )
        result = run_analysis(context)
        explanation = explain_analysis(result).model_dump(mode="json")
    except (IngestionError, SimulationError, ValueError, KeyError) as exc:
        st.session_state.confirmation_error = str(exc)
        return
    st.session_state.confirmed_changeset = changeset.to_dict()
    st.session_state.analysis_result = result.model_dump(mode="json")
    st.session_state.analysis_explanation = explanation
    # Objets gardés pour l'analyste IA et les correctifs vérifiés (page Analysis).
    st.session_state.analysis_context = context
    st.session_state.analysis_result_obj = result
    st.session_state.ai_cache = {}
    st.session_state.future_twin = future_twin
    st.session_state.state_diff = state_diff.to_dict()
    st.session_state.affected_resources = structural.model_dump(mode="json")
    st.session_state.pop("confirmation_error", None)


def render_changeset(changeset: dict[str, Any]) -> None:
    st.markdown("### Normalized ChangeSet")
    change = changeset["changes"][0]
    fields = [
        ("Change ID", change["change_id"]), ("Operation", change["operation"].upper()),
        ("Canonical resource ID", change["resource_id"]), ("Resource type", change["resource_type"]),
        ("Property path", change.get("property_path") or "Whole resource"), ("Before", json.dumps(change.get("before"), sort_keys=True)),
        ("After", json.dumps(change.get("after"), sort_keys=True)), ("Source", change.get("source", {}).get("kind", changeset["source_kind"])),
    ]
    columns = st.columns(2)
    for index, (label, value) in enumerate(fields):
        with columns[index % 2]:
            st.markdown(f"**{label}**")
            st.code(str(value))
    with st.expander("View raw normalized ChangeSet"):
        st.json(changeset)


def _resource(twin: dict[str, Any], resource_id: str) -> dict[str, Any] | None:
    return next((item for item in twin.get("resources", []) if item["id"] == resource_id), None)


def _display_name(resource_id: str, current: dict[str, Any], future: dict[str, Any]) -> str:
    resource = _resource(future, resource_id) or _resource(current, resource_id)
    return readable_label(resource) if resource else resource_id.split(":")[-1].replace("-", " ").title()


def _diff_groups(diff: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    changes = diff.get("changes", [])
    return {
        "added": [item for item in changes if item["change_type"] == "ADDED"],
        "removed": [item for item in changes if item["change_type"] == "REMOVED"],
        "modified": [item for item in changes if item["change_type"] == "MODIFIED"],
        "relationships_added": diff.get("added_relationships", []),
        "relationships_removed": diff.get("removed_relationships", []),
    }


def _plain_explanation(
    changeset: dict[str, Any], diff: dict[str, Any], current: dict[str, Any], future: dict[str, Any]
) -> str:
    change = changeset["changes"][0]
    name = _display_name(change["resource_id"], current, future)
    groups = _diff_groups(diff)
    if change["operation"] == "delete":
        count = len(groups["relationships_removed"])
        return (
            f"The simulated Future Twin removes {name}. {count} relationship{'s' if count != 1 else ''} "
            "connected to this resource disappear from the simulated topology. "
            "The real Kubernetes cluster and AWS infrastructure remain unchanged."
        )
    property_name = (change.get("property_path") or "property").split(".")[-1].replace("_", " ")
    relationship_changes = len(groups["relationships_added"]) + len(groups["relationships_removed"])
    return (
        f"The simulated Future Twin changes {name} {property_name} from {change.get('before')} to "
        f"{change.get('after')}. No resources are removed and {relationship_changes} relationships change. "
        "The real Kubernetes cluster and AWS infrastructure remain unchanged."
    )


def render_diff_summary(diff: dict[str, Any]) -> None:
    groups = _diff_groups(diff)
    relationship_count = len(groups["relationships_added"]) + len(groups["relationships_removed"])
    values = [
        ("ADDED RESOURCES", len(groups["added"])),
        ("REMOVED RESOURCES", len(groups["removed"])),
        ("MODIFIED PROPERTIES", len(groups["modified"])),
        ("ADDED RELATIONSHIPS", len(groups["relationships_added"])),
        ("REMOVED RELATIONSHIPS", len(groups["relationships_removed"])),
    ]
    for column, (label, value) in zip(st.columns(5), values):
        with column:
            active = " active" if value else ""
            st.markdown(
                f'<div class="diff-stat{active}"><div class="eyebrow">{label}</div>'
                f'<div class="diff-number">{value}</div></div>', unsafe_allow_html=True,
            )


def render_affected_resources() -> None:
    result = st.session_state.get("affected_resources", {})
    affected = [item for item in result.get("affected", []) if item.get("distance", 0) > 0]
    st.markdown("### Affected Resources")
    st.caption("Structural impact from dependency paths. Risk interpretation remains in Fatma's analysis below.")
    if not affected:
        st.info("No dependent resource is structurally affected; only the changed resource is impacted.")
    for item in affected:
        name = item.get("resource_name") or item["target"].split(":")[-1]
        nodes = [node.split(":")[-1] for node in item.get("path", [])]
        relationships = item.get("relationship_types", [])
        path: list[str] = []
        for index, node in enumerate(nodes):
            path.append(node)
            if index < len(relationships):
                path.append(relationships[index])
        with st.container(border=True):
            st.markdown(f"**{name}**")
            st.write(f"Impact: {item['impact'].title()} · Distance: {item['distance']}")
            st.write(item.get("reason") or "A structural dependency path was identified.")
            st.code(" → ".join(path))
    with st.expander("Technical Details — affected-resource contract"):
        st.json(result)


def render_human_diff(
    diff: dict[str, Any], current: dict[str, Any], future: dict[str, Any]
) -> None:
    groups = _diff_groups(diff)
    for change in groups["modified"]:
        name = _display_name(change["resource_id"], current, future)
        prop = change["property"].split(".")[-1].replace("_", " ").title()
        st.markdown(f"**Modified — {name}**  \n{prop}: `{change.get('before')}` → `{change.get('after')}`")
    for change in groups["removed"]:
        st.markdown(f"**Removed resource — {_display_name(change['resource_id'], current, future)}**")
    for relationship in groups["relationships_removed"]:
        source = _display_name(relationship["source"], current, future)
        target = _display_name(relationship["target"], current, future)
        verb = relationship["type"].replace("_", " ").lower()
        st.markdown(
            f'<div class="relationship-card"><div class="eyebrow">Removed relationship</div>'
            f'<strong>{html.escape(source)}</strong> &nbsp;→ {html.escape(verb)} →&nbsp; '
            f'<strong>{html.escape(target)}</strong></div>', unsafe_allow_html=True,
        )


def render_simulation_experience(
    scenario: dict[str, Any], changeset: dict[str, Any], diff: dict[str, Any],
    current: dict[str, Any], future: dict[str, Any],
    *, datasource_mode: str = "Local Fixture Mode", aws_region: str | None = None,
    eks_cluster: str | None = None,
) -> None:
    change = changeset["changes"][0]
    target_name = _display_name(change["resource_id"], current, future)
    groups = _diff_groups(diff)
    st.header("Simulation Result")
    summary = st.columns(4)
    summary[0].markdown(f"**Change**  \n{scenario.get('name', 'Proposed change')}")
    summary[1].markdown(f"**Target**  \n{target_name}")
    summary[2].markdown(f"**Operation**  \n{change['operation'].upper()}")
    result_parts = []
    if groups["modified"]:
        result_parts.append(f"{len(groups['modified'])} modified property")
    if groups["removed"]:
        result_parts.append(f"{len(groups['removed'])} resource removed")
    if groups["relationships_removed"]:
        result_parts.append(f"{len(groups['relationships_removed'])} relationships removed")
    summary[3].markdown(f"**Result**  \n{', '.join(result_parts) or 'Structural change recorded'}")
    st.markdown('<div class="safe-note"><b>SIMULATION ONLY</b> — real AWS/EKS infrastructure is unchanged.</div>', unsafe_allow_html=True)

    action_left, action_right = st.columns(2)
    with action_left:
        if st.button("View Diff Graph", use_container_width=True):
            st.session_state.show_diff_graph = True
    with action_right:
        if st.button("Generate Simulation Report", type="primary", use_container_width=True):
            report = build_simulation_report(
                scenario=scenario,
                changeset=changeset,
                current_twin=current,
                future_twin=future,
                state_diff=diff,
                analysis_result=st.session_state.get("analysis_result"),
                affected_resources=st.session_state.get("affected_resources"),
                explanation=st.session_state.get("analysis_explanation"),
                datasource_mode=datasource_mode,
                source=st.session_state.get("pending_origin", "Simulation"),
                aws_region=aws_region,
                eks_cluster=eks_cluster,
            )
            st.session_state.simulation_report = {
                "filename": report.filename,
                "html": report.html,
                "generated_at": report.generated_at,
            }
    if st.session_state.get("show_diff_graph"):
        st.markdown("### Focused Diff Graph")
        action_projection = project_diff(current, future, diff)
        build_graph(action_projection, DIFF_GRAPH_PATH, view="Diff View")
        components.html(DIFF_GRAPH_PATH.read_text(encoding="utf-8"), height=720, scrolling=True)
        st.caption("🟢 Added  ·  🟠 Modified  ·  🔴 Removed  ·  ⚪ Context")
    report_data = st.session_state.get("simulation_report")
    if report_data:
        st.markdown("## Simulation Report Preview")
        st.caption(f"Generated locally: {report_data['generated_at']}")
        components.html(report_data["html"], height=900, scrolling=True)
        st.download_button(
            "Download Report",
            data=report_data["html"].encode("utf-8"),
            file_name=report_data["filename"],
            mime="text/html",
            use_container_width=True,
        )

    tabs = st.tabs(["Summary", "Diff View", "Current Twin", "Future Twin", "Technical Details"])
    with tabs[0]:
        st.markdown("### Current Twin → Future Twin")
        current_col, arrow_col, future_col = st.columns([5, 1, 5])
        deleted = change["operation"] == "delete"
        property_name = (change.get("property_path") or "Resource").split(".")[-1].replace("_", " ").title()
        with current_col:
            current_value = "PRESENT" if deleted else change.get("before")
            st.markdown(
                f'<div class="sim-card"><div class="eyebrow">CURRENT TWIN</div><strong>{html.escape(target_name)}</strong>'
                f'<div class="sim-value">{html.escape(str(current_value))}</div><span>{property_name}</span>'
                f'<div class="sim-id">{html.escape(change["resource_id"])}</div></div>', unsafe_allow_html=True,
            )
        with arrow_col:
            st.markdown('<div style="font-size:2rem;text-align:center;padding-top:2.5rem;color:#5EE6D8">→</div>', unsafe_allow_html=True)
        with future_col:
            future_value = "REMOVED" if deleted else change.get("after")
            card_class = "sim-card sim-removed" if deleted else "sim-card sim-future"
            st.markdown(
                f'<div class="{card_class}"><div class="eyebrow">FUTURE TWIN — SIMULATED</div><strong>{html.escape(target_name)}</strong>'
                f'<div class="sim-value">{html.escape(str(future_value))}</div><span>{property_name}</span>'
                f'<div class="sim-id">No infrastructure mutation performed</div></div>', unsafe_allow_html=True,
            )
        st.markdown("### State Diff")
        render_diff_summary(diff)
        render_human_diff(diff, current, future)
        render_affected_resources()
        st.markdown("### What changed?")
        st.info(_plain_explanation(changeset, diff, current, future))
    with tabs[1]:
        st.markdown("### Focused Diff View")
        st.caption("Only changed resources, real direct neighbors, and actual relationships are shown.")
        projection = project_diff(current, future, diff)
        build_graph(projection, DIFF_GRAPH_PATH, view="Diff View")
        components.html(DIFF_GRAPH_PATH.read_text(encoding="utf-8"), height=720, scrolling=True)
        st.caption("🟢 Added  ·  🟠 Modified  ·  🔴 Removed  ·  ⚪ Context")
    with tabs[2]:
        build_graph(current, CURRENT_COMPARE_PATH, view="Application View")
        components.html(CURRENT_COMPARE_PATH.read_text(encoding="utf-8"), height=720, scrolling=True)
    with tabs[3]:
        st.caption("Future Twin — simulated. Real infrastructure is unchanged.")
        build_graph(future, FUTURE_COMPARE_PATH, view="Application View")
        components.html(FUTURE_COMPARE_PATH.read_text(encoding="utf-8"), height=720, scrolling=True)
    with tabs[4]:
        st.caption("Canonical identifiers and raw contracts for technical review.")
        with st.expander("View normalized ChangeSet"):
            st.json(changeset)
        with st.expander("View raw State Diff"):
            st.json(diff)
        with st.expander("View raw Current resource"):
            st.json(_resource(current, change["resource_id"]))
        with st.expander("View raw Future resource"):
            st.json(_resource(future, change["resource_id"]))


def render_change_assistant(mode: str, region: str, cluster_name: str) -> None:
    st.title("Change Assistant")
    st.subheader("What infrastructure change would you like to simulate?")
    st.markdown('<div class="safe-note">Natural language → proposed structured change → confirmation → CloudTwin ingestion. This assistant never runs Terraform, kubectl, or AWS mutations.</div>', unsafe_allow_html=True)
    available = scenarios()
    tabs = st.tabs(["Use predefined scenario", "Describe custom change"])
    with tabs[0]:
        st.markdown("#### Suggested changes")
        columns = st.columns(5)
        for column, (label, scenario_id) in zip(columns, SUGGESTIONS.items()):
            with column:
                if st.button(label, key=f"suggest-{scenario_id}", use_container_width=True):
                    twin, note = twin_for_scenario(scenario_id, available[scenario_id], mode)
                    if twin:
                        set_pending_change(available[scenario_id], twin, scenario_id, note)
                    else:
                        st.session_state.confirmation_error = "The selected target is not available in the active Current Twin."
        selected_id = st.selectbox("Scenario pack", list(available), format_func=lambda sid: f"{sid} — {available[sid]['name']}")
        if st.button("Prepare selected scenario", use_container_width=True):
            twin, note = twin_for_scenario(selected_id, available[selected_id], mode)
            if twin:
                set_pending_change(available[selected_id], twin, selected_id, note)
            else:
                st.session_state.confirmation_error = "The selected target is not available in the active Current Twin."
    with tabs[1]:
        st.caption("Examples: “Scale the UI deployment from 1 to 4 replicas” or “Reduce UI replicas from 3 to 1”.")
        request = st.chat_input("Describe a proposed infrastructure change")
        if request:
            try:
                parsed = parse_change_request(request)
                twin = current_twin_for(parsed.scenario, mode)
                if not twin:
                    raise UnsupportedChangeRequest("The request was parsed, but its target/current value is not present in the active Current Twin.")
                set_pending_change(parsed.scenario, twin, "Custom request")
            except UnsupportedChangeRequest as exc:
                st.session_state.parse_error = str(exc)
    if "parse_error" in st.session_state:
        st.warning(st.session_state.pop("parse_error"))
    if "confirmation_error" in st.session_state:
        st.warning(st.session_state.confirmation_error)
    scenario = st.session_state.get("pending_scenario")
    if scenario:
        render_proposal(scenario)
        if st.session_state.get("scenario_twin_note"):
            st.info(st.session_state.scenario_twin_note)
        confirm, cancel = st.columns(2)
        with confirm:
            if st.button("Confirm for Analysis", type="primary", use_container_width=True):
                confirm_pending_change()
        with cancel:
            if st.button("Cancel", use_container_width=True):
                for key in (
                    "pending_scenario", "pending_twin", "pending_origin", "confirmed_changeset",
                    "analysis_result", "analysis_explanation", "future_twin", "state_diff", "affected_resources",
                    "simulation_report", "show_diff_graph",
                ):
                    st.session_state.pop(key, None)
                st.rerun()
    changeset = st.session_state.get("confirmed_changeset")
    if changeset:
        render_simulation_experience(
            st.session_state["pending_scenario"],
            changeset,
            st.session_state["state_diff"],
            st.session_state["pending_twin"],
            st.session_state["future_twin"],
            datasource_mode=mode,
            aws_region=region,
            eks_cluster=cluster_name,
        )
        st.success("Future Twin and State Diff generated in memory. Open Analysis for the available resilience finding.")
    render_pipeline(
        parsed=bool(st.session_state.get("pending_scenario")),
        changeset_ready=bool(st.session_state.get("confirmed_changeset")),
        future_ready=bool(st.session_state.get("future_twin")),
        diff_ready=bool(st.session_state.get("state_diff")),
        analysis_ready=bool(st.session_state.get("analysis_result")),
    )


def render_analysis_result(result: dict[str, Any]) -> None:
    findings = result.get("findings", [])
    if not findings:
        st.info("Aucune règle ne s'applique à ce changement.")
        return
    order = ["critical", "high", "medium", "low"]
    failed = sorted((f for f in findings if f["status"] == "fail"),
                    key=lambda f: order.index(f["severity"]) if f["severity"] in order else len(order))
    unknown = [f for f in findings if f["status"] == "not_evaluated"]
    passed = [f for f in findings if f["status"] == "pass"]

    st.markdown(f"### Risques actifs ({len(failed)})")
    if not failed:
        st.success("Aucun risque actif : toutes les règles évaluées passent.")
    for finding in failed:
        st.markdown(ui.finding_card(finding), unsafe_allow_html=True)
        with st.expander(f"Preuves de {finding['rule_id']}"):
            st.json(finding.get("evidence", {}))
    if unknown:
        with st.expander(f"{len(unknown)} règle(s) non évaluée(s) : données manquantes"):
            st.markdown(ui.quiet_list(unknown), unsafe_allow_html=True)
    if passed:
        with st.expander(f"{len(passed)} règle(s) vérifiée(s) sans risque"):
            st.markdown(ui.quiet_list(passed), unsafe_allow_html=True)

    risk = result.get("risk") or {}
    radius = result.get("blast_radius") or {}
    with st.expander("Détail du calcul du score"):
        st.write(risk.get("explanation", ""))
        st.caption(risk.get("formula", ""))
        st.write(f"Blast radius : {len(radius.get('direct', []))} ressource(s) directe(s), "
                 f"{len(radius.get('indirect', []))} indirecte(s).")


def ai_outputs(run: bool = False) -> tuple[dict[str, Any], dict[str, Any], list[str]] | None:
    """Explication IA + correctifs vérifiés pour le changement confirmé, mis en cache.

    Sans LLM, tout est calculé tout de suite (texte de secours + catalogue).
    Avec Ollama activé, on attend le clic : un appel prend quelques secondes.
    """
    context = st.session_state.get("analysis_context")
    result = st.session_state.get("analysis_result_obj")
    if context is None or result is None:
        return None
    client = llm_client()
    key = (st.session_state.get("llm_model", "") if client else "", bool(client))
    cache = st.session_state.setdefault("ai_cache", {})
    if key not in cache:
        if client and not run:
            return None
        with st.spinner("L'analyste IA rédige son explication et vérifie les correctifs..."):
            report, rejected = remediate_with_ai(context, result, client)
            explanation = explain_analysis(result, client=client).model_dump(mode="json")
        cache[key] = (explanation, report.model_dump(mode="json"), rejected)
        st.session_state.analysis_explanation = explanation
    return cache[key]


def render_attack_graph(changeset: dict[str, Any], result: dict[str, Any]) -> None:
    """Future Twin coloré (modifié / direct / indirect) et chemin d'attaque IAM-003."""
    future = st.session_state.get("future_twin")
    if not future:
        st.info("Future Twin indisponible.")
        return
    created = [c["resource_id"] for c in changeset["changes"] if c["operation"] == "create"]
    changed = [c["resource_id"] for c in changeset["changes"] if c["operation"] != "create"]
    out = {"future_twin": future, "analysis": result,
           "simulation": {"changed": changed, "created": created}}
    radius = result.get("blast_radius") or {}
    cols = st.columns(2)
    cols[0].metric("Impact direct (1 saut)", len(radius.get("direct", [])))
    cols[1].metric("Impact indirect (2–3 sauts)", len(radius.get("indirect", [])))
    st.markdown(
        f'<span style="color:{HIGHLIGHT["changed"]}">●</span> modifié &nbsp; '
        f'<span style="color:{HIGHLIGHT["direct"]}">●</span> direct &nbsp; '
        f'<span style="color:{HIGHLIGHT["indirect"]}">●</span> indirect &nbsp; '
        f'<span style="color:{ATTACK_EDGE}">━</span> chemin d\'attaque (IAM-003)',
        unsafe_allow_html=True,
    )
    components.html(impact_graph_html(out), height=640, scrolling=False)


def load_scenario_for_analysis(mode: str) -> None:
    """Rappel du sélecteur S01–S05 : on simule le scénario choisi sans passer par Change Assistant."""
    scenario_id = st.session_state.get("analysis_pick") or ""
    scenario = scenarios().get(scenario_id)
    if scenario is None:
        return
    twin, note = twin_for_scenario(scenario_id, scenario, mode)
    if twin is None:
        st.session_state.confirmation_error = "Aucun Current Twin disponible : lance d'abord la découverte."
        return
    set_pending_change(scenario, twin, scenario_id, note)
    confirm_pending_change()


def render_scenario_switch(mode: str) -> None:
    options = list(scenarios())
    current = (st.session_state.get("pending_scenario") or {}).get("scenario_id")
    # Le sélecteur suit le changement confirmé, même s'il vient de Change Assistant.
    st.session_state.analysis_pick = current if current in options and st.session_state.get("confirmed_changeset") else None
    st.segmented_control(
        "Simuler un scénario", options, key="analysis_pick",
        on_change=load_scenario_for_analysis, args=(mode,),
        help="Chaque scénario est simulé sur le jumeau : rien n'est déployé.",
    )
    if st.session_state.get("confirmation_error"):
        st.error(st.session_state.confirmation_error)


def render_analysis(mode: str, region: str, cluster_name: str) -> None:
    result = st.session_state.get("analysis_result")
    changeset = st.session_state.get("confirmed_changeset")
    if not result or not changeset:
        head, switch = st.columns([3, 2], vertical_alignment="bottom")
        with head:
            st.markdown(ui.page_head(
                "Analysis", "Choisis un scénario ici, ou confirme un changement dans Change Assistant : "
                "le Current Twin et le Future Twin s'affichent face à face.",
            ), unsafe_allow_html=True)
        with switch:
            render_scenario_switch(mode)
        render_pipeline()
        return
    scenario = st.session_state["pending_scenario"]
    current = st.session_state["pending_twin"]
    names = {r["id"]: r.get("name", r["id"]) for r in current.get("resources", [])}
    head, switch = st.columns([3, 2], vertical_alignment="bottom")
    with head:
        label = ", ".join(x for x in (scenario.get("scenario_id"), scenario.get("name")) if x)
        st.markdown(ui.page_head(label or "Changement proposé",
                                 scenario.get("description") or "Changement proposé, simulé sur le jumeau."),
                    unsafe_allow_html=True)
    with switch:
        render_scenario_switch(mode)
    st.markdown(f'<div class="change-chip">{html.escape(ui.change_line(changeset, names))}</div>',
                unsafe_allow_html=True)
    if st.session_state.get("scenario_twin_note"):
        st.info(st.session_state.scenario_twin_note)
    st.markdown(ui.mirror(current, st.session_state["future_twin"], changeset, result), unsafe_allow_html=True)
    for warning in st.session_state.get("simulation_warnings") or []:
        st.caption(f"Simulation : {warning}")
    tabs = st.tabs(["Risque", "Impact et chemin d'attaque", "Analyste IA", "Correctifs vérifiés", "Simulation"])
    with tabs[0]:
        render_analysis_result(result)
    with tabs[1]:
        render_attack_graph(changeset, result)
        st.caption("Les ressources impactées structurellement sont détaillées dans l'onglet Simulation.")
    ai = ai_outputs()
    with tabs[2]:
        if ai is None:
            st.info("Ollama est activé : l'appel prend quelques secondes.")
            if st.button("Demander à l'analyste IA", type="primary"):
                ai = ai_outputs(run=True)
                st.rerun()
        else:
            render_ai(ai[0])
    with tabs[3]:
        if ai is None:
            st.info("Les correctifs apparaissent après la réponse de l'analyste IA (onglet Analyste IA).")
        else:
            render_fixes(ai[1], ai[2])
    with tabs[4]:
        render_simulation_experience(
            scenario,
            changeset,
            st.session_state["state_diff"],
            st.session_state["pending_twin"],
            st.session_state["future_twin"],
            datasource_mode=mode,
            aws_region=region,
            eks_cluster=cluster_name,
        )
    st.divider()
    render_pipeline(
        parsed=True,
        changeset_ready=True,
        future_ready=True,
        diff_ready=True,
        analysis_ready=True,
    )


NAV_ICONS = {
    "Overview": ":material/space_dashboard:",
    "Infrastructure": ":material/lan:",
    "Current Twin": ":material/account_tree:",
    "Change Assistant": ":material/edit_note:",
    "Analysis": ":material/radar:",
}


def render_sidebar() -> tuple[str, str, str, str]:
    """Navigation, source des données, analyste IA ; renvoie (page, mode, région, cluster)."""
    st.markdown(ui.brand(), unsafe_allow_html=True)
    page = st.radio("Navigation", PAGES, key="nav", label_visibility="collapsed",
                    format_func=lambda name: f"{NAV_ICONS.get(name, '')} {name}")
    result = st.session_state.get("analysis_result")
    scenario = st.session_state.get("pending_scenario") or {}
    if result and st.session_state.get("confirmed_changeset"):
        st.markdown(ui.side_last_run(scenario.get("scenario_id") or "Custom", scenario.get("name", ""),
                                     int((result.get("risk") or {}).get("score", 0))), unsafe_allow_html=True)

    st.markdown(ui.side_title("Source des données"), unsafe_allow_html=True)
    mode = st.radio("Data source", ["Local Fixture Mode", "Live AWS Mode"], key="data_source",
                    label_visibility="collapsed",
                    format_func=lambda m: "Fixtures" if m == "Local Fixture Mode" else "AWS live")
    region, cluster_name = "eu-west-3", "retail-store"
    if mode == "Live AWS Mode":
        region = st.text_input("AWS Region", region)
        cluster_name = st.text_input("EKS Cluster", cluster_name)
        st.markdown(ui.side_status("Lecture seule sur AWS : découverte, jamais de modification.", "live"),
                    unsafe_allow_html=True)
    else:
        st.markdown(ui.side_status("Jumeaux de démonstration : aucun appel AWS.", "ok"), unsafe_allow_html=True)

    render_llm_settings()
    st.markdown(ui.side_footer(), unsafe_allow_html=True)
    return page, mode, region, cluster_name


def main() -> None:
    configure_page()
    with st.sidebar:
        page, mode, region, cluster_name = render_sidebar()
    if mode == "Local Fixture Mode" and "twin" not in st.session_state:
        st.session_state.twin = fixture_for_scenario("S01")
        st.session_state.twin_source = "Local fixture S01"
    if page == "Overview":
        render_overview(mode)
    elif page == "Infrastructure":
        render_infrastructure(mode, region, cluster_name)
    elif page == "Current Twin":
        render_current_twin(mode)
    elif page == "Change Assistant":
        render_change_assistant(mode, region, cluster_name)
    else:
        render_analysis(mode, region, cluster_name)


if __name__ == "__main__":
    main()
