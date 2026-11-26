# CloudTwin AI - Automatic Discovery and Current Twin

This proof of concept performs read-only discovery of the AWS account in `eu-west-3` and the Kubernetes cluster selected by the local kubeconfig. It normalizes the discovered resources and relationships into a Current Digital Twin, then renders that twin as an interactive HTML graph.

It does not run Terraform, apply Kubernetes manifests, or change cloud resources. The proposed scenario is data only and is not executed.

## Gemini Change Assistant

The **Describe custom change** flow can use the Gemini Developer API as a
natural-language adapter. Gemini produces a validated proposed intent; CloudTwin
then resolves the target and all current values from the active Current Twin and
converts the proposal through the existing canonical `ChangeSet` ingestion path.
Simulation, State Diff, affected resources, deterministic rules, blast radius,
severity, and risk score remain authoritative CloudTwin calculations. Gemini may
explain those results, but cannot replace or modify them.

Configuration is read only from the process environment:

```text
GEMINI_API_KEY=               # required to enable Gemini; no default
GEMINI_MODEL=gemini-2.5-flash # optional
GEMINI_TIMEOUT_SECONDS=15     # optional
```

Copy `.env.example` only as a local reference; never commit a populated `.env`
or bake the key into a container image. The repository ignores `.env` files. If
Gemini is missing or fails, supported requests fall back to the existing local
deterministic parser and the UI reports that fallback. Ambiguous requests are
rejected without simulation.

Gemini receives only sanitized resource identifiers, types, names, namespaces,
and the minimum deterministic analysis facts needed for explanation. It never
receives credentials, Kubernetes Secret contents, or permission to call AWS,
Kubernetes, Terraform, or deployment APIs. CloudTwin remains simulation-only.

Local manual test (PowerShell):

```powershell
$env:GEMINI_API_KEY="<set-locally-by-user>"
$env:GEMINI_MODEL="gemini-2.5-flash"
$env:GEMINI_TIMEOUT_SECONDS="15"
python -m streamlit run app.py
```

Open **Change Assistant**, select **Describe custom change**, enter `Expose the
ui service publicly`, inspect the interpretation and **Structured Change JSON**,
then click **Confirm for Analysis**. Verify the Future Twin, State Diff,
deterministic findings/risk, and Gemini explanation. No real infrastructure is
changed.

## Discovered resources

AWS discovery includes VPCs, subnets, security groups, EC2 instances, EKS clusters and managed node groups, Elastic Load Balancers (v2 and classic), and IAM roles.

Kubernetes discovery includes namespaces, nodes, deployments, pods, services, StatefulSets, service accounts, ingresses, and network policies. Kubernetes Secrets are not read.

Relationships are created only from discovered metadata, including VPC and subnet membership, EKS/node-group membership, node provider IDs, Kubernetes selectors, pod scheduling, service accounts, ingress backends, and load-balancer hostnames.

## Prerequisites

- Python 3.10 or newer
- AWS CLI authentication that can read the listed AWS services
- A kubeconfig whose current context points to the `retail-store` EKS cluster
- Network access to AWS APIs and the Kubernetes API server

Confirm access from Windows CMD:

```bat
aws sts get-caller-identity
kubectl config current-context
kubectl get nodes
```

AWS authentication uses Boto3's standard credential resolution chain. No access key or secret is stored by this project. Kubernetes authentication uses the existing kubeconfig, normally `%USERPROFILE%\.kube\config`, including its configured EKS authentication command.

## Install

Run these commands in Windows CMD:

```bat
cd path\to\CloudTwin\cloudtwin-poc
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

## Discover the Current Twin

```bat
python discovery\discover.py
```

The command continues when an individual AWS service or Kubernetes resource API cannot be read. It reports a warning and leaves that collection empty. Review warnings before using the output in a demonstration.

The normalized result is written to:

```text
outputs\current_state.json
```

Each run replaces that generated file with a fresh snapshot of the live environment.

## Build the interactive graph

After discovery, run:

```bat
python visualization\build_graph.py
```

Open the generated graph in a browser:

```bat
start outputs\current_twin.html
```

Hover over a node to inspect its discovered properties. Use the selection and filter menus to reduce visual noise in a large account.

## Run the Streamlit dashboard

The dashboard tests the configured AWS, EKS, and Kubernetes connections, runs the same read-only collectors, displays live metrics, and embeds the interactive graph:

```bat
streamlit run app.py
```

Use **Test Connection** in the sidebar, then **Discover Infrastructure** to regenerate both output files from the current environment.

## Run the HTTP API

The API exposes the same simulation as the dashboard and `python -m simulation` (no AWS or Kubernetes call):

```bat
uvicorn api.main:app --reload
```

Interactive documentation: http://localhost:8000/docs. Main routes:

| Route | Purpose |
| --- | --- |
| `GET /health` | API status and number of rules |
| `GET /rules` | Rule catalog |
| `GET /scenarios` | Demonstration scenarios S01 to S05 |
| `POST /simulations` | Simulate a change sent in the body (Current Twin + Terraform plan, Kubernetes manifest or scenario) |
| `POST /simulations/scenario/{id}` | Simulate a demonstration scenario (`?explain=true&fixes=true` adds the AI explanation and verified fixes) |

Unreadable input returns `400` with the reason; an unknown scenario returns `404`.

## Proposed future change

`scenarios\scenario-01.json` describes a proposed increase of the `ui` Deployment from one to three replicas. It is not applied. A later Future Twin engine can consume this scenario, create a virtual future state, compare it with the Current Twin, and assess impact and risk.

## Project layout

```text
cloudtwin-poc\
|-- discovery\
|   |-- aws_collector.py
|   |-- kubernetes_collector.py
|   |-- normalizer.py
|   `-- discover.py
|-- visualization\
|   `-- build_graph.py
|-- scenarios\
|   `-- scenario-01.json
|-- outputs\
|   |-- current_state.json
|   `-- current_twin.html
|-- requirements.txt
`-- README.md
```

`current_state.json` and `current_twin.html` are generated artifacts. The JSON snapshot may contain account identifiers, ARNs, private IP addresses, and resource metadata; review it before sharing publicly.
