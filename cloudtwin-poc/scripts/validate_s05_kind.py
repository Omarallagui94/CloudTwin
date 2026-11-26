"""Run S05 dependency removal against a disposable local Kind cluster."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from kind_scale_validation import (
    DEFAULT_CLUSTER,
    ValidationFailure,
    create_kind_cluster,
    delete_kind_cluster,
    kind_clusters,
    run,
    utc_now,
    validate_prerequisites,
)


ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = ROOT / "cloudtwin-poc" / "scenarios" / "S05-remove-critical-dependency"
BASELINE = SCENARIO_DIR / "baseline-resources.yaml"
DELETE_INTENT = SCENARIO_DIR / "proposed-delete.json"
DEFAULT_EVIDENCE = SCENARIO_DIR / "evidence" / "s05-kind-validation.json"


def service_exists(kubectl: str, context: str, namespace: str, name: str) -> bool:
    result = run([
        kubectl, "--context", context, "get", "service", name,
        "--namespace", namespace, "--output", "name",
    ], timeout=30, check=False)
    return result.returncode == 0


def orders_reference(kubectl: str, context: str) -> str:
    result = run([
        kubectl, "--context", context, "exec", "deployment/orders",
        "--namespace", "default", "--", "printenv", "ORDERS_DB_HOST",
    ], timeout=30)
    return result.stdout.strip()


def dependency_reachable(kubectl: str, context: str) -> bool:
    result = run([
        kubectl, "--context", context, "exec", "deployment/orders",
        "--namespace", "default", "--", "sh", "-c",
        "nc -z -w 5 orders-postgresql 5432",
    ], timeout=30, check=False)
    return result.returncode == 0


def write_evidence(path: Path, evidence: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate CloudTwin S05 on a disposable Kind cluster")
    parser.add_argument("--cluster-name", default=DEFAULT_CLUSTER)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--rollout-timeout", default="600s")
    args = parser.parse_args()

    evidence: dict[str, Any] = {
        "scenario_id": "S05",
        "validation": "kind-local-dependency-removal",
        "cluster_name": args.cluster_name,
        "started_at": utc_now(),
        "before": None,
        "after": None,
        "cleanup": {"attempted": False, "cluster_absent": None},
        "result": "FAIL",
    }
    kind: str | None = None
    cluster_preexisting = False
    create_attempted = False
    context = f"kind-{args.cluster_name}"

    try:
        print("[CHECK] Docker, Kind, and kubectl")
        _, kind, kubectl = validate_prerequisites()
        print("[PASS] Prerequisites available")
        cluster_preexisting = args.cluster_name in kind_clusters(kind)
        if cluster_preexisting:
            raise ValidationFailure(
                f"Kind cluster {args.cluster_name!r} already exists; refusing to modify or delete it"
            )

        print(f"[CREATE] Kind cluster {args.cluster_name}")
        create_attempted = True
        create_kind_cluster(kind, args.cluster_name)
        print("[PASS] Kind cluster created")

        print("[DEPLOY] S05 Orders and PostgreSQL baseline")
        run([kubectl, "--context", context, "apply", "--filename", str(BASELINE)], timeout=60)
        for resource in ("statefulset/orders-postgresql", "deployment/orders"):
            run([
                kubectl, "--context", context, "rollout", "status", resource,
                "--namespace", "default", f"--timeout={args.rollout_timeout}",
            ], timeout=720)

        reference = orders_reference(kubectl, context)
        before = {
            "service_exists": service_exists(kubectl, context, "default", "orders-postgresql"),
            "orders_reference": reference,
            "dependency_reachable": dependency_reachable(kubectl, context),
        }
        evidence["before"] = before
        if before != {
            "service_exists": True,
            "orders_reference": "orders-postgresql",
            "dependency_reachable": True,
        }:
            raise ValidationFailure(f"baseline dependency verification failed: {before}")
        print("[PASS] Before: Service present, Orders reference configured, TCP/5432 reachable")

        intent = json.loads(DELETE_INTENT.read_text(encoding="utf-8"))
        target = intent.get("target", {})
        metadata = target.get("metadata", {})
        if intent.get("operation") != "delete" or target.get("kind") != "Service":
            raise ValidationFailure("S05 proposed-delete.json is not a Service delete intent")
        name, namespace = metadata.get("name"), metadata.get("namespace")
        if (name, namespace) != ("orders-postgresql", "default"):
            raise ValidationFailure("S05 delete target does not match the scenario resource")

        print("[APPLY] S05 existing delete intent")
        run([
            kubectl, "--context", context, "delete", "service", name,
            "--namespace", namespace, "--wait=true",
        ], timeout=60)

        reachable_after = True
        for _ in range(15):
            if not dependency_reachable(kubectl, context):
                reachable_after = False
                break
            time.sleep(2)
        after = {
            "service_exists": service_exists(kubectl, context, namespace, name),
            "dependency_reachable": reachable_after,
            "statefulset_exists": run([
                kubectl, "--context", context, "get", "statefulset", "orders-postgresql",
                "--namespace", "default", "--output", "name",
            ], timeout=30, check=False).returncode == 0,
        }
        evidence["after"] = after
        if after != {
            "service_exists": False,
            "dependency_reachable": False,
            "statefulset_exists": True,
        }:
            raise ValidationFailure(f"post-change dependency verification failed: {after}")
        print("[PASS] After: Service absent, dependency unreachable, StatefulSet preserved")
        evidence["result"] = "PASS"
    except (ValidationFailure, json.JSONDecodeError, OSError) as exc:
        evidence["failure"] = str(exc)
        print(f"[FAIL] {exc}", file=sys.stderr)
    finally:
        if kind and create_attempted and not cluster_preexisting:
            print(f"[DELETE] Kind cluster {args.cluster_name}")
            evidence["cleanup"] = delete_kind_cluster(kind, args.cluster_name)
            if evidence["cleanup"]["cluster_absent"]:
                print("[PASS] Cleanup verified: cluster absent")
            else:
                print("[FAIL] Cleanup verification failed", file=sys.stderr)
                evidence["result"] = "FAIL"
        elif not create_attempted:
            evidence["cleanup"] = {
                "attempted": False,
                "cluster_absent": True,
                "note": "No cluster creation was attempted",
            }
        evidence["finished_at"] = utc_now()
        write_evidence(args.evidence, evidence)
        print(f"[EVIDENCE] {args.evidence}")

    print(f"[RESULT] {evidence['result']}")
    return 0 if evidence["result"] == "PASS" and evidence["cleanup"]["cluster_absent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
