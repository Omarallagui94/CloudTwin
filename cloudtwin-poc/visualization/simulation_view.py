"""Vue « Simulate Change » du dashboard : Future Twin, Diff, risque, impact, IA, correctifs.

Toute la chaîne tourne sur le jumeau du scénario (mode fixture), jamais sur AWS :
    changement → ingestion → Future Twin → Diff → analyse → explication IA → correctifs vérifiés
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network

from ai_analyst import explain
from ai_analyst.client import OllamaClient
from ai_analyst.fixes import remediate_with_ai
from simulation.pipeline import simulate
from visualization.build_graph import STYLES

SOURCES = {
    "scenario.json": ("Scénario", "scenario"),
    "proposed-plan.json": ("Plan Terraform (tfplan.json)", "terraform"),
    "proposed-service.yaml": ("Manifest Kubernetes", "kubernetes"),
}
LEVEL_COLORS = {"critical": "#b91c1c", "high": "#ea580c", "medium": "#ca8a04", "low": "#15803d"}
HIGHLIGHT = {"changed": "#dc2626", "direct": "#f97316", "indirect": "#facc15"}
ATTACK_EDGE = "#dc2626"


# ------------------------------------------------------------------ données

def available_sources(scenario_dir: Path) -> list[str]:
    return [name for name in SOURCES if (scenario_dir / name).exists()]


def run_simulation(scenario_dir: Path, source: str) -> dict[str, Any]:
    _, kind = SOURCES[source]
    return simulate(scenario_dir / "current-twin.json", scenario_dir / source, kind=kind)


def attack_edges(findings: list[dict[str, Any]]) -> set[tuple[str, str]]:
    edges = set()
    for f in findings:
        if f["rule_id"] == "RULE-IAM-003" and f["status"] == "fail":
            for path in f["evidence"].get("new_paths", []):
                edges |= set(zip(path["path"], path["path"][1:]))
    return edges


def impact_graph_html(out: dict[str, Any]) -> str:
    """Le Future Twin, avec modifié / direct / indirect en couleur et le chemin d'attaque en rouge."""
    twin, analysis = out["future_twin"], out["analysis"]
    changed = set(out["simulation"]["changed"]) | set(out["simulation"]["created"])
    radius = analysis.get("blast_radius") or {}
    direct, indirect = set(radius.get("direct", [])), set(radius.get("indirect", []))
    attack = attack_edges(analysis["findings"])
    attack_nodes = {n for edge in attack for n in edge}

    net = Network(height="620px", width="100%", directed=True, bgcolor="#ffffff",
                  font_color="#0f172a", cdn_resources="in_line")
    net.set_options('{"physics":{"barnesHut":{"gravitationalConstant":-9000,"springLength":140}},'
                    '"interaction":{"hover":true,"navigationButtons":true},'
                    '"edges":{"arrows":{"to":{"enabled":true,"scaleFactor":0.6}},"smooth":{"type":"dynamic"}}}')
    ids = {r["id"] for r in twin["resources"]} | attack_nodes
    for r in twin["resources"]:
        rid = r["id"]
        base, shape = STYLES.get(r["type"], ("#64748b", "dot"))
        role = ("changed" if rid in changed else "direct" if rid in direct
                else "indirect" if rid in indirect else None)
        color = HIGHLIGHT[role] if role else base
        title = (f"<b>{html.escape(r['name'])}</b><br>{r['type']}"
                 + (f"<br><b>{role}</b>" if role else "")
                 + f"<pre>{html.escape(json.dumps(r.get('properties', {}), indent=2, default=str))}</pre>")
        net.add_node(rid, label=r["name"], title=title, color=color, shape=shape,
                     size=30 if role == "changed" else 22 if role or rid in attack_nodes else 14,
                     borderWidth=4 if rid in attack_nodes else 1)
    for node in attack_nodes - {r["id"] for r in twin["resources"]}:
        net.add_node(node, label=node.rsplit(":", 1)[-1], color="#2563eb", shape="star", size=26)
    for rel in twin["relationships"]:
        if rel["source"] in ids and rel["target"] in ids:
            on_path = (rel["source"], rel["target"]) in attack
            net.add_edge(rel["source"], rel["target"], title=rel["type"],
                         color=ATTACK_EDGE if on_path else "#cbd5e1", width=4 if on_path else 1)
    for source, target in attack:
        if not any(r["source"] == source and r["target"] == target for r in twin["relationships"]):
            net.add_edge(source, target, title="chemin d'attaque", color=ATTACK_EDGE, width=4, dashes=True)
    page = net.generate_html(notebook=False)
    # Le graphe est dessiné dans un onglet d'abord caché (largeur 0) : on le recadre
    # quand l'onglet s'affiche (resize) et à la fin de la mise en place.
    refit = ("<script>(function(){function f(){if(typeof network!=='undefined'){network.redraw();"
             "network.fit({animation:false});}}window.addEventListener('resize',f);"
             "var t=setInterval(function(){if(typeof network!=='undefined'){"
             "network.once('stabilizationIterationsDone',f);f();clearInterval(t);}},200);})();</script>")
    return page.replace("</body>", refit + "</body>")


