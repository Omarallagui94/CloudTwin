"""Composants d'interface du dashboard CloudTwin (HTML + CSS, rendus par Streamlit).

Thème « Miroir » : fond indigo, accent cyan, couleurs de risque vives. L'élément fort est le
miroir de la page Analysis : le Current Twin à gauche, le score au centre, le Future Twin
simulé à droite, avec les ressources touchées en couleur. Le reste reste sobre : piste du
pipeline, fiches de risque, tableau des scénarios.
"""

from __future__ import annotations

import html
import json
import math
from typing import Any, Iterable

# Couleurs du thème (voir aussi .streamlit/config.toml).
BG, PANEL, PANEL2 = "#0E1430", "#161E42", "#1C2550"
LINE, EDGE = "#2A3566", "#3B4880"
TEXT, SOFT, SLATE = "#E9EDFF", "#C9CFEA", "#9AA3C7"
CYAN = "#5EE6D8"
# Ancien nom gardé pour les modules qui l'importent.
INK, MIST, TWIN = TEXT, BG, CYAN

LEVELS = [  # (clé, libellé, début de zone, couleur) — mêmes seuils que analysis/scoring
    ("low", "Faible", 0, "#46C98A"),
    ("medium", "Moyen", 30, "#F2B544"),
    ("high", "Élevé", 60, "#FF8A4C"),
    ("critical", "Critique", 80, "#FF5470"),
]
SEVERITY_COLORS = {"critical": "#FF5470", "high": "#FF8A4C", "medium": "#F2B544", "low": "#46C98A"}
SEVERITY_LABELS = {"critical": "Critique", "high": "Élevée", "medium": "Moyenne", "low": "Faible"}
SEVERITY_ORDER = ["low", "medium", "high", "critical"]
# Règles qui signifient « exposé à Internet » : le miroir dessine alors le flux entrant.
EXPOSURE_RULES = {"RULE-NET-001", "RULE-NET-002", "RULE-NET-003", "RULE-NET-004"}
# Types trop génériques pour éclairer un changement : jamais montrés dans le miroir.
BACKGROUND_TYPES = {"VPC", "SUBNET", "NAMESPACE", "EKS_CLUSTER", "NODE", "EKS_NODE_GROUP", "EC2_INSTANCE", "POD"}
TYPE_LABELS = {
    "DEPLOYMENT": "Deployment", "SERVICE": "Service", "STATEFULSET": "StatefulSet",
    "SECURITY_GROUP": "Security Group", "LOAD_BALANCER": "Load balancer", "IAM_ROLE": "Rôle IAM",
    "SERVICE_ACCOUNT": "ServiceAccount", "RDS_INSTANCE": "RDS", "INTERNET": "Extérieur",
    "IAM_POLICY": "Politique IAM", "CLUSTER_ROLE_BINDING": "ClusterRoleBinding",
}
MAX_NODES = 8

