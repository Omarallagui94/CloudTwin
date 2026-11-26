# CloudTwin local professor demo

This demonstration is local and read-only. It does not run Terraform apply,
mutate Kubernetes, or change AWS resources.

## Start

```powershell
cd cloudtwin-poc
.venv\Scripts\python.exe -m streamlit run app.py
```

1. Open **Overview** and show Local Fixture Mode, Current Twin counts, and the pipeline.
2. Open **Current Twin**, demonstrate the icon-based views, filters, selection, and technical IDs.
3. Open **Change Assistant** and run S01. Confirm the normalized ChangeSet, Future Twin, and State Diff.
4. Explain that S01 improves replicas and produces no active resilience finding.
5. Run S05 and show the removed PostgreSQL Service and its two removed relationships.
6. Show the direct Orders Deployment path and transitive Orders Service path.
7. Open **Analysis** and show RULE-RES-003, blast radius, the 11/100 score, remediation, and deterministic explanation.
8. Generate and download the sanitized HTML simulation report.
9. State explicitly that Current Twin stayed unchanged and no AWS/Kubernetes mutation occurred.

Security validation: S03 triggers public-Service rule NET-004. S04 exposes
PostgreSQL 5432 to `0.0.0.0/0`, triggering NET-002 and critical NET-003.

## Local validation

```powershell
$env:PYTHONUTF8='1'
.venv\Scripts\python.exe -m pytest . -q
.venv\Scripts\python.exe scripts\validate_scenarios.py
```