# ------------------------------------------------------------------ rendu

def render_score(risk: dict[str, Any]) -> None:
    color = LEVEL_COLORS.get(risk["level"], "#334155")
    st.markdown(
        f'<div class="metric-card" style="border-left:8px solid {color}">'
        f'<div class="metric-label">Risk score</div>'
        f'<div class="metric-value" style="color:{color}">{risk["score"]}/100 — {html.escape(risk["label"])}</div>'
        f'<div class="metric-label">{html.escape(risk["explanation"])}</div></div>',
        unsafe_allow_html=True,
    )
    if risk.get("partial"):
        st.caption(f"Score minimum : {risk['not_evaluated']} règle(s) n'ont pas pu conclure (not_evaluated).")


def render_findings(findings: list[dict[str, Any]]) -> None:
    if not findings:
        st.info("Aucune règle ne s'applique à ce changement.")
    for f in findings:
        severity = f["severity"].upper()
        label = f"{f['rule_id']} · {f['title']}"
        if f["status"] == "fail":
            st.error(f"ACTIVE {severity}-SEVERITY FINDING — {label}")
            st.write(f["description"])
        elif f["status"] == "pass":
            st.success(f"PASS — no active {f['category']} finding")
            st.write(f["description"])
            st.caption(f"Potential rule severity if it fails: {severity}")
        else:
            st.warning(f"NOT EVALUATED — {label}")
            st.write(f["description"])
            st.caption(f"Potential rule severity if it fails: {severity}")
        if f["status"] != "pass":
            with st.expander(f"Détails {f['rule_id']}"):
                if f.get("remediation"):
                    st.markdown(f"**Remédiation :** {f['remediation']}")
                if f.get("reference"):
                    st.markdown(f"**Référence :** {f['reference']}")
                if f.get("affected_resource_ids"):
                    st.markdown("**Ressources impactées :**")
                    st.code("\n".join(f["affected_resource_ids"]))
                st.json(f.get("evidence", {}), expanded=False)


def render_diff(out: dict[str, Any]) -> None:
    sim = out["simulation"]
    cols = st.columns(3)
    cols[0].metric("Propriétés modifiées", sum(1 for c in out["diff"]["changes"] if c["change_type"] == "MODIFIED"))
    cols[1].metric("Ressources créées", len(sim["created"]))
    cols[2].metric("Ressources supprimées", len(sim["deleted"]))
    rows = [{"Ressource": c["resource_id"], "Type": c["change_type"], "Propriété": c.get("property", "—"),
             "Avant": json.dumps(c.get("before"), default=str)[:300],
             "Après": json.dumps(c.get("after"), default=str)[:300]}
            for c in out["diff"]["changes"]]
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("Le Future Twin est identique au Current Twin.")
    if out["removed_relationships"]:
        st.markdown("**Relations retirées avec la ressource supprimée**")
        st.dataframe(out["removed_relationships"], use_container_width=True, hide_index=True)
    for warning in sim["warnings"]:
        st.warning(warning)


def render_impact(out: dict[str, Any]) -> None:
    radius = out["analysis"].get("blast_radius") or {}
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


