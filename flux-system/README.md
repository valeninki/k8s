# Flux

Flux deploys this repository (branch `k0s`) from the in-cluster Forgejo via the
bootstrap `flux-system` GitRepository. Everything the cluster runs is defined
under `layers/`, applied in order:

| Layer | Contents |
|-------|----------|
| `l2-namespaces` | Namespaces |
| `l2-sops-secrets` | Empty placeholder for encrypted Secrets (see its README) |
| `l3-storage` | Longhorn (owns the default `longhorn` StorageClass) |
| `l3-platform` | cert-manager, MetalLB, Traefik, CloudNativePG, reflector, kured |
| `l4-prerequisites` | ClusterIssuer, wildcard certificate, MetalLB pool, Calico IP reservation, Forgejo database |
| `l5-applications` | Applications, the Forgejo runner and bot CronJobs |
| `l6-routes` | Traefik IngressRoutes, Middlewares and public Services |

All layers use `wait: true` and `prune: false`: removing a manifest from Git
does **not** delete the object from the cluster, so delete it by hand when
that is intended. The root `flux-system` Kustomization (this directory) does
prune, which covers `sources.yaml` and the layer definitions themselves.

The `forgejo-flux-runtime` Secret is intentionally not stored in Git. It must
contain `identity`, `identity.pub`, and verified `known_hosts` data for the
`flux-system` GitRepository. Other Secrets created outside Git are listed in
`layers/l2-sops-secrets/README.md`.
