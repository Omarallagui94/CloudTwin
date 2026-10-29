"""Ingester for Kubernetes manifest YAML and JSON files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from ingestion.models import (
    ChangeSet,
    InvalidChangeInputError,
    NormalizedChange,
    OperationType,
    UnresolvedTargetError,
)
from ingestion.resolver import CanonicalResourceResolver


class KubernetesManifestIngester:
    """Ingests Kubernetes manifests (YAML or JSON) into normalized change records."""

    def __init__(self, resolver: CanonicalResourceResolver | None = None) -> None:
        self.resolver = resolver or CanonicalResourceResolver()

    def ingest(self, manifest: str | Path | dict[str, Any] | list[dict[str, Any]]) -> ChangeSet:
        """Parse and normalize one or more Kubernetes manifests.

        Args:
            manifest: YAML/JSON string, Path to file, or parsed dictionary / list.

        Returns:
            ChangeSet containing normalized change records.
        """
        documents = self._load_documents(manifest)
        if not documents:
            raise InvalidChangeInputError("Kubernetes manifest is empty or contains no valid documents.")

        changes: list[NormalizedChange] = []
        change_idx = 1

        for doc in documents:
            if not isinstance(doc, dict):
                continue

            kind = doc.get("kind")
            metadata = doc.get("metadata") or {}
            name = metadata.get("name")
            namespace = metadata.get("namespace", "default")
            spec = doc.get("spec") or {}

            if not kind or not name:
                raise InvalidChangeInputError(
                    f"Malformed Kubernetes manifest: 'kind' and 'metadata.name' are required. Got: {doc}"
                )

            # Check for explicit operational annotations (e.g. cloudtwin.dev/action: delete)
            annotations = metadata.get("annotations") or {}
            annotated_op = annotations.get("cloudtwin.dev/action") or annotations.get("cloudtwin.dev/operation")

            canonical_id, canonical_type, provider, resolved_ns = self.resolver.resolve_kubernetes_manifest_target(
                kind=kind,
                name=name,
                namespace=namespace,
                allow_synthesize=True,
            )

            source_meta = {
                "kind": "kubernetes_manifest",
                "reference": f"{kind}/{name}",
                "api_version": doc.get("apiVersion"),
                "name": name,
                "namespace": resolved_ns,
            }

            existing_resource = self.resolver.resources_by_id.get(canonical_id)

            if annotated_op:
                op_val = annotated_op.strip().lower()
                try:
                    operation = OperationType(op_val)
                except ValueError:
                    raise InvalidChangeInputError(f"Unsupported Kubernetes manifest operation: '{op_val}'")

                if operation == OperationType.DELETE:
                    before_val = existing_resource.get("properties") if existing_resource else None
                    changes.append(
                        NormalizedChange(
                            change_id=f"change-k8s-{change_idx:03d}",
                            operation=OperationType.DELETE,
                            resource_id=canonical_id,
                            resource_type=canonical_type,
                            provider=provider,
                            property_path="properties",
                            before=before_val,
                            after=None,
                            source=source_meta,
                            namespace=resolved_ns,
                        )
                    )
                    change_idx += 1
                    continue

                if operation == OperationType.REPLACE:
                    before_val = existing_resource.get("properties") if existing_resource else None
                    changes.append(
                        NormalizedChange(
                            change_id=f"change-k8s-{change_idx:03d}",
                            operation=OperationType.REPLACE,
                            resource_id=canonical_id,
                            resource_type=canonical_type,
                            provider=provider,
                            property_path="properties",
                            before=before_val,
                            after=spec,
                            source=source_meta,
                            namespace=resolved_ns,
                        )
                    )
                    change_idx += 1
                    continue

            # If existing resource in Current Twin, detect property mutations
            if existing_resource:
                existing_props = existing_resource.get("properties", {})
                detected_changes = self._diff_kubernetes_properties(existing_props, doc)

                if detected_changes:
                    for prop_path, b_val, a_val in detected_changes:
                        changes.append(
                            NormalizedChange(
                                change_id=f"change-k8s-{change_idx:03d}",
                                operation=OperationType.UPDATE,
                                resource_id=canonical_id,
                                resource_type=canonical_type,
                                provider=provider,
                                property_path=prop_path,
                                before=b_val,
                                after=a_val,
                                source=source_meta,
                                namespace=resolved_ns,
                            )
                        )
                        change_idx += 1
                else:
                    # Overall update of spec
                    changes.append(
                        NormalizedChange(
                            change_id=f"change-k8s-{change_idx:03d}",
                            operation=OperationType.UPDATE,
                            resource_id=canonical_id,
                            resource_type=canonical_type,
                            provider=provider,
                            property_path="properties.spec",
                            before=existing_props,
                            after=spec,
                            source=source_meta,
                            namespace=resolved_ns,
                        )
                    )
                    change_idx += 1
            else:
                # Newly proposed resource
                changes.append(
                    NormalizedChange(
                        change_id=f"change-k8s-{change_idx:03d}",
                        operation=OperationType.CREATE,
                        resource_id=canonical_id,
                        resource_type=canonical_type,
                        provider=provider,
                        property_path="properties",
                        before=None,
                        after=doc,
                        source=source_meta,
                        namespace=resolved_ns,
                    )
                )
                change_idx += 1

        return ChangeSet(
            schema_version="1.0.0",
            source_kind="kubernetes_manifest",
            changes=changes,
            metadata={"document_count": len(documents)},
        )

    @staticmethod
    def _diff_kubernetes_properties(
        existing_properties: dict[str, Any],
        manifest_doc: dict[str, Any],
    ) -> list[tuple[str, Any, Any]]:
        diffs = []
        spec = manifest_doc.get("spec", {})

        # 1. Replicas check (Deployments, StatefulSets)
        if "replicas" in spec:
            new_replicas = spec["replicas"]
            old_replicas = existing_properties.get("replicas")
            if old_replicas is not None and old_replicas != new_replicas:
                diffs.append(("properties.replicas", old_replicas, new_replicas))

        # 2. Image check in containers
        containers = spec.get("template", {}).get("spec", {}).get("containers", [])
        if containers and "containers" in existing_properties:
            old_containers = existing_properties.get("containers", [])
            for idx, c in enumerate(containers):
                new_image = c.get("image")
                if idx < len(old_containers):
                    old_image = old_containers[idx].get("image")
                    if old_image and new_image and old_image != new_image:
                        diffs.append((f"properties.containers[{idx}].image", old_image, new_image))

        return diffs

    @staticmethod
    def _load_documents(manifest: str | Path | dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
        if isinstance(manifest, list):
            return manifest
        if isinstance(manifest, dict):
            return [manifest]
        if isinstance(manifest, Path):
            content = manifest.read_text(encoding="utf-8")
        elif isinstance(manifest, str):
            try:
                p = Path(manifest)
                if p.is_file():
                    content = p.read_text(encoding="utf-8")
                else:
                    content = manifest
            except OSError:
                content = manifest
        else:
            raise InvalidChangeInputError(f"Unsupported manifest input type: {type(manifest)}")

        # Try YAML (supports multi-doc and JSON)
        try:
            docs = list(yaml.safe_load_all(content))
            return [d for d in docs if isinstance(d, dict)]
        except yaml.YAMLError as exc:
            raise InvalidChangeInputError(f"Failed to parse Kubernetes manifest: {exc}") from exc
