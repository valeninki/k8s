# Flux bootstrap gate

This directory stages the ArgoCD-to-Flux migration without reconciling any
workload. All `HelmRelease` and application `Kustomization` resources are
suspended. Flux's bootstrap `GitRepository` is the sole Git source; the
temporary bootstrap credential is replaced with the read-only runtime key
immediately after controller installation.

The `forgejo-flux-runtime` Secret is intentionally not stored in Git. It must
contain `identity`, `identity.pub`, and verified `known_hosts` data before the
bootstrap `flux-system` GitRepository is switched to it.
