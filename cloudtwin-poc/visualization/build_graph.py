"""Interactive, projection-aware visualization of a normalized Current Twin."""

from __future__ import annotations

import base64
import html
import json
import sys
from pathlib import Path
from typing import Any

import networkx as nx
from pyvis.network import Network

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "outputs" / "current_state.json"
OUTPUT = ROOT / "outputs" / "current_twin.html"

VIEW_RESOURCE_TYPES = {
    "Application View": {
        "INTERNET", "LOAD_BALANCER", "INGRESS", "SERVICE", "DEPLOYMENT", "STATEFUL_SET",
    },
    "Kubernetes View": {
        "DEPLOYMENT", "SERVICE", "STATEFUL_SET", "POD", "NODE", "INGRESS", "NETWORK_POLICY",
    },
    "AWS Infrastructure View": {
        "INTERNET", "LOAD_BALANCER", "EKS_CLUSTER", "EKS_NODE_GROUP", "EC2_INSTANCE",
        "VPC", "SUBNET", "SECURITY_GROUP",
    },
}

SYSTEM_NAMESPACES = {
    "kube-system", "kube-public", "kube-node-lease", "cert-manager",
    "opentelemetry-operator-system",
}

STYLES = {
    "INTERNET": ("#475569", "star", "Network"),
    "VPC": ("#64748b", "box", "Network"),
    "SUBNET": ("#94a3b8", "box", "Network"),
    "SECURITY_GROUP": ("#b45353", "diamond", "Security"),
    "EKS_CLUSTER": ("#475569", "hexagon", "AWS"),
    "EKS_NODE_GROUP": ("#64748b", "box", "AWS"),
    "EC2_INSTANCE": ("#78716c", "dot", "AWS"),
    "LOAD_BALANCER": ("#4f6f8f", "triangle", "AWS"),
    "IAM_ROLE": ("#9ca3af", "diamond", "Security"),
    "NAMESPACE": ("#64748b", "box", "Kubernetes"),
    "NODE": ("#64748b", "dot", "Kubernetes"),
    "DEPLOYMENT": ("#3f6873", "box", "Application Service"),
    "STATEFUL_SET": ("#76624f", "database", "Database / Stateful dependency"),
    "POD": ("#7b9aa1", "dot", "Kubernetes"),
    "SERVICE": ("#4f5d95", "triangle", "Application Service"),
    "SERVICE_ACCOUNT": ("#a3a3a3", "diamond", "Security"),
    "INGRESS": ("#5b6f91", "triangle", "Network"),
    "NETWORK_POLICY": ("#9f5f5f", "diamond", "Security"),
}

ICON_BY_TYPE = {
    "INTERNET": "globe", "VPC": "cloud", "SUBNET": "layers",
    "SECURITY_GROUP": "shield", "EKS_CLUSTER": "kubernetes",
    "EKS_NODE_GROUP": "servers", "EC2_INSTANCE": "server",
    "LOAD_BALANCER": "network", "IAM_ROLE": "key", "NAMESPACE": "brackets",
    "NODE": "server", "DEPLOYMENT": "cube", "STATEFUL_SET": "database",
    "POD": "container", "SERVICE": "connection", "SERVICE_ACCOUNT": "user",
    "INGRESS": "gateway", "NETWORK_POLICY": "shield",
}