CSS = f"""
<style>
:root {{ --bg:{BG}; --panel:{PANEL}; --panel2:{PANEL2}; --line:{LINE}; --edge:{EDGE};
        --text:{TEXT}; --soft:{SOFT}; --slate:{SLATE}; --cyan:{CYAN}; }}
[data-testid="stAppViewContainer"] {{ background-color: var(--bg);
  background-image: radial-gradient(rgba(154,163,199,.13) 1px, transparent 1px); background-size: 26px 26px; }}
.block-container {{ max-width: 1360px; padding-top: 2rem; padding-bottom: 4rem; }}
h1, h2, h3 {{ letter-spacing: -0.02em; color: var(--text); font-family: Sora, system-ui, sans-serif; }}
h1 {{ font-weight: 800; }}
code, .rid, .mono {{ font-family: "IBM Plex Mono", ui-monospace, monospace; }}
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stBaseButton-primary"] {{ color: {BG}; font-weight: 600; }}
[data-testid="stBaseButton-primary"] p {{ color: {BG}; }}

/* Barre latérale */
[data-testid="stSidebar"] {{ border-right:1px solid #232C5C; }}
[data-testid="stSidebar"] hr {{ display:none; }}
[data-testid="stSidebarUserContent"] {{ padding-top: 1.2rem; }}
.side-title {{ font-size:.78rem; font-weight:600; color:var(--slate); margin:1.6rem 0 .5rem; letter-spacing:.02em; }}
/* Navigation : des lignes pleines largeur, la page active en cyan.
   Structure Streamlit 1.67 : radiogroup > div > label[stRadioOption][data-selected] > (input, div > (pastille, texte)) */
.st-key-nav, .st-key-nav [data-testid="stRadio"], .st-key-data_source, .st-key-data_source [data-testid="stRadio"] {{ width:100% !important; }}
.st-key-nav [role="radiogroup"] {{ gap:2px; width:100%; }}
.st-key-nav [role="radiogroup"] > div {{ width:100%; }}
.st-key-nav [data-testid="stRadioOption"] {{ width:100%; box-sizing:border-box; margin:0; padding:.55rem .8rem; border-radius:10px;
  border-left:3px solid transparent; transition:background .15s ease; cursor:pointer; }}
.st-key-nav [data-testid="stRadioOption"] > div > div:first-child,
.st-key-data_source [data-testid="stRadioOption"] > div > div:first-child {{ display:none; }}
.st-key-nav [data-testid="stRadioOption"]:hover {{ background:#161E42; }}
.st-key-nav [data-testid="stRadioOption"][data-selected="true"] {{ background:rgba(94,230,216,.10); border-left-color:var(--cyan); }}
.st-key-nav [data-testid="stRadioOption"][data-selected="true"] p {{ color:var(--cyan); font-weight:600; }}
.st-key-nav [data-testid="stRadioOption"] p {{ font-size:.98rem; }}
.st-key-nav [data-testid="stRadioOption"] span[role="img"] {{ font-size:1.25rem; color:var(--slate); margin-right:.55rem; vertical-align:-4px !important; }}
.st-key-nav [data-testid="stRadioOption"][data-selected="true"] span[role="img"] {{ color:var(--cyan); }}
.st-key-nav [data-testid="stRadioOption"]:focus-within,
.st-key-data_source [data-testid="stRadioOption"]:focus-within {{ outline:2px solid var(--cyan); outline-offset:1px; }}
/* Source des données : un interrupteur à deux positions */
.st-key-data_source [role="radiogroup"] {{ flex-direction:row; flex-wrap:nowrap; gap:0; width:100%; background:#161E42;
  border:1px solid var(--line); border-radius:12px; padding:4px; }}
.st-key-data_source [role="radiogroup"] > div {{ flex:1; }}
.st-key-data_source [data-testid="stRadioOption"] {{ width:100%; box-sizing:border-box; justify-content:center; margin:0; padding:.45rem .5rem; border-radius:9px; cursor:pointer; }}
.st-key-data_source [data-testid="stRadioOption"] > div {{ justify-content:center; width:100%; }}
.st-key-data_source [data-testid="stRadioOption"][data-selected="true"] {{ background:var(--cyan); }}
.st-key-data_source [data-testid="stRadioOption"][data-selected="true"] p {{ color:{BG}; font-weight:600; }}
.st-key-data_source [data-testid="stRadioOption"] p {{ white-space:nowrap; }}
.side-status {{ display:flex; gap:.55rem; align-items:flex-start; font-size:.82rem; line-height:1.45; color:var(--soft); margin:.6rem 0 0; }}
.side-status i {{ flex:none; width:8px; height:8px; border-radius:50%; margin-top:.4rem; }}
.side-last {{ display:block; margin:1.1rem 0 0; padding:.8rem .9rem; border-radius:12px; background:#161E42;
  border:1px solid var(--line); border-left:4px solid var(--lvl); }}
.side-last small {{ display:block; font-size:.75rem; color:var(--slate); }}
.side-last b {{ display:block; font-size:.92rem; color:var(--text); margin:.15rem 0 .1rem; }}
.side-last span {{ font-size:.85rem; color:var(--lvl); font-weight:600; }}
.side-foot {{ margin-top:2.2rem; padding-top:1rem; border-top:1px solid #232C5C; font-size:.78rem; color:var(--slate); line-height:1.5; }}
.side-foot b {{ color:var(--soft); font-weight:600; }}
.brand {{ display:flex; gap:.75rem; align-items:center; margin:.2rem 0 1.2rem; }}
.brand-mark {{ width:38px; height:38px; position:relative; flex:none; }}
.brand-mark i {{ position:absolute; width:24px; height:24px; border-radius:7px; }}
.brand-mark i:first-child {{ left:0; top:0; border:2px solid var(--text); }}
.brand-mark i:last-child {{ right:0; bottom:0; background:var(--cyan); opacity:.9; }}
.brand-name {{ font-size:1.25rem; font-weight:700; color:var(--text); line-height:1.1; }}
.brand-sub {{ font-size:.8rem; color:var(--slate); }}

/* Titre de page */
.page-head {{ margin: 0 0 1.6rem; max-width: 62ch; }}
.page-head h1 {{ font-size: 2.5rem; line-height: 1.1; margin: 0 0 .55rem; padding: 0; }}
.page-head p {{ color: var(--slate); font-size: 1.05rem; margin: 0; line-height: 1.55; }}
.change-chip {{ display:inline-block; font-family:"IBM Plex Mono", monospace; font-size:.88rem; color:var(--soft);
               background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:.6rem .9rem;
               max-width:100%; overflow-x:auto; white-space:pre; line-height:1.7; margin:-.6rem 0 1.4rem; }}

/* Cartes d'information */
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:.9rem 1.1rem;
        min-height: 92px; height: 100%; }}
.eyebrow {{ font-size:.82rem; font-weight:500; color:var(--slate); }}
.metric-value {{ font-size:1.45rem; font-weight:700; color:var(--text); margin-top:.25rem; line-height:1.25; }}

/* Le miroir : l'élément fort */
.mirror {{ display:grid; grid-template-columns: minmax(0,1fr) 250px minmax(0,1fr); gap:20px; align-items:stretch; margin:.2rem 0 1.8rem; }}
.twin-panel {{ background:rgba(22,30,66,.85); border:1px solid var(--line); border-radius:20px; padding:20px 20px 16px; min-width:0; }}
.twin-panel.future {{ border-color: var(--alert); box-shadow: 0 0 0 1px var(--alert) inset, 0 18px 50px -30px var(--alert); }}
.twin-head {{ margin-bottom:.9rem; }}
.twin-head b {{ display:block; font-size:1.1rem; color:var(--text); }}
.twin-head span {{ font-size:.82rem; color:var(--slate); }}
.twin-panel svg {{ width:100%; max-width:420px; height:auto; display:block; margin:0 auto; overflow:visible; }}
.twin-legend {{ display:flex; flex-wrap:wrap; gap:.4rem 1rem; font-size:.78rem; color:var(--slate); margin-top:.5rem; }}
.twin-legend i {{ display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:.35rem; vertical-align:-1px; }}
.hinge {{ display:flex; flex-direction:column; align-items:center; justify-content:center; gap:.8rem; text-align:center; }}
.hinge .num {{ font-family:Sora, sans-serif; font-size:4.4rem; font-weight:800; line-height:1; color:var(--lvl); font-variant-numeric:tabular-nums; }}
.hinge .of {{ font-size:.85rem; color:var(--slate); }}
.hinge .lvl {{ font-size:1.3rem; font-weight:700; color:var(--text); }}
.hinge .lvl span {{ color:var(--lvl); }}
.hinge p {{ font-size:.85rem; color:var(--slate); margin:0; max-width:28ch; line-height:1.5; }}
.hinge .sim {{ display:flex; align-items:center; gap:.5rem; color:var(--cyan); font-size:.8rem; }}
.hinge .sim::before, .hinge .sim::after {{ content:""; width:26px; height:2px; background:var(--cyan); }}
.dial {{ position:relative; width:200px; height:200px; }}
.dial svg {{ position:absolute; inset:0; }}
.dial .center {{ position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; }}
@keyframes ct-hot {{ 0%,100% {{ stroke-opacity:1; }} 50% {{ stroke-opacity:.35; }} }}
@keyframes ct-flow {{ to {{ stroke-dashoffset:-24; }} }}
.ct-hot {{ animation: ct-hot 1.6s ease-in-out infinite; }}
.ct-flow {{ stroke-dasharray:6 6; animation: ct-flow 1s linear infinite; }}
@media (prefers-reduced-motion: reduce) {{ .ct-hot, .ct-flow {{ animation:none; }} }}

/* Bande de verdict (pages sans miroir) */
.verdict {{ display:grid; grid-template-columns: auto 1fr; gap: 2rem; align-items:center;
           background:var(--panel); border:1px solid var(--line); border-left:8px solid var(--lvl);
           border-radius:16px; padding:1.4rem 1.8rem; margin: .4rem 0 1.6rem; }}
.verdict-score {{ font-weight:800; color:var(--lvl); line-height:.9; }}
.verdict-score .num {{ font-size:4.6rem; font-variant-numeric: tabular-nums; }}
.verdict-score .of {{ font-size:1.3rem; color:var(--slate); font-weight:500; margin-left:.2rem; }}
.verdict-level {{ font-size:1.5rem; font-weight:700; color:var(--text); }}
.verdict-level span {{ color:var(--lvl); }}
.verdict p {{ color:var(--slate); margin:.25rem 0 .9rem; max-width:70ch; }}
.scale {{ position:relative; display:flex; height:12px; border-radius:6px; overflow:hidden; gap:2px; }}
.scale div {{ height:100%; opacity:.25; }}
.scale div.on {{ opacity:1; }}
.scale-wrap {{ position:relative; padding-top:14px; }}
.scale-marker {{ position:absolute; top:0; width:0; height:0; transform:translateX(-7px);
                border-left:7px solid transparent; border-right:7px solid transparent; border-top:10px solid var(--text); }}
.scale-labels {{ display:flex; font-size:.78rem; color:var(--slate); margin-top:.35rem; }}
.scale-labels span {{ flex: none; }}

/* Piste du pipeline */
.track {{ display:flex; flex-wrap:wrap; align-items:center; gap:.5rem 0; margin:.6rem 0 1.6rem; }}
.stage {{ display:flex; align-items:center; gap:.45rem; font-size:.88rem; color:var(--slate); }}
.stage .dot {{ width:12px; height:12px; border-radius:50%; border:2px solid var(--edge); background:var(--bg); flex:none; }}
.stage.done {{ color:var(--text); font-weight:600; }}
.stage.done .dot {{ background:var(--cyan); border-color:var(--cyan); box-shadow:0 0 0 4px rgba(94,230,216,.15); }}
.link {{ width:28px; height:2px; background:var(--line); margin:0 .45rem; }}
.link.done {{ background:var(--cyan); }}
.track-note {{ font-size:.85rem; color:var(--slate); margin:-1rem 0 1.4rem; }}

/* Fiches de risque */
.finding {{ background:var(--panel); border:1px solid var(--line); border-left:6px solid var(--sev);
           border-radius:14px; padding:1rem 1.2rem; margin:.7rem 0; }}
.finding-top {{ display:flex; justify-content:space-between; gap:1rem; align-items:baseline; flex-wrap:wrap; }}
.finding h4 {{ margin:0; font-size:1.08rem; font-weight:600; color:var(--text); }}
.finding .sev {{ font-size:.82rem; font-weight:600; color:var(--sev); white-space:nowrap; }}
.finding .rid {{ font-size:.8rem; color:var(--slate); display:flex; gap:.8rem; flex-wrap:wrap; margin-top:.15rem; }}
.finding .ref {{ font-family: Sora, sans-serif; }}
.finding p {{ margin:.45rem 0 0; color:var(--soft); line-height:1.55; }}
.finding .fix {{ margin-top:.6rem; padding-top:.6rem; border-top:1px dashed var(--line); }}
.finding .fix b {{ color:var(--cyan); font-weight:600; }}
.quiet-list {{ margin:.2rem 0 0; padding:0; list-style:none; }}
.quiet-list li {{ padding:.45rem 0; border-bottom:1px solid var(--line); color:var(--soft); font-size:.93rem; }}
.quiet-list li .rid {{ color:var(--slate); font-size:.8rem; margin-right:.6rem; }}

/* Tableau des scénarios */
.board {{ width:100%; border-collapse:separate; border-spacing:0; background:var(--panel); border:1px solid var(--line);
         border-radius:16px; overflow:hidden; font-size:.95rem; }}
.board th {{ text-align:left; font-weight:500; color:var(--slate); font-size:.85rem;
            padding:.75rem 1rem; border-bottom:1px solid var(--line); background:var(--panel2); }}
.board td {{ padding:.8rem 1rem; border-bottom:1px solid var(--line); vertical-align:middle; color:var(--text); }}
.board tr:last-child td {{ border-bottom:none; }}
.board .sid {{ font-family:"IBM Plex Mono", monospace; color:var(--slate); font-size:.85rem; }}
.minibar {{ display:flex; align-items:center; gap:.6rem; }}
.minibar .bar {{ width:120px; height:8px; background:var(--line); border-radius:4px; overflow:hidden; }}
.minibar .bar i {{ display:block; height:100%; }}
.minibar b {{ font-variant-numeric: tabular-nums; min-width:3.2rem; }}
.minibar span {{ color:var(--slate); }}
.rules {{ color:var(--soft); font-size:.88rem; }}

/* Widgets Streamlit */
[data-testid="stTabs"] button[role="tab"] p {{ font-size:1rem; font-weight:500; }}
[data-testid="stExpander"] details {{ background:var(--panel); border-color:var(--line); }}
.safe-note {{ background:rgba(94,230,216,.08); border:1px solid rgba(94,230,216,.35); color:var(--cyan); border-radius:12px; padding:.75rem 1rem; }}
@media (max-width: 1100px) {{
  .mirror {{ grid-template-columns: 1fr; }}
}}
@media (max-width: 760px) {{
  .verdict {{ grid-template-columns: 1fr; gap: .8rem; }}
  .verdict-score .num {{ font-size: 3.4rem; }}
  .link {{ width: 14px; }}
  .page-head h1 {{ font-size: 1.9rem; }}
}}
</style>
"""


