"""Build a sanitized, self-contained HTML report from existing simulation data."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


SENSITIVE_KEY = re.compile(r"(secret|password|token|credential|access.?key|private.?key)", re.I)
SENSITIVE_TEXT = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED AWS ACCESS KEY]"),
    (re.compile(r"arn:aws(?:-[a-z]+)?:[^\s<\"']+", re.I), "[REDACTED ARN]"),
    (re.compile(r"(?<!\d)\d{12}(?!\d)"), "[REDACTED ACCOUNT ID]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED PRIVATE KEY]"),
]


@dataclass(frozen=True)
class SimulationReport:
    filename: str
    html: str
    generated_at: str


def _sanitize(value: Any, key: str = "") -> Any:
    if SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): _sanitize(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        result = value
        for pattern, replacement in SENSITIVE_TEXT:
            result = pattern.sub(replacement, result)
        return result
    return value


def _resource(twin: dict[str, Any], resource_id: str) -> dict[str, Any] | None:
    return next((item for item in twin.get("resources", []) if item.get("id") == resource_id), None)


def _title(value: str) -> str:
    acronyms = {"ui": "UI", "aws": "AWS", "eks": "EKS", "postgresql": "PostgreSQL"}
    return " ".join(acronyms.get(part.lower(), part.capitalize()) for part in value.replace("_", "-").split("-") if part)


def _name(resource: dict[str, Any] | None, resource_id: str) -> str:
    if not resource:
        return _title(resource_id.split(":")[-1])
    name = _title(str(resource.get("name") or resource_id.split(":")[-1]))
    kind = resource.get("type", "").replace("_", " ").title()
    if kind in {"Service", "Deployment", "Stateful Set"} and not name.lower().endswith(kind.lower()):
        return f"{name} {kind}"
    return name


def _counts(diff: dict[str, Any]) -> dict[str, int]:
    changes = diff.get("changes", [])
    return {
        "Added resources": sum(item.get("change_type") == "ADDED" for item in changes),
        "Removed resources": sum(item.get("change_type") == "REMOVED" for item in changes),
        "Modified properties": sum(item.get("change_type") == "MODIFIED" for item in changes),
        "Added relationships": len(diff.get("added_relationships", [])),
        "Removed relationships": len(diff.get("removed_relationships", [])),
    }


def _executive_summary(change: dict[str, Any], name: str, counts: dict[str, int]) -> str:
    if change["operation"] == "delete":
        return (
            f"CloudTwin simulated removal of {name}. The simulated Future Twin contains "
            f"{counts['Removed resources']} fewer resource and {counts['Removed relationships']} fewer relationships. "
            "The real infrastructure was not modified."
        )
    prop = _title((change.get("property_path") or "property").split(".")[-1])
    return (
        f"CloudTwin simulated changing {name} {prop.lower()} from {change.get('before')} to {change.get('after')}. "
        "The Future Twin reflects the proposed change while the real Kubernetes and AWS environments remain unchanged."
    )


def _analysis_html(analysis: dict[str, Any] | None) -> str:
    findings = (analysis or {}).get("findings", [])
    if not findings:
        return "<p class='muted'>Not evaluated yet.</p>"
    rows = []
    for finding in findings:
        status = str(finding.get("status", "not_evaluated")).upper()
        severity = str(finding.get("severity", "")).upper()
        severity_text = f" · Severity: {html.escape(severity)}" if status == "FAIL" else ""
        rows.append(
            f"<div class='finding'><b>{html.escape(status)}</b>{severity_text}<br>"
            f"{html.escape(str(finding.get('title', 'Resilience finding')))}<br>"
            f"<span class='muted'>{html.escape(str(finding.get('description', '')))}</span></div>"
        )
    return "".join(rows)


def build_simulation_report(
    *,
    scenario: dict[str, Any],
    changeset: dict[str, Any],
    current_twin: dict[str, Any],
    future_twin: dict[str, Any],
    state_diff: dict[str, Any],
    analysis_result: dict[str, Any] | None,
    affected_resources: dict[str, Any] | None = None,
    explanation: dict[str, Any] | None = None,
    datasource_mode: str,
    source: str,
    aws_region: str | None = None,
    eks_cluster: str | None = None,
    generated_at: datetime | None = None,
) -> SimulationReport:
    """Return report bytes as text without mutating or enriching simulation inputs."""
    timestamp = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated_text = timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
    stamp = timestamp.strftime("%Y%m%d-%H%M%S")
    scenario_id = str(scenario.get("scenario_id") or "custom")
    change = changeset["changes"][0]
    target_id = str(change["resource_id"])
    current_resource = _resource(current_twin, target_id)
    future_resource = _resource(future_twin, target_id)
    target_name = _name(current_resource or future_resource, target_id)
    counts = _counts(state_diff)
    deleted = change["operation"] == "delete"
    current_value = "PRESENT" if deleted else change.get("before")
    future_value = "REMOVED" if deleted else change.get("after")
    property_name = _title((change.get("property_path") or "resource").split(".")[-1])
    summary = _executive_summary(change, target_name, counts)

    metadata = [
        ("Generated", generated_text), ("Change ID", scenario_id), ("Source", source),
        ("Mode", datasource_mode),
    ]
    if datasource_mode == "Live AWS Mode":
        metadata.extend((("AWS region", aws_region or "Not supplied"), ("EKS cluster", eks_cluster or "Not supplied")))
    metadata_html = "".join(f"<div><span>{html.escape(k)}</span><b>{html.escape(str(v))}</b></div>" for k, v in metadata)
    count_html = "".join(
        f"<div class='count {'active' if value else ''}'><span>{html.escape(label)}</span><b>{value}</b></div>"
        for label, value in counts.items()
    )
    relationships = []
    for relation in state_diff.get("removed_relationships", []):
        source_name = _name(_resource(current_twin, relation["source"]), relation["source"])
        target_name_rel = _name(_resource(current_twin, relation["target"]), relation["target"])
        verb = relation["type"].replace("_", " ").lower()
        relationships.append(
            f"<div class='relation'><small>REMOVED RELATIONSHIP</small><br><b>{html.escape(source_name)}</b> "
            f"→ {html.escape(verb)} → <b>{html.escape(target_name_rel)}</b></div>"
        )
    affected_rows = []
    for item in (affected_resources or {}).get("affected", []):
        if item.get("distance", 0) == 0:
            continue
        path = " → ".join(item.get("path", []))
        affected_rows.append(
            f"<div class='finding'><b>{html.escape(str(item.get('resource_name') or item.get('target')))}</b> "
            f"· {html.escape(str(item.get('impact', '')).title())} · distance {item.get('distance')}<br>"
            f"<span class='muted'>{html.escape(str(item.get('reason', '')))}</span><br>"
            f"<code>{html.escape(path)}</code></div>"
        )
    analysis = analysis_result or {}
    risk = analysis.get("risk") or {}
    radius = analysis.get("blast_radius") or {}
    remediation_rows = [
        f"<li><b>{html.escape(str(finding.get('rule_id')))}</b>: {html.escape(str(finding['remediation']))}</li>"
        for finding in analysis.get("findings", []) if finding.get("remediation") and finding.get("status") == "fail"
    ]
    analysis_complete = bool(analysis_result)
    affected_complete = affected_resources is not None
    explanation_complete = bool(explanation)
    pipeline = [
        ("Current Twin", "COMPLETE"), ("Change Parsed", "COMPLETE"), ("ChangeSet", "COMPLETE"),
        ("Future Twin", "COMPLETE"), ("State Diff", "COMPLETE"),
        ("Affected Resources", "COMPLETE" if affected_complete else "NOT AVAILABLE"),
        ("Security Analysis", "COMPLETE" if analysis_complete else "NOT AVAILABLE"),
        ("Resilience Analysis", "COMPLETE" if analysis_complete else "NOT AVAILABLE"),
        ("Blast Radius", "COMPLETE" if analysis_complete else "NOT AVAILABLE"),
        ("Risk Score", "COMPLETE" if analysis_complete else "NOT AVAILABLE"),
        ("AI Explanation", "COMPLETE" if explanation_complete else "NOT AVAILABLE"),
    ]
    pipeline_html = "".join(f"<tr><td>{name}</td><td>{status}</td></tr>" for name, status in pipeline)
    technical = _sanitize({
        "normalized_changeset": changeset,
        "state_diff": state_diff,
        "canonical_target_resource_id": target_id,
        "current_resource": current_resource,
        "future_resource": future_resource,
        "affected_resources": affected_resources,
        "analysis": analysis_result,
        "explanation": explanation,
    })
    technical_json = html.escape(json.dumps(technical, indent=2, ensure_ascii=False, default=str))
    live_note = (
        "<li>The Current Twin was based on discovered live infrastructure, but the proposed Future Twin remained virtual.</li>"
        if datasource_mode == "Live AWS Mode" else ""
    )
    document = f"""<!doctype html><html><head><meta charset='utf-8'><title>CloudTwin Simulation Report {html.escape(scenario_id)}</title>
