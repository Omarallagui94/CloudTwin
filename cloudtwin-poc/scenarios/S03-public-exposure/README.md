# S03 — Public exposure

This scenario changes the internal `k8s:service:default:ui` Service from `ClusterIP` to an internet-facing AWS NLB-backed `LoadBalancer`.

The manifest can be parsed and diffed completely locally. Kind can validate the Kubernetes object shape, but it cannot reproduce an AWS NLB. Real load-balancer creation eventually requires a temporary EKS cluster, correctly tagged subnets, the AWS Load Balancer Controller, and deployment IAM.

Expected result: Service type and scheme change, exposure increases, and a Future Twin may add synthetic/real Internet and Load Balancer nodes connected to the Service. Those nodes are structural expectations, not instructions to provision them.

Cleanup: local fixture use requires none. Future AWS validation must delete the Service, wait for the NLB to disappear, destroy the cluster/VPC, and verify that no load balancer, target group, security group, Elastic IP, EC2 instance, NAT Gateway, or EKS cluster remains.
