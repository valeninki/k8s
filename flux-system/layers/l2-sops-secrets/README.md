# SOPS Secret Layer

This layer is intentionally empty until an off-cluster SOPS age or KMS root of
trust is provisioned. It will contain encrypted Secrets for the infrastructure
and application credentials referenced by the later layers.

The Flux Git SSH identity is not placed here because source-controller needs it
before Flux can fetch this repository. Bootstrap automation must provision that
Secret first.
