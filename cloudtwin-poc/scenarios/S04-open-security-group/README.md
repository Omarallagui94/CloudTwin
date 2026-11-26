# S04 — Open security group

This scenario models a Terraform plan that adds `0.0.0.0/0` TCP ingress on PostgreSQL port `5432` to a synthetic Orders database security group. The identifier is deliberately non-real.

Use `proposed-plan.json` locally as a Terraform-plan-parser fixture. It must never be passed to `terraform apply`. The Current Twin matches the existing collector's count-only security-group properties; rule-level CIDR and port details come from the plan artifact. A real validation would require a temporary AWS security group and is unnecessary for basic Future Twin, Diff, and deterministic security tests.

Expected result: the security group's ingress rule set changes and network exposure increases. The PostgreSQL workload and Internet node are obvious structural participants. The fixture does not assert severity, blast radius, or a risk score.

Cleanup: none locally. If explicitly approved for future AWS validation, remove the public rule immediately, destroy the temporary stack, and verify the security group and all supporting billable resources are gone.
