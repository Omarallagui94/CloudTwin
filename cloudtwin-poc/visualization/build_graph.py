"""Build an interactive HTML visualization from the normalized Current Twin."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import networkx as nx
from pyvis.network import Network

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "outputs" / "current_state.json"
OUTPUT = ROOT / "outputs" / "current_twin.html"

STYLES = {
    "INTERNET": ("#2563eb", "star"), "VPC": ("#0f766e", "box"),
    "SUBNET": ("#14b8a6", "box"), "SECURITY_GROUP": ("#64748b", "diamond"),
    "EKS_CLUSTER": ("#f97316", "hexagon"), "EKS_NODE_GROUP": ("#fb923c", "box"),
    "EC2_INSTANCE": ("#f59e0b", "dot"), "LOAD_BALANCER": ("#7c3aed", "triangle"),
    "IAM_ROLE": ("#94a3b8", "diamond"), "NAMESPACE": ("#0ea5e9", "box"),
    "NODE": ("#eab308", "dot"), "DEPLOYMENT": ("#22c55e", "box"),
    "STATEFUL_SET": ("#16a34a", "database"), "POD": ("#86efac", "dot"),
    "SERVICE": ("#a855f7", "triangle"), "SERVICE_ACCOUNT": ("#cbd5e1", "diamond"),
    "INGRESS": ("#8b5cf6", "triangle"), "NETWORK_POLICY": ("#ef4444", "diamond"),
}


def main() -> int:
    if not INPUT.exists():
        print(f"Current Twin not found: {INPUT}\nRun python discovery\\discover.py first.", file=sys.stderr)
        return 1
    twin = json.loads(INPUT.read_text(encoding="utf-8"))
    graph = nx.MultiDiGraph()
    for resource in twin["resources"]:
        graph.add_node(resource["id"], **resource)
    for relationship in twin["relationships"]:
        if relationship["source"] in graph and relationship["target"] in graph:
            graph.add_edge(
                relationship["source"],
                relationship["target"],
                type=relationship["type"],
            )

    network = Network(
        height="900px",
        width="100%",
        directed=True,
        bgcolor="#f8fafc",
        font_color="#0f172a",
        select_menu=True,
        filter_menu=True,
        cdn_resources="in_line",
    )
    network.set_options("""
    {"physics":{"barnesHut":{"gravitationalConstant":-12000,"springLength":150,"springConstant":0.03},"stabilization":{"iterations":250}},
     "interaction":{"hover":true,"navigationButtons":true},
     "edges":{"arrows":{"to":{"enabled":true,"scaleFactor":0.6}},"smooth":{"type":"dynamic"},"font":{"size":10,"align":"middle"}}}
    """)
    for node_id, data in graph.nodes(data=True):
        color, shape = STYLES.get(data["type"], ("#64748b", "dot"))
        properties = json.dumps(data.get("properties", {}), indent=2, default=str)
        title = f"<b>{data['name']}</b><br>Type: {data['type']}<br>Provider: {data['provider']}<pre>{properties}</pre>"
        size = 28 if data["type"] in {"INTERNET", "VPC", "EKS_CLUSTER", "LOAD_BALANCER"} else 18
        network.add_node(node_id, label=data["name"], title=title, group=data["type"], color=color, shape=shape, size=size)
    for source, target, data in graph.edges(data=True):
        network.add_edge(source, target, label=data["type"], title=data["type"], color="#94a3b8")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    html = network.generate_html(name=str(OUTPUT), notebook=False)
    OUTPUT.write_text(html, encoding="utf-8")
    print(f"Current Twin graph generated: {OUTPUT}")
    print(f"Nodes: {graph.number_of_nodes()} | Relationships: {graph.number_of_edges()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