def _e(value: Any) -> str:
    return html.escape(str(value))


def level_of(score: int) -> tuple[str, str, str]:
    """(clé, libellé, couleur) du niveau d'un score."""
    current = LEVELS[0]
    for level in LEVELS:
        if score >= level[2]:
            current = level
    return current[0], current[1], current[3]


def brand() -> str:
    return ('<div class="brand"><div class="brand-mark"><i></i><i></i></div>'
            '<div><div class="brand-name">CloudTwin</div>'
            '<div class="brand-sub">Simuler avant de déployer</div></div></div>')


def side_title(text: str) -> str:
    return f'<div class="side-title">{_e(text)}</div>'


SIDE_DOTS = {"ok": CYAN, "live": "#F2B544", "ai": "#46C98A", "off": "#5B6490"}


def side_status(text: str, kind: str = "ok") -> str:
    """Une ligne d'état avec une pastille : fixtures, AWS live, IA active ou non."""
    return f'<div class="side-status"><i style="background:{SIDE_DOTS.get(kind, SLATE)}"></i><span>{_e(text)}</span></div>'


def side_last_run(scenario_id: str, name: str, score: int) -> str:
    """Rappel de la dernière simulation ; la couleur suit le niveau du score."""
    _, label, color = level_of(score)
    return (f'<div class="side-last" style="--lvl:{color}"><small>Dernière simulation</small>'
            f'<b>{_e(scenario_id)}, {_e(name)}</b><span>{score}/100, risque {_e(label.lower())}</span></div>')


