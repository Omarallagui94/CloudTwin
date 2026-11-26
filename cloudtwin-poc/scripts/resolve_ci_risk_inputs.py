"""Resolve whether a pull request needs the production CloudTwin risk gate.

S01-S05 are deliberately excluded from this contract. Kubernetes pull requests
can be analysed from their changed manifests. Terraform pull requests require a
machine-generated plan JSON because configuration text is not a state change.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


DEFAULT_CHANGE = Path("cloudtwin-ci/proposed-change.json")
_KUBERNETES_PREFIXES = ("cloudtwin-poc/security/", "kubernetes/", "k8s/")
_SENSITIVE_PATTERNS = (
    re.compile(r"\b\d{12}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:10|127)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b"),
)


def is_terraform_change(path: str) -> bool:
    return path.startswith("terraform/") and path.endswith((".tf", ".tf.json"))


def is_kubernetes_manifest(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized.endswith((".yaml", ".yml")) and (
        normalized.startswith(_KUBERNETES_PREFIXES) or "/templates/" in normalized
    )


def classify_changes(paths: list[str]) -> tuple[list[str], list[str]]:
    return (
        [path for path in paths if is_terraform_change(path)],
        [path for path in paths if is_kubernetes_manifest(path)],
    )


def _validate_sanitized_twin(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"sanitized Current Twin is missing: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"Current Twin is not valid JSON: {path}: {error}") from error
    if not isinstance(document.get("resources"), list) or not isinstance(
        document.get("relationships"), list
    ):
        raise ValueError(f"Current Twin lacks resources/relationships arrays: {path}")
    for pattern in _SENSITIVE_PATTERNS:
        if pattern.search(text):
            raise ValueError(f"Current Twin is not sanitized ({pattern.pattern}): {path}")


def _validate_terraform_plan(path: Path) -> None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Terraform proposed change is not readable plan JSON: {path}: {error}") from error
    if not isinstance(document.get("resource_changes"), list):
        raise ValueError(
            f"Terraform proposed change lacks resource_changes; supply `terraform show -json` output: {path}"
        )


def _write_kubernetes_bundle(paths: list[str], destination: Path) -> None:
    missing = [path for path in paths if not Path(path).is_file()]
    if missing:
        raise ValueError("changed Kubernetes manifests are missing from checkout: " + ", ".join(missing))
    destination.parent.mkdir(parents=True, exist_ok=True)
    documents = [Path(path).read_text(encoding="utf-8").strip() for path in paths]
    destination.write_text("\n---\n".join(documents) + "\n", encoding="utf-8")


def resolve(
    changed_paths: list[str],
    current_twin: Path,
    proposed_change: Path | None,
    change_kind: str | None,
    generated_kubernetes_change: Path,
    deployment_risk: bool = False,
) -> dict[str, str]:
    terraform, kubernetes = classify_changes(changed_paths)
    if not deployment_risk:
        return {
            "enabled": "false",
            "required": "false",
            "reason": "code-only/source validation mode; deployment-risk analysis was not requested",
            "twin": str(current_twin),
            "change": "",
            "kind": "",
        }

    _validate_sanitized_twin(current_twin)
    if terraform:
        plan = proposed_change or DEFAULT_CHANGE
        if not plan.is_file():
            raise ValueError(
                "Terraform infrastructure changed but no real Terraform plan JSON was supplied. "
                f"Missing: {plan}. Generate it from this PR revision, sanitize it, and provide it as a CI input."
            )
        if change_kind not in (None, "terraform"):
            raise ValueError("Terraform changes require change_kind=terraform")
        _validate_terraform_plan(plan)
        return {
            "enabled": "true", "required": "true",
            "reason": "Terraform plan and sanitized Current Twin are available",
            "twin": str(current_twin), "change": str(plan), "kind": "terraform",
        }

    if proposed_change is not None:
        if not proposed_change.is_file():
            raise ValueError(f"proposed Kubernetes change is missing: {proposed_change}")
        if change_kind not in (None, "kubernetes"):
            raise ValueError("Kubernetes changes require change_kind=kubernetes")
        change = proposed_change
    else:
        _write_kubernetes_bundle(kubernetes, generated_kubernetes_change)
        change = generated_kubernetes_change
    return {
        "enabled": "true", "required": "true",
        "reason": "actual changed Kubernetes manifests and sanitized Current Twin are available",
        "twin": str(current_twin), "change": str(change), "kind": "kubernetes",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--changed-files", required=True, type=Path)
    parser.add_argument("--current-twin", required=True, type=Path)
    parser.add_argument("--proposed-change", type=Path)
    parser.add_argument("--change-kind", choices=("terraform", "kubernetes"))
    parser.add_argument("--generated-kubernetes-change", type=Path,
                        default=Path("cloudtwin-ci-artifacts/pr-kubernetes-change.yaml"))
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--deployment-risk", action="store_true")
    args = parser.parse_args()
    paths = [line.strip().replace("\\", "/") for line in args.changed_files.read_text(
        encoding="utf-8").splitlines() if line.strip()]
    try:
        result = resolve(paths, args.current_twin, args.proposed_change, args.change_kind,
                         args.generated_kubernetes_change, args.deployment_risk)
    except ValueError as error:
        parser.error(str(error))
    output = "\n".join(f"{key}={value}" for key, value in result.items()) + "\n"
    if args.github_output:
        args.github_output.parent.mkdir(parents=True, exist_ok=True)
        with args.github_output.open("a", encoding="utf-8") as stream:
            stream.write(output)
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
