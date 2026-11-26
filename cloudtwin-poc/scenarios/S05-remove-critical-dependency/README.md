# S05 — Remove critical dependency

This scenario removes `k8s:service:default:orders-postgresql`, the stable network endpoint between the Orders workload and PostgreSQL. It represents dependency unavailability without deleting data or implementing blast-radius analysis.

Use `proposed-delete.json` locally as an explicit delete operation against the sanitized Current Twin. For a local behavioral demonstration, an expendable Kind cluster could delete the Service while leaving its StatefulSet intact. AWS is not required.

Expected result: the database Service becomes absent, availability decreases, and the Orders Deployment, Orders Service, and PostgreSQL StatefulSet are structurally involved. The expected file deliberately does not assign impact severity or risk.

Cleanup: fixture-only use requires none. On Kind, recreate the Service from the Helm chart or delete the cluster. A future EKS reproduction should restore the Service, validate recovery, then destroy and verify the entire temporary AWS environment.

## Automated local Kind reproduction

Run from the repository root with Docker, Kind, kubectl, and the PoC Python environment available:

```powershell
cloudtwin-poc\.venv\Scripts\python.exe cloudtwin-poc\scripts\validate_s05_kind.py
```

The local baseline uses the same canonical identities as `current-twin.json`: Orders Deployment/Service and PostgreSQL StatefulSet/Service. The Orders Deployment is a lightweight dependency probe rather than the full application; it references `orders-postgresql` and proves DNS plus TCP port `5432` reachability. PostgreSQL uses its real chart image with ephemeral storage and local trust authentication.

The runner reads `proposed-delete.json`, verifies that it targets `Service/default/orders-postgresql`, executes that delete intent, confirms the Service and reachability are gone while the PostgreSQL StatefulSet remains, and writes sanitized evidence to `evidence/s05-kind-validation.json`.

The cluster is deleted in the cleanup path even after validation failure. Success requires Kind to confirm that the cluster is absent.