def side_footer() -> str:
    return ('<div class="side-foot"><b>Lecture seule.</b> Rien n\'est appliqué, déployé ni supprimé : '
            'chaque changement est simulé sur le jumeau.<br>TEK-UP 2026-2027</div>')


def page_head(title: str, subtitle: str) -> str:
    return f'<div class="page-head"><h1>{_e(title)}</h1><p>{_e(subtitle)}</p></div>'


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n > 1 else ''}"


def verdict_sentence(findings: Iterable[dict[str, Any]], risk: dict[str, Any] | None = None) -> str:
    """Une phrase : combien de risques actifs, le plus grave, et les règles non évaluées."""
    findings = list(findings)
    risk = risk or {}
    failed = [f for f in findings if f.get("status") == "fail"]
    unknown = [f for f in findings if f.get("status") == "not_evaluated"]
    passed = len(findings) - len(failed) - len(unknown)
    if failed:
        worst = max(failed, key=lambda f: SEVERITY_ORDER.index(f.get("severity", "low")))
        sentence = (f"{len(failed)} risque{'s' if len(failed) > 1 else ''} actif"
                    f"{'s' if len(failed) > 1 else ''}, le plus grave : {worst['title'].rstrip('.')}. "
                    f"{_plural(passed, 'règle')} sans risque.")
    else:
        sentence = f"Aucun risque actif. {_plural(passed, 'règle')} vérifiée{'s' if passed > 1 else ''} sans risque."
    if unknown:
        sentence += (f" {len(unknown)} règle{'s' if len(unknown) > 1 else ''} non évaluée"
                     f"{'s' if len(unknown) > 1 else ''} : le score est un minimum.")
    bonus = risk.get("blast_radius_bonus", 0)
    if bonus:
        sentence += f" Blast radius : +{bonus}."
    return sentence


