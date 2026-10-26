"""Read-only Kubernetes discovery using the active local kubeconfig context."""

from __future__ import annotations

from typing import Any, Callable

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException


def _labels(metadata: Any) -> dict[str, str]:
    return metadata.labels or {}


def _owner(metadata: Any) -> dict[str, str] | None:
    owners = metadata.owner_references or []
    if not owners:
        return None
    controller = next((item for item in owners if item.controller), owners[0])
    return {"kind": controller.kind, "name": controller.name, "uid": controller.uid}


def _selector(selector: Any) -> dict[str, str]:
    return (selector.match_labels if selector else None) or {}


class KubernetesCollector:
    def __init__(self) -> None:
        config.load_kube_config()
        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()
        self.networking = client.NetworkingV1Api()
        self.warnings: list[str] = []

    def _safe(self, label: str, operation: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
        try:
            return operation()
        except ApiException as exc:
            self.warnings.append(f"{label}: Kubernetes API returned {exc.status} {exc.reason}")
            return []

    def collect_namespaces(self) -> list[dict[str, Any]]:
        return self._safe("Namespace discovery", lambda: [{
            "name": x.metadata.name, "uid": x.metadata.uid, "labels": _labels(x.metadata),
            "phase": x.status.phase,
        } for x in self.core.list_namespace().items])

    def collect_nodes(self) -> list[dict[str, Any]]:
        def operation() -> list[dict[str, Any]]:
            result = []
            for x in self.core.list_node().items:
                info = x.status.node_info
                addresses = {a.type: a.address for a in x.status.addresses or []}
                result.append({
                    "name": x.metadata.name, "uid": x.metadata.uid, "labels": _labels(x.metadata),
                    "provider_id": x.spec.provider_id, "pod_cidr": x.spec.pod_cidr,
                    "internal_ip": addresses.get("InternalIP"), "instance_type": _labels(x.metadata).get("node.kubernetes.io/instance-type"),
                    "availability_zone": _labels(x.metadata).get("topology.kubernetes.io/zone"),
                    "kubelet_version": info.kubelet_version if info else None,
                })
            return result
        return self._safe("Node discovery", operation)

    def collect_deployments(self) -> list[dict[str, Any]]:
        return self._safe("Deployment discovery", lambda: [{
            "name": x.metadata.name, "namespace": x.metadata.namespace, "uid": x.metadata.uid,
            "labels": _labels(x.metadata), "selector": _selector(x.spec.selector),
            "replicas": x.spec.replicas, "ready_replicas": x.status.ready_replicas or 0,
            "service_account": x.spec.template.spec.service_account_name or "default",
            "images": [c.image for c in x.spec.template.spec.containers],
        } for x in self.apps.list_deployment_for_all_namespaces().items])

    def collect_pods(self) -> list[dict[str, Any]]:
        return self._safe("Pod discovery", lambda: [{
            "name": x.metadata.name, "namespace": x.metadata.namespace, "uid": x.metadata.uid,
            "labels": _labels(x.metadata), "owner": _owner(x.metadata), "node_name": x.spec.node_name,
            "service_account": x.spec.service_account_name or "default", "phase": x.status.phase,
            "pod_ip": x.status.pod_ip, "images": [c.image for c in x.spec.containers],
        } for x in self.core.list_pod_for_all_namespaces().items])

    def collect_services(self) -> list[dict[str, Any]]:
        def operation() -> list[dict[str, Any]]:
            result = []
            for x in self.core.list_service_for_all_namespaces().items:
                ingress = x.status.load_balancer.ingress or [] if x.status.load_balancer else []
                result.append({
                    "name": x.metadata.name, "namespace": x.metadata.namespace, "uid": x.metadata.uid,
                    "labels": _labels(x.metadata), "annotations": x.metadata.annotations or {},
                    "selector": x.spec.selector or {}, "service_type": x.spec.type,
                    "cluster_ip": x.spec.cluster_ip,
                    "ports": [{"name": p.name, "port": p.port, "target_port": str(p.target_port), "protocol": p.protocol} for p in x.spec.ports or []],
                    "external_endpoints": [i.hostname or i.ip for i in ingress if i.hostname or i.ip],
                })
            return result
        return self._safe("Service discovery", operation)

    def collect_statefulsets(self) -> list[dict[str, Any]]:
        return self._safe("StatefulSet discovery", lambda: [{
            "name": x.metadata.name, "namespace": x.metadata.namespace, "uid": x.metadata.uid,
            "labels": _labels(x.metadata), "selector": _selector(x.spec.selector),
            "replicas": x.spec.replicas, "ready_replicas": x.status.ready_replicas or 0,
            "service_name": x.spec.service_name,
            "service_account": x.spec.template.spec.service_account_name or "default",
            "images": [c.image for c in x.spec.template.spec.containers],
        } for x in self.apps.list_stateful_set_for_all_namespaces().items])

    def collect_service_accounts(self) -> list[dict[str, Any]]:
        return self._safe("ServiceAccount discovery", lambda: [{
            "name": x.metadata.name, "namespace": x.metadata.namespace, "uid": x.metadata.uid,
            "labels": _labels(x.metadata), "annotations": x.metadata.annotations or {},
        } for x in self.core.list_service_account_for_all_namespaces().items])

    def collect_ingresses(self) -> list[dict[str, Any]]:
        def operation() -> list[dict[str, Any]]:
            result = []
            for x in self.networking.list_ingress_for_all_namespaces().items:
                backends = []
                if x.spec.default_backend and x.spec.default_backend.service:
                    backends.append(x.spec.default_backend.service.name)
                for rule in x.spec.rules or []:
                    for path in rule.http.paths if rule.http else []:
                        if path.backend.service:
                            backends.append(path.backend.service.name)
                result.append({"name": x.metadata.name, "namespace": x.metadata.namespace, "uid": x.metadata.uid,
                               "labels": _labels(x.metadata), "class_name": x.spec.ingress_class_name,
                               "hosts": [r.host for r in x.spec.rules or [] if r.host], "backend_services": sorted(set(backends))})
            return result
        return self._safe("Ingress discovery", operation)

    def collect_network_policies(self) -> list[dict[str, Any]]:
        return self._safe("NetworkPolicy discovery", lambda: [{
            "name": x.metadata.name, "namespace": x.metadata.namespace, "uid": x.metadata.uid,
            "labels": _labels(x.metadata), "pod_selector": _selector(x.spec.pod_selector),
            "policy_types": x.spec.policy_types or [],
        } for x in self.networking.list_network_policy_for_all_namespaces().items])

    def collect(self) -> dict[str, Any]:
        return {
            "namespaces": self.collect_namespaces(), "nodes": self.collect_nodes(),
            "deployments": self.collect_deployments(), "pods": self.collect_pods(),
            "services": self.collect_services(), "statefulsets": self.collect_statefulsets(),
            "service_accounts": self.collect_service_accounts(), "ingresses": self.collect_ingresses(),
            "network_policies": self.collect_network_policies(), "warnings": self.warnings,
        }
