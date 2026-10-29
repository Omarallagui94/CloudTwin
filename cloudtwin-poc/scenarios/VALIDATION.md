# Local scenario validation

The validation harness checks Omar's infrastructure scenario fixtures without contacting AWS, applying Terraform, deploying Kubernetes resources, or implementing CloudTwin analysis logic.

It performs basic structural validation because no Hamdi-owned canonical schema is currently present in the repository. When canonical schemas are added, the harness should consume them rather than introducing schemas here.

## Prerequisites

- Python 3.10 or newer
- Dependencies from `cloudtwin-poc/requirements.txt`
- Terraform on `PATH`
- Previously initialized Terraform roots if `terraform validate` should run for those roots

From the repository root on Windows PowerShell:

```powershell
python -m pip install -r cloudtwin-poc/requirements.txt
python cloudtwin-poc/scripts/validate_scenarios.py
```

With the existing PoC virtual environment:

```powershell
cloudtwin-poc\.venv\Scripts\python.exe cloudtwin-poc\scripts\validate_scenarios.py
```

On macOS or Linux:

```bash
python3 -m pip install -r cloudtwin-poc/requirements.txt
python3 cloudtwin-poc/scripts/validate_scenarios.py
```

The command exits with code `0` on success and `1` when any required check fails. Warnings do not change the exit code.

`terraform fmt -check -recursive terraform` covers the repository Terraform code. `terraform validate` runs only in root modules whose provider/module dependencies are already initialized. The harness never runs `terraform init`, so it does not download providers or contact a backend. Uninitialized roots are reported as warnings.

## Team usage

- **Omar:** run after changing Terraform, Kubernetes fixtures, scenarios, or infrastructure expectations.
- **Hamdi:** run before consuming fixtures in Future Twin, Diff, plan parsing, or dependency tests.
- **Fatma:** run before consuming expectations in security, resilience, blast-radius, or risk tests.

The validator checks fixture integrity only. A PASS does not assert that Hamdi's or Fatma's analysis output is correct.