def verdict_strip(risk: dict[str, Any], findings: Iterable[dict[str, Any]]) -> str:
    """Score, niveau, phrase de synthèse et échelle 0-100 avec le repère."""
    score = int(risk.get("score", 0))
    _, label, color = level_of(score)
    sentence = verdict_sentence(findings, risk)
    zones, labels = [], []
    bounds = [lvl[2] for lvl in LEVELS] + [101]
    for (key, name, start, col), end in zip(LEVELS, bounds[1:]):
        on = " on" if start <= score < end else ""
        zones.append(f'<div class="{key}{on}" style="width:{end - start}%;background:{col}"></div>')
        labels.append(f'<span style="width:{end - start}%">{_e(name)} {start}</span>')
    marker = min(max(score, 0), 100)
    return (
        f'<div class="verdict" style="--lvl:{color}">'
        f'<div class="verdict-score"><span class="num">{score}</span><span class="of">/100</span></div>'
        f'<div><div class="verdict-level">Risque <span>{_e(label.lower())}</span></div>'
        f'<p>{_e(sentence)}</p>'
        f'<div class="scale-wrap"><div class="scale-marker" style="left:{marker}%"></div>'
        f'<div class="scale">{"".join(zones)}</div></div>'
        f'<div class="scale-labels">{"".join(labels)}</div></div></div>'
    )


# ------------------------------------------------------------------ le miroir

def _short(value: Any, limit: int = 60) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _delta(path: str, before: Any, after: Any) -> list[str]:
    """Ce qui change vraiment entre deux valeurs, chemin par chemin."""
    if isinstance(before, dict) and isinstance(after, dict):
        out: list[str] = []
        for key in sorted(set(before) | set(after)):
            if before.get(key) != after.get(key):
                out += _delta(f"{path}.{key}", before.get(key), after.get(key))
        return out
    if isinstance(before, list) and isinstance(after, list):
        if len(before) == len(after) and all(isinstance(x, dict) for x in before + after):
            return [line for i, (b, a) in enumerate(zip(before, after)) for line in _delta(f"{path}[{i}]", b, a)]
        added = [_short(x, 40) for x in after if x not in before]
        removed = [_short(x, 40) for x in before if x not in after]
        parts = [f"+ {x}" for x in added] + [f"− {x}" for x in removed]
        return [f"{path} : {', '.join(parts)}"]
    return [f"{path} : {_short(before)} → {_short(after)}"]


