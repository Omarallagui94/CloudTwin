"""Unified entry point for proposed infrastructure change ingestion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ingestion.kubernetes_ingester import KubernetesManifestIngester
from ingestion.models import (
    ChangeSet,
    ConflictError,
    IngestionError,
    InvalidChangeInputError,
    NormalizedChange,
    OperationType,
    UnresolvedTargetError,
)
from ingestion.resolver import CanonicalResourceResolver
from ingestion.scenario_ingester import ScenarioIngester
from ingestion.terraform_ingester import TerraformPlanIngester


class ChangeIngester:
    """Unified proposed-change ingester for Scenarios, Terraform plans, and Kubernetes manifests."""

    def __init__(self, current_twin: dict[str, Any] | str | Path | None = None) -> None:
        twin_data = self._load_twin(current_twin) if current_twin else None
        self.resolver = CanonicalResourceResolver(twin_data)
        self.scenario_ingester = ScenarioIngester(self.resolver)
        self.terraform_ingester = TerraformPlanIngester(self.resolver)
        self.kubernetes_ingester = KubernetesManifestIngester(self.resolver)

    def set_current_twin(self, current_twin: dict[str, Any] | str | Path) -> None:
        """Update or index a new Current Twin snapshot for target resolution."""
        twin_data = self._load_twin(current_twin)
        self.resolver.index_current_twin(twin_data)

    def ingest_scenario(
        self,
        scenario: dict[str, Any] | str | Path,
        strict_conflict_check: bool = False,
    ) -> ChangeSet:
        """Ingest a scenario JSON document."""
        return self.scenario_ingester.ingest(
            scenario,
            strict_conflict_check=strict_conflict_check,
        )

    def ingest_terraform_plan(self, plan: dict[str, Any] | str | Path) -> ChangeSet:
        """Ingest a machine-generated Terraform plan JSON document."""
        return self.terraform_ingester.ingest(plan)

    def ingest_kubernetes_manifest(
        self,
        manifest: str | Path | dict[str, Any] | list[dict[str, Any]],
    ) -> ChangeSet:
        """Ingest a Kubernetes manifest (YAML or JSON)."""
        return self.kubernetes_ingester.ingest(manifest)

    def ingest(
        self,
        source: Any,
        kind: str | None = None,
        **kwargs: Any,
    ) -> ChangeSet:
        """Auto-detect source format and ingest into normalized change records."""
        if kind:
            k = kind.lower()
            if k in ("scenario", "scenarios"):
                return self.ingest_scenario(source, **kwargs)
            if k in ("terraform", "tf", "terraform_plan", "plan"):
                return self.ingest_terraform_plan(source)
            if k in ("k8s", "kubernetes", "manifest", "manifests", "kubernetes_manifest"):
                return self.ingest_kubernetes_manifest(source)
            raise ValueError(f"Unknown ingestion kind: '{kind}'")

        # Auto-detect from data / structure
        detected_kind = self._detect_kind(source)
        return self.ingest(source, kind=detected_kind, **kwargs)

    @classmethod
    def _detect_kind(cls, source: Any) -> str:
        # If source is dict
        if isinstance(source, dict):
            if "scenario_id" in source or "resource_type" in source:
                return "scenario"
            if "resource_changes" in source or "format_version" in source:
                return "terraform_plan"
            if "apiVersion" in source and "kind" in source:
                return "kubernetes_manifest"

        # If source is Path or str
        text = ""
        if isinstance(source, Path):
            text = source.read_text(encoding="utf-8")
        elif isinstance(source, str):
            try:
                p = Path(source)
                if p.is_file():
                    text = p.read_text(encoding="utf-8")
                else:
                    text = source
            except OSError:
                text = source

        text_stripped = text.strip()
        if text_stripped.startswith("{"):
            try:
                data = json.loads(text_stripped)
                if isinstance(data, dict):
                    if "scenario_id" in data:
                        return "scenario"
                    if "resource_changes" in data:
                        return "terraform_plan"
                    if "apiVersion" in data:
                        return "kubernetes_manifest"
            except json.JSONDecodeError:
                pass

        if "apiVersion:" in text and "kind:" in text:
            return "kubernetes_manifest"

        raise InvalidChangeInputError(
            "Could not auto-detect format of change input. Specify kind explicitly ('scenario', 'terraform_plan', or 'kubernetes_manifest')."
        )

    @staticmethod
    def _load_twin(twin: dict[str, Any] | str | Path) -> dict[str, Any]:
        if isinstance(twin, dict):
            return twin
        if isinstance(twin, Path):
            return json.loads(twin.read_text(encoding="utf-8"))
        if isinstance(twin, str):
            try:
                p = Path(twin)
                if p.is_file():
                    return json.loads(p.read_text(encoding="utf-8"))
            except OSError:
                pass
            return json.loads(twin)
        raise TypeError(f"Unsupported twin type: {type(twin)}")