def render_ai(explanation: dict[str, Any]) -> None:
    badge = ("🤖 LLM " + (explanation.get("model") or "")) if explanation["source"] == "llm" \
        else "Texte de secours (sans IA)"
    st.caption(f"Source : {badge} · L'IA explique, elle ne décide pas : le score vient des règles.")
    st.markdown(f"> {explanation['summary']}")
    if explanation["recommendations"]:
        st.markdown("**Recommandations**")
        for rec in explanation["recommendations"]:
            st.markdown(f"- **{rec['rule_id']}** : {rec['action']}")
    for warning in explanation.get("warnings", []):
        st.caption(f"Réponse de l'IA écartée : {warning}")


def render_fixes(report: dict[str, Any], rejected: list[str]) -> None:
    if not report["options"]:
        st.info("Aucun correctif nécessaire : aucune règle n'est en échec.")
        return
    st.caption("Chaque correctif est rejoué dans le jumeau. Il n'est vérifié que si le score baisse "
               "sans créer de nouveau risque.")
    rows = [{"": "✔" if o["verified"] else "✘", "Correctif": o["description"],
             "Origine": "IA" if o["origin"] == "llm" else "Catalogue",
             "Score": f"{o['score_before']} → {o['score_after']}",
             "Gain": o["score_after"] - o["score_before"],
             "Risques résolus": ", ".join(o["resolved"]) or "—",
             "Nouveaux risques": ", ".join(o["new_risks"]) or "—"}
            for o in report["options"]]
    st.dataframe(rows, use_container_width=True, hide_index=True)
    if report.get("all_fixes"):
        allf = report["all_fixes"]
        st.markdown(f"**Tous les correctifs vérifiés ensemble :** {allf['score_before']} → "
                    f"**{allf['score_after']}/100** ({allf['level_after']})")
    for reason in rejected:
        st.caption(f"Proposition de l'IA écartée avant rejeu : {reason}")


def llm_client() -> OllamaClient | None:
    if not st.session_state.get("llm_enabled"):
        return None
    return OllamaClient(model=st.session_state.get("llm_model", "llama3.1"),
                        url=st.session_state.get("llm_url", "http://localhost:11434"))


def render_llm_settings() -> None:
    st.header("Analyste IA")
    st.toggle("Utiliser un LLM local (Ollama)", key="llm_enabled")
    if st.session_state.get("llm_enabled"):
        st.text_input("Modèle", value="llama3.1", key="llm_model")
        st.text_input("Adresse Ollama", value="http://localhost:11434", key="llm_url")
    st.caption("Désactivé : explication et correctifs du catalogue, sans IA.")


def render_simulation(scenario_id: str, scenario_dir: Path) -> None:
    sources = available_sources(scenario_dir)
    source = st.radio("Source du changement", sources, horizontal=True,
                      format_func=lambda s: SOURCES[s][0], key=f"source-{scenario_id}")
    try:
        out = run_simulation(scenario_dir, source)
    except Exception as exc:          # format illisible, ressource absente… : on l'affiche, sans planter
        st.warning(f"La simulation n'a pas pu être construite : {exc}")
        return
    context, result = out.pop("_context"), out.pop("_result")

    render_score(out["analysis"]["risk"])
    tabs = st.tabs(["Risque", "Diff", "Impact", "Analyste IA", "Correctifs"])
    with tabs[0]:
        render_findings(out["analysis"]["findings"])
    with tabs[1]:
        render_diff(out)
    with tabs[2]:
        render_impact(out)

    client = llm_client()
    key = (scenario_id, source, bool(client))
    cache = st.session_state.setdefault("ai_cache", {})
    with tabs[3]:
        if client and key not in cache and not st.button("Demander à l'analyste IA", key=f"ask-{key}"):
            st.info("Clique pour interroger le LLM local (quelques secondes).")
        else:
            if key not in cache:
                with st.spinner("L'analyste IA rédige son explication..."):
                    report, rejected = remediate_with_ai(context, result, client)
                    cache[key] = (explain(result, client=client).model_dump(mode="json"),
                                  report.model_dump(mode="json"), rejected)
            render_ai(cache[key][0])
    with tabs[4]:
        if key in cache:
            render_fixes(cache[key][1], cache[key][2])
        else:
            st.info("Les correctifs apparaissent après la réponse de l'analyste IA.")
