# Resource criticality decision

CloudTwin does not currently have an explicit resource-criticality model.
Finding severity describes the seriousness of an observed condition; it is not
the importance of the affected resource. Blast radius currently uses directed
dependency paths and distance, not business importance.

A standalone `LOW | MEDIUM | HIGH | CRITICAL` resource-criticality contract
would improve later prioritization, but it needs an authoritative input such as
service ownership, environment, data classification, SLO, or an explicit
scenario annotation. It must not be inferred from graph degree. No runtime
criticality implementation is added in this milestone because the fixtures do
not yet carry that authoritative data and inventing it would distort risk.

Recommended future seam: a mapping from canonical resource ID to criticality,
provenance, and confidence, supplied to scoring as a separate optional input.
Existing scores must remain unchanged until that contract and weights are
approved and tested.