<style>
body{{margin:0;background:#f5f7fb;color:#172033;font:14px/1.55 Inter,Segoe UI,Arial,sans-serif}}main{{max-width:980px;margin:28px auto;background:white;padding:38px 44px;border:1px solid #dfe5ee;border-radius:18px}}h1{{margin:.15rem 0;color:#0f172a}}h2{{margin-top:2rem;border-bottom:1px solid #e2e8f0;padding-bottom:.35rem}}.brand{{color:#0e7490;font-weight:750}}.muted,small{{color:#64748b}}.meta,.counts,.compare{{display:grid;gap:12px}}.meta{{grid-template-columns:repeat(4,1fr);margin:20px 0}}.meta div,.count,.panel,.finding,.relation{{border:1px solid #e2e8f0;border-radius:11px;padding:12px;background:#fbfdff}}.meta span,.count span{{display:block;color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.06em}}.counts{{grid-template-columns:repeat(5,1fr)}}.count b{{font-size:24px;color:#94a3b8}}.count.active{{border-color:#94a3b8}}.count.active b{{color:#0f172a}}.compare{{grid-template-columns:1fr 42px 1fr;align-items:stretch}}.panel b.value{{display:block;font-size:30px;margin:8px 0}}.future{{border-top:4px solid {'#b91c1c' if deleted else '#d97706'}}}.arrow{{font-size:28px;text-align:center;padding-top:55px;color:#64748b}}.warning{{background:#ecfeff;border:1px solid #a5f3fc;border-radius:10px;padding:12px;color:#155e75}}.relation{{border-left:4px solid #b91c1c;margin:8px 0}}table{{width:100%;border-collapse:collapse}}td{{padding:7px;border-bottom:1px solid #e2e8f0}}td:last-child{{font-weight:700;text-align:right}}details{{margin-top:14px}}pre{{white-space:pre-wrap;word-break:break-word;background:#0f172a;color:#e2e8f0;padding:16px;border-radius:10px;font-size:11px}}@media print{{body{{background:white}}main{{margin:0;border:0}}}}
</style></head><body><main>
<div class='brand'>CloudTwin AI · AWS &amp; Kubernetes Digital Twin</div><h1>Simulation Report</h1><div class='meta'>{metadata_html}</div>
<div class='warning'><b>SIMULATION ONLY</b> — real AWS/EKS infrastructure was not modified.</div>
<h2>Executive Summary</h2><p>{html.escape(summary)}</p>
<h2>Proposed Change</h2><table><tr><td>Change</td><td>{html.escape(str(scenario.get('name', 'Custom change')))}</td></tr><tr><td>Target</td><td>{html.escape(target_name)}</td></tr><tr><td>Resource type</td><td>{html.escape(str(change.get('resource_type', 'Unknown')).replace('_', ' ').title())}</td></tr><tr><td>Operation</td><td>{html.escape(str(change['operation']).upper())}</td></tr><tr><td>Property</td><td>{html.escape(property_name)}</td></tr><tr><td>Current</td><td>{html.escape(str(current_value))}</td></tr><tr><td>Proposed</td><td>{html.escape(str(future_value))}</td></tr></table><p class='muted'>Canonical ID: {html.escape(target_id)}</p>
<h2>Current Twin vs Simulated Future</h2><div class='compare'><div class='panel'><small>CURRENT TWIN</small><h3>{html.escape(target_name)}</h3><b class='value'>{html.escape(str(current_value))}</b><span>{html.escape(property_name)}</span></div><div class='arrow'>→</div><div class='panel future'><small>SIMULATED FUTURE</small><h3>{html.escape(target_name)}</h3><b class='value'>{html.escape(str(future_value))}</b><span>{html.escape(property_name)}</span></div></div>
<h2>State Diff</h2><div class='counts'>{count_html}</div>{''.join(relationships)}
<h2>Affected Resources</h2>{''.join(affected_rows) or "<p class='muted'>No dependent resource affected.</p>"}
<h2>Security &amp; Resilience Findings</h2>{_analysis_html(_sanitize(analysis_result))}
<h2>Blast Radius</h2><p>Direct: {len(radius.get('direct', []))} · Indirect: {len(radius.get('indirect', []))}</p>
<h2>Risk Score</h2><p><b>{risk.get('score', 0)} / 100 — {html.escape(str(risk.get('label', 'Not evaluated')))}</b></p><p>{html.escape(str(risk.get('explanation', 'Not evaluated.')))}</p>
<h2>Remediation</h2><ul>{''.join(remediation_rows) or '<li>No active remediation required.</li>'}</ul>
<h2>Explanation</h2><p><b>Source: {html.escape(str((explanation or {}).get('source', 'deterministic fallback')))}</b></p><p>{html.escape(str((explanation or {}).get('summary', risk.get('explanation', 'Not available.'))))}</p>
<h2>CloudTwin Pipeline</h2><table>{pipeline_html}</table>
<h2>Safety &amp; Execution</h2><ul><li>No Terraform apply was executed by CloudTwin.</li><li>No kubectl mutation was executed by CloudTwin.</li><li>No AWS resource was changed by this simulation.</li><li>The Current Twin remains unchanged.</li><li>The Future Twin exists only as a simulated state.</li>{live_note}</ul>
<details><summary><b>Technical Details</b></summary><pre>{technical_json}</pre></details>
</main></body></html>"""
    # Sanitize the final text as defense-in-depth for metadata and finding text.
    safe_document = _sanitize(document)
    return SimulationReport(
        filename=f"cloudtwin-simulation-{scenario_id}-{stamp}.html",
        html=safe_document,
        generated_at=generated_text,
    )
