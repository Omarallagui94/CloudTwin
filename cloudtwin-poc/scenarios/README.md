# CloudTwin infrastructure scenario pack

This directory contains local, deterministic infrastructure-change fixtures owned by Omar. The scenarios are inputs and expected structural outcomes for the Future Twin, Diff, dependency, security, resilience, blast-radius, and risk components owned by other team members. They do not implement those components and must not be applied directly.

Each scenario contains:

- `scenario.json`: the proposed operation in a common, tool-neutral shape.
- `current-twin.json`: a minimal sanitized Current Twin using the IDs emitted by `discovery/normalizer.py`.
- A proposed Kubernetes or Terraform-plan-like change artifact.
- `expected.json`: infrastructure-level assertions, not risk findings or scores.
- `README.md`: reproduction and cleanup guidance.

| ID | Scenario | Proposed artifact | AWS needed for local use |
|---|---|---|---|
| S01 | Scale UI from 1 to 3 replicas | Kubernetes Deployment | No |
| S02 | Scale UI from 3 to 1 replica | Kubernetes Deployment | No |
| S03 | Expose the UI Service publicly | Kubernetes Service | No |
| S04 | Open PostgreSQL to `0.0.0.0/0` | Terraform-plan-like JSON | No |
| S05 | Remove the Orders PostgreSQL Service | Kubernetes delete intent | No |

All AWS identifiers are synthetic. The fixtures contain no account IDs, ARNs, credentials, secrets, IP addresses, or snapshots of the former environment.

Run the local validation harness after changing the pack:

```powershell
cloudtwin-poc\.venv\Scripts\python.exe cloudtwin-poc\scripts\validate_scenarios.py
```

See [VALIDATION.md](VALIDATION.md) for prerequisites, behavior, and team usage.