def change_line(changeset: dict[str, Any], names: dict[str, str] | None = None) -> str:
    """Le changement, une ligne par valeur modifiée : « ressource · propriété : avant → après »."""
    names = names or {}
    parts = []
    for change in changeset.get("changes", []):
        rid = change["resource_id"]
        name = names.get(rid) or rid.rsplit(":", 1)[-1]
        op = change.get("operation", "update")
        if op == "delete":
            parts.append(f"{name} : supprimé")
        elif op == "create":
            parts.append(f"{name} : créé")
        else:
            prop = (change.get("property_path") or "").removeprefix("properties.")
            parts += [f"{name} · {line}" for line in _delta(prop, change.get("before"), change.get("after"))]
    return "\n".join(parts)


def _resources(twin: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in (twin or {}).get("resources", [])}


def _failed(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for f in result.get("findings", []) if f.get("status") == "fail"]


def focal_nodes(current: dict[str, Any], future: dict[str, Any], changeset: dict[str, Any],
                result: dict[str, Any]) -> list[str]:
    """Les ressources qui racontent le changement, dans l'ordre du récit (8 au plus).

    Internet (si un risque d'exposition est actif), la ressource modifiée, ses voisins en
    amont, puis le blast radius direct et indirect, puis les autres voisins.
    """
    known = _resources(current) | _resources(future)
    changed = [c["resource_id"] for c in changeset.get("changes", [])]
    radius = result.get("blast_radius") or {}
    exposed = any(f.get("rule_id") in EXPOSURE_RULES for f in _failed(result))
    upstream, downstream = [], []
    for rel in (current or {}).get("relationships", []) + (future or {}).get("relationships", []):
        for here, there, bucket in ((rel["target"], rel["source"], upstream), (rel["source"], rel["target"], downstream)):
            if here in changed and there not in changed and there in known \
                    and known[there].get("type") not in BACKGROUND_TYPES:
                bucket.append(there)
    order = (["aws:internet"] if exposed else []) + changed + upstream \
        + list(radius.get("direct", [])) + list(radius.get("indirect", [])) + downstream
    nodes: list[str] = []
    for rid in order:
        if rid not in nodes and (rid in known or rid == "aws:internet"):
            nodes.append(rid)
    return nodes[:MAX_NODES]


