
import json
import sys
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schema" / "v1" / "twin.schema.json"
SNAPSHOT_PATH = (
    Path(sys.argv[1]) if len(sys.argv) > 1
    else ROOT / "fixtures" / "sample_current_state.json"
)

ALLOWED_RELATIONSHIPS = {
    "CONTAINS", "LOCATED_IN", "USES", "MEMBER_OF",
    "RUNS_ON", "SELECTS", "EXPOSES", "ROUTES_TO",
}


def validate_snapshot():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    errors = list(Draft202012Validator(schema).iter_errors(snapshot))
    for error in errors:
        print(f"SCHEMA ERROR at {list(error.path)}: {error.message}")

    resources = snapshot.get("resources", [])
    relationships = snapshot.get("relationships", [])
    ids = [resource.get("id") for resource in resources]
    known_ids = set(ids)

    duplicates = [resource_id for resource_id, count in Counter(ids).items()
                  if count > 1]
    for resource_id in duplicates:
        print(f"DUPLICATE ID: {resource_id}")

    for edge in relationships:
        if edge.get("type") not in ALLOWED_RELATIONSHIPS:
            print(f"INVALID RELATIONSHIP TYPE: {edge.get('type')}")
        if edge.get("source") not in known_ids:
            print(f"DANGLING SOURCE: {edge.get('source')}")
        if edge.get("target") not in known_ids:
            print(f"DANGLING TARGET: {edge.get('target')}")

    summary = snapshot.get("summary", {})
    actual_aws = sum(r.get("provider") == "aws" for r in resources)
    actual_k8s = sum(r.get("provider") == "kubernetes" for r in resources)

    if summary.get("aws_resources") != actual_aws:
        print(f"SUMMARY ERROR: aws_resources should be {actual_aws}")
    if summary.get("kubernetes_resources") != actual_k8s:
        print(f"SUMMARY ERROR: kubernetes_resources should be {actual_k8s}")
    if summary.get("relationships") != len(relationships):
        print(f"SUMMARY ERROR: relationships should be {len(relationships)}")

    failed = (
        bool(errors) or bool(duplicates)
        or any(edge.get("type") not in ALLOWED_RELATIONSHIPS
               or edge.get("source") not in known_ids
               or edge.get("target") not in known_ids
               for edge in relationships)
        or summary.get("aws_resources") != actual_aws
        or summary.get("kubernetes_resources") != actual_k8s
        or summary.get("relationships") != len(relationships)
    )

    print(f"Snapshot: {SNAPSHOT_PATH}")
    print("RESULT:", "FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(validate_snapshot())