ICON_DRAWINGS = {
    "globe": '<circle cx="48" cy="48" r="25"/><path d="M23 48h50M48 23c10 10 10 40 0 50M48 23c-10 10-10 40 0 50"/>',
    "cloud": '<path d="M27 64h40a13 13 0 0 0 1-26 21 21 0 0 0-40-2 14 14 0 0 0-1 28Z"/>',
    "layers": '<path d="m48 25 27 14-27 14-27-14 27-14Z"/><path d="m24 50 24 13 24-13M24 61l24 13 24-13"/>',
    "shield": '<path d="M48 20 72 30v18c0 16-10 25-24 30-14-5-24-14-24-30V30l24-10Z"/><path d="m37 48 8 8 15-17"/>',
    "kubernetes": '<circle cx="48" cy="48" r="25"/><circle cx="48" cy="48" r="7"/><path d="M48 23v18M48 55v18M23 48h18M55 48h18M30 30l13 13M53 53l13 13M66 30 53 43M43 53 30 66"/>',
    "servers": '<rect x="22" y="25" width="52" height="20" rx="4"/><rect x="22" y="53" width="52" height="20" rx="4"/><path d="M30 35h1M30 63h1M40 35h24M40 63h24"/>',
    "server": '<rect x="25" y="20" width="46" height="56" rx="6"/><path d="M25 43h46M25 58h46M34 32h1M34 50h1M34 67h1M43 32h19"/>',
    "network": '<rect x="35" y="20" width="26" height="18" rx="4"/><rect x="18" y="59" width="24" height="18" rx="4"/><rect x="54" y="59" width="24" height="18" rx="4"/><path d="M48 38v11M30 59v-10h36v10"/>',
    "key": '<circle cx="35" cy="45" r="13"/><path d="m45 54 24 20M57 64l6-6M64 71l6-6"/>',
    "brackets": '<path d="M35 22H25v52h10M61 22h10v52H61"/><path d="M42 40h12M42 55h12"/>',
    "cube": '<path d="m48 20 25 14v28L48 76 23 62V34l25-14Z"/><path d="m23 34 25 14 25-14M48 48v28"/>',
    "database": '<ellipse cx="48" cy="28" rx="24" ry="10"/><path d="M24 28v36c0 6 11 10 24 10s24-4 24-10V28M24 46c0 6 11 10 24 10s24-4 24-10"/>',
    "container": '<rect x="21" y="27" width="54" height="42" rx="5"/><path d="M32 27v42M43 27v42M54 27v42M65 27v42"/>',
    "connection": '<circle cx="29" cy="48" r="10"/><circle cx="67" cy="31" r="10"/><circle cx="67" cy="65" r="10"/><path d="m38 43 20-9M38 53l20 9"/>',
    "user": '<circle cx="48" cy="35" r="13"/><path d="M25 75c2-16 11-24 23-24s21 8 23 24"/>',
    "gateway": '<path d="M22 73V28h52v45M22 41h52M35 73V53h26v20"/><path d="m45 62 7-7 7 7"/>',
    "monitor": '<rect x="18" y="21" width="60" height="42" rx="5"/><path d="M38 76h20M48 63v13"/>',
    "package": '<path d="m48 20 25 13v30L48 76 23 63V33l25-13Z"/><path d="m23 33 25 13 25-13M48 46v30M36 27l25 13"/>',
    "cart": '<path d="M20 25h9l7 34h31l8-23H33M40 70h1M64 70h1"/><circle cx="40" cy="70" r="4"/><circle cx="64" cy="70" r="4"/>',
    "card": '<rect x="18" y="28" width="60" height="42" rx="7"/><path d="M18 41h60M28 58h18"/>',
    "clipboard": '<rect x="24" y="25" width="48" height="52" rx="6"/><path d="M39 25v-6h18v6M35 43h26M35 55h26M35 67h16"/>',
    "lightning": '<path d="m53 17-25 36h18l-4 27 26-39H50l3-24Z"/>',
    "message": '<rect x="18" y="23" width="60" height="43" rx="8"/><path d="m31 66-8 11 20-11M31 38h34M31 51h24"/>',
    "nosql": '<rect x="20" y="24" width="24" height="24" rx="3"/><rect x="52" y="24" width="24" height="24" rx="3"/><rect x="20" y="56" width="24" height="18" rx="3"/><rect x="52" y="56" width="24" height="18" rx="3"/>',
}

IMPORTANT_PROPERTIES = (
    "namespace", "replicas", "ready_replicas", "available_replicas", "service_type",
    "scheme", "status", "phase", "region", "cluster_name", "instance_type",
    "desired_size", "min_size", "max_size", "ports", "protocol",
)

LEVELS = {
    "Application View": {
        "INTERNET": 0, "LOAD_BALANCER": 1, "INGRESS": 2, "SERVICE": 2,
        "DEPLOYMENT": 3, "STATEFUL_SET": 4,
    },
    "Kubernetes View": {
        "INGRESS": 0, "SERVICE": 0, "DEPLOYMENT": 1, "STATEFUL_SET": 1,
        "NETWORK_POLICY": 1, "POD": 2, "NODE": 3,
    },
    "AWS Infrastructure View": {
        "INTERNET": 0, "VPC": 0, "SUBNET": 1, "SECURITY_GROUP": 1,
        "LOAD_BALANCER": 2, "EKS_CLUSTER": 2, "EKS_NODE_GROUP": 3, "EC2_INSTANCE": 4,
    },
}


