# S02 — Scale UI down

This scenario reduces `k8s:deployment:default:ui` from three replicas to one. The expected infrastructure interpretation is reduced redundancy; no risk score is asserted.

Reproduce locally by comparing the sanitized Current Twin with the proposed Deployment, or apply it to an expendable Kind cluster. AWS is not required.

Expected result: the Deployment's `replicas` property changes from `3` to `1`; the Service relationship remains and network exposure is unchanged.

Cleanup: fixture-only use requires none. On Kind, restore three replicas or delete the cluster. A future EKS run must restore the baseline and then destroy and verify the temporary AWS environment.

## Automated local Kind reproduction

With Docker running and Kind plus kubectl available, run from the repository root:

```powershell
cloudtwin-poc\.venv\Scripts\python.exe cloudtwin-poc\scripts\validate_s02_kind.py
```

The runner creates `cloudtwin-validation`, deploys the real Retail Store UI at three replicas, waits for all three replicas to become ready, applies the existing one-replica S02 proposal, verifies one desired and ready replica, writes sanitized evidence to `evidence/s02-kind-validation.json`, and always attempts cluster deletion and absence verification.

The evidence directory is ignored by Git. The runner refuses to modify or delete a cluster with the requested name if that cluster existed before the run.
