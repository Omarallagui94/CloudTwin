"""Composants d'interface du dashboard CloudTwin (HTML + CSS, rendus par Streamlit).

Un seul élément fort : la bande de verdict (score sur l'échelle 0-100 et ses quatre
niveaux). Le reste est volontairement sobre : la piste du pipeline, les fiches de
risque et le tableau des scénarios.
"""

from __future__ import annotations

import html
from typing import Any, Iterable

# Couleurs du thème (voir aussi .streamlit/config.toml).
INK, SLATE, MIST, TWIN = "#1B2433", "#5B6B82", "#EEF2F6", "#0E7C86"
LEVELS = [  # (clé, libellé, début de zone, couleur) — mêmes seuils que analysis/scoring
    ("low", "Faible", 0, "#2F855A"),
    ("medium", "Moyen", 30, "#B7791F"),
    ("high", "Élevé", 60, "#DD6B20"),
    ("critical", "Critique", 80, "#C53030"),
]
SEVERITY_COLORS = {"critical": "#C53030", "high": "#DD6B20", "medium": "#B7791F", "low": "#2F855A"}
SEVERITY_LABELS = {"critical": "Critique", "high": "Élevée", "medium": "Moyenne", "low": "Faible"}

CSS = f"""
<style>
:root {{ --ink:{INK}; --slate:{SLATE}; --mist:{MIST}; --twin:{TWIN}; --line:#D5DDE7; }}
.block-container {{ max-width: 1280px; padding-top: 2.2rem; padding-bottom: 4rem; }}
h1, h2, h3 {{ letter-spacing: -0.01em; color: var(--ink); }}
h1 {{ font-weight: 700; }}
code, .rid {{ font-family: "IBM Plex Mono", ui-monospace, monospace; }}
[data-testid="stHeader"] {{ background: transparent; }}

/* Barre latérale */
[data-testid="stSidebar"] h2 {{ font-size: 1.05rem; font-weight: 600; margin-top: .4rem; }}
.brand {{ display:flex; gap:.7rem; align-items:center; margin:.2rem 0 1.2rem; }}
.brand-mark {{ width:34px; height:34px; position:relative; flex:none; }}
.brand-mark i {{ position:absolute; width:22px; height:22px; border-radius:6px; }}
.brand-mark i:first-child {{ left:0; top:0; border:2px solid #DCE3EE; }}
.brand-mark i:last-child {{ right:0; bottom:0; background:#5FC4CC; opacity:.9; }}
.brand-name {{ font-size:1.2rem; font-weight:700; color:#FFFFFF; line-height:1.1; }}
.brand-sub {{ font-size:.8rem; color:#9AA8BD; }}

/* Titre de page */
.page-head {{ margin: 0 0 1.6rem; max-width: 60ch; }}
.page-head h1 {{ font-size: 2.1rem; line-height: 1.15; margin: 0 0 .5rem; padding: 0; }}
.page-head p {{ color: var(--slate); font-size: 1.05rem; margin: 0; line-height: 1.5; }}

/* Cartes d'information */
.card {{ background:#fff; border:1px solid var(--line); border-radius:10px; padding:.9rem 1.1rem;
        min-height: 92px; height: 100%; }}
.eyebrow {{ font-size:.82rem; font-weight:500; color:var(--slate); }}
.metric-value {{ font-size:1.45rem; font-weight:600; color:var(--ink); margin-top:.25rem; line-height:1.25; }}

/* Bande de verdict : l'élément fort */
.verdict {{ display:grid; grid-template-columns: auto 1fr; gap: 2rem; align-items:center;
           background:#fff; border:1px solid var(--line); border-left:8px solid var(--lvl);
           border-radius:12px; padding:1.4rem 1.8rem; margin: .4rem 0 1.6rem; }}
.verdict-score {{ font-weight:700; color:var(--lvl); line-height:.9; }}
.verdict-score .num {{ font-size:4.6rem; font-variant-numeric: tabular-nums; }}
.verdict-score .of {{ font-size:1.3rem; color:var(--slate); font-weight:500; margin-left:.2rem; }}
.verdict-level {{ font-size:1.5rem; font-weight:700; color:var(--ink); }}
.verdict-level span {{ color:var(--lvl); }}
.verdict p {{ color:var(--slate); margin:.25rem 0 .9rem; max-width:70ch; }}
.scale {{ position:relative; display:flex; height:12px; border-radius:6px; overflow:hidden; }}
.scale div {{ height:100%; opacity:.22; }}
.scale div.on {{ opacity:1; }}
.scale-wrap {{ position:relative; padding-top:14px; }}
.scale-marker {{ position:absolute; top:0; width:0; height:0; transform:translateX(-7px);
                border-left:7px solid transparent; border-right:7px solid transparent; border-top:10px solid var(--ink); }}
.scale-labels {{ display:flex; font-size:.78rem; color:var(--slate); margin-top:.35rem; }}
.scale-labels span {{ flex: none; }}

/* Piste du pipeline */
.track {{ display:flex; flex-wrap:wrap; align-items:center; gap:.35rem 0; margin:.6rem 0 1.6rem; }}
.stage {{ display:flex; align-items:center; gap:.45rem; font-size:.9rem; color:var(--slate); }}
.stage .dot {{ width:12px; height:12px; border-radius:50%; border:2px solid #A9B6C8; background:#fff; flex:none; }}
.stage.done {{ color:var(--ink); font-weight:600; }}
.stage.done .dot {{ background:var(--twin); border-color:var(--twin); }}
.link {{ width:28px; height:2px; background:#C5CFDC; margin:0 .45rem; }}
.link.done {{ background:var(--twin); }}
.track-note {{ font-size:.85rem; color:var(--slate); margin:-1rem 0 1.4rem; }}

/* Fiches de risque */
.finding {{ background:#fff; border:1px solid var(--line); border-left:6px solid var(--sev);
           border-radius:10px; padding:1rem 1.2rem; margin:.7rem 0; }}
.finding-top {{ display:flex; justify-content:space-between; gap:1rem; align-items:baseline; flex-wrap:wrap; }}
.finding h4 {{ margin:0; font-size:1.08rem; font-weight:600; color:var(--ink); }}
.finding .sev {{ font-size:.82rem; font-weight:600; color:var(--sev); white-space:nowrap; }}
.finding .rid {{ font-size:.8rem; color:var(--slate); display:flex; gap:.8rem; flex-wrap:wrap; margin-top:.15rem; }}
.finding .ref {{ font-family:"IBM Plex Sans", sans-serif; }}
.finding p {{ margin:.45rem 0 0; color:#33415A; line-height:1.5; }}
.finding .fix {{ margin-top:.6rem; padding-top:.6rem; border-top:1px dashed var(--line); }}
.finding .fix b {{ color:var(--twin); font-weight:600; }}
.quiet-list {{ margin:.2rem 0 0; padding:0; list-style:none; }}
.quiet-list li {{ padding:.45rem 0; border-bottom:1px solid #E6EBF1; color:#33415A; font-size:.93rem; }}
.quiet-list li .rid {{ color:var(--slate); font-size:.8rem; margin-right:.6rem; }}

/* Tableau des scénarios */
.board {{ width:100%; border-collapse:collapse; background:#fff; border:1px solid var(--line);
         border-radius:10px; overflow:hidden; font-size:.95rem; }}
.board th {{ text-align:left; font-weight:500; color:var(--slate); font-size:.85rem;
            padding:.65rem 1rem; border-bottom:1px solid var(--line); background:#F7F9FB; }}
.board td {{ padding:.7rem 1rem; border-bottom:1px solid #E6EBF1; vertical-align:middle; }}
.board tr:last-child td {{ border-bottom:none; }}
.board .sid {{ font-family:"IBM Plex Mono", monospace; color:var(--slate); font-size:.85rem; }}
.minibar {{ display:flex; align-items:center; gap:.6rem; }}
.minibar .bar {{ width:120px; height:8px; background:#E6EBF1; border-radius:4px; overflow:hidden; }}
.minibar .bar i {{ display:block; height:100%; }}
.minibar b {{ font-variant-numeric: tabular-nums; min-width:3.2rem; }}
.rules {{ color:#33415A; font-size:.88rem; }}

/* Widgets Streamlit */
[data-testid="stTabs"] button[role="tab"] p {{ font-size:1rem; font-weight:500; }}
[data-testid="stExpander"] details {{ background:#fff; }}
.safe-note {{ background:#E6F4F5; border:1px solid #B5DEE1; color:#0B5960; border-radius:10px; padding:.75rem 1rem; }}
@media (max-width: 760px) {{
  .verdict {{ grid-template-columns: 1fr; gap: .8rem; }}
  .verdict-score .num {{ font-size: 3.4rem; }}
  .link {{ width: 14px; }}
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
            '<div class="brand-sub">Jumeau numérique AWS et Kubernetes</div></div></div>')


def page_head(title: str, subtitle: str) -> str:
    return f'<div class="page-head"><h1>{_e(title)}</h1><p>{_e(subtitle)}</p></div>'


def verdict_strip(risk: dict[str, Any], findings: Iterable[dict[str, Any]]) -> str:
    """Score, niveau, phrase de synthèse et échelle 0-100 avec le repère."""
    findings = list(findings)
    score = int(risk.get("score", 0))
    _, label, color = level_of(score)
    failed = [f for f in findings if f.get("status") == "fail"]
    unknown = [f for f in findings if f.get("status") == "not_evaluated"]
    passed = len(findings) - len(failed) - len(unknown)
    if failed:
        worst = max(failed, key=lambda f: ["low", "medium", "high", "critical"].index(f["severity"]))
        sentence = (f"{len(failed)} risque{'s' if len(failed) > 1 else ''} actif"
                    f"{'s' if len(failed) > 1 else ''}, le plus grave : {worst['title'].rstrip('.')}. "
                    f"{passed} règle{'s' if passed > 1 else ''} sans risque.")
    else:
        sentence = f"Aucun risque actif. {passed} règle{'s' if passed > 1 else ''} vérifiée{'s' if passed > 1 else ''} sans risque."
    if unknown:
        sentence += (f" {len(unknown)} règle{'s' if len(unknown) > 1 else ''} non évaluée"
                     f"{'s' if len(unknown) > 1 else ''} : le score est un minimum.")
    bonus = risk.get("blast_radius_bonus", 0)
    if bonus:
        sentence += f" Blast radius : +{bonus}."

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
