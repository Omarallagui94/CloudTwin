"""Modèles de données du moteur d'analyse CloudTwin.

Ce fichier définit le contrat d'entrée (ce que Hamdi fournit) et de sortie
(ce que le moteur produit). Tout est validé par Pydantic : une entrée mal
formée est rejetée avec une erreur claire au lieu de produire un faux résultat.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0.0"


# --------------------------------------------------------------------------
# Énumérations partagées (à garder identiques chez Hamdi et dans le frontend)
# --------------------------------------------------------------------------

class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Status(str, Enum):
    FAIL = "fail"                    # le risque est présent
    PASS = "pass"                    # la règle a été évaluée, pas de risque
    NOT_EVALUATED = "not_evaluated"  # données manquantes : on ne conclut PAS


class Category(str, Enum):
    SECURITY = "security"
    RESILIENCE = "resilience"


Operation = Literal["create", "update", "delete", "replace"]


# --------------------------------------------------------------------------
# Entrée : le jumeau numérique (format existant de Hamdi)
# --------------------------------------------------------------------------

class Resource(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str                      # ex. "k8s:deployment:default:ui"
    name: str
    type: str                    # ex. "DEPLOYMENT"
    provider: str                # "aws" ou "kubernetes"
    properties: dict[str, Any] = Field(default_factory=dict)


class Relationship(BaseModel):
    source: str
    target: str
    type: str                    # ex. "ROUTES_TO"


class Twin(BaseModel):
    """Format réel de outputs/current_state.json (normalizer.py).

    `metadata` et `summary` sont produits par le normalizer actuel.
    `snapshot_id` n'existe pas encore : il sera ajouté par Hamdi (tâche H1).
    """

    model_config = ConfigDict(extra="allow")

    snapshot_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    resources: list[Resource] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)

    def get(self, resource_id: str) -> Resource | None:
        for r in self.resources:
            if r.id == resource_id:
                return r
        return None


# --------------------------------------------------------------------------
# Entrée : les changements et ressources impactées (contrat Hamdi -> Fatma)
# --------------------------------------------------------------------------

class ChangeSource(BaseModel):
    kind: str                    # "scenario", "terraform_plan", "k8s_manifest"
    reference: str | None = None


class Change(BaseModel):
    change_id: str
    operation: Operation
    resource_id: str
    resource_type: str
    provider: str
    property_path: str | None = None   # None pour create/delete d'une ressource entière
    before: Any = None
    after: Any = None
    source: ChangeSource | None = None


class AffectedResource(BaseModel):
    change_id: str
    source: str
    target: str
    relationship: str
    direction: Literal["incoming", "outgoing"]
    distance: int
    impact: Literal["direct", "indirect"]
    path: list[str]
    reason: str | None = None


class AnalysisContext(BaseModel):
    """Tout ce qu'une règle a le droit de lire. Rien d'autre (pas de Neo4j)."""

    schema_version: str
    simulation_id: str
    current_twin: Twin
    future_twin: Twin | None = None
    changes: list[Change] = Field(default_factory=list)
    affected_resources: list[AffectedResource] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Sortie : les findings
# --------------------------------------------------------------------------

class Finding(BaseModel):
    rule_id: str
    rule_version: str
    status: Status
    category: Category
    severity: Severity
    title: str
    description: str
    change_ids: list[str] = Field(default_factory=list)
    affected_resource_ids: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    remediation: str | None = None
    reference: str | None = None   # ex. "CIS EKS 4.1.1" (vérifié à la source)


class AnalysisResult(BaseModel):
    schema_version: str = SCHEMA_VERSION
    simulation_id: str
    findings: list[Finding]
    summary: dict[str, int]
