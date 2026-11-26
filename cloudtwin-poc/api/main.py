"""API HTTP de CloudTwin (tâche H8) : la simulation et l'analyse, exposées pour une interface ou la CI.

    uvicorn api.main:app --reload            (depuis cloudtwin-poc/)
    Documentation interactive : http://localhost:8000/docs
    Schéma OpenAPI            : http://localhost:8000/openapi.json

L'API n'ajoute aucune logique : elle appelle `simulation.pipeline.simulate`, la même
chaîne que le dashboard et la ligne de commande. Elle ne parle jamais à AWS ni à
Kubernetes : tout se passe sur des copies du jumeau envoyé ou d'un scénario.

Erreurs : une entrée illisible (plan invalide, ressource introuvable, format inconnu)
renvoie 400 avec la raison ; un scénario inconnu renvoie 404.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from analysis.rules import ALL_RULES
from ingestion import IngestionError
from simulation.future_twin import SimulationError
from simulation.pipeline import simulate

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "scenarios"

# Fichier « natif » de chaque scénario, par ordre de préférence, et son format.
NATIVE_SOURCES = {
    "proposed-plan.json": "terraform",
    "proposed-deployment.yaml": "kubernetes",
    "proposed-service.yaml": "kubernetes",
    "proposed-delete.json": "kubernetes",
}

app = FastAPI(
    title="CloudTwin API",
    version="1.0.0",
    description=("Simule un changement AWS ou Kubernetes sur un jumeau numérique et renvoie son "
                 "analyse de risque : findings, blast radius, score, explication et correctifs vérifiés. "
                 "Aucun appel à AWS ni à Kubernetes."),
)


class SimulationRequest(BaseModel):
    """Un Current Twin et un changement proposé, envoyés tels quels."""

    current_twin: dict[str, Any] = Field(description="Current Twin au format schema/v1/twin.schema.json")
    change: dict[str, Any] | list[Any] | str = Field(
        description="Plan Terraform (JSON de `terraform show -json`), manifest Kubernetes (objet ou YAML en texte) "
                    "ou scénario CloudTwin")
    kind: Literal["terraform", "kubernetes", "scenario"] | None = Field(
        default=None, description="Format du changement ; détecté automatiquement s'il est omis")
    explain: bool = Field(default=False, description="Ajouter l'explication de l'analyste IA")
    fixes: bool = Field(default=False, description="Ajouter les correctifs rejoués et vérifiés dans le jumeau")


def _run(twin: Any, change: Any, kind: str | None, explain: bool, fixes: bool) -> dict[str, Any]:
    try:
        out = simulate(twin, change, kind=kind)
    except (IngestionError, ValueError, KeyError, TypeError, SimulationError) as exc:
        raise HTTPException(status_code=400, detail=f"{type(exc).__name__} : {exc}") from exc
    context, result = out.pop("_context"), out.pop("_result")
    if explain:
        from ai_analyst import explain as explain_result
        out["ai_explanation"] = explain_result(result).model_dump(mode="json")
    if fixes:
        from analysis.remediation import remediate
        out["fixes"] = remediate(context, result).model_dump(mode="json")
    return out


def _scenario_dir(scenario_id: str) -> Path:
    matches = sorted(SCENARIOS.glob(f"{scenario_id.upper()}-*/"))
    if len(matches) != 1 or not (matches[0] / "current-twin.json").exists():
        raise HTTPException(status_code=404, detail=f"Scénario inconnu : {scenario_id}")
    return matches[0]


@app.get("/health", tags=["Système"], summary="Vérifier que l'API répond")
def health() -> dict[str, Any]:
    return {"status": "ok", "rules": len(ALL_RULES)}


@app.get("/rules", tags=["Règles"], summary="Le catalogue des règles d'analyse")
def rules() -> list[dict[str, Any]]:
    return [{"rule_id": r.rule_id, "title": r.title, "category": r.category.value,
             "severity": r.severity.value, "reference": r.reference} for r in ALL_RULES]


@app.get("/scenarios", tags=["Scénarios"], summary="Les scénarios de démonstration disponibles")
def scenarios() -> list[dict[str, Any]]:
    out = []
    for folder in sorted(p for p in SCENARIOS.glob("S*-*/") if (p / "scenario.json").exists()):
        meta = json.loads((folder / "scenario.json").read_text(encoding="utf-8"))
        native = next((name for name in NATIVE_SOURCES if (folder / name).exists()), None)
        out.append({"id": folder.name.split("-")[0], "folder": folder.name,
                    "name": meta.get("name"), "description": meta.get("description"),
                    "native_source": native})
    return out


@app.post("/simulations", tags=["Simulations"], summary="Simuler un changement sur un jumeau envoyé")
def simulate_change(request: SimulationRequest) -> dict[str, Any]:
    return _run(request.current_twin, request.change, request.kind, request.explain, request.fixes)


@app.post("/simulations/scenario/{scenario_id}", tags=["Simulations"],
          summary="Simuler un scénario de démonstration (S01 à S05)")
def simulate_scenario(
    scenario_id: str,
    source: Literal["native", "scenario"] = Query(
        default="native", description="native : plan Terraform ou manifest du scénario ; scenario : scenario.json"),
    explain: bool = Query(default=False, description="Ajouter l'explication de l'analyste IA"),
    fixes: bool = Query(default=False, description="Ajouter les correctifs vérifiés"),
) -> dict[str, Any]:
    folder = _scenario_dir(scenario_id)
    twin = folder / "current-twin.json"
    if source == "native":
        native = next((name for name in NATIVE_SOURCES if (folder / name).exists()), None)
        if native:
            return _run(twin, folder / native, NATIVE_SOURCES[native], explain, fixes)
    return _run(twin, folder / "scenario.json", "scenario", explain, fixes)
