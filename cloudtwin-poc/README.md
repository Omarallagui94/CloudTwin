# CloudTwin AI - Automatic Discovery and Current Twin

This proof of concept performs read-only discovery of the AWS account in `eu-west-3` and the Kubernetes cluster selected by the local kubeconfig. It normalizes the discovered resources and relationships into a Current Digital Twin, then renders that twin as an interactive HTML graph.

It does not run Terraform, apply Kubernetes manifests, or change cloud resources. The proposed scenario is data only and is not executed.

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