def _positions(count: int) -> tuple[list[tuple[float, float]], float, float]:
    """Deux colonnes au plus : les panneaux sont étroits, les noeuds restent lisibles."""
    cols = 1 if count <= 1 else 2
    rows = max(math.ceil(count / cols), 1)
    cell = 180
    pts = [(cell * (i % cols + .5), 40 + 112 * (i // cols)) for i in range(count)]
    return pts, cell * cols, 112 * rows - 22


def _node_svg(x: float, y: float, kind: str, name: str, stroke: str, *, width: float = 2,
              dashed: bool = False, hot: bool = False, faded: bool = False, struck: bool = False,
              tag: str = "") -> str:
    w, h = 152, 60
    name = name if len(name) <= 18 else name[:17] + "…"
    cls = ' class="ct-hot"' if hot else ""
    dash = ' stroke-dasharray="6 5"' if dashed else ""
    opacity = ' opacity=".55"' if faded else ""
    deco = ' text-decoration="line-through"' if struck else ""
    tag_svg = (f'<text x="{x:.0f}" y="{y + h / 2 + 17:.0f}" text-anchor="middle" font-size="12" '
               f'font-weight="600" fill="{stroke}">{_e(tag)}</text>') if tag else ""
    return (f'<g{opacity}><rect x="{x - w / 2:.0f}" y="{y - h / 2:.0f}" width="{w}" height="{h}" rx="12" '
            f'fill="{PANEL2}" stroke="{stroke}" stroke-width="{width}"{dash}{cls}/>'
            f'<text x="{x:.0f}" y="{y - 7:.0f}" text-anchor="middle" font-size="12" fill="{SLATE}">{_e(kind)}</text>'
            f'<text x="{x:.0f}" y="{y + 14:.0f}" text-anchor="middle" font-size="14" font-weight="600" '
            f'fill="{TEXT}"{deco}>{_e(name)}</text></g>{tag_svg}')


def _edge_svg(a: tuple[float, float], b: tuple[float, float], color: str, *, width: float = 2,
              dashed: bool = False, flow: bool = False) -> str:
    cls = ' class="ct-flow"' if flow else ""
    dash = ' stroke-dasharray="6 6"' if dashed and not flow else ""
    return (f'<line x1="{a[0]:.0f}" y1="{a[1]:.0f}" x2="{b[0]:.0f}" y2="{b[1]:.0f}" '
            f'stroke="{color}" stroke-width="{width}"{dash}{cls}/>')


def _panel_svg(nodes: list[str], twin: dict[str, Any], known: dict[str, dict[str, Any]],
               roles: dict[str, tuple[str, str]], extra_edges: list[tuple[str, str, str, bool]],
               label: str) -> str:
    pts, width, height = _positions(len(nodes))
    where = dict(zip(nodes, pts))
    present = set(_resources(twin)) | ({"aws:internet"} if "aws:internet" in roles else set())
    edges = []
    seen = set()
    for rel in twin.get("relationships", []):
        pair = (rel["source"], rel["target"])
        if pair[0] in where and pair[1] in where and frozenset(pair) not in seen:
            seen.add(frozenset(pair))
            gone = roles.get(pair[0], ("",))[0] == "deleted" or roles.get(pair[1], ("",))[0] == "deleted"
            edges.append(_edge_svg(where[pair[0]], where[pair[1]], SEVERITY_COLORS["critical"] if gone else EDGE,
                                   dashed=gone))
    for source, target, color, flow in extra_edges:
        if source in where and target in where and frozenset((source, target)) not in seen:
            edges.append(_edge_svg(where[source], where[target], color, width=3, flow=flow))
    boxes = []
    for rid in nodes:
        x, y = where[rid]
        res = known.get(rid, {"type": "INTERNET", "name": "Internet"})
        kind = TYPE_LABELS.get(res.get("type", ""), res.get("type", "").replace("_", " ").title())
        role, color = roles.get(rid, ("", EDGE))
        if rid not in present and role not in ("deleted",):
            role, color = "absent", EDGE
        boxes.append(_node_svg(
            x, y, kind, res.get("name") or rid.rsplit(":", 1)[-1], color,
            width=3 if role in ("changed", "deleted", "created") else 2,
            dashed=role in ("deleted", "indirect", "absent"),
            hot=role == "changed" and color in (SEVERITY_COLORS["critical"], SEVERITY_COLORS["high"]),
            faded=role in ("deleted", "absent"), struck=role == "deleted",
            tag={"deleted": "supprimé", "created": "nouveau", "absent": "absent"}.get(role, ""),
        ))
    return (f'<svg viewBox="-6 -8 {width + 12:.0f} {height + 34:.0f}" role="img" aria-label="{_e(label)}" '
            f'font-family="Sora, system-ui, sans-serif">{"".join(edges)}{"".join(boxes)}</svg>')


def mirror(current: dict[str, Any], future: dict[str, Any], changeset: dict[str, Any],
           result: dict[str, Any]) -> str:
    """Le héros de la page Analysis : Current Twin | score | Future Twin simulé."""
    risk = result.get("risk") or {}
    score = int(risk.get("score", 0))
    _, label, color = level_of(score)
    failed = _failed(result)
    worst = max((f.get("severity", "low") for f in failed), key=SEVERITY_ORDER.index, default=None)
    known = _resources(current) | _resources(future)
    nodes = focal_nodes(current, future, changeset, result)
    radius = result.get("blast_radius") or {}
    ops = {c["resource_id"]: c.get("operation", "update") for c in changeset.get("changes", [])}
    exposed = "aws:internet" in nodes

    now_roles: dict[str, tuple[str, str]] = {rid: ("changed", CYAN) for rid in ops}
    if exposed:
        now_roles["aws:internet"] = ("", EDGE)
    future_roles: dict[str, tuple[str, str]] = {}
    for rid in radius.get("indirect", []):
        future_roles[rid] = ("indirect", SEVERITY_COLORS["medium"])
    for rid in radius.get("direct", []):
        future_roles[rid] = ("direct", SEVERITY_COLORS["medium"])
    for rid, op in ops.items():
        if op == "delete":
            future_roles[rid] = ("deleted", SEVERITY_COLORS["critical"])
        elif op == "create":
            future_roles[rid] = ("created", SEVERITY_COLORS["low"])
        else:
            future_roles[rid] = ("changed", SEVERITY_COLORS[worst] if worst else CYAN)
    extra: list[tuple[str, str, str, bool]] = []
    if exposed:
        future_roles["aws:internet"] = ("", SEVERITY_COLORS["critical"])
        for rid, op in ops.items():
            if op != "delete":
                extra.append(("aws:internet", rid, SEVERITY_COLORS[worst or "high"], True))

    names = [known[r]["name"] for r in nodes if r in known]
    now_svg = _panel_svg(nodes, current, known, now_roles, [],
                         "Current Twin : " + ", ".join(names))
    future_svg = _panel_svg(nodes, future, known, future_roles, extra,
                            f"Future Twin simulé, risque {label.lower()} {score} sur 100")
    circumference = 2 * math.pi * 86
    arc = circumference * min(max(score, 0), 100) / 100
    legend = (f'<div class="twin-legend"><span><i style="background:{SEVERITY_COLORS[worst] if worst else CYAN}"></i>modifié</span>'
              f'<span><i style="background:{SEVERITY_COLORS["medium"]}"></i>impact direct ou indirect</span>'
              f'<span><i style="background:{SEVERITY_COLORS["critical"]}"></i>supprimé ou exposé</span></div>')
    return (
        f'<div class="mirror" style="--lvl:{color};--alert:{SEVERITY_COLORS[worst] if worst else CYAN}">'
        f'<section class="twin-panel"><div class="twin-head"><b>Aujourd\'hui</b><span>Current Twin, tel que découvert</span></div>'
        f'{now_svg}</section>'
        f'<div class="hinge"><div class="dial">'
        f'<svg viewBox="0 0 200 200" aria-hidden="true"><circle cx="100" cy="100" r="86" fill="none" stroke="#232C5C" stroke-width="14"/>'
        f'<circle cx="100" cy="100" r="86" fill="none" stroke="{color}" stroke-width="14" stroke-linecap="round" '
        f'stroke-dasharray="{arc:.1f} {circumference:.1f}" transform="rotate(-90 100 100)"/></svg>'
        f'<div class="center"><span class="num">{score}</span><span class="of">sur 100</span></div></div>'
        f'<div class="lvl">Risque <span>{_e(label.lower())}</span></div>'
        f'<p>{_e(verdict_sentence(result.get("findings", []), risk))}</p>'
        f'<div class="sim">simulé, rien de déployé</div></div>'
        f'<section class="twin-panel future"><div class="twin-head"><b>Après le changement</b><span>Future Twin simulé</span></div>'
        f'{future_svg}{legend}</section></div>'
    )


# ------------------------------------------------------------------ autres composants

def pipeline_track(stages: list[tuple[str, bool]], note: str | None = None) -> str:
    """Étapes reliées ; les étapes faites sont pleines, la liaison aussi."""
    parts = []
    for index, (name, done) in enumerate(stages):
        if index:
            parts.append(f'<div class="link{" done" if done else ""}"></div>')
        state = "terminée" if done else "en attente"
        parts.append(f'<div class="stage{" done" if done else ""}" title="{_e(name)} : {state}">'
                     f'<span class="dot"></span>{_e(name)}</div>')
    out = f'<div class="track">{"".join(parts)}</div>'
    if note:
        out += f'<div class="track-note">{_e(note)}</div>'
    return out


def finding_card(finding: dict[str, Any]) -> str:
    sev = finding.get("severity", "low")
    color = SEVERITY_COLORS.get(sev, SLATE)
    fix = (f'<p class="fix"><b>Correction :</b> {_e(finding["remediation"])}</p>'
           if finding.get("remediation") else "")
    ref = f'<span class="ref">{_e(finding["reference"])}</span>' if finding.get("reference") else ""
    return (
        f'<div class="finding" style="--sev:{color}">'
        f'<div class="finding-top"><h4>{_e(finding["title"])}</h4>'
        f'<span class="sev">Gravité {_e(SEVERITY_LABELS.get(sev, sev).lower())}</span></div>'
        f'<div class="rid"><span>{_e(finding["rule_id"])}</span>{ref}</div>'
        f'<p>{_e(finding["description"])}</p>{fix}</div>'
    )


def quiet_list(findings: Iterable[dict[str, Any]]) -> str:
    items = "".join(f'<li><span class="rid">{_e(f["rule_id"])}</span>{_e(f["description"])}</li>'
                    for f in findings)
    return f'<ul class="quiet-list">{items}</ul>'


def scenario_board(rows: list[dict[str, Any]]) -> str:
    """Une ligne par scénario : identifiant, nom, score sur une mini-barre, règles en échec."""
    body = []
    for row in rows:
        if row.get("error"):
            body.append(f'<tr><td class="sid">{_e(row["id"])}</td><td>{_e(row["name"])}</td>'
                        f'<td colspan="2" class="rules">Aperçu indisponible : {_e(row["error"])}</td></tr>')
            continue
        score = row["score"]
        _, label, color = level_of(score)
        rules = ", ".join(row["failed"]) or "aucune"
        body.append(
            f'<tr><td class="sid">{_e(row["id"])}</td><td>{_e(row["name"])}</td>'
            f'<td><div class="minibar"><div class="bar"><i style="width:{max(score, 2)}%;background:{color}"></i></div>'
            f'<b style="color:{color}">{score}/100</b><span>{_e(label)}</span></div></td>'
            f'<td class="rules">{_e(rules)}</td></tr>')
    return ('<table class="board"><thead><tr><th>Scénario</th><th>Changement proposé</th>'
            '<th>Risque simulé</th><th>Règles en échec</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')
