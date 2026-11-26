"""Shared local Kind workflow for CloudTwin replica-scaling scenarios."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CLUSTER = "cloudtwin-validation"


@dataclass(frozen=True)
class ScaleScenario:
    scenario_id: str
    scenario_dir: Path
    baseline_replicas: int
    proposed_replicas: int

    @property
    def baseline(self) -> Path:
        return self.scenario_dir / "baseline-deployment.yaml"

    @property
    def proposed(self) -> Path:
        return self.scenario_dir / "proposed-deployment.yaml"

    @property
    def evidence(self) -> Path:
        return self.scenario_dir / "evidence" / f"{self.scenario_id.lower()}-kind-validation.json"


class ValidationFailure(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def executable(name: str) -> str | None:
    path = shutil.which(name)
    if path:
        return path
    if sys.platform == "win32":
        packages = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
        try:
            matches = sorted(packages.glob(f"*{name}*/*{name}.exe"))
        except OSError:
            matches = []
        if matches:
            return str(matches[0])
    return None


def run(command: list[str], *, timeout: int = 240, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValidationFailure(f"command failed to execute: {command[0]}: {exc}") from exc
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise ValidationFailure(f"command failed: {' '.join(command[:3])}: {detail}")
    return result


def run_live(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    """Run with UTF-8-safe live combined output and a hard timeout."""
    lines: list[str] = []
    try:
        process = subprocess.Popen(
            command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
    except OSError as exc:
        raise ValidationFailure(f"command failed to execute: {command[0]}: {exc}") from exc
    assert process.stdout is not None

    def stream_output() -> None:
        for line in process.stdout:
            lines.append(line)
            console_encoding = sys.stdout.encoding or "utf-8"
            safe_line = line.encode(console_encoding, errors="replace").decode(
                console_encoding, errors="replace"
            )
            sys.stdout.write(safe_line)
            sys.stdout.flush()

    reader = threading.Thread(target=stream_output, name="kind-output", daemon=True)
    reader.start()
    try:
        return_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        reader.join(timeout=5)
        raise ValidationFailure(
            f"command timed out after {timeout} seconds: {' '.join(command[:3])}"
        ) from exc
    finally:
        process.stdout.close()
    reader.join(timeout=5)
    output = "".join(lines)
    result = subprocess.CompletedProcess(command, return_code, stdout=output, stderr="")
    if return_code != 0:
        raise ValidationFailure(
            f"command failed: {' '.join(command[:3])}: {output.strip() or f'exit code {return_code}'}"
        )
    return result


def kind_clusters(kind: str) -> set[str]:
    result = run([kind, "get", "clusters"], timeout=30)
    return {
        line.strip() for line in result.stdout.splitlines()
        if line.strip() and line.strip() != "No kind clusters found."
    }


def deployment_evidence(kubectl: str, context: str) -> dict[str, int]:
    result = run([
        kubectl, "--context", context, "get", "deployment", "ui",
        "--namespace", "default", "--output", "json",
    ], timeout=30)
    deployment = json.loads(result.stdout)
    spec, status = deployment.get("spec", {}), deployment.get("status", {})
    return {
        "desired_replicas": int(spec.get("replicas", 0)),
        "ready_replicas": int(status.get("readyReplicas", 0)),
        "available_replicas": int(status.get("availableReplicas", 0)),
        "updated_replicas": int(status.get("updatedReplicas", 0)),
    }


def validate_prerequisites() -> tuple[str, str, str]:
    paths = {name: executable(name) for name in ("docker", "kind", "kubectl")}
    problems = [f"{name} not found on PATH" for name, path in paths.items() if not path]
    docker, kind, kubectl = paths["docker"], paths["kind"], paths["kubectl"]
    if docker and run([docker, "info", "--format", "{{.ServerVersion}}"], timeout=30, check=False).returncode != 0:
        problems.append("Docker daemon unavailable")
    if kind and run([kind, "version"], timeout=30, check=False).returncode != 0:
        problems.append("Kind is not executable")
    if kubectl and run([kubectl, "version", "--client=true", "--output=json"], timeout=30, check=False).returncode != 0:
        problems.append("kubectl client is not executable")
    if problems:
        raise ValidationFailure("prerequisite check failed: " + "; ".join(problems))
    assert docker and kind and kubectl
    return docker, kind, kubectl


def create_kind_cluster(kind: str, cluster_name: str) -> None:
    run_live(
        [kind, "create", "cluster", "--name", cluster_name, "--wait", "5m"],
        timeout=360,
    )


def delete_kind_cluster(kind: str, cluster_name: str) -> dict[str, Any]:
    result: dict[str, Any] = {"attempted": True, "cluster_absent": False}
    deleted = run([kind, "delete", "cluster", "--name", cluster_name], timeout=120, check=False)
    if deleted.returncode != 0:
        result["error"] = deleted.stderr.strip() or deleted.stdout.strip()
    try:
        result["cluster_absent"] = cluster_name not in kind_clusters(kind)
    except ValidationFailure as exc:
        result["verification_error"] = str(exc)
    return result


def run_scale_validation(config: ScaleScenario, argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=f"Validate CloudTwin {config.scenario_id} on a disposable local Kind cluster"
    )
    parser.add_argument("--cluster-name", default=DEFAULT_CLUSTER)
    parser.add_argument("--evidence", type=Path, default=config.evidence)
    parser.add_argument("--rollout-timeout", default="600s")
    args = parser.parse_args(argv)
    evidence: dict[str, Any] = {
        "scenario_id": config.scenario_id,
        "validation": "kind-local-reproduction",
        "cluster_name": args.cluster_name,
        "started_at": utc_now(),
        "baseline": None,
        "proposed": None,
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

        print(f"[DEPLOY] {config.scenario_id} baseline UI workload")
        run([kubectl, "--context", context, "apply", "--filename", str(config.baseline)], timeout=60)
        run([
            kubectl, "--context", context, "rollout", "status", "deployment/ui",
            "--namespace", "default", f"--timeout={args.rollout_timeout}",
        ], timeout=720)
        evidence["baseline"] = deployment_evidence(kubectl, context)
        if any(evidence["baseline"][key] != config.baseline_replicas for key in ("desired_replicas", "ready_replicas")):
            raise ValidationFailure(f"baseline replica verification failed: {evidence['baseline']}")
        print(f"[PASS] Baseline desired={config.baseline_replicas} ready={config.baseline_replicas}")

        print(f"[APPLY] {config.scenario_id} proposed change")
        run([kubectl, "--context", context, "apply", "--filename", str(config.proposed)], timeout=60)
        run([
            kubectl, "--context", context, "rollout", "status", "deployment/ui",
            "--namespace", "default", f"--timeout={args.rollout_timeout}",
        ], timeout=720)
        evidence["proposed"] = deployment_evidence(kubectl, context)
        if any(evidence["proposed"][key] != config.proposed_replicas for key in ("desired_replicas", "ready_replicas")):
            raise ValidationFailure(f"proposed replica verification failed: {evidence['proposed']}")
        print(f"[PASS] Proposed desired={config.proposed_replicas} ready={config.proposed_replicas}")
        evidence["result"] = "PASS"
    except (ValidationFailure, json.JSONDecodeError) as exc:
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
            evidence["cleanup"]["cluster_absent"] = True
            evidence["cleanup"]["note"] = "No cluster creation was attempted"
        evidence["finished_at"] = utc_now()
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(f"[EVIDENCE] {args.evidence}")

    print(f"[RESULT] {evidence['result']}")
    return 0 if evidence["result"] == "PASS" and evidence["cleanup"]["cluster_absent"] else 1
