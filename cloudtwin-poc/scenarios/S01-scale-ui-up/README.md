# S01 — Scale UI up

This scenario increases `k8s:deployment:default:ui` from one replica to three. The expected infrastructure interpretation is increased workload redundancy; it is not a resilience score.

Reproduce locally by comparing `current-twin.json` with `proposed-deployment.yaml`, or by applying the manifest to an expendable local Kind cluster. AWS is not required. A future real-EKS validation may use the same replica change after checking node capacity.

Expected result: one Deployment changes its `replicas` property from `1` to `3`; the Service remains structurally connected and exposure is unchanged.

Cleanup: no cleanup is needed for fixture-only use. On Kind, scale the Deployment back to one replica or delete the cluster. On future EKS, revert to one replica before destroying and verifying the temporary environment.

## Automated local Kind reproduction

Prerequisites:

- Docker CLI with Docker Desktop or another Docker daemon running
- Kind on `PATH`
- kubectl on `PATH`
- Python 3.10 or newer

From the repository root, run:

```powershell
cloudtwin-poc\.venv\Scripts\python.exe cloudtwin-poc\scripts\validate_s01_kind.py
```

The workflow performs:

```text
CREATE cloudtwin-validation
→ APPLY baseline-deployment.yaml
→ WAIT up to 10 minutes for the real UI image pull and verify 1 ready replica
→ APPLY proposed-deployment.yaml
→ WAIT FOR and verify 3 ready replicas
→ WRITE sanitized evidence
→ DELETE cluster
→ VERIFY cluster absence
```

Evidence is written to `evidence/s01-kind-validation.json`. The directory is ignored by Git because it contains run-specific validation output. The evidence includes replica counts, timestamps, and cleanup status only; it excludes kubeconfig data, endpoints, host details, pod names, container IDs, and image-pull credentials.

The runner refuses to touch a cluster named `cloudtwin-validation` if it already exists. A different disposable name and output path can be supplied explicitly:

```powershell
cloudtwin-poc\.venv\Scripts\python.exe cloudtwin-poc\scripts\validate_s01_kind.py `
  --cluster-name cloudtwin-validation-s01 `
  --evidence cloudtwin-poc\scenarios\S01-scale-ui-up\evidence\custom-run.json
```

Cleanup is attempted in a `finally` block whenever this run attempted cluster creation. A successful command exits with code `0` only after Kind confirms the cluster is absent.
