"""Local-only validation harness for Omar's infrastructure scenario pack.

This intentionally performs basic structural checks. It does not define CloudTwin
schemas, build a Future Twin, calculate a diff, or make risk decisions.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only in an incomplete environment
    yaml = None


ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "cloudtwin-poc" / "scenarios"
TERRAFORM = ROOT / "terraform"
EXPECTED_IDS = {f"S0{number}" for number in range(1, 6)}
REQUIRED_FILES = {"scenario.json", "expected.json", "current-twin.json", "README.md"}
REQUIRED_SCENARIO_FIELDS = {
    "scenario_id", "name", "description", "target_resource", "resource_type",
    "operation", "property", "before", "after",
}
REQUIRED_EXPECTED_FIELDS = {
    "scenario_id", "expected_changed_resource", "expected_property_change",
    "structurally_involved_resources", "expected_direction",
}
RESOURCE_ID = re.compile(r"^(?:aws:internet|(?:aws|k8s):[a-z0-9-]+:.+)$")
SENSITIVE_PATTERNS = {
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "AWS credential assignment": re.compile(
        r"(?i)\b(?:aws_access_key_id|aws_secret_access_key|aws_session_token)\b\s*[:=]"
    ),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS ARN": re.compile(r"\barn:(?:aws|aws-us-gov|aws-cn):"),
    "12-digit account ID": re.compile(r"(?<!\d)\d{12}(?!\d)"),
}


@dataclass
class Result:
    level: str
    check: str
    detail: str


class Validator:
    def __init__(self) -> None:
        self.results: list[Result] = []

    def pass_(self, check: str, detail: str) -> None:
        self.results.append(Result("PASS", check, detail))

    def fail(self, check: str, detail: str) -> None:
        self.results.append(Result("FAIL", check, detail))

    def warn(self, check: str, detail: str) -> None:
        self.results.append(Result("WARN", check, detail))

    def run_command(self, command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update({
            "AWS_EC2_METADATA_DISABLED": "true",
            "CHECKPOINT_DISABLE": "1",
            "TF_IN_AUTOMATION": "1",
        })
        return subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


def load_json(path: Path, validator: Validator) -> Any | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        validator.fail("JSON", f"{path.relative_to(ROOT)}: {exc}")
        return None
    return value


def validate_scenarios(validator: Validator) -> None:
    directories = sorted(path for path in SCENARIOS.glob("S*-*") if path.is_dir())
    if not directories:
        validator.fail("Scenario discovery", "No S*-* scenario directories found")
        return

    scenario_ids: list[str] = []
    json_count = 0
    yaml_count = 0

    for directory in directories:
        names = {path.name for path in directory.iterdir() if path.is_file()}
        missing = sorted(REQUIRED_FILES - names)
        proposed = sorted(name for name in names if name.startswith("proposed-") and name.endswith((".json", ".yaml", ".yml", ".tf")))
        if missing or not proposed:
            detail = []
            if missing:
                detail.append(f"missing {', '.join(missing)}")
            if not proposed:
                detail.append("missing proposed-change artifact")
            validator.fail("Required files", f"{directory.name}: {'; '.join(detail)}")
        else:
            validator.pass_("Required files", f"{directory.name}: complete")

        parsed_json: dict[str, Any] = {}
        for path in sorted(directory.glob("*.json")):
            value = load_json(path, validator)
            json_count += 1
            if value is not None:
                parsed_json[path.name] = value

        scenario = parsed_json.get("scenario.json")
        expected = parsed_json.get("expected.json")
        twin = parsed_json.get("current-twin.json")
        if not all(isinstance(value, dict) for value in (scenario, expected, twin)):
            continue

        missing_scenario_fields = sorted(REQUIRED_SCENARIO_FIELDS - scenario.keys())
        missing_expected_fields = sorted(REQUIRED_EXPECTED_FIELDS - expected.keys())
        if missing_scenario_fields or missing_expected_fields:
            validator.fail(
                "Basic structure",
                f"{directory.name}: missing fields "
                f"scenario={missing_scenario_fields}, expected={missing_expected_fields}",
            )
        else:
            validator.pass_("Basic structure", f"{directory.name}: required fields present")

        scenario_id = scenario.get("scenario_id")
        if isinstance(scenario_id, str):
            scenario_ids.append(scenario_id)
        if scenario_id != expected.get("scenario_id"):
            validator.fail(
                "Scenario ID match",
                f"{directory.name}: scenario={scenario_id!r}, expected={expected.get('scenario_id')!r}",
            )
        else:
            validator.pass_("Scenario ID match", f"{directory.name}: {scenario_id}")

        resources = twin.get("resources", [])
        relationships = twin.get("relationships", [])
        if not isinstance(resources, list) or not isinstance(relationships, list):
            validator.fail("Resource consistency", f"{directory.name}: invalid Current Twin collections")
            continue
        resource_ids = [item.get("id") for item in resources if isinstance(item, dict)]
        unique_resource_ids = {item for item in resource_ids if isinstance(item, str)}
        problems: list[str] = []
        if len(resource_ids) != len(unique_resource_ids):
            problems.append("resource IDs are missing or duplicated")
        target = scenario.get("target_resource")
        if target not in unique_resource_ids:
            problems.append(f"target {target!r} is absent from current-twin.json")
        if expected.get("expected_changed_resource") != target:
            problems.append("expected_changed_resource does not match target_resource")
        for relationship in relationships:
            if not isinstance(relationship, dict):
                problems.append("relationship is not an object")
                continue
            for endpoint in ("source", "target"):
                if relationship.get(endpoint) not in unique_resource_ids:
                    problems.append(f"relationship {endpoint} {relationship.get(endpoint)!r} is dangling")
        involved = expected.get("structurally_involved_resources", [])
        if not isinstance(involved, list):
            problems.append("structurally_involved_resources is not a list")
        else:
            invalid = [item for item in involved if not isinstance(item, str) or not RESOURCE_ID.match(item)]
            if invalid:
                problems.append(f"invalid involved resource IDs: {invalid}")
        if problems:
            validator.fail("Resource consistency", f"{directory.name}: {'; '.join(problems)}")
        else:
            validator.pass_("Resource consistency", f"{directory.name}: IDs and relationships consistent")

        for path in sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml")):
            yaml_count += 1
            if yaml is None:
                validator.fail("YAML", "PyYAML is not installed; run pip install -r cloudtwin-poc/requirements.txt")
                continue
            try:
                documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
                if not documents or any(document is None for document in documents):
                    raise ValueError("contains an empty YAML document")
            except (OSError, ValueError, yaml.YAMLError) as exc:
                validator.fail("YAML", f"{path.relative_to(ROOT)}: {exc}")

    if len(scenario_ids) != len(set(scenario_ids)):
        validator.fail("Unique scenario IDs", f"duplicates found: {scenario_ids}")
    elif set(scenario_ids) != EXPECTED_IDS:
        validator.fail("Unique scenario IDs", f"expected {sorted(EXPECTED_IDS)}, found {sorted(scenario_ids)}")
    else:
        validator.pass_("Unique scenario IDs", ", ".join(sorted(scenario_ids)))

    if not any(result.level == "FAIL" and result.check == "JSON" for result in validator.results):
        validator.pass_("JSON", f"{json_count} files parsed")
    if yaml is not None and not any(result.level == "FAIL" and result.check == "YAML" for result in validator.results):
        validator.pass_("YAML", f"{yaml_count} files parsed")


def scan_sensitive_content(validator: Validator) -> None:
    findings: list[str] = []
    for path in sorted(item for item in SCENARIOS.rglob("*") if item.is_file()):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(f"binary file: {path.relative_to(ROOT)}")
            continue
        for label, pattern in SENSITIVE_PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{label}: {path.relative_to(ROOT)}")
    if findings:
        validator.fail("Sensitive content", "; ".join(findings))
    else:
        validator.pass_("Sensitive content", "no credentials, keys, account IDs, private keys, or AWS ARNs found")


def terraform_executable() -> str | None:
    executable = shutil.which("terraform")
    if not executable and os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            winget_link = Path(local_app_data) / "Microsoft" / "WinGet" / "Links" / "terraform.exe"
            try:
                if winget_link.exists():
                    executable = str(winget_link)
            except OSError:
                # Restricted environments may deny access outside the workspace.
                pass
    if not executable:
        return None
    try:
        resolved = Path(executable).resolve()
    except OSError:
        resolved = Path(executable)
    return str(resolved if resolved.exists() else Path(executable))


def validate_terraform(validator: Validator) -> None:
    executable = terraform_executable()
    if not executable:
        validator.fail("Terraform availability", "terraform was not found on PATH")
        return
    try:
        version = validator.run_command([executable, "version", "-json"], ROOT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        validator.fail("Terraform availability", f"cannot execute {executable}: {exc}")
        return
    if version.returncode != 0:
        validator.fail("Terraform availability", version.stderr.strip() or version.stdout.strip())
        return
    validator.pass_("Terraform availability", executable)

    try:
        formatted = validator.run_command([executable, "fmt", "-check", "-recursive", str(TERRAFORM)], ROOT)
    except subprocess.TimeoutExpired:
        validator.fail("Terraform format", "terraform fmt -check timed out")
    else:
        if formatted.returncode == 0:
            validator.pass_("Terraform format", "terraform fmt -check -recursive terraform")
        else:
            detail = (formatted.stdout + formatted.stderr).strip()
            validator.fail("Terraform format", detail or "format check failed")

    roots = [
        TERRAFORM / "apprunner" / "default",
        TERRAFORM / "ecs" / "default",
        TERRAFORM / "eks" / "default",
        TERRAFORM / "eks" / "minimal",
    ]
    roots = [path for path in roots if path.is_dir() and any(path.glob("*.tf"))]
    initialized = [path for path in roots if (path / ".terraform").is_dir()]
    skipped = [path for path in roots if path not in initialized]
    for path in initialized:
        try:
            result = validator.run_command([executable, "validate", "-no-color"], path)
        except subprocess.TimeoutExpired:
            validator.fail("Terraform validate", f"{path.relative_to(ROOT)}: timed out")
            continue
        if result.returncode == 0:
            validator.pass_("Terraform validate", str(path.relative_to(ROOT)))
        else:
            detail = (result.stdout + result.stderr).strip()
            validator.fail("Terraform validate", f"{path.relative_to(ROOT)}: {detail}")
    if skipped:
        validator.warn(
            "Terraform validate",
            "not initialized; safely skipped without terraform init: "
            + ", ".join(str(path.relative_to(ROOT)) for path in skipped),
        )
    if not initialized:
        validator.warn("Terraform validate", "no initialized Terraform root modules found")


def print_summary(validator: Validator) -> int:
    print("CloudTwin local infrastructure scenario validation\n")
    for result in validator.results:
        print(f"[{result.level}] {result.check}: {result.detail}")
    counts = {level: sum(result.level == level for result in validator.results) for level in ("PASS", "WARN", "FAIL")}
    print("\nSummary")
    print(f"  PASS: {counts['PASS']}")
    print(f"  WARN: {counts['WARN']}")
    print(f"  FAIL: {counts['FAIL']}")
    print(f"  RESULT: {'FAIL' if counts['FAIL'] else 'PASS'}")
    return 1 if counts["FAIL"] else 0


def main() -> int:
    validator = Validator()
    validate_scenarios(validator)
    scan_sensitive_content(validator)
    validate_terraform(validator)
    return print_summary(validator)


if __name__ == "__main__":
    sys.exit(main())