def allowed_types(view: str, twin: dict[str, Any]) -> list[str]:
    """Return types available to a view, ordered for stable Streamlit controls."""
    discovered = {resource["type"] for resource in twin.get("resources", [])}
    permitted = VIEW_RESOURCE_TYPES.get(view, discovered)
    return sorted(discovered & permitted)


def icon_name(resource: dict[str, Any]) -> str:
    """Choose an icon from discovered type/name without changing graph semantics."""
    name = str(resource.get("name", "")).lower()
    if any(token in name for token in ("mysql", "postgres", "mariadb")):
        return "database"
    if "redis" in name:
        return "lightning"
    if any(token in name for token in ("rabbit", "mq", "queue")):
        return "message"
    if "dynamo" in name:
        return "nosql"
    if "ui" == name or name.startswith("ui-"):
        return "monitor"
    if "catalog" in name:
        return "package"
    if "cart" in name:
        return "cart"
    if "checkout" in name:
        return "card"
    if "order" in name:
        return "clipboard"
    return ICON_BY_TYPE.get(resource.get("type", ""), "cube")


def icon_data_uri(icon: str, color: str = "#4f5d95") -> str:
    """Build a self-contained SVG icon card for vis-network image nodes."""
    drawing = ICON_DRAWINGS.get(icon, ICON_DRAWINGS["cube"])
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96">
    <rect x="3" y="3" width="90" height="90" rx="24" fill="{color}" stroke="#e2e8f0" stroke-width="3"/>
    <g fill="none" stroke="#f8fafc" stroke-width="4" stroke-linecap="round" stroke-linejoin="round">{drawing}</g>
    </svg>"""
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def legend_items() -> list[tuple[str, str, str]]:
    """Return labels, colors, and inline icons shared with the Streamlit legend."""
    definitions = [
        ("Network", "#64748b", "network"),
        ("Kubernetes", "#3f6873", "kubernetes"),
        ("Application", "#4f5d95", "cube"),
        ("Database / Messaging", "#76624f", "database"),
        ("AWS Infrastructure", "#475569", "cloud"),
        ("Security", "#b45353", "shield"),
    ]
    return [(label, color, icon_data_uri(icon, color)) for label, color, icon in definitions]


def _is_application_resource(resource: dict[str, Any]) -> bool:
    if resource.get("provider") == "aws":
        return resource.get("type") in {"INTERNET", "LOAD_BALANCER"}
    namespace = resource.get("properties", {}).get("namespace")
    return namespace not in SYSTEM_NAMESPACES


def project_twin(
    twin: dict[str, Any],
    view: str,
    *,
    providers: set[str] | None = None,
    resource_types: set[str] | None = None,
) -> dict[str, Any]:
    """Create a read-only graph projection using only discovered resources/edges."""
    permitted = VIEW_RESOURCE_TYPES.get(view)
    resources = []
    for resource in twin.get("resources", []):
        if permitted is not None and resource["type"] not in permitted:
            continue
        if view == "Application View" and not _is_application_resource(resource):
            continue
        if providers is not None and resource.get("provider") not in providers:
            continue
        if resource_types is not None and resource.get("type") not in resource_types:
            continue
        resources.append(resource)

    resource_ids = {resource["id"] for resource in resources}
    relationships = [
        relationship for relationship in twin.get("relationships", [])
        if relationship["source"] in resource_ids and relationship["target"] in resource_ids
    ]

    # A demo-oriented Application View omits isolated objects when actual
    # application relationships exist. It never creates substitute edges.
    if view == "Application View" and relationships:
        connected = {
            endpoint
            for relationship in relationships
            for endpoint in (relationship["source"], relationship["target"])
        }
        resources = [resource for resource in resources if resource["id"] in connected]

    return {
        **{key: value for key, value in twin.items() if key not in {"resources", "relationships", "summary"}},
        "resources": resources,
        "relationships": relationships,
        "summary": {
            "aws_resources": sum(r.get("provider") == "aws" for r in resources),
            "kubernetes_resources": sum(r.get("provider") == "kubernetes" for r in resources),
            "relationships": len(relationships),
        },
    }


def project_diff(
    current_twin: dict[str, Any],
    future_twin: dict[str, Any],
    state_diff: dict[str, Any],
) -> dict[str, Any]:
    """Build a focused visual projection using only real twin/diff objects."""
    changes = state_diff.get("changes", [])
    resource_states = {
        change["resource_id"]: change["change_type"].lower()
        for change in changes
    }
    removed_relationships = state_diff.get("removed_relationships", [])
    added_relationships = state_diff.get("added_relationships", [])
    changed_ids = set(resource_states)
    for relationship in [*removed_relationships, *added_relationships]:
        changed_ids.update((relationship["source"], relationship["target"]))

    # Context is limited to actual direct neighbors of a changed resource.
    current_relationships = current_twin.get("relationships", [])
    future_relationships = future_twin.get("relationships", [])
    direct_seeds = set(resource_states)
    for relationship in [*current_relationships, *future_relationships]:
        if relationship["source"] in direct_seeds or relationship["target"] in direct_seeds:
            changed_ids.update((relationship["source"], relationship["target"]))

    resources_by_id = {
        resource["id"]: resource
        for resource in [*current_twin.get("resources", []), *future_twin.get("resources", [])]
    }
    resources = [
        {**resources_by_id[resource_id], "_diff_state": resource_states.get(resource_id, "context")}
        for resource_id in sorted(changed_ids)
        if resource_id in resources_by_id
    ]
    removed_keys = {_relationship_key(item) for item in removed_relationships}
    added_keys = {_relationship_key(item) for item in added_relationships}
    relationships_by_key = {}
    for item in [*current_relationships, *future_relationships]:
        key = _relationship_key(item)
        changed_edge = key in removed_keys or key in added_keys
        direct_context = item["source"] in direct_seeds or item["target"] in direct_seeds
        if changed_edge or direct_context:
            relationships_by_key[key] = item
    relationships = []
    for key in sorted(relationships_by_key):
        state = "removed" if key in removed_keys else "added" if key in added_keys else "context"
        relationships.append({**relationships_by_key[key], "_diff_state": state})
    return {"resources": resources, "relationships": relationships}


def _relationship_key(relationship: dict[str, Any]) -> tuple[str, str, str]:
    return relationship["source"], relationship["target"], relationship["type"]


def _title_case_name(name: str) -> str:
    acronyms = {
        "ui": "UI", "api": "API", "eks": "EKS", "vpc": "VPC", "nlb": "NLB",
        "alb": "ALB", "rds": "RDS", "postgresql": "PostgreSQL",
    }
    return " ".join(
        acronyms.get(part.lower(), part.capitalize())
        for part in name.replace("_", "-").split("-")
        if part
    )


def readable_label(resource: dict[str, Any]) -> str:
    """Create a compact label while retaining canonical IDs in tooltips."""
    name = _title_case_name(str(resource.get("name") or resource["id"].split(":")[-1]))
    resource_type = resource["type"]
    if resource_type == "SERVICE":
        return name if name.lower().endswith("service") else f"{name} Service"
    if resource_type == "STATEFUL_SET":
        return name
    if resource_type == "LOAD_BALANCER":
        return name if len(name) <= 30 else "Public Load Balancer"
    if resource_type == "SECURITY_GROUP":
        return name if len(name) <= 30 else "Security Group"
    if resource_type == "EC2_INSTANCE":
        return name if len(name) <= 30 else "Worker Instance"
    return name[:36]


def _safe_properties(resource: dict[str, Any]) -> list[tuple[str, Any]]:
    properties = resource.get("properties", {})
    return [(key, properties[key]) for key in IMPORTANT_PROPERTIES if key in properties]


def _tooltip(resource: dict[str, Any], degree: int) -> str:
    rows = [
        ("Name", resource.get("name", "unknown")),
        ("Canonical ID", resource["id"]),
        ("Type", resource["type"]),
        ("Provider", resource.get("provider", "unknown")),
        ("Relationships", degree),
        *_safe_properties(resource),
    ]
    return "<br>".join(
        f"<b>{html.escape(str(key).replace('_', ' ').title())}:</b> "
        f"{html.escape(json.dumps(value, default=str) if isinstance(value, (dict, list)) else str(value))}"
        for key, value in rows
    )


def _selection_script() -> str:
    """Highlight a selected node and direct neighbors; reset on deselection."""
    return """
    <script>
    (function () {
      const originals = {};
      nodes.get().forEach(function (node) {
        originals[node.id] = {opacity: node.opacity || 1, size: node.size || 42};
      });
      function resetHighlight() {
        nodes.update(nodes.get().map(function (node) {
          return {id: node.id, opacity: originals[node.id].opacity, size: originals[node.id].size};
        }));
        edges.update(edges.get().map(function (edge) { return {id: edge.id, color: {color: '#94a3b8', opacity: 0.72}}; }));
        document.getElementById('cloudtwin-detail-content').innerHTML = '<span class="ct-muted">Select a node to inspect its discovered properties and direct connections.</span>';
      }
      window.cloudTwinResetHighlight = resetHighlight;
      network.on('selectNode', function (params) {
        const selected = params.nodes[0];
        const related = new Set(network.getConnectedNodes(selected));
        related.add(selected);
        nodes.update(nodes.get().map(function (node) {
          return {id: node.id, opacity: related.has(node.id) ? 1 : 0.13, size: node.id === selected ? originals[node.id].size + 9 : originals[node.id].size};
        }));
        edges.update(edges.get().map(function (edge) {
          const active = edge.from === selected || edge.to === selected;
          return {id: edge.id, color: {color: active ? '#334155' : '#cbd5e1', opacity: active ? 1 : 0.12}};
        }));
        const node = nodes.get(selected);
        document.getElementById('cloudtwin-detail-content').innerHTML = node.title || node.label;
      });
      network.on('deselectNode', resetHighlight);
      network.on('deselectEdge', function (params) { if (!params.nodes.length) resetHighlight(); });
      network.once('stabilizationIterationsDone', function () { network.fit({animation: false}); });
      setTimeout(function () { network.fit({animation: {duration: 450, easingFunction: 'easeInOutQuad'}}); }, 180);
    })();
    </script>
    """


def _graph_overlay() -> str:
    return """
    <style>
      #mynetwork { border: 1px solid #e2e8f0 !important; border-radius: 18px; background: linear-gradient(180deg,#fbfdff 0%,#f8fafc 100%) !important; }
      .ct-toolbar { position:absolute; top:14px; left:14px; z-index:20; display:flex; gap:8px; }
      .ct-toolbar button { border:1px solid #cbd5e1; background:rgba(255,255,255,.94); color:#334155; border-radius:9px; padding:7px 11px; font:600 12px Inter,Arial,sans-serif; box-shadow:0 2px 8px rgba(15,23,42,.08); cursor:pointer; }
      .ct-toolbar button:hover { background:#f1f5f9; }
      .ct-details { position:absolute; top:14px; right:14px; z-index:20; width:280px; max-height:240px; overflow:auto; border:1px solid #dbe3ee; background:rgba(255,255,255,.96); border-radius:12px; padding:12px 14px; box-shadow:0 8px 24px rgba(15,23,42,.10); color:#334155; font:12px/1.55 Inter,Arial,sans-serif; backdrop-filter:blur(8px); }
      .ct-details strong { display:block; color:#0f172a; font-size:13px; margin-bottom:5px; }
      .ct-muted { color:#64748b; }
    </style>
    <div class="ct-toolbar">
      <button onclick="network.fit({animation:{duration:450,easingFunction:'easeInOutQuad'}})">Fit view</button>
      <button onclick="network.unselectAll();window.cloudTwinResetHighlight();network.fit({animation:true})">Reset</button>
    </div>
    <div class="ct-details"><strong>Resource details</strong><div id="cloudtwin-detail-content"><span class="ct-muted">Select a node to inspect its discovered properties and direct connections.</span></div></div>
    """


def build_graph(twin: dict[str, Any], output: Path = OUTPUT, *, view: str = "Full Infrastructure") -> Path:
    """Render an interactive Current Twin projection without changing its data."""
    graph = nx.MultiDiGraph()
    for resource in twin.get("resources", []):
        graph.add_node(resource["id"], **resource)
    for relationship in twin.get("relationships", []):
        if relationship["source"] in graph and relationship["target"] in graph:
            graph.add_edge(relationship["source"], relationship["target"], **relationship)

    simplified = view != "Full Infrastructure"
    network = Network(
        height="860px", width="100%", directed=True, bgcolor="#f8fafc",
        font_color="#0f172a", select_menu=False, filter_menu=False, cdn_resources="in_line",
    )
    if simplified:
        network.set_options("""
        {"layout":{"hierarchical":{"enabled":true,"direction":"UD","sortMethod":"directed","levelSeparation":150,"nodeSpacing":180,"treeSpacing":220,"blockShifting":true,"edgeMinimization":true,"parentCentralization":true}},
         "physics":{"enabled":false},
         "interaction":{"hover":true,"navigationButtons":true,"keyboard":true,"multiselect":false},
         "edges":{"arrows":{"to":{"enabled":true,"scaleFactor":0.55}},"smooth":{"enabled":true,"type":"cubicBezier","forceDirection":"vertical","roundness":0.35},"font":{"size":10,"align":"middle","color":"#475569","strokeWidth":3,"strokeColor":"#f8fafc"}}}
        """)
    else:
        network.set_options("""
        {"layout":{"improvedLayout":true},
         "physics":{"barnesHut":{"gravitationalConstant":-6500,"centralGravity":0.22,"springLength":190,"springConstant":0.025,"damping":0.22,"avoidOverlap":0.65},"stabilization":{"enabled":true,"iterations":450,"updateInterval":50,"fit":true}},
         "interaction":{"hover":true,"navigationButtons":true,"keyboard":true,"multiselect":false},
         "edges":{"arrows":{"to":{"enabled":true,"scaleFactor":0.5}},"smooth":{"enabled":true,"type":"dynamic"}}}
        """)

    levels = LEVELS.get(view, {})
    for node_id, data in graph.nodes(data=True):
        color, _, category = STYLES.get(data["type"], ("#64748b", "dot", "Kubernetes"))
        diff_state = data.get("_diff_state")
        if diff_state == "added":
            color = "#15803d"
        elif diff_state == "modified":
            color = "#d97706"
        elif diff_state == "removed":
            color = "#b91c1c"
        elif diff_state == "context":
            color = "#64748b"
        size = 48 if data["type"] in {"INTERNET", "VPC", "EKS_CLUSTER", "LOAD_BALANCER"} else 42
        node_options = {
            "label": f"{'✕ ' if diff_state == 'removed' else ''}{readable_label(data)}",
            "title": _tooltip(data, graph.degree(node_id)),
            "group": category,
            "shape": "image",
            "image": icon_data_uri(icon_name(data), color),
            "size": size,
            "font": {"size": 12, "face": "Inter, Arial, sans-serif", "color": "#1e293b", "vadjust": 5},
            "shapeProperties": {"useBorderWithImage": False, "interpolation": True},
        }
        if data["type"] in levels:
            node_options["level"] = levels[data["type"]]
        network.add_node(node_id, **node_options)

    for source, target, data in graph.edges(data=True):
        relationship = data["type"].replace("_", " ")
        diff_state = data.get("_diff_state", "context")
        edge_color = "#b91c1c" if diff_state == "removed" else "#15803d" if diff_state == "added" else "#94a3b8"
        network.add_edge(
            source, target,
            label=relationship.title() if view == "Diff View" else "",
            title=relationship.title(),
            color={"color": edge_color, "opacity": 0.8},
            width=2.4 if diff_state in {"removed", "added"} else 1.4,
            dashes=diff_state == "removed",
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    graph_html = network.generate_html(name=str(output), notebook=False)
    graph_html = graph_html.replace("</body>", f"{_graph_overlay()}{_selection_script()}</body>")
    output.write_text(graph_html, encoding="utf-8")
    return output


def main() -> int:
    if not INPUT.exists():
        print(f"Current Twin not found: {INPUT}\nRun python discovery\\discover.py first.", file=sys.stderr)
        return 1
    twin = json.loads(INPUT.read_text(encoding="utf-8"))
    build_graph(twin, OUTPUT)
    print(f"Current Twin graph generated: {OUTPUT}")
    print(f"Nodes: {len(twin.get('resources', []))} | Relationships: {len(twin.get('relationships', []))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
